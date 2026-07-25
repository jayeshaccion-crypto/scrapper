import time
import json
import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from scrapling.fetchers import Fetcher, StealthyFetcher

RATE_LIMIT_MIN = 1.0


class Deduplicator:
    def __init__(self, site_name: str, seen_file: str | None = None):
        self.site_name = site_name
        if seen_file is None:
            seen_file = Path("output") / site_name / ".seen.json"
        self.seen_file = Path(seen_file)
        self.seen_file.parent.mkdir(parents=True, exist_ok=True)
        self.seen: set[str] = set()
        self._load()

    def _load(self):
        if self.seen_file.exists():
            with open(self.seen_file) as f:
                self.seen = set(json.load(f))

    def _save(self):
        with open(self.seen_file, "w") as f:
            json.dump(list(self.seen), f)

    def is_duplicate(self, record: dict) -> bool:
        key_source = (
            record.get("prop_id")
            or record.get("listing_url")
            or record.get("link")
            or record.get("title")
            or json.dumps(record, sort_keys=True)
        )
        if isinstance(key_source, list):
            key_source = json.dumps(key_source, sort_keys=True)
        key = hashlib.sha256(str(key_source).encode()).hexdigest()
        if key in self.seen:
            return True
        self.seen.add(key)
        self._save()
        return False


