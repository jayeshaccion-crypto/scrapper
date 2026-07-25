import argparse
import json
from pathlib import Path

from storage import Storage
from dedup import store_duplicates


def read_all_json(site_dir: Path) -> list[dict]:
    json_files = sorted(f for f in site_dir.glob("*.json") if not f.name.startswith("."))
    all_records = []
    for f in json_files:
        with open(f, encoding="utf-8-sig") as fh:
            all_records.extend(json.load(fh))
    return all_records


def consolidate_sites(site_names: list[str] | None = None, output_dir: str = "output", run_dedup: bool = True):
    storage = Storage()
    all_ids = []

    site_dirs = [d for d in Path(output_dir).iterdir() if d.is_dir()]
    for site_dir in site_dirs:
        name = site_dir.name
        if site_names and name not in site_names:
            continue
        records = read_all_json(site_dir)
        if not records:
            print(f"[SKIP] {name}: no data found")
            continue
        ids = storage.upsert_many(records)
        all_ids.extend(ids)
        print(f"[OK] {name}: {len(records)} records consolidated ({len(ids)} upserted)")

    counts = storage.get_counts_by_site()
    print("\n" + "=" * 50)
    print("CONSOLIDATION SUMMARY")
    print("=" * 50)
    for c in counts:
        print(f"  {c['site']:20s}: {c['count']} listings")
    print("=" * 50)

    if run_dedup and len(counts) > 1:
        print("\nRunning cross-site dedup...")
        matches = store_duplicates(storage, min_confidence=0.7)
        print(f"  Found {len(matches)} possible duplicate pairs")
        for m in matches[:10]:
            a, b = m["listing_a"], m["listing_b"]
            print(f"  [{m['confidence']:.2f}] {a['source_site']}/{a['title'][:40]} <-> {b['source_site']}/{b['title'][:40]}")
            print(f"         Reasons: {m['reasons']}")
        if len(matches) > 10:
            print(f"  ... and {len(matches) - 10} more")

    storage.close()
    return all_ids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate scraped data into SQLite")
    parser.add_argument("--sites", nargs="*", help="Site names to consolidate (default: all)")
    parser.add_argument("--no-dedup", action="store_true", help="Skip dedup step")
    args = parser.parse_args()

    consolidate_sites(site_names=args.sites, run_dedup=not args.no_dedup)
