# Scrapling Noida Property Scraper

Production-grade multi-site real estate scraper for Noida properties, built on the **Scrapling** framework. Scrapes 6 Indian property portals, normalizes data into a unified schema, stores in SQLite, detects cross-site duplicates, and serves a live dashboard via Cloudflare Pages.

---

## Architecture

```
main.py                  CLI entry point
config/sites.yaml        YAML config (9 scrapers)
scrapers/
  base.py                PropertySpider(Spider) — async fetch, parse, filter, output
  registry.py            Factory -> BaseScraper sync wrapper
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
| **nobroker** | `json_embed` | `stealthy` | `property/sale/noida` (BLOCKED) |
| **housing** | `json_embed` | `stealthy` | `in/property-for-sale-noida` (PARTIAL) |
| **makaan** | `json_embed` | `stealthy` | `noida-residential-property-in-noida-buy` (REDIRECT) |

### Extraction Methods

- **json_embed** — Extracts JavaScript embedded JSON variables (`__initialData__`, `SERVER_PRELOADED_STATE_`, `__NEXT_DATA__`) using brace-depth parsing
- **jsonld** — Parses `<script type="application/ld+json">` blocks (Product, Apartment, SingleFamilyResidence)
- **css** — CSS3 selectors with pseudo-selectors (`::text`, `::attr(href)`) and comma-separated fallbacks

**Important:** Only `css`-based scrapers (`olx`, `proptiger`) benefit from Scrapling's adaptive DOM relocation and `auto_save` caching. `json_embed` and `jsonld` sites (`99acres`, `magicbricks`, `squareyards`, `proptiger-flats`) extract data from static JSON blobs and do not pass through the adaptive parser. Their `adaptive_cache` config key is omitted (see `config/sites.yaml`).

**Adaptive cache:** Fingerprints and page snapshots for CSS scrapers are persisted to `./.scrapling_cache` (configured per-site via `adaptive_cache` in `config/sites.yaml`). This directory is intended for CI persistence via `actions/cache` so relocation data survives across workflow runs.

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
python -m playwright install chromium
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
**Concurrency:** `group: scrape-db` — ensures only one scrape runs at a time, eliminating race conditions on the SQLite DB.

### Job 1 — `scrape`:

1. Checkout repo + setup Python 3.12
2. Cache pip packages, Playwright browsers, and adaptive fingerprints
3. `pip install -r requirements.txt` + `playwright install chromium`
3. `gh run download` previous artifacts: `scraper-db` (SQLite DB), `scraper-state` (yield history + fallback state)
4. `python main.py --all` — scrape all sites
5. `python check_anomalies.py` — compare per-site yield against 7-day rolling median; writes to `$GITHUB_STEP_SUMMARY`
6. `python consolidate.py --sites ...` — upsert into SQLite
7. **Sync DB to Cloudflare R2** — gzip-compressed backup of `noida_properties.db` pushed to R2 bucket as both a timestamped copy and `noida_properties-latest.db.gz`
8. `python dashboard/build-data.py` — build data.json from DB
9. Upload artifacts: `scraper-db`, `scraper-state`, `dashboard-build`, raw output

### Job 2 — `deploy` (needs: scrape, environment: deploy):
1. Download dashboard artifact
2. `wrangler pages deploy dashboard --branch master`

### Artifact persistence:
- `scraper-db` (90-day retention): SQLite database, downloaded at start of each run so data accumulates
- `scraper-state` (90-day retention): `yield_history.json` + `fallback_state.json` + `fallback_anomaly.json` for anomaly detection and StealthyFetcher fallback persistence

### R2 Restore Instructions

To restore the database from Cloudflare R2:

```bash
# Install AWS CLI configured for Cloudflare R2 S3 endpoint
aws s3 cp s3://<bucket>/noida_properties-latest.db.gz /tmp/ --endpoint-url https://<account-id>.r2.cloudflarestorage.com --region auto
gzip -d /tmp/noida_properties-latest.db.gz
cp /tmp/noida_properties-latest.db data/consolidated/noida_properties.db
```

### Required Secrets

| Secret | Purpose |
|--------|---------|
| `CLOUDFLARE_API_TOKEN` | Wrangler Pages deploy |
| `CLOUDFLARE_ACCOUNT_ID` | R2 endpoint URL construction (`https://<account-id>.r2.cloudflarestorage.com`) |
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | R2 S3 API credential |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | R2 S3 API credential |
| `R2_BUCKET_NAME` | R2 bucket name for DB backups |

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

## Blocked / Attempted Sites

| Site | Config | Fetcher | Status | StealthyFetcher Result |
|------|--------|---------|--------|----------------------|
| **nobroker.in** | Active | `stealthy` | BLOCKED | TIMEOUT — `network_idle` never fires due to continuous SPA background requests (analytics, polling). StealthyFetcher unable to reach idle state. |
| **housing.com** | Active | `stealthy` | PARTIAL | TIMEOUT — `network_idle` never fires (Akamai WAF + continuous analytics requests). WAF returns 406 on Noida-specific paths regardless of headless mode. |
| **makaan.com** | Active | `stealthy` | REDIRECT | Not independently tested; inherits housing.com WAF behavior (redirects to housing.com). |

Sites are configured with `fetcher: stealthy` and will attempt headless browser rendering on each run. If they yield 0 items for 3 consecutive runs, the auto-fallback in `BaseScraper._fallback_tracker` has already attempted stealthy mode (no further escalation).

---

## Compliance

- **robots.txt**: Checked by default (`respect_robots: true`). 99acres explicitly disabled.
- **Rate limiting**: 3s between requests per site (minimum 1s enforced in code).
- **Proxy rotation**: Configured in `config/sites.yaml` per-site as `proxy.url` (default: `socks5://127.0.0.1:9050`) with `proxy.enabled: false`. Proxy rotation is **disabled by default** — sites currently use the machine's public IP. To enable, set `proxy.enabled: true` for the target site and ensure the proxy service (e.g., local Tor daemon) is running. No open-source proxy feed is configured; the local Tor binding is the recommended free option.
- **Responsibility**: Scraping legality depends on the target site's ToS, data type, and your jurisdiction.
