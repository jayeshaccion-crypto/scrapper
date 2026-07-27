import argparse
import json
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scrapling.fetchers import Fetcher, StealthyFetcher

from scrapers.registry import get_scraper
from scrapers.base import BaseScraper

Fetcher.configure(adaptive=True)
StealthyFetcher.configure(adaptive=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"logs/run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scraper")

FALLBACK_STATE_PATH = Path("data/fallback_state.json")
ANOMALY_STATE_PATH = Path("data/fallback_anomaly.json")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config(path: str = "config/sites.yaml") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["sites"]


def run_site(config: dict, dry_run: bool = False, max_items: int | None = None) -> dict:
    start = time.time()
    name = config["name"]
    log.info("Starting scrape: %s", name)
    try:
        scraper = get_scraper(config, dry_run=dry_run, max_items=max_items)
        records = scraper.run()
        duration = time.time() - start
        new_count = len(records)
        error = None
        written = 0
        failed = 0
        if not dry_run and records:
            try:
                scraper.write_output(records)
                written = len(records)
            except Exception as e:
                error = str(e)
                failed = len(records)
        log.info("Finished %s: %d items in %.1fs", name, new_count, duration)
        return {"name": name, "new": new_count, "written": written, "failed": failed, "error": error, "duration": duration}
    except Exception as e:
        duration = time.time() - start
        log.error("Failed %s: %s", name, e)
        return {"name": name, "new": 0, "written": 0, "failed": 0, "error": str(e), "duration": duration}


def main():
    parser = argparse.ArgumentParser(description="Multi-site web scraper using Scrapling")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site", type=str, help="Run a single site by name")
    group.add_argument("--all", action="store_true", help="Run all sites in config")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse without saving")
    parser.add_argument("--limit", type=int, default=None, help="Max items per site")
    args = parser.parse_args()

    sites = load_config()
    if args.site:
        matched = [s for s in sites if s["name"] == args.site]
        if not matched:
            log.error("Site '%s' not found in config", args.site)
            sys.exit(1)
        targets = matched
    else:
        targets = sites

    # Load persistent fallback state across CI runs
    BaseScraper._fallback_tracker = _load_json(FALLBACK_STATE_PATH)
    BaseScraper._anomaly_tracker = _load_json(ANOMALY_STATE_PATH)

    results = []
    for config in targets:
        result = run_site(config, dry_run=args.dry_run, max_items=args.limit)
        results.append(result)

    # Save zero-yield fallback state for next run
    _save_json(FALLBACK_STATE_PATH, BaseScraper._fallback_tracker)

    total_new = sum(r["new"] for r in results)
    total_failed = sum(1 for r in results if r["error"])
    total_ok = sum(1 for r in results if not r["error"])

    print("\n" + "=" * 72)
    print(f"  RUN SUMMARY  ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})")
    print("=" * 72)
    print(f"  {'SITE':<20} {'NEW':>6} {'WROTE':>6} {'FAIL':>6} {'TIME':>8}")
    print("  " + "-" * 48)
    for r in results:
        print(f"  {r['name']:<20} {r['new']:>6} {r['written']:>6} {r['failed']:>6} {r['duration']:>7.1f}s")
    print("  " + "-" * 48)
    print(f"  {'TOTAL':<20} {total_new:>6} {sum(r['written'] for r in results):>6} {total_failed:>6} {'':>8}")
    print(f"  Sites OK: {total_ok}, Failed: {total_failed}")
    print("=" * 72)

    if total_ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
