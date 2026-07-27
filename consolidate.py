import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from normalizer import normalize
from models.property import PropertyListing
from storage import Storage
from dedup import store_duplicates

from pydantic import ValidationError


REJECTED_DIR = Path("output/rejected")


def read_all_json(site_dir: Path) -> list[dict]:
    json_files = sorted(f for f in site_dir.glob("*.json") if not f.name.startswith("."))
    all_records = []
    for f in json_files:
        with open(f, encoding="utf-8-sig") as fh:
            all_records.extend(json.load(fh))
    return all_records


def write_rejection(raw: dict, norm: dict, error: str):
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = REJECTED_DIR / f"{date_str}.jsonl"
    entry = {
        "rejected_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": error,
        "normalized": {k: v for k, v in norm.items() if not k.startswith("_")},
        "raw": raw,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def consolidate_sites(site_names: list[str] | None = None, output_dir: str = "output", run_dedup: bool = True):
    storage = Storage()
    all_ids = []
    total_rejected = 0

    site_dirs = [d for d in Path(output_dir).iterdir() if d.is_dir()]
    for site_dir in site_dirs:
        name = site_dir.name
        if site_names and name not in site_names:
            continue
        records = read_all_json(site_dir)
        if not records:
            print(f"[SKIP] {name}: no data found")
            continue

        valid_raw = []
        valid_norm = []
        for raw in records:
            norm = normalize(raw)

            # Locality allowlist rejection
            if norm.get("_rejected"):
                write_rejection(raw, norm, norm["_rejected_reason"])
                total_rejected += 1
                continue

            # Pydantic schema validation
            try:
                PropertyListing(**{k: v for k, v in norm.items() if not k.startswith("_")})
            except ValidationError as e:
                msg = "; ".join(f"{err['loc']}: {err['msg']}" for err in e.errors())
                write_rejection(raw, norm, msg)
                total_rejected += 1
                continue

            valid_raw.append(raw)
            valid_norm.append(norm)

        if not valid_raw:
            print(f"[SKIP] {name}: all {len(records)} records rejected")
            continue

        ids = storage.upsert_many(valid_raw, normalized=valid_norm)
        all_ids.extend(ids)
        rejected_in_site = len(records) - len(valid_raw)
        print(f"[OK] {name}: {len(valid_raw)}/{len(records)} records consolidated ({len(ids)} upserted, {rejected_in_site} rejected)")

    counts = storage.get_counts_by_site()
    print("\n" + "=" * 50)
    print("CONSOLIDATION SUMMARY")
    print("=" * 50)
    for c in counts:
        print(f"  {c['site']:20s}: {c['count']} listings")
    if total_rejected:
        print(f"\n  ** {total_rejected} records rejected (see output/rejected/)")
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
