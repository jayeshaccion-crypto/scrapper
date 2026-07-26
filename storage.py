import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

from normalizer import normalize


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site TEXT NOT NULL,
    listing_id TEXT,
    url TEXT,
    title TEXT,
    price_inr REAL,
    price_per_sqft_inr REAL,
    area_sqft REAL,
    bhk INTEGER,
    property_type TEXT,
    furnishing TEXT,
    floor TEXT,
    total_floors INTEGER,
    age_years INTEGER,
    locality TEXT,
    city TEXT DEFAULT 'Noida',
    seller_type TEXT,
    latitude REAL,
    longitude REAL,
    amenities TEXT,
    raw_data TEXT,
    scraped_at_utc TEXT,
    schema_version TEXT,
    consolidated_at_utc TEXT,
    UNIQUE(source_site, listing_id)
);

CREATE TABLE IF NOT EXISTS possible_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_a_id INTEGER,
    listing_b_id INTEGER,
    confidence REAL,
    match_reason TEXT,
    created_at_utc TEXT,
    FOREIGN KEY(listing_a_id) REFERENCES listings(id),
    FOREIGN KEY(listing_b_id) REFERENCES listings(id)
);

CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source_site);
CREATE INDEX IF NOT EXISTS idx_listings_locality ON listings(locality);
CREATE INDEX IF NOT EXISTS idx_listings_bhk ON listings(bhk);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price_inr);
"""


class Storage:
    def __init__(self, db_path: str = "data/consolidated/noida_properties.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def upsert_listing(self, record: dict, normalized: dict | None = None) -> int | None:
        norm = normalized if normalized is not None else normalize(record)
        listing_id = norm.get("listing_id")
        if not listing_id:
            listing_id = record.get("listing_url") or record.get("url")
        source_site = norm.get("source_site")

        if not listing_id or not source_site:
            return None

        now = datetime.now(timezone.utc).isoformat()
        amenities_json = json.dumps(norm.get("amenities", []) or [])
        raw_json = json.dumps(record, default=str, ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO listings
               (source_site, listing_id, url, title, price_inr, price_per_sqft_inr,
                area_sqft, bhk, property_type, furnishing, floor, total_floors,
                age_years, locality, city, seller_type, latitude, longitude,
                amenities, raw_data, scraped_at_utc, schema_version, consolidated_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_site, listing_id) DO UPDATE SET
                price_inr=excluded.price_inr,
                price_per_sqft_inr=excluded.price_per_sqft_inr,
                area_sqft=excluded.area_sqft,
                title=excluded.title,
                furnishing=excluded.furnishing,
                locality=excluded.locality,
                raw_data=excluded.raw_data,
                consolidated_at_utc=excluded.consolidated_at_utc""",
            (
                source_site, listing_id, norm.get("url"), norm.get("title"),
                norm.get("price_inr"), norm.get("price_per_sqft_inr"),
                norm.get("area_sqft"), norm.get("bhk"), norm.get("property_type"),
                norm.get("furnishing"), norm.get("floor"), norm.get("total_floors"),
                norm.get("age_years"), norm.get("locality"), norm.get("city"),
                norm.get("seller_type"), norm.get("latitude"), norm.get("longitude"),
                amenities_json, raw_json, record.get("scraped_at", now),
                norm.get("schema_version"), now,
            ),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM listings WHERE source_site=? AND listing_id=?",
            (source_site, listing_id),
        ).fetchone()[0]

    def upsert_many(self, records: list[dict], normalized: list[dict] | None = None) -> list[int]:
        ids = []
        for i, r in enumerate(records):
            norm = normalized[i] if normalized else None
            lid = self.upsert_listing(r, normalized=norm)
            if lid:
                ids.append(lid)
        return ids

    def get_all_listings(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT source_site, listing_id, url, title, price_inr, bhk,
                      locality, city, property_type, furnishing, area_sqft,
                      price_per_sqft_inr, latitude, longitude
               FROM listings ORDER BY source_site, price_inr"""
        ).fetchall()
        cols = ["source_site", "listing_id", "url", "title", "price_inr", "bhk",
                "locality", "city", "property_type", "furnishing", "area_sqft",
                "price_per_sqft_inr", "latitude", "longitude"]
        return [dict(zip(cols, r)) for r in rows]

    def get_counts_by_site(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT source_site, COUNT(*) as count FROM listings GROUP BY source_site ORDER BY source_site"
        ).fetchall()
        return [{"site": r[0], "count": r[1]} for r in rows]

    def add_duplicate_pair(self, id_a: int, id_b: int, confidence: float, reason: str):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO possible_duplicates (listing_a_id, listing_b_id, confidence, match_reason, created_at_utc) VALUES (?,?,?,?,?)",
            (id_a, id_b, confidence, reason, now),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
