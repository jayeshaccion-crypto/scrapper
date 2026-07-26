import re
import json
from pathlib import Path


# ------------------------------------------------------------------ #
#  PII Redaction Patterns                                            #
# ------------------------------------------------------------------ #
# Indian mobile phone: +91 prefix (optional - or space) or domestic 0
# prefix, then 10 digits starting with 6-9 (DoT numbering plan).
# Catches: 9876543210, +919876543210, +91-9876543210, 09876543210.
_PII_PHONE_RE = re.compile(r"(?:\+91[\-\s]?|0)?[6-9]\d{9}")
# RFC 5322 simplified email pattern
_PII_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


# Free-text fields in raw records that may contain PII and should be
# redacted before DB upsert.
_PII_TEXT_FIELDS: frozenset[str] = frozenset([
    "title", "description", "contact_name", "builder",
    "full_address", "sub_locality", "building_name", "society_name",
    "locality", "location", "seller_type", "user_type",
])


def _redact_pii(text: str | None) -> str | None:
    """Replace Indian phone numbers and email addresses with [REDACTED]."""
    if not text:
        return text
    text = _PII_PHONE_RE.sub("[REDACTED]", text)
    text = _PII_EMAIL_RE.sub("[REDACTED]", text)
    return text


# ------------------------------------------------------------------ #
#  Noida Locality Allowlist                                          #
# ------------------------------------------------------------------ #
# Source: Manual curation of Noida, Greater Noida, and surrounding
#   sectors as of July 2026. Covers all residential sectors notified
#   by Noida Authority, Greater Noida Authority, and Yamuna Expressway
#   Industrial Development Authority (YEIDA).
#
# Tokens are lowercase; the matcher normalizes input before comparison.
# ------------------------------------------------------------------ #
NOIDA_LOCALITY_PATTERNS: list[re.Pattern] = [
    # Core Noida tokens (require "noida" proximity for sector matches)
    re.compile(r"noida\s*extension"),
    re.compile(r"greater\s+noida"),
    re.compile(r"(?:^|\W)noida(?:\W|$)"),
    # Sector patterns — standalone sector numbers match because scrapers
    # already filter by Noida URL; "Gurgaon Sector 45" is excluded at
    # the scraper level via location_filter.
    re.compile(r"noida\s+sector\s+\d{1,3}[a-z]?"),
    re.compile(r"sector\s+\d{1,3}[a-z]?\s+(?:noida|greater\s+noida)"),
    re.compile(r"(?:^|\W)sector\s+\d{1,3}[a-z]?(?:\W|$)"),
    # Landmark / zone tokens specific to Noida region
    re.compile(r"yamuna\s+expressway"),
    re.compile(r"gamma\s+\d{1,2}"),
    re.compile(r"zeta\s+\d{1,2}"),
    re.compile(r"beta\s+\d{1,2}"),
    re.compile(r"alpha\s+\d{1,2}"),
    re.compile(r"delta\s+\d{1,2}"),
    re.compile(r"omega\s+\d{1,2}"),
    re.compile(r"pari\s*(?:j|chowk)"),
    re.compile(r"knowledge\s+park"),
    re.compile(r"film\s+city"),
    re.compile(r"ecotech"),
    re.compile(r"techzone"),
]

# Fallback substring tokens when locality field is empty
NOIDA_SUBSTRINGS: list[str] = [
    "noida", "greater noida", "sector", "expressway", "ecotech",
]


def matches_noida_locality(locality: str | None) -> bool:
    """Check a locality string against the Noida allowlist.

    Returns True if the locality matches any allowed Noida/Greater Noida
    pattern. Falls back to substring matching for backwards compatibility
    with unstructured data.
    """
    if not locality:
        return False
    text = locality.lower()
    for pat in NOIDA_LOCALITY_PATTERNS:
        if pat.search(text):
            return True
    return False


def matches_noida_fallback(text: str | None) -> bool:
    """Substring-based Noida check used when locality field is empty."""
    if not text:
        return False
    t = text.lower()
    return any(s in t for s in NOIDA_SUBSTRINGS)


def _parse_price_india(v):
    text = v.replace(',', '').replace('Onwards', '').replace('Onward', '').strip()
    if 'Cr' in text:
        return round(float(text.replace('Cr', '').replace('Lac', '').replace(' L ', ' ').replace('L', ' ').strip()) * 10000000)
    if 'Lac' in text or ' L ' in f' {text} ':
        return round(float(text.replace('Lac', '').replace(' L ', ' ').replace('L', ' ').strip()) * 100000)
    if 'K' in text:
        return round(float(text.replace('K', '').strip()) * 1000)
    return None


