"""
Scrapling Spider integration for property scraping.

Architecture:
  PropertySpider(Spider) — Async Spider subclass that drives all site scraping.
    Directly subclasses Scrapling's Spider framework for session management,
    concurrency, and adaptive caching.

  BaseScraper — Synchronous facade wrapping PropertySpider for CLI compatibility
    (main.py). Not a Spider subclass. The sync wrapper avoids forcing CLI code
    into an async event loop. All actual scraping logic lives in PropertySpider.
"""

import json
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from scrapling.spiders import Spider, Request
from scrapling.fetchers import (
    FetcherSession,
    AsyncDynamicSession,
    AsyncStealthySession,
)

RATE_LIMIT_MIN = 1.0


class PropertySpider(Spider):
    """Scrapling Spider subclass that drives all site scraping."""

    def __init__(self, config: dict, dry_run: bool = False, max_items: int | None = None):
        self._config = config
        self._dry_run = dry_run
        self._max_items = max_items
        self._records: list[dict] = []
        self._page_count = 0

        # Set Spider class attributes from config
        self.name = config["name"]
        self.start_urls = []
        urls = config.get("start_urls", [])
        # Respect max_pages for URL-limited sites
        max_pages = config.get("pagination", {}).get("max_pages", 1)
        self.start_urls = urls[:max_pages]

        self.concurrent_requests = 1  # polite per-site
        self.download_delay = max(config.get("rate_limit_seconds", RATE_LIMIT_MIN), RATE_LIMIT_MIN)
        self.robots_txt_obey = config.get("respect_robots", True)
        self.allowed_domains = set()

        # Adaptive parsing cache (only for sites that explicitly opt in)
        has_cache = "adaptive_cache" in config
        cache_dir = config.get("adaptive_cache", ".scrapling_cache")
        self.development_mode = has_cache
        self.development_cache_dir = cache_dir

        # Proxy
        self._proxy_config = config.get("proxy", {})
        self._fetcher_type = config.get("fetcher", "dynamic")
        self._parser_type = config.get("parser", "css")
        self._selectors = config.get("selectors", {})
        self._embed = config.get("embed", {})
        self._pagination = config.get("pagination", {})
        self._location_filter = config.get("location_filter", {})

        super().__init__()

    def configure_sessions(self, manager):
        ft = self._fetcher_type
        proxy = self._proxy_config.get("url") if self._proxy_config.get("enabled") else None
        wait_sel = self._config.get("wait_selector")

        session_kwargs = dict(
            headless=True,
            load_dom=True,
            proxy=proxy,
            adaptive=True,
        )
        if wait_sel:
            session_kwargs["wait_selector"] = wait_sel

        if ft == "stealthy":
            session = AsyncStealthySession(
                network_idle=True,
                timeout=90000,
                solve_cloudflare=True,
                **session_kwargs,
            )
            manager.add("default", session, default=True)
        elif ft == "dynamic":
            network_idle = self._config.get("network_idle", True)
            session = AsyncDynamicSession(
                network_idle=network_idle,
                timeout=120000,
                **session_kwargs,
            )
            manager.add("default", session, default=True)
        else:
            session = FetcherSession(**session_kwargs)
            manager.add("default", session, default=True)

    async def start_requests(self):
        for url in self.start_urls:
            yield Request(url, sid="default")

    async def parse(self, response):
        self._page_count += 1
        parser = self._parser_type

        if parser == "json_embed":
            items = self._parse_json_embed(response)
        elif parser == "jsonld":
            items = self._parse_jsonld(response)
        else:
            items = self._parse_css(response)

        for item in items:
            yield item

        # Pagination: follow next page if within max_pages
        max_pages = self._pagination.get("max_pages", 1)
        next_sel = self._pagination.get("next_selector")
        if next_sel and self._page_count < max_pages:
            href = response.css(next_sel, auto_save=True)
            if href:
                next_url = urljoin(str(response.url), href.get().strip())
                yield Request(next_url, sid="default")

    # ------------------------------------------------------------------ #
    #  Parser implementations (adapted from BaseScraper, now generators)  #
    # ------------------------------------------------------------------ #

    def _parse_json_embed(self, response):
        body = response.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        var_name = self._embed.get("var", "")
        data_path = self._embed.get("data_path", "")
        filter_config = self._embed.get("filter", {})
        field_map = self._selectors.get("fields", {})

        if not var_name or not data_path:
            return []

        raw = self._extract_js_var(body, var_name)
        if not raw:
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        items = self._follow_path(data, data_path)
        if not isinstance(items, list):
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
            records.append(record)
            if self._max_items and len(records) >= self._max_items:
                break
        return records

    def _parse_jsonld(self, response):
        body = response.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        allowed_types = self._embed.get("types", ["Product", "SingleFamilyResidence", "Apartment", "House"])
        field_map = self._selectors.get("fields", {})
        filter_config = self._embed.get("filter", {})

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
                    for el in data.get("itemListElement", []):
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
                self._clean_jsonld_record(record)
                records.append(record)
                if self._max_items and len(records) >= self._max_items:
                    return records
        return records

    def _parse_css(self, response):
        item_sel = self._selectors.get("item", "")
        if not item_sel:
            return []
        items_container = response.css(item_sel, auto_save=True)
        fields = self._selectors.get("fields", {})

        records = []
        for item in items_container:
            record = {"site_name": self.name, "scraped_at": datetime.now(timezone.utc).isoformat()}
            for field_name, css_sel in fields.items():
                record[field_name] = self._extract_field_value(item, css_sel)
            records.append(record)
            if self._max_items and len(records) >= self._max_items:
                break
        return records

    # ------------------------------------------------------------------ #
    #  Helper methods (ported from BaseScraper)                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_js_var(body: str, var_name: str):
        # Try JS variable assignment pattern first: window.VAR = {...}
        pattern = re.compile(rf'(?:window\.)?{re.escape(var_name)}\s*=\s*(\{{)', re.DOTALL)
        m = pattern.search(body)
        start = m.start(1) if m else None
        # Fall back to Next.js <script id="VAR">JSON</script> pattern
        if not m:
            pattern2 = re.compile(rf'<script[^>]*?id="{re.escape(var_name)}"[^>]*?>\s*(\{{)', re.DOTALL)
            m2 = pattern2.search(body)
            if m2:
                start = m2.start(1)
        if start is None:
            return None
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

    @staticmethod
    def _follow_path(data, path: str):
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
                continue
            if isinstance(data, dict) and p in data:
                data = data[p]
            else:
                return None
        return data

    @staticmethod
    def _clean_jsonld_record(record: dict):
        for k, v in record.items():
            if isinstance(v, dict) and "@type" in v:
                record[k] = None

    @staticmethod
    def _extract_field_value(item, css_sel: str):
        sel = item.css(css_sel, auto_save=True)
        values = [v.strip() for v in sel.getall() if v and v.strip()]
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        return values

    # ------------------------------------------------------------------ #
    #  Hooks                                                             #
    # ------------------------------------------------------------------ #

    async def on_scraped_item(self, item: dict) -> dict | None:
        item = self._apply_filters(item)
        if item is None:
            return None
        self._records.append(item)
        return item

    def _apply_filters(self, record: dict) -> dict | None:
        filt = self._location_filter
        if not filt:
            return record
        field = filt.get("field", "location")
        terms = filt.get("contains", [])
        if isinstance(terms, str):
            terms = [terms]
        if not terms:
            return record
        val = str(record.get(field) or "")
        if not any(t.lower() in val.lower() for t in terms):
            return None
        return record


