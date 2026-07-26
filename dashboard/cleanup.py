#!/usr/bin/env python3
"""
dashboard/cleanup.py

Backfill PII redaction on existing database records that were stored
before Phase 4 PII/XSS protections were added (commit 4fe0ee6).

What it does:
  1. Backs up the SQLite DB (noida_properties.db → *.pre_cleanup.bak)
  2. Redacts Indian phone numbers and emails from raw_data JSON blobs
     and from DB text columns (title, locality, seller_type, etc.)
  3. Rebuilds data.json with full XSS sanitization

Run manually:  python dashboard/cleanup.py
"""

import html
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# Reuse PII redaction from normalizer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from normalizer import _redact_pii, _PII_TEXT_FIELDS  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "consolidated" / "noida_properties.db"
BACKUP_SUFFIX = ".pre_cleanup.bak"

# DB text columns (besides raw_data) that should be redacted for PII
_DB_TEXT_COLUMNS = ["title", "locality", "seller_type", "furnishing", "property_type"]

# Fields that appear in raw_data dicts (mirrors normalizer._PII_TEXT_FIELDS)
_PII_RAW_FIELDS = list(_PII_TEXT_FIELDS)


def _redact_raw_data(raw_json: str | None) -> str | None:
    """Redact PII from a raw_data JSON string. Returns original if unchanged."""
    if not raw_json:
        return raw_json
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json
    if not isinstance(data, dict):
        return raw_json

    changed = False
    for field in _PII_RAW_FIELDS:
        val = data.get(field)
        if isinstance(val, str):
            redacted = _redact_pii(val)
            if redacted != val:
                data[field] = redacted
                changed = True

    return json.dumps(data, ensure_ascii=False) if changed else raw_json


def main():
    if not DB.exists():
        print(f"Database not found: {DB}")
        sys.exit(1)

    print(f"Database: {DB}")

    # ── Step 1: Backup ──
    backup = DB.with_suffix(DB.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(str(DB), str(backup))
        print(f"  Backup:  {backup}")
    else:
        print(f"  Backup:  {backup} (exists, skipped)")

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Step 2: Count rows ──
    total = cur.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"  Records: {total}")

    # ── Step 3: Redact raw_data blobs ──
    rows = cur.execute(
        "SELECT id, raw_data FROM listings"
    ).fetchall()

    updated_raw = 0
    for row in rows:
        new_raw = _redact_raw_data(row["raw_data"])
        if new_raw != row["raw_data"]:
            cur.execute("UPDATE listings SET raw_data = ? WHERE id = ?",
                        (new_raw, row["id"]))
            updated_raw += 1

    conn.commit()
    print(f"  raw_data redacted:  {updated_raw} / {total}")

    # ── Step 4: Redact DB text columns ──
    updated_cols = 0
    for col in _DB_TEXT_COLUMNS:
        rows = cur.execute(f"SELECT id, {col} FROM listings WHERE {col} IS NOT NULL").fetchall()
        for row in rows:
            new_val = _redact_pii(row[col])
            if new_val != row[col]:
                cur.execute(f"UPDATE listings SET {col} = ? WHERE id = ?",
                            (new_val, row["id"]))
                updated_cols += 1

    conn.commit()
    conn.close()
    print(f"  DB columns redacted: {updated_cols}")

    # ── Step 5: Rebuild data.json ──
    print(f"\n  Rebuilding data.json ...")
    result = subprocess.run(
        [sys.executable, "dashboard/build-data.py"],
        capture_output=True, text=True, cwd=DB.parent.parent,
    )
    if result.returncode:
        print(f"  ERROR: build-data.py failed:\n{result.stderr}")
        sys.exit(1)
    print(f"  {result.stdout.strip()}")

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