def _parse_area_sqft(v):
    m = re.search(r'([\d,]+)\s*(?:-|\sto\s)', v)
    if m:
        return float(m.group(1).replace(',', ''))
    m = re.search(r'[\d,]+', v)
    if m:
        return float(m.group().replace(',', ''))
    return None


def _parse_bhk(v):
    m = re.search(r'(\d+)', v)
    return int(m.group(1)) if m else None


_PRICE_UNITS = {"cr": 10_000_000, "crore": 10_000_000, "lac": 100_000, "lakh": 100_000, "k": 1_000}


def _extract_price_from_text(text: str) -> float | None:
    """Extract price from natural-language text like 'Priced at 45 lakh' or 'Rs. 1.36 Cr'."""
    if not text:
        return None
    m = re.search(
        r'(?:Rs\.?\s*|price[d]?\s*(?:at\s+)?|is\s+)?(\d+\.?\d*)\s*'
        r'(cr|crore|lac|lakh|k)\b',
        text, re.I,
    )
    if m:
        val = float(m.group(1))
        unit = m.group(2).lower()
        multiplier = _PRICE_UNITS.get(unit)
        if multiplier:
            return round(val * multiplier)
    # Standalone L/lakh abbreviation — require a space before L to avoid
    # false positives like "Sector 12L"
    m = re.search(r'(\d+\.?\d*)\s+[Ll]\b', text)
    if m:
        return round(float(m.group(1)) * 100_000)
    return None


def _parse_olx_area(v):
    m = re.search(r'([\d,]+)\s*sq\s*ft', v, re.I)
    return float(m.group(1).replace(',', '')) if m else None


def _parse_olx_property_type(v):
    if 'for-rent' in v.lower() or '/rent/' in v.lower():
        return 'rent'
    if 'for-sale' in v.lower() or '/sale/' in v.lower():
        return 'sale'
    return None


_FURNISH_KEYWORDS = [
    ("unfurnished", "Unfurnished"),
    ("semi furnished", "Semi-Furnished"),
    ("semifurnished", "Semi-Furnished"),
    ("fully furnished", "Furnished"),
    ("furnished", "Furnished"),
]


def _parse_olx_furnishing(v):
    vl = v.lower()
    for kw, label in _FURNISH_KEYWORDS:
        if kw in vl:
            return label
    return None


def _parse_bhk_float(v):
    """Handle values like '3.5', '6+', '2.5' by flooring to integer."""
    m = re.search(r'([\d.]+)', str(v))
    if m:
        try:
            return int(float(m.group(1)))
        except ValueError:
            pass
    m = re.search(r'(\d+)', str(v))
    return int(m.group(1)) if m else None