class BaseScraper:
    def __init__(self, config: dict, dry_run: bool = False, max_items: int | None = None):
        self.config = config
        self.dry_run = dry_run
        self.max_items = max_items
        self.name = config["name"]
        self.fetcher_type = config.get("fetcher", "basic")
        self.selectors = config["selectors"]
        self.pagination = config.get("pagination", {})
        self.rate_limit = max(config.get("rate_limit_seconds", RATE_LIMIT_MIN), RATE_LIMIT_MIN)
        self.output_format = config.get("output_format", "json")
        self.respect_robots = config.get("respect_robots", True)
        self._last_request_time = 0.0
        self.dedup = Deduplicator(self.name) if not dry_run else None
        self._robots_cache: dict[str, RobotFileParser] = {}

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_time = time.time()

    def _check_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        netloc = parsed.netloc
        if netloc not in self._robots_cache:
            rp = RobotFileParser(f"{parsed.scheme}://{netloc}/robots.txt")
            try:
                rp.read()
            except Exception:
                return True
            self._robots_cache[netloc] = rp
        return self._robots_cache[netloc].can_fetch("*", url)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _fetch_url(self, url: str):
        self._rate_limit()
        if not self._check_robots(url):
            print(f"[WARN] {self.name}: robots.txt disallows {url}")
            return None
        kwargs = {"headless": True}
        if self.fetcher_type in ("dynamic", "stealthy"):
            kwargs["network_idle"] = True
            kwargs["load_dom"] = True
            kwargs["timeout"] = 90000
        if self.config.get("wait_selector"):
            kwargs["wait_selector"] = self.config["wait_selector"]
        if self.fetcher_type == "stealthy":
            kwargs["solve_cloudflare"] = True
            return StealthyFetcher.fetch(url, **kwargs)
        elif self.fetcher_type == "dynamic":
            kwargs["solve_cloudflare"] = False
            return StealthyFetcher.fetch(url, **kwargs)
        else:
            return Fetcher.get(url)

    def _extract_js_var(self, body: str, var_name: str) -> str | None:
        pattern = re.compile(
            rf'(?:window\.)?{re.escape(var_name)}\s*=\s*(\{{)',
            re.DOTALL
        )
        m = pattern.search(body)
        if not m:
            return None
        start = m.start(1)
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(body)):
            ch = body[i]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return body[start:i + 1]
        return None

    def _follow_path(self, data, path: str):
        parts = path.split(".")
        for p in parts:
            if isinstance(data, list):
                if not data:
                    return None
                if p.lstrip('-').isdigit():
                    data = data[int(p)]
                else:
                    data = data[0]
            if isinstance(data, dict) and p in data:
                data = data[p]
            else:
                return None
        return data

    def _extract_items_json_embed(self, page) -> list[dict]:
        body = page.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        embed = self.config.get("embed", {})
        var_name = embed.get("var", "")
        data_path = embed.get("data_path", "")
        filter_config = embed.get("filter", {})
        field_map = self.selectors.get("fields", {})

        if not var_name or not data_path:
            print(f"[ERROR] {self.name}: embed.var and embed.data_path required")
            return []

        raw = self._extract_js_var(body, var_name)
        if not raw:
            print(f"[ERROR] {self.name}: Could not find {var_name} in page")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[ERROR] {self.name}: Failed to parse {var_name}: {e}")
            return []

        items = self._follow_path(data, data_path)
        if items is None:
            print(f"[ERROR] {self.name}: Path '{data_path}' not found in data")
            return []
        if not isinstance(items, list):
            print(f"[ERROR] {self.name}: Path '{data_path}' is not a list")
            return []

        records = []
        for item in items:
            if filter_config:
                f_field = filter_config.get("field", "")
                f_contains = filter_config.get("contains", "")
                f_match = filter_config.get("match", "")
                val = str(item.get(f_field, ""))
                if f_contains and f_contains not in val:
                    continue
                if f_match and val != f_match:
                    continue

            record = {"site_name": self.name, "scraped_at": datetime.now(timezone.utc).isoformat()}
            for field_name, json_key in field_map.items():
                if json_key:
                    record[field_name] = item.get(json_key)
            if self.dedup and self.dedup.is_duplicate(record):
                continue
            records.append(record)
        return records

    def _extract_items_jsonld(self, page) -> list[dict]:
        body = page.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        embed = self.config.get("embed", {})
        allowed_types = embed.get("types", ["Product", "SingleFamilyResidence", "Apartment", "House"])
        field_map = self.selectors.get("fields", {})
        filter_config = embed.get("filter", {})

        raw_blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
        )

        records = []
        for raw in raw_blocks:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if data.get("@type") in allowed_types:
                    items = [data]
                elif data.get("@type") == "ItemList":
                    elements = data.get("itemListElement", [])
                    for el in elements:
                        if isinstance(el, dict):
                            item_data = el.get("item", el)
                            if item_data.get("@type") in allowed_types:
                                items.append(item_data)

            for item in items:
                if filter_config:
                    f_field = filter_config.get("field", "")
                    f_contains = filter_config.get("contains", "")
                    f_match = filter_config.get("match", "")
                    val = str(self._follow_path(item, f_field) if "." in f_field else item.get(f_field, ""))
                    if f_contains and f_contains not in val:
                        continue
                    if f_match and val != f_match:
                        continue

                record = {"site_name": self.name, "scraped_at": datetime.now(timezone.utc).isoformat()}
                for field_name, json_key in field_map.items():
                    if json_key:
                        if "." in json_key:
                            record[field_name] = self._follow_path(item, json_key)
                        else:
                            record[field_name] = item.get(json_key)
                record = self._clean_jsonld_record(record)
                if self.dedup and self.dedup.is_duplicate(record):
                    continue
                records.append(record)
                if self.max_items and len(records) >= self.max_items:
                    return records
        return records

    def _clean_jsonld_record(self, record: dict) -> dict:
        for k, v in record.items():
            if isinstance(v, dict) and "@type" in v:
                record[k] = None
        return record

    def _extract_field_value(self, item, css_sel: str):
        sel = item.css(css_sel, auto_save=True)
        values = [v.strip() for v in sel.getall() if v and v.strip()]
        if len(values) == 0:
            return None
        elif len(values) == 1:
            return values[0]
        else:
            return values

    def _extract_items(self, page) -> list[dict]:
        parser = self.config.get("parser", "css")
        if parser == "json_embed":
            return self._extract_items_json_embed(page)
        if parser == "jsonld":
            return self._extract_items_jsonld(page)

        items_container = page.css(self.selectors["item"], auto_save=True)
        records = []
        for item in items_container:
            record = {"site_name": self.name, "scraped_at": datetime.now(timezone.utc).isoformat()}
            for field_name, css_sel in self.selectors["fields"].items():
                record[field_name] = self._extract_field_value(item, css_sel)
            if self.dedup and self.dedup.is_duplicate(record):
                continue
            records.append(record)
            if self.max_items and len(records) >= self.max_items:
                break
        return records

    def _get_next_url(self, page, base_url: str) -> str | None:
        next_sel = self.pagination.get("next_selector")
        if not next_sel:
            return None
        sel = page.css(next_sel, auto_save=True)
        href = sel.get()
        if href:
            return urljoin(base_url, href.strip())
        return None

    def run(self) -> list[dict]:
        all_records = []
        max_pages = self.pagination.get("max_pages", 1)
        urls_to_visit = list(self.config["start_urls"])

        for page_num in range(1, max_pages + 1):
            if not urls_to_visit:
                break
            url = urls_to_visit.pop(0)
            try:
                page = self._fetch_url(url)
            except Exception as e:
                print(f"[ERROR] {self.name}: Failed to fetch {url}: {e}")
                continue
            if page is None:
                continue

            records = self._extract_items(page)
            all_records.extend(records)
            print(f"[OK] {self.name}: Page {page_num} -> {len(records)} items")

            if self.max_items and len(all_records) >= self.max_items:
                print(f"[OK] {self.name}: Reached limit of {self.max_items} items, stopping")
                break

            if page_num < max_pages:
                next_url = self._get_next_url(page, url)
                if next_url:
                    urls_to_visit.append(next_url)

        return all_records[:self.max_items] if self.max_items else all_records

    def write_output(self, records: list[dict]):
        if not records:
            print(f"[SKIP] {self.name}: No records to write")
            return
        out_dir = Path("output") / self.name
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.output_format == "csv":
            path = out_dir / f"{date_str}.csv"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
        else:
            path = out_dir / f"{date_str}.json"
            with open(path, "w", encoding="utf-8-sig") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        print(f"[SAVED] {self.name}: {len(records)} records -> {path}")
