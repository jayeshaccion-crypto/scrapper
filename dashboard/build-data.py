import html
import json
import sqlite3
import sys
from pathlib import Path

# Free-text fields that must be HTML-escaped before dashboard rendering
# to prevent stored XSS via poisoned listing content.
_XSS_TEXT_FIELDS = {
    "title", "description", "locality", "seller_type", "builder",
    "contact_name", "full_address", "sub_locality", "building_name",
    "society_name", "furnishing", "property_type", "floor", "status",
    "possession", "ownership", "overlooking", "transaction_type",
    "flooring", "parking", "facing", "project", "developer",
    "listing_url", "site_name",
}

DB = Path(__file__).resolve().parent.parent / "data" / "consolidated" / "noida_properties.db"
OUT = Path(__file__).resolve().parent / "data.json"

SITE_BASE = {
    "99acres": "https://www.99acres.com",
    "magicbricks": "https://www.magicbricks.com",
    "squareyards": "https://www.squareyards.com",
    "olx": "https://www.olx.in",
    "proptiger": "https://www.proptiger.com",
    "proptiger-flats": "https://www.proptiger.com",
    "housing": "https://housing.com",
    "makaan": "https://www.makaan.com",
}

if not DB.exists():
    print(f"DB not found at {DB}, writing empty data.json")
    OUT.write_text("[]", encoding="utf-8")
    sys.exit(0)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM listings ORDER BY scraped_at_utc DESC, source_site, price_inr").fetchall()
conn.close()

props = []
for row in rows:
    r = dict(row)
    raw = json.loads(r.get("raw_data") or "{}") if r.get("raw_data") else {}
    merged = {**r, **raw}
    merged.pop("raw_data", None)
    merged.pop("amenities", None)
    site = merged.get("site_name") or r.get("source_site") or ""
    base = SITE_BASE.get(site, "")

    # Resolve listing URL
    url = r.get("url") or raw.get("listing_url") or raw.get("url") or ""
    if url and not url.startswith("http"):
        if url.startswith("/"):
            url = base + url
        elif base:
            url = base + "/" + url
    merged["listing_url"] = url

    # Extract first available image
    img = ""
    for field in ("photo_url", "medium_photo_url", "image"):
        val = raw.get(field)
        if isinstance(val, str):
            img = val
            break
        if isinstance(val, dict):
            img = val.get("url") or val.get("@id") or ""
            if img:
                break
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

    # XSS sanitization: escape HTML-unsafe characters in free-text fields
    for k in _XSS_TEXT_FIELDS:
        val = merged.get(k)
        if isinstance(val, str):
            merged[k] = html.escape(val)

    props.append(merged)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {len(props)} properties to {OUT}")
