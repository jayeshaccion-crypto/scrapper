import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "consolidated" / "noida_properties.db"
OUT = Path(__file__).resolve().parent / "data.json"

if not DB.exists():
    print(f"DB not found at {DB}, writing empty data.json")
    OUT.write_text("[]", encoding="utf-8")
    sys.exit(0)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM listings ORDER BY source_site, price_inr").fetchall()
conn.close()

props = []
for row in rows:
    r = dict(row)
    raw = json.loads(r.get("raw_data") or "{}") if r.get("raw_data") else {}
    # Merge normalized fields with raw data (raw takes precedence for display)
    merged = {**r, **raw}
    merged.pop("raw_data", None)
    merged.pop("amenities", None)
    # Extract first available image
    img = raw.get("photo_url") or raw.get("medium_photo_url") or raw.get("image") or ""
    if not img and raw.get("property_images"):
        imgs = raw["property_images"]
        if isinstance(imgs, list) and imgs:
            img = imgs[0] if isinstance(imgs[0], str) else (imgs[0].get("url") or "")
    if not img and raw.get("all_images"):
        imgs = raw["all_images"]
        if isinstance(imgs, list) and imgs:
            img = imgs[0] if isinstance(imgs[0], str) else ""
    if not img and raw.get("thumbnail_images"):
        imgs = raw["thumbnail_images"]
        if isinstance(imgs, list) and imgs:
            img = imgs[0] if isinstance(imgs[0], str) else (imgs[0].get("url") or "")
    if not img and raw.get("allImgPath"):
        imgs = raw["allImgPath"]
        if isinstance(imgs, list) and imgs:
            img = imgs[0] if isinstance(imgs[0], str) else ""
    merged["image_url"] = img
    props.append(merged)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {len(props)} properties to {OUT}")