# ------------------------------------------------------------------ #
#  Synchronous wrapper (keeps BaseScraper API compatible)            #
# ------------------------------------------------------------------ #

class BaseScraper:
    """Synchronous facade that wraps PropertySpider for CLI compatibility."""

    _fallback_tracker: dict[str, int] = {}  # class-level: site -> consecutive zero runs
    _anomaly_tracker: dict[str, int] = {}  # class-level: site -> consecutive anomaly runs

    def __init__(self, config: dict, dry_run: bool = False, max_items: int | None = None):
        self.config = config
        self.dry_run = dry_run
        self.max_items = max_items
        self.name = config["name"]
        self.fetcher_type = config.get("fetcher", "basic")
        self.output_format = config.get("output_format", "json")
        self.respect_robots = config.get("respect_robots", True)
        self.dedup = None  # dedup now handled by Spider's fingerprint mechanism

    def run(self) -> list[dict]:
        config = dict(self.config)

        # Auto fallback: zero-yield (>=3) or anomaly (>=2) triggers stealthy switch
        if self.fetcher_type == "dynamic":
            key = self.name
            prev = BaseScraper._fallback_tracker.get(key, 0)
            anomaly = BaseScraper._anomaly_tracker.get(key, 0)
            if anomaly >= 2:
                print(f"[FALLBACK] {self.name}: {anomaly} consecutive anomalies, switching to stealthy")
                config["fetcher"] = "stealthy"
                self.fetcher_type = "stealthy"
            elif prev >= 3:
                print(f"[FALLBACK] {self.name}: {prev} consecutive zero-yield runs, switching to stealthy")
                config["fetcher"] = "stealthy"
                self.fetcher_type = "stealthy"

        spider = PropertySpider(config, dry_run=self.dry_run, max_items=self.max_items)

        if self.dry_run:
            spider.development_mode = False  # don't cache in dry-run

        result = spider.start()
        records = spider._records

        # Apply dedup filter using raw output if needed
        if self.dedup:
            before = len(records)
            records = [r for r in records if not self.dedup.is_duplicate(r)]
            if len(records) < before:
                print(f"[DEDUP] {self.name}: removed {before - len(records)} duplicates")

        # Track fallback
        if self.fetcher_type == "dynamic":
            if len(records) == 0:
                BaseScraper._fallback_tracker[self.name] = BaseScraper._fallback_tracker.get(self.name, 0) + 1
            else:
                BaseScraper._fallback_tracker[self.name] = 0

        return records[:self.max_items] if self.max_items else records

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