SITE_FIELD_MAP = {
    "99acres": {
        "source_site": ("site_name", None),
        "listing_id": ("prop_id", None),
        "url": ("listing_url", lambda v: f"https://www.99acres.com{v}" if v and v.startswith("/") else v),
        "title": ("title", None),
        "price_inr": ("price_raw", lambda v: float(v) if v and str(v).replace(".", "").isdigit() else None),
        "price_per_sqft_inr": ("price_per_sqft", lambda v: float(v) if v else None),
        "area_sqft": ("carpet_sqft", lambda v: float(v) if v else None),
        "bhk": ("bedrooms", _parse_bhk_float),
        "property_type": ("property_type", None),
        "furnishing": ("furnish", lambda v: {2: "semi", 1: "full", 3: "unfurnished", 4: "unfurnished"}.get(int(v)) if v and str(v).strip().isdigit() else None),
        "floor": ("floor", None),
        "total_floors": ("total_floors", lambda v: int(v) if v and str(v).isdigit() else None),
        "age_years": ("age", lambda v: int(v) if v and str(v).isdigit() else None),
        "locality": ("locality", None),
        "city": ("city", None),
        "seller_type": ("contact_name", None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": ("top_usps", lambda v: v if isinstance(v, list) else []),
    },
    "magicbricks": {
        "source_site": ("site_name", None),
        "listing_id": ("prop_id", None),
        "url": ("listing_url", lambda v: f"https://www.magicbricks.com/{v}" if v and not v.startswith("http") else v),
        "title": ("title", None),
        "price_inr": ("price_raw", lambda v: float(v) if v else None),
        "price_per_sqft_inr": ("price_per_sqft", lambda v: float(v) if v else None),
        "area_sqft": ("carpet_area", lambda v: float(v) if v else None),
        "bhk": ("bedroom", _parse_bhk_float),
        "property_type": (None, None),
        "furnishing": ("furnished", lambda v: v.lower() if v else None),
        "floor": ("floor", None),
        "total_floors": ("total_floors", lambda v: int(v) if v and str(v).isdigit() else None),
        "age_years": (None, None),
        "locality": ("locality", None),
        "city": (None, lambda _: "Noida"),
        "seller_type": ("user_type", lambda v: v.lower() if v else None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
    "squareyards": {
        "source_site": ("site_name", None),
        "listing_id": ("url", lambda v: v.split("/")[-1] if v else None),
        "url": ("url", None),
        "title": ("title", None),
        "price_inr": ("price", lambda v: float(v) if v else None),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("floor_size", _parse_area_sqft),
        "bhk": ("bedrooms", _parse_bhk_float),
        "property_type": (None, lambda _: "apartment"),
        "furnishing": (None, None),
        "floor": (None, None),
        "total_floors": (None, None),
        "age_years": (None, None),
        "locality": ("locality", None),
        "city": ("city", lambda v: v if v else "Noida"),
        "seller_type": (None, None),
        "latitude": ("geo.latitude", lambda v: float(v) if v else None),
        "longitude": ("geo.longitude", lambda v: float(v) if v else None),
        "amenities": (None, None),
    },
    "olx": {
        "source_site": ("site_name", None),
        "listing_id": ("url", lambda v: v.split('iid-')[-1].split('/')[0] if v and 'iid-' in str(v) else None),
        "url": ("url", lambda v: f"https://www.olx.in{v}" if isinstance(v, str) and v.startswith('/') else v),
        "title": ("title", None),
        "price_inr": ("price", lambda v: float(v.replace('\u20b9', '').replace(',', '').strip()) if v else None),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("details", lambda v: _parse_olx_area(v) if v else None),
        "bhk": ("details", lambda v: _parse_bhk(v.replace('BHK', ' BHK')) if v and re.search(r'(\d+)\s*BHK', v, re.I) else None),
        "property_type": ("url", lambda v: _parse_olx_property_type(v) if v else None),
        "furnishing": ("title", lambda v: _parse_olx_furnishing(v) if v else None),
        "floor": (None, None),
        "total_floors": (None, None),
        "age_years": (None, None),
        "locality": ("location", lambda v: v.strip() if v else None),
        "city": (None, lambda _: "Noida"),
        "seller_type": (None, None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
    "proptiger": {
        "source_site": ("site_name", None),
        "listing_id": ("url", lambda v: v.rstrip('/').split('-')[-1] if v else None),
        "url": ("url", lambda v: f"https://www.proptiger.com{v}" if isinstance(v, str) and v.startswith('/') else v),
        "title": ("title", None),
        "price_inr": ("price", _parse_price_india),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("area", _parse_area_sqft),
        "bhk": ("bhk", lambda v: _parse_bhk(v) if v else None),
        "property_type": ("bhk", lambda v: 'apartment' if v and ('Apartment' in v or 'BHK' in v) else None),
        "furnishing": (None, None),
        "floor": (None, None),
        "total_floors": (None, None),
        "age_years": (None, None),
        "locality": ("location", lambda v: v.strip() if v else None),
        "city": (None, lambda _: "Noida"),
        "seller_type": ("builder", lambda v: v.replace('By ', '').strip() if v else None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
    "housing": {
        "source_site": ("site_name", None),
        "listing_id": ("url", lambda v: v.rstrip('/').split('/')[-1] if v else None),
        "url": ("url", lambda v: f"https://housing.com{v}" if isinstance(v, str) and v.startswith('/') else v),
        "title": ("title", None),
        "price_inr": ("price", lambda v: float(v) if v else None),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("area", lambda v: float(v) if v else None),
        "bhk": ("bhk", lambda v: int(v) if v else None),
        "property_type": (None, lambda _: "apartment"),
        "furnishing": (None, None),
        "floor": (None, None),
        "total_floors": (None, None),
        "age_years": (None, None),
        "locality": ("location", None),
        "city": (None, lambda _: "Noida"),
        "seller_type": (None, None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
    "makaan": {
        "source_site": ("site_name", None),
        "listing_id": ("url", lambda v: v.rstrip('/').split('-')[-1] if v else None),
        "url": ("url", lambda v: f"https://www.makaan.com{v}" if isinstance(v, str) and v.startswith('/') else v),
        "title": ("title", None),
        "price_inr": ("price", lambda v: float(v) if v else None),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("floor_size", lambda v: float(v) if v else None),
        "bhk": ("bedrooms", lambda v: int(v) if v else None),
        "property_type": (None, lambda _: "apartment"),
        "furnishing": (None, None),
        "floor": (None, None),
        "total_floors": (None, None),
        "age_years": (None, None),
        "locality": ("locality", None),
        "city": ("city", None),
        "seller_type": (None, None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
    "nobroker": {
        "source_site": ("site_name", None),
        "listing_id": ("prop_id", lambda v: str(v) if v else None),
        "url": ("url", None),
        "title": ("title", None),
        "price_inr": ("price", lambda v: float(v) if v else None),
        "price_per_sqft_inr": (None, None),
        "area_sqft": ("area", lambda v: float(v) if v else None),
        "bhk": ("bhk", lambda v: int(v) if v else None),
        "property_type": ("property_type", None),
        "furnishing": ("furnished", None),
        "floor": ("floor", None),
        "total_floors": ("total_floors", lambda v: int(v) if v else None),
        "age_years": (None, None),
        "locality": ("location", None),
        "city": (None, lambda _: "Noida"),
        "seller_type": (None, None),
        "latitude": (None, None),
        "longitude": (None, None),
        "amenities": (None, None),
    },
}

SCHEMA_VERSION = "1.0"


def normalize(record: dict) -> dict:
    """Normalize a raw scraped record into the unified schema.

    NOTE: This function mutates *record* in-place by redacting PII from
    known text fields (*record* is the caller's raw dict, which later gets
    stored as ``raw_data`` in the DB). The redacted values therefore appear
    in both the normalized output and the raw_data JSON blob.
    """
    site = record.get("site_name", "")
    field_map = SITE_FIELD_MAP.get(site, SITE_FIELD_MAP.get("99acres"))
    normalized = {"schema_version": SCHEMA_VERSION}

    # PII redaction on known free-text fields before any field mapping
    for k in _PII_TEXT_FIELDS:
        val = record.get(k)
        if isinstance(val, str):
            record[k] = _redact_pii(val)
    for out_key, (src_key, transform) in field_map.items():
        if src_key is None and transform is None:
            normalized[out_key] = None
            continue
        if src_key is None:
            normalized[out_key] = transform(None) if transform else None
            continue
        value = record.get(src_key)
        if transform and value is not None:
            try:
                value = transform(value)
            except (ValueError, TypeError):
                value = None
        normalized[out_key] = value
    normalized["scraped_at_utc"] = record.get("scraped_at") or record.get("scraped_at_utc")

    # Squareyards: JSON-LD often omits price; fall back to description text
    if site == "squareyards" and normalized.get("price_inr") is None:
        desc = record.get("description") or ""
        inferred = _extract_price_from_text(desc)
        if inferred is not None:
            normalized["price_inr"] = inferred

    # Noida locality allowlist check
    locality = normalized.get("locality")
    rejected = None
    if locality and not matches_noida_locality(locality):
        rejected = f"locality '{locality}' not in Noida allowlist"
    elif not locality:
        # Fallback: check title + location fields for Noida substrings
        haystack = " ".join(filter(None, [
            record.get("title", ""),
            record.get("location", ""),
            record.get("locality", ""),
        ]))
        if haystack and not matches_noida_fallback(haystack):
            rejected = f"no Noida substring found in title/location"
    if rejected:
        normalized["_rejected"] = True
        normalized["_rejected_reason"] = rejected

    return normalized


def normalize_file(input_path: str) -> list[dict]:
    path = Path(input_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        records = json.load(f)
    return [normalize(r) for r in records]


def normalize_output_dir(site_name: str, output_dir: str = "output") -> list[dict]:
    site_dir = Path(output_dir) / site_name
    if not site_dir.exists():
        return []
    json_files = sorted(f for f in site_dir.glob("*.json") if not f.name.startswith("."))
    if not json_files:
        return []
    latest = json_files[-1]
    return normalize_file(str(latest))
