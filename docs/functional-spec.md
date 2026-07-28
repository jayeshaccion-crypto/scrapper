# Scrapling Noida Property Scraper — Functional Specification Document

**Version:** 2.0 | **Status:** Production | **Last Updated:** July 2026

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture & Component Hierarchy](#2-architecture--component-hierarchy)
3. [Data Flow](#3-data-flow)
4. [Functional Capabilities](#4-functional-capabilities)
5. [Configuration Reference](#5-configuration-reference)
6. [CLI Reference](#6-cli-reference)
7. [Data Schema](#7-data-schema)
8. [CI/CD Pipeline](#8-cicd-pipeline)
9. [Testing Strategy](#9-testing-strategy)
10. [Security & Compliance](#10-security--compliance)
11. [Known Limitations](#11-known-limitations)

---

## 1. System Overview

### 1.1 Purpose

Production-grade multi-site web scraping system that systematically extracts real estate property listings from 6+ Indian property portals, normalizes them into a unified schema, detects cross-site duplicates, and serves a live public dashboard — all running on a **zero-cost open-source stack**.

### 1.2 Scope

| Dimension | Value |
|-----------|-------|
| **Target City** | Noida (incl. Greater Noida, Noida Extension, Yamuna Expressway) |
| **Active Sites** | 4 (magicbricks, 99acres, squareyards, olx) |
| **Configured Sites** | 9 (above + proptiger, housing, makaan, nobroker) |
| **Data Collected** | Price, area, BHK, locality, furnishing, floor, amenities, images, GPS coordinates |
| **Update Cadence** | Hourly (GitHub Actions cron) |
| **Dashboard** | Public Cloudflare Pages (`noida-property-dashboard.pages.dev`) |
| **Infrastructure Cost** | $0/month |

### 1.3 Stack

| Layer | Technology |
|-------|-----------|
| **Scraping Framework** | [Scrapling](https://github.com/D4Vinci/Scrapling) v0.4+ |
| **Data Validation** | Pydantic v2 |
| **Storage** | SQLite (WAL mode) |
| **CI/CD** | GitHub Actions |
| **Container Runtime** | `ghcr.io/d4vinci/scrapling:latest` |
| **Dashboard Hosting** | Cloudflare Pages |
| **DB Backup** | Cloudflare R2 (S3-compatible) |
| **Orchestration** | Python CLI + cron (local) / GitHub Actions (CI) |

### 1.4 Design Principles

1. **Zero-cost**: All infrastructure must be free-tier or open-source
2. **Spec-driven**: All Phase 1-5 items from Version2.md govern architecture
3. **Graceful degradation**: One site failing never blocks the pipeline
4. **Security-first**: PII redaction, XSS escaping, robots.txt compliance documentation
5. **Extensibility by config**: Adding a new site requires only `sites.yaml` + `normalizer.py` field map — no code changes to the scraping engine

---

## 2. Architecture & Component Hierarchy

### 2.1 Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI ENTRY POINT                          │
│                         main.py (130L)                          │
│                                                                 │
│  load_config() → sites.yaml          run_site() → per-site     │
│  Fetcher.configure(adaptive=True)     save_state → fallback     │
│  Run summary table                   exit 1 if all failed       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER                         │
│                    scrapers/base.py (440L)                      │
│                                                                 │
│  ┌─────────────────────────────┐    ┌─────────────────────────┐ │
│  │     BaseScraper (sync)      │    │   PropertySpider(Spider) │ │
│  │                             │    │          (async)         │ │
│  │  - fallback_tracker (class) │───▶│                         │ │
│  │  - anomaly_tracker (class)  │    │  - configure_sessions() │ │
│  │  - run() → creates spider   │    │  - start_requests()     │ │
│  │  - write_output() → JSON    │    │  - parse() → dispatcher │ │
│  └─────────────────────────────┘    └──────────┬──────────────┘ │
└───────────────────────────────────────────────────┬─────────────┘
                                                    │
                                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       FETCHER LAYER                             │
│               Scrapling session manager                         │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ AsyncStealth │  │AsyncDynamic  │  │   Fetcher    │          │
│  │ ySession     │  │Session       │  │   Session    │          │
│  │              │  │              │  │              │          │
│  │ network_idle │  │ network_idle │  │ lightweight  │          │
│  │ solve_CF     │  │ wait_sel     │  │ no JS        │          │
│  │ 90s timeout  │  │ 120s timeout │  │              │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  All: headless=True, load_dom=True, adaptive=True, proxy=...   │
└─────────────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       PARSER LAYER                              │
│                 PropertySpider dispatch                         │
│                                                                 │
│  ┌────────────────┐ ┌──────────────┐ ┌──────────────┐          │
│  │  json_embed    │ │   jsonld     │ │    css       │          │
│  │                │ │              │ │              │          │
│  │ Brace-depth JS │ │ RegEx <script│ │ Scrapling CSS│          │
│  │ var extraction │ │ type=ld+json │ │ auto_save    │          │
│  │ json.loads()   │ │ json.loads() │ │ adaptive     │          │
│  │ _follow_path() │ │ @type filter │ │ ::text/attr  │          │
│  │                │ │              │ │ fallbacks    │          │
│  └────────────────┘ └──────────────┘ └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                                                    │
                                  ┌─────────────────┼─────────────────┐
                                  ▼                 ▼                 ▼
┌─────────────────────────┐ ┌──────────┐ ┌──────────────────────────┐
│  output/{site}/{date}.  │ │  filter/ │ │  on_scraped_item() hook  │
│  json (raw)             │ │  reject  │ │  → _apply_filters()     │
│                         │ │  non-    │ │  → _records[]           │
│                         │ │  Noida   │ │                          │
└─────────────────────────┘ └──────────┘ └──────────────────────────┘

                            CONSOLIDATION (consolidate.py / CI step)
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CONSOLIDATION PIPELINE                       │
│                    consolidate.py (118L)                        │
│                                                                 │
│  read_all_json(site_dir) → records[]                            │
│       │                                                         │
│       ▼                                                         │
│  for each record:                                               │
│       │                                                         │
│       ├── normalize(record) → normalizer.py                    │
│       │    ├── PII redact 12 text fields                       │
│       │    ├── Map fields via SITE_FIELD_MAP                   │
│       │    ├── Price parsing (Cr/Lac/K, natural language)      │
│       │    ├── Area/BHK extraction                             │
│       │    ├── Furnishing normalization                        │
│       │    └── Locality allowlist → _rejected if non-Noida     │
│       │                                                         │
│       ├── if _rejected → output/rejected/{date}.jsonl          │
│       │                                                         │
│       └── PropertyListing(**norm) → Pydantic v2 validation    │
│            ├── price > 0 / area > 0 / bhk 0-10                 │
│            ├── title non-empty, URL http/https + domain match   │
│            └── if ValidationError → output/rejected/{date}.jsonl│
│                    │                                            │
│                    ▼                                            │
│  storage.upsert_many(valid_raw, normalized=valid_norm)         │
│       │                                                         │
│       ▼                                                         │
│  store_duplicates(storage) → cross-site dedup                  │
│       │                                                         │
│       ▼                                                         │
│  Storage.get_counts_by_site() → summary table                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  ANOMALY DETECTION (CI Step)                    │
│               check_anomalies.py (175L)                        │
│                                                                 │
│  get_site_yields() → today's item count per site                │
│  Compare vs 7-day rolling median                                │
│  Flag: zero-yield (with baseline) or >80% drop                  │
│  Track consecutive anomalies → fallback_anomaly.json            │
│  2+ consecutive → StealthyFetcher trigger warning               │
│  Write to $GITHUB_STEP_SUMMARY                                  │
│  Always returns 0 (alert-only, never fails pipeline)            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  DASHBOARD BUILD (CI Step)                      │
│               dashboard/build-data.py (100L)                   │
│                                                                 │
│  SQLite → all rows                                              │
│  Merge raw_data JSON into row                                   │
│  Resolve listing URL (prepend SITE_BASE if relative)            │
│  Extract first image (from 8 possible source fields)            │
│  html.escape() on 23 free-text fields → XSS defense             │
│  Write → dashboard/data.json                                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    DASHBOARD (Frontend)                         │
│               dashboard/index.html (229L)                      │
│                                                                 │
│  Vanilla JS single-page app                                     │
│  Light/dark theme toggle (CSS variables)                        │
│  Filters: site, BHK, min/max price, min area, text search       │
│  Property cards: image, price, badge, title, meta, description  │
│  Responsive grid (auto-fill minmax 340px)                       │
│  Go-to-top button                                               │
│  Fetches data.json at load                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Inheritance & Composition

```
scrapling.spiders.Spider (abstract async base)
    └── PropertySpider (334L - all scraping logic)
            ├── Uses: AsyncStealthySession
            ├── Uses: AsyncDynamicSession
            ├── Uses: FetcherSession
            ├── Has: _parse_json_embed()
            ├── Has: _parse_jsonld()
            ├── Has: _parse_css()
            ├── Has: _extract_js_var(), _follow_path()
            └── Has: _apply_filters() hook

BaseScraper (standalone - sync facade, 106L)
    ├── Class-level: _fallback_tracker, _anomaly_tracker
    ├── run() → creates PropertySpider internally → calls spider.start()
    └── write_output() → JSON or CSV file

pydantic.BaseModel
    └── PropertyListing (102L - 20 fields, 6 validators)

Storage (standalone - 152L)
    ├── SQLite CRUD
    ├── upsert_listing() / upsert_many()
    ├── get_all_listings() / get_counts_by_site()
    └── add_duplicate_pair()
```

---

## 3. Data Flow

### 3.1 End-to-End Pipeline

```
[CLI]                                    [CI/CD]
  │                                        │
  python main.py --all                     GitHub Actions (hourly)
  │                                        │
  ▼                                        ▼
  BaseScraper.run()                        gh run download → get previous DB + state
  │                                        │
  ├── Check fallback: if 3+ zero runs →   │
  │   stealthy; if 2+ anomaly → stealthy   │
  │                                        │
  └── PropertySpider.start()               │
       │                                   │
       ├── configure_sessions()            │
       ├── start_requests() → yield URL    │
       ├── fetch (via Scrapling fetcher)   │
       ├── parse()                         │
       │   ├── _parse_json_embed()         │
       │   ├── _parse_jsonld()             │
       │   └── _parse_css()                │
       │                                   │
       └── on_scraped_item()               │
            └── _apply_filters()           │
                 │                         │
                 ▼                         │
            output/{site}/{date}.json      │
                 │                         │
                 ▼                         ▼
            consolidate.py       ═══════════
                 │
                 ├── normalize()           normalizer.py
                 │   ├── PII redact
                 │   ├── SITE_FIELD_MAP
                 │   └── locality allowlist
                 │
                 ├── PropertyListing()     pydantic validation
                 │
                 ├── upsert_many()         storage.py → SQLite
                 │
                 └── store_duplicates()    dedup.py → possible_duplicates
                      │
                      ▼
                 check_anomalies.py        yield anomaly detection
                      │
                      ▼
                 build-data.py             SQLite → dashboard/data.json
                      │
                      ▼
                 wrangler pages deploy     Cloudflare Pages → live dashboard
```

### 3.2 State Persistence Across CI Runs

```
                        Run N                           Run N+1
┌─────────────────┐                   ┌─────────────────┐
│   GitHub Actions │                   │   GitHub Actions │
│                  │   upload artifact │                  │
│  scraper-db      │──────────────────▶│  download artifact│
│  scraper-state   │                   │  ↓               │
│  dashboard-build │                   │  python main.py  │
│                  │                   │  ↓               │
│                  │←──────────────────│  use downloaded   │
└─────────────────┘    upload artifact │  DB + state      │
                                       └─────────────────┘

┌─────────────────────────────────────────────────────────┐
│  R2 Backup (durable, survives artifact expiry)           │
│                                                          │
│  aws s3 cp /tmp/backup.db.gz s3://bucket/db-backups/     │
│    noida_properties-20260726_235959.db.gz                │
│    noida_properties-latest.db.gz   (overwrite)           │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Functional Capabilities

### 4.1 Site Configuration (9 Sites)

| Site | Parser | Fetcher | Status | Data Extraction Method | Notes |
|------|--------|---------|--------|----------------------|-------|
| **magicbricks** | json_embed | dynamic | Active | `SERVER_PRELOADED_STATE_` → `searchResult` | 41 fields, rich dataset |
| **99acres** | json_embed | stealthy | Active | `__initialData__` → `srp.pageData.properties` | 33 fields, robots.txt disabled |
| **squareyards** | jsonld | dynamic | Active | JSON-LD Product/SingleFamilyResidence/Apartment | 14 fields, price fallback from text |
| **olx** | css | dynamic | Active | CSS selectors, 5 pages | 8 fields, adaptive cache enabled |
| **proptiger** | css | dynamic | Partial | `.popular-project-card` (project cards) | 8 fields, project-level only |
| **proptiger-flats** | json_embed | dynamic | Active | `__NEXT_DATA__` → `props.pageProps.listings` | 14 fields, individual units |
| **housing** | json_embed | stealthy | BLOCKED | `__INITIAL_STATE__` → `searchResults` | Akamai CDN, all routes blocked |
| **makaan** | jsonld | stealthy | BLOCKED | JSON-LD Product/Apartment | CDN-level block |
| **nobroker** | json_embed | stealthy | BLOCKED | `nb.appState` → `listPageProperties` | SPA never reaches `network_idle` |

### 4.2 Extraction Methods

**json_embed** — Brace-depth JS variable extraction:
1. Regex match `window.VAR_NAME = {` or `<script id="VAR_NAME">{`
2. Walk character-by-character tracking brace depth, handling string escapes
3. `json.loads()` the extracted JSON string
4. Navigate via dotted `data_path` (e.g. `props.pageProps.listings`)
5. Map raw JSON keys → unified field names

**jsonld** — Structured data extraction:
1. Regex `<script type="application/ld+json">(.*?)</script>`
2. Filter by `@type` (Product, Apartment, SingleFamilyResidence, House)
3. Handle `ItemList` wrapper (common on listing pages)
4. Follow dotted field paths for nested data

**css** — Adaptive DOM extraction:
1. Scrapling CSS3 with `auto_save=True` for adaptive DOM relocation
2. Pseudo-selectors: `::text`, `::attr(href)`, `::attr(src)`
3. Comma-separated fallbacks: `sel1::text, sel2::text`
4. Fingerprints cached to `./.scrapling_cache` for CI persistence

### 4.3 Auto Fallback (StealthyFetcher Trigger)

| Condition | Action | File |
|-----------|--------|------|
| 3+ consecutive zero-yield runs | Switch `dynamic` → `stealthy` | `base.py:BaseScraper.run()` |
| 2+ consecutive anomaly runs | Switch `dynamic` → `stealthy` | `base.py:BaseScraper.run()` |
| Single zero yield | Increment `_fallback_tracker` | `base.py:BaseScraper.run()` |
| Normal yield after zero | Reset `_fallback_tracker` to 0 | `base.py:BaseScraper.run()` |

### 4.4 Anomaly Detection

- **Rolling window**: 7 days
- **Baseline**: Median of past yields (excluding current run)
- **Triggers**: Zero yield with non-zero baseline OR >80% drop vs median
- **Consecutive tracking**: Sites with 2+ consecutive anomaly runs flagged for stealthy fallback
- **Data pruning**: History older than 14 days is trimmed
- **Exits 0 always**: Alert-only, never fails the pipeline

### 4.5 Dedup Engine

Cross-site duplicate detection using weighted signal aggregation:

| Signal | Weight | Threshold | Method |
|--------|--------|-----------|--------|
| BHK match | +0.30 | Exact | Same BHK number |
| Locality | +0.30 | Jaccard > 0.5 | Token intersection over union |
| Title | +0.25 | Sim ratio > 0.4 | `difflib.SequenceMatcher` |
| Price | +0.15 | Ratio within [0.97, 1.03] | min/max |
| Area | +0.15 | Ratio within [0.95, 1.05] | min/max |

**Match threshold**: `confidence >= 0.70` → stored in `possible_duplicates` table

### 4.6 Normalization Engine

**Field Mapping**: 9 `SITE_FIELD_MAP` entries in `normalizer.py`, each mapping `(source_key, transform_fn)` tuples:
- `source_key = None` → static value via transform
- `transform_fn = None` → pass-through
- Transform catches `(ValueError, TypeError)` → returns `None`

**Price Parsing**:
- `Cr` suffix → × 10,000,000
- `Lac`/`Lakh`/`L` suffix → × 100,000
- `K` suffix → × 1,000
- Natural language: `Rs. 1.36 Cr`, `Priced at 45 lakh`
- Squareyards JSON-LD fallback: extract from description text

**Area Parsing**:
- Range handling: `1000-1200 sqft` → takes first value
- OLX: specific `NNNN sq ft` pattern

**BHK Parsing**:
- First digit group: `"3 BHK"` → 3
- Float floor: `3.5` → 3
- Range clamping: 0–10

**Locality Allowlist** (18 compiled regex patterns):
- Core: `noida`, `noida extension`, `greater noida`
- Sector: `sector \d{1,3}[a-z]?` (standalone or with "Noida" prefix)
- Greek zones: `gamma\2`, `zeta\2`, `beta\2`, `alpha\2`, `delta\2`, `omega\2`
- Landmarks: `knowledge park`, `film city`, `ecotech`, `techzone`, `pari` chowk
- Transport: `yamuna expressway`

---

## 5. Configuration Reference

### 5.1 Sites YAML Schema (`config/sites.yaml`)

```yaml
sites:
  - name: <string>                    # Site identifier (required)
    parser: json_embed | jsonld | css # Extraction method (required)
    start_urls:                       # One or more URLs (required)
      - "https://example.com/listings"
    fetcher: dynamic | stealthy       # Browser mode (required)
    wait_selector: <css-string>        # Wait for element before extract (optional)
    respect_robots: true | false       # robots.txt compliance (default: true)
    embed:                             # Required for json_embed/jsonld
      var: <string>                    #  JS var name (json_embed only)
      data_path: <string>              #  Dotted path to array (json_embed only)
      types:                           #  JSON-LD @type list (jsonld only)
        - Product
        - Apartment
      filter:                          #  Pre-extraction filter (optional)
        field: <string>
        contains: <string>
    selectors:                         # Required for css/json_embed
      item: <css-string>               #  CSS selector for item rows (css only)
      fields:                          #  Field name → JSON key / CSS selector
        title: <string>
        price: <string>
    pagination:                        # Optional
      max_pages: <int>
      next_selector: <css-string>
    rate_limit_seconds: <float>        # Min delay between requests (default: 3.0)
    output_format: json | csv          # Default: json
    location_filter:                   # Post-extraction Noida filter (optional)
      field: <string>
      contains: <string>
    adaptive_cache: <path>             # Cache dir (css parser only)
    proxy:                             # Proxy settings (optional)
      url: "socks5://127.0.0.1:9050"
      enabled: false
```

### 5.2 Environment / Secrets

| Secret / Config | Source | Purpose |
|----------------|--------|---------|
| `CLOUDFLARE_API_TOKEN` | GitHub Secrets | `wrangler pages deploy` |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Secrets | R2 endpoint URL |
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | GitHub Secrets | R2 S3 credential |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | GitHub Secrets | R2 S3 credential |
| `R2_BUCKET_NAME` | GitHub Secrets | R2 bucket name |
| `.env` file | Local (gitignored) | For future proxy/API keys |

---

## 6. CLI Reference

### 6.1 Commands

```bash
# Run all configured sites
python main.py --all

# Run a single site (case-sensitive name from sites.yaml)
python main.py --site magicbricks

# Dry-run (fetch + parse, no output file written)
python main.py --site olx --dry-run

# Limit items per site
python main.py --all --limit 10

# Consolidate scraped output to SQLite
python consolidate.py --sites 99acres magicbricks squareyards olx proptiger proptiger-flats

# Skip dedup during consolidation
python consolidate.py --sites magicbricks --no-dedup

# Build dashboard data.json from SQLite DB
python dashboard/build-data.py

# Detect yield anomalies
python check_anomalies.py

# Full cleanup (delete DB, outputs, state)
python dashboard/cleanup.py
```

### 6.2 Output Structure

```
output/
├── {site_name}/
│   ├── .seen.json           # Dedup tracker (optional)
│   ├── {YYYY-MM-DD}.json    # Raw scraped records
│   └── {YYYY-MM-DD}.csv     # (if output_format: csv)
├── rejected/
│   └── {YYYY-MM-DD}.jsonl   # Rejected records + reasons
data/
├── consolidated/
│   └── noida_properties.db  # SQLite (WAL mode)
├── fallback_state.json      # Zero-yield run counter per site
├── fallback_anomaly.json    # Consecutive anomaly counter per site
├── yield_history.json       # Per-site yield time series
logs/
└── run_{YYYYMMDD_HHMMSS}.log
dashboard/
└── data.json                # Built from DB, served to frontend
```

---

## 7. Data Schema

### 7.1 SQLite: `listings` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `source_site` | TEXT | NOT NULL | Site name from config |
| `listing_id` | TEXT | | Site-specific listing ID |
| `url` | TEXT | | Full listing URL |
| `title` | TEXT | | Property title |
| `price_inr` | REAL | | Price in Indian Rupees |
| `price_per_sqft_inr` | REAL | | Price per square foot |
| `area_sqft` | REAL | | Carpet/super area in sq.ft. |
| `bhk` | INTEGER | | Bedrooms (0-10) |
| `property_type` | TEXT | | apartment/flat/villa/plot |
| `furnishing` | TEXT | | full/semi/unfurnished |
| `floor` | TEXT | | Floor information |
| `total_floors` | INTEGER | | Total building floors |
| `age_years` | INTEGER | | Property age |
| `locality` | TEXT | | Area/locality name |
| `city` | TEXT | DEFAULT 'Noida' | City |
| `seller_type` | TEXT | | owner/dealer/builder |
| `latitude` | REAL | | GPS latitude |
| `longitude` | REAL | | GPS longitude |
| `amenities` | TEXT | | JSON-encoded list |
| `raw_data` | TEXT | | Full original JSON (with PII redacted) |
| `scraped_at_utc` | TEXT | | When scraped |
| `schema_version` | TEXT | DEFAULT '1.0' | Normalizer version |
| `consolidated_at_utc` | TEXT | | When upserted to DB |
| | | UNIQUE(source_site, listing_id) | Dedup constraint |

**Indices**: `source_site`, `locality`, `bhk`, `price_inr`

### 7.2 SQLite: `possible_duplicates` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `listing_a_id` | INTEGER | FK → listings(id) | First listing |
| `listing_b_id` | INTEGER | FK → listings(id) | Second listing |
| `confidence` | REAL | | Match confidence 0.0-1.0 |
| `match_reason` | TEXT | | Human-readable reasons |
| `created_at_utc` | TEXT | | When detected |

### 7.3 Pydantic Model: `PropertyListing`

```
PropertyListing(BaseModel):
    source_site: str                    # required
    listing_id: str | None              # optional
    url: str | None                     # optional, validated http/https + domain match
    title: str                          # required, non-empty
    price_inr: float | None             # must be > 0 if present
    price_per_sqft_inr: float | None
    area_sqft: float | None             # must be > 0 if present
    bhk: int | None                     # must be 0-10 if present
    property_type: str | None
    furnishing: str | None
    floor: str | None
    total_floors: int | None
    age_years: int | None
    locality: str | None
    city: str | None                    # default: "Noida"
    seller_type: str | None
    latitude: float | None
    longitude: float | None
    amenities: list[str] | None
    scraped_at_utc: str | None
    schema_version: str | None

Validators:
    │
    ├── price_must_be_positive: v > 0 or None
    ├── area_must_be_positive: v > 0 or None
    ├── bhk_must_be_in_range: 0 <= v <= 10 or None
    ├── title_must_be_non_empty: stripped, non-empty
    ├── url_must_be_valid: scheme in (http, https)
    └── url_matches_site_domain: netloc matches SITE_DOMAINS[source_site]
```

---

## 8. CI/CD Pipeline

### 8.1 Workflow: `scrape.yml`

| Aspect | Detail |
|--------|--------|
| Trigger | `cron: '0 * * * *'` (hourly) + `workflow_dispatch` |
| Concurrency | Group `scrape-db`, `cancel-in-progress: false` |
| Container | `ghcr.io/d4vinci/scrapling:latest` (root user) |
| Runner | `ubuntu-latest` |

**Job 1 — `scrape`**:

```
1. actions/checkout@v4
2. Cache Playwright browsers (key: playwright-{os}-{hashFiles(requirements.txt)})
3. Install: gh, awscli, Python deps
4. Install Playwright browsers if cache miss
5. Cache adaptive fingerprints (key: scrapling-cache-{os}-{sha})
6. mkdir -p logs output data/consolidated .scrapling_cache
7. gh run download --name scraper-db → data/consolidated/
8. gh run download --name scraper-state → data/
9. python main.py --all
10. python check_anomalies.py --summary $GITHUB_STEP_SUMMARY
11. python consolidate.py --sites 99acres magicbricks squareyards olx proptiger proptiger-flats
12. Sync DB to R2 (gzip, timestamped + -latest copy)
13. python dashboard/build-data.py
14. Upload artifacts: scraper-db (90d), scraper-state (90d), dashboard-build (7d), raw output (7d)
```

**Job 2 — `deploy`** (depends on scrape):
```
1. Download dashboard artifact
2. wrangler pages deploy dashboard --branch master
```

### 8.2 Workflow: `cleanup.yml`

| Aspect | Detail |
|--------|--------|
| Trigger | Manual (`workflow_dispatch` with `confirm: "yes"` input) |
| Action | `python dashboard/cleanup.py` |

### 8.3 CI Safety Mechanisms

| Mechanism | Purpose |
|-----------|---------|
| `concurrency.group: scrape-db` | Prevents concurrent runs corrupting SQLite |
| `cancel-in-progress: false` | Ensures running scrape completes |
| `gh run download ... \|\| true` | First run has no artifact — doesn't fail |
| `check_anomalies.py` exit 0 | Anomalies are alerts, not pipeline failures |
| `python main.py` exit 1 if all fail | Detects total pipeline failure |
| Secrets as env vars | Never logged, never in code |
| R2 backup | Durable off-platform DB storage |

---

## 9. Testing Strategy

### 9.1 Test Suite (148 tests, all passing)

| Test File | Tests | Focus |
|-----------|-------|-------|
| `test_base.py` | 290L, 30 tests | `_extract_js_var`, `_follow_path`, `_clean_jsonld_record`, `_apply_filters`, all parser types, fallback tracker, inheritance |
| `test_config.py` | 142L, 15 tests | YAML structure, required fields, adaptive_cache, stealthy fetcher assignment, site domains, site count (9) |
| `test_phase3.py` | 502L, 50+ tests | Pydantic validators, locality allowlist (16 patterns), locality fallback, rejection handling, consolidation pipeline |
| `test_readme_spec.py` | 64L, 14 tests | README documentation completeness, Blocked Sites table, adaptive cache docs, proxy docs |
| `test_scrape_yml_structure.py` | 88L, 11 tests | Workflow YAML validity, concurrency lock, Docker image, Playwright cache, adaptive cache, R2 secrets, anomaly step |
| `test_anomaly_edge_cases.py` | 118L, 8 tests | First run, zero yield with baseline, drop >80%, consecutive drops, normal reset, mixed sites, exit code 0 |

### 9.2 Key Test Patterns

- `sys.path.insert(0, ...)` to add project root (no conftest)
- `tempfile.mkdtemp()` with `autouse` fixtures for file isolation
- `unittest.mock.patch` for HTTP/mock responses
- Static HTML/JSON fixtures for parser tests
- YAML round-trip validation for workflow structure tests

### 9.3 Pending — Phase 5 Items (not yet implemented)

- `tests/test_parsers.py`: Fixture-based parser tests for all 6 sites
- `tests/test_dedup.py`: Dedup unit tests (exact match, near-miss, mismatch)
- Stale data lifecycle (`status = 'stale'` for 5+ consecutive absence)

---

## 10. Security & Compliance

### 10.1 PII Redaction

| PII Type | Pattern | Replacement | Scope |
|----------|---------|-------------|-------|
| Indian mobile | `(?:\+91[\-\s]?\|0)?[6-9]\d{9}` | `[REDACTED]` | 12 free-text fields in raw records |
| Email | RFC 5322 simplified | `[REDACTED]` | Same 12 fields |

Redaction runs **in-place on the raw record** before field mapping, so redacted values appear in both the normalized output and the `raw_data` JSON blob in SQLite.

### 10.2 XSS Defense

23 free-text fields HTML-escaped via `html.escape()` in `build-data.py` before dashboard serialization. Fields include: title, description, locality, seller_type, builder, contact_name, full_address, sub_locality, building_name, society_name, furnishing, property_type, floor, status, possession, ownership, overlooking, transaction_type, flooring, parking, facing, project, developer, listing_url, site_name.

### 10.3 robots.txt Compliance

- **Default**: `respect_robots: true` — Scrapling fetches and obeys robots.txt
- **99acres**: Explicitly disabled (`respect_robots: false`) with documented justification (blanket `Disallow: /`, public SRP data only, 20 req/min, PII redacted, XSS escaped)

### 10.4 Rate Limiting

- **Default delay**: 3.0 seconds between requests to same domain
- **Minimum enforced**: 1.0 second (hard-coded in `RATE_LIMIT_MIN = 1.0`)
- **Concurrency**: 1 request at a time per site

### 10.5 Proxy

- **Default**: Disabled (`proxy.enabled: false`)
- **Configured**: SOCKS5 `127.0.0.1:9050` (local Tor daemon)
- **Risk accepted**: Sites currently use machine's public IP

### 10.6 LLM Prompt-Injection Warning

If future extension uses Scrapling's `--ai-targeted` feature, the README documents the requirement to sanitize input, isolate extraction prompts, scan for injection patterns, and use output guards.

### 10.7 Data Retention

| Data | Location | Retention |
|------|----------|-----------|
| Raw output | `output/{site}/{date}.json` | Not auto-deleted (CI artifact: 7 days) |
| Rejected records | `output/rejected/{date}.jsonl` | Not auto-deleted |
| SQLite DB | `data/consolidated/noida_properties.db` | Accumulates indefinitely (CI artifact: 90 days) |
| R2 backups | `s3://bucket/db-backups/` + `latest` | Bucket policy dependent |
| Yield history | `data/yield_history.json` | Auto-pruned to 14 days |

---

## 11. Known Limitations

### 11.1 Blocked Sites

| Site | Root Cause | Attempted Workarounds | Status |
|------|-----------|----------------------|--------|
| **housing.com** | Akamai CDN bot detection | Direct (406), Tor (403), Tor + StealthyFetcher (403) | Permanently blocked from current infrastructure |
| **nobroker.in** | SPA continuous polling prevents `network_idle` | StealthyFetcher with 90s timeout, none succeeded | Requires `network_idle=False` |
| **makaan.com** | CDN blocks non-browser UAs | StealthyFetcher, none succeeded | May need different user-agent strategy |

### 11.2 Technical Debt

| Issue | Location | Impact |
|-------|----------|--------|
| No `__init__` session kwargs for `solve_cloudflare` override per site | `base.py:86-90` | Housing.com has Akamai, not Cloudflare — `solve_cloudflare=True` is wasted |
| Hardcoded R2 endpoint construction | `scrape.yml:89` | No way to customize endpoint |
| `normalizer.py` mutates `record` in-place | `normalizer.py:382-385` | Side effect on caller's data (documented in docstring) |
| No `network_idle` config option for `stealthy` | `base.py:94` | Nobroker blocked because `network_idle` never fires |
| CLI `--limit` enforced per-site, not global | `main.py:84` | Cannot set a global limit across all sites |
| No structured/JSON logging | `main.py:19-27` | Logs are plaintext, not machine-parseable |
| Dedup compares all-pairs O(n²) | `dedup.py:38` | Scales poorly with large datasets |

### 11.3 Phase 5 Items Not Yet Implemented

From Version2.md specification:

- **Item 17**: JSON-lines structured logging (`output/logs/{date}.jsonl`)
- **Item 18**: Run summary with rejection counts in CI summary (partial — anomaly summary exists)
- **Item 19**: Parser unit tests with static HTML/JSON fixtures (3 of 9 sites tested)
- **Item 20**: Dedup unit tests (exact match, near-miss, mismatch)
- **Item 22**: Stale data lifecycle — mark listings absent >5 consecutive runs as `stale`
