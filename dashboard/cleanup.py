#!/usr/bin/env python3
"""
dashboard/cleanup.py

Clean all dashboard data and scrape outputs so the pipeline can be re-run
from scratch. Deletes:
  - output/<site>/*.json and .seen.json
  - output/rejected/
  - dashboard/data.json
  - data/consolidated/noida_properties.db
  - data/consolidated/*.bak
  - data/fallback_*.json, data/yield_history.json

Run:  python dashboard/cleanup.py
"""

import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT / "output"
DATA = PROJECT / "data"
DASHBOARD = PROJECT / "dashboard"
CONSOLIDATED = DATA / "consolidated"


def _rm(path: Path, label: str) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"  Deleted {label}: {path}")


def main():
    print("Cleaning dashboard data and listings for fresh re-run...\n")

    # ── 1. Scrape outputs ──
    if OUTPUT.exists():
        for site_dir in sorted(OUTPUT.iterdir()):
            if not site_dir.is_dir() or site_dir.name == "rejected":
                continue
            for f in site_dir.rglob("*"):
                if f.is_file() and f.suffix in (".json", ".csv"):
                    _rm(f, f"scrape output [{site_dir.name}]")
            # remove empty site dirs
            remaining = list(site_dir.iterdir())
            if not remaining:
                _rm(site_dir, f"empty site dir [{site_dir.name}]")
    else:
        print("  (no output/ directory)")

    # ── 2. Rejected records ──
    rejected = OUTPUT / "rejected"
    if rejected.exists():
        _rm(rejected, "rejected records")
    else:
        print("  (no output/rejected/)")

    # ── 3. Dashboard data.json ──
    dj = DASHBOARD / "data.json"
    if dj.exists():
        _rm(dj, "dashboard data.json")
    else:
        print("  (no dashboard/data.json)")

    # ── 4. SQLite database ──
    db = CONSOLIDATED / "noida_properties.db"
    if db.exists():
        _rm(db, "SQLite database")
    else:
        print("  (no noida_properties.db)")

    # ── 5. DB backups ──
    if CONSOLIDATED.exists():
        for bak in CONSOLIDATED.glob("*.bak"):
            _rm(bak, "DB backup")
    else:
        print("  (no data/consolidated/)")

    # ── 6. Scraper state files ──
    if DATA.exists():
        for f in DATA.glob("fallback_*.json"):
            _rm(f, "scraper state")
        for f in DATA.glob("yield_history*.json"):
            _rm(f, "yield history")
    else:
        print("  (no data/ directory)")

    print("\nCleanup complete. Ready to re-run scrape service.")


if __name__ == "__main__":
    main()
