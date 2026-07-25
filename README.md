# Scrapling Noida Property Scraper

Production-grade multi-site real estate scraper for Noida properties, built on the **Scrapling** framework. Scrapes 5 Indian property portals, normalizes data into a unified schema, stores in SQLite, detects cross-site duplicates, and serves a live dashboard via Cloudflare Pages.

---

## Architecture

```
main.py                  CLI entry point
config/sites.yaml        YAML config (6 scrapers)
scrapers/
  base.py                BaseScraper — fetch, parse, filter, output
  registry.py            Factory
normalizer.py            Field normalization per site (raw → unified schema)
storage.py               SQLite persistence (listings + possible_duplicates)
dedup.py                 Cross-site dedup (price±3%, area±5%, Jaccard locality, title similarity)
consolidate.py           Reads output/ → upserts into SQLite → dedup
dashboard/
  build-data.py          SQLite → data.json
  index.html             Frontend (filters, images, listing links)
.github/workflows/
  scrape.yml             CI/CD pipeline
```

**Data flow:**

```
Scrape → output/{site}/{date}.json → consolidate.py → SQLite (upsert) → build-data.py → data.json → Deploy to Cloudflare Pages
                                              ↓
                                       cross-site dedup
```

---

## Active Sites

| Site | Parser | Fetcher | URL |
|------|--------|---------|-----|
| **99acres** | `json_embed` | `dynamic` | `property-in-noida` |
| **magicbricks** | `json_embed` | `dynamic` | `flats-in-noida-for-sale` |
| **squareyards** | `jsonld` | `dynamic` | `sale/property-for-sale-in-noida` |
| **olx** | `css` | `dynamic` | 5 pages (`?page=1..5`) |
| **proptiger** | `css` | `dynamic` | `noida-real-estate` (project cards) |
| **proptiger-flats** | `json_embed` | `dynamic` | `flats-in-noida` (individual units) |

### Extraction Methods

- **json_embed** — Extracts JavaScript embedded JSON variables (`__initialData__`, `SERVER_PRELOADED_STATE_`, `__NEXT_DATA__`) using brace-depth parsing
- **jsonld** — Parses `<script type="application/ld+json">` blocks (Product, Apartment, SingleFamilyResidence)
- **css** — CSS3 selectors with pseudo-selectors (`::text`, `::attr(href)`) and comma-separated fallbacks

All sites have `location_filter: contains "Noida"` to exclude out-of-area results.

---

## Database Schema

**File:** `data/consolidated/noida_properties.db`

**Table `listings`:**
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `source_site` | TEXT | Site name |
| `listing_id` | TEXT | Site-specific ID |
| `url` | TEXT | Listing URL |
| `title` | TEXT | Property title |
| `price_inr` | REAL | Price in INR |
| `price_per_sqft_inr` | REAL | Price per sq.ft. |
| `area_sqft` | REAL | Area in sq.ft. |
| `bhk` | INTEGER | Bedrooms |
| `property_type` | TEXT | apartment/flat/villa |
| `furnishing` | TEXT | full/semi/unfurnished |
| `floor` | TEXT | Floor number |
| `total_floors` | INTEGER | Total floors |
| `locality` | TEXT | Area/locality |
| `city` | TEXT | City (default: Noida) |
| `latitude` / `longitude` | REAL | Coordinates |
| `raw_data` | TEXT | Full original JSON |
| `scraped_at_utc` / `consolidated_at_utc` | TEXT | Timestamps |
| `UNIQUE(source_site, listing_id)` | | Dedup constraint |

**Table `possible_duplicates`:** Cross-site duplicate pairs with confidence scores.

---

## Setup

```bash
cd scraper_project
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

---

## Usage

```bash
# Run all sites
python main.py --all

# Run single site with limit
python main.py --site 99acres --limit 10

# Dry-run (no output saved)
python main.py --site olx --dry-run

# Consolidate scraped data into SQLite
python consolidate.py --sites 99acres magicbricks squareyards olx proptiger proptiger-flats

# Build dashboard data.json from DB
python dashboard/build-data.py
```

---

## Dashboard

**Live at:** `https://noida-property-dashboard.pages.dev`

Built with vanilla HTML/CSS/JS. Features:
- Light/dark theme toggle
- Filters: BHK, min/max price, min area, text search
- Property cards with images, price, BHK, area, floor, description
- Direct links to original listings on each source site
- Responsive grid layout

---

## CI/CD Pipeline

**File:** `.github/workflows/scrape.yml`
**Schedule:** Every hour (`0 * * * *`)

**Job 1 — `scrape`:**
1. Checkout repo
2. Install Python deps + Playwright
3. `gh run download` previous `scraper-db` artifact (DB persists across runs)
4. `python main.py --all` (scrape all sites)
5. `python consolidate.py --sites ...` (upsert into SQLite)
6. `python dashboard/build-data.py` (build data.json from DB)
7. Upload artifacts: `scraper-db`, `dashboard-build`, raw output

**Job 2 — `deploy`** (needs: scrape, environment: deploy):
1. Download dashboard artifact
2. `wrangler pages deploy dashboard --branch master`

**Artifact persistence:** The `scraper-db` artifact (90-day retention) is downloaded at the start of each run and overwritten at the end, so the SQLite database accumulates data across runs.

---

## Duplicate Detection

Cross-site dedup (`dedup.py`) compares listing pairs from different sites using:
| Signal | Weight | Method |
|--------|--------|--------|
| BHK match | +0.30 | Exact match |
| Locality | +0.30 | Jaccard similarity on locality tokens |
| Title | +0.25 | `SequenceMatcher` ratio |
| Price | +0.15 | Ratio within [0.97, 1.03] |
| Area | +0.15 | Ratio within [0.95, 1.05] |

Matches with confidence ≥ 0.70 are stored in `possible_duplicates` table.

---

## Blocked Sites

| Site | Status | Reason |
|------|--------|--------|
| **nobroker.in** | BLOCKED | XHR-only React SPA. Public API requires polygon geo-location token. `window.nb.appState` destroyed by React after load. |
| **housing.com** | PARTIAL | Akamai Bot Manager blocks Noida-specific paths. GraphQL API discovered at `mightyzeus-mum.housing.com/api/gql` but query is buried in obfuscated Apollo Client bundles. |
| **makaan.com** | REDIRECT | Redirects to housing.com (same WAF). |

---

## Compliance

- **robots.txt**: Checked by default (`respect_robots: true`). 99acres explicitly disabled.
- **Rate limiting**: 3s between requests per site (minimum 1s enforced in code).
- **Responsibility**: Scraping legality depends on the target site's ToS, data type, and your jurisdiction.
