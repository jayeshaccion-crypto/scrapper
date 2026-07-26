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
models/
  property.py            Pydantic v2 PropertyListing schema (pre-upsert validation)
normalizer.py            Field normalization + Noida locality allowlist engine
storage.py               SQLite persistence (listings + possible_duplicates)
dedup.py                 Cross-site dedup (price±3%, area±5%, Jaccard locality, title similarity)
consolidate.py           Reads output/ → normalize + Pydantic validate → upsert SQLite → dedup
dashboard/
  build-data.py          SQLite → data.json
  index.html             Frontend (filters, images, listing links)
.github/workflows/
  scrape.yml             CI/CD pipeline
```

**Data flow:**

```
Scrape → output/{site}/{date}.json → consolidate.py → normalize + Pydantic validate → SQLite (upsert) → build-data.py → data.json → Deploy
                                        ↓                        ↓
                                   rejected/{date}.jsonl    cross-site dedup
```

---

## Active Sites

| Site | Parser | Fetcher | URL |
|------|--------|---------|-----|
| **99acres** | `json_embed` | `stealthy` | `property-in-noida` (BLOCKED) |
| **magicbricks** | `json_embed` | `dynamic` | `flats-in-noida-for-sale` |
| **squareyards** | `jsonld` | `dynamic` | `sale/property-for-sale-in-noida` |
| **olx** | `css` | `dynamic` | 5 pages (`?page=1..5`) |
| **proptiger** | `css` | `dynamic` | `noida-real-estate` (project cards) |
| **proptiger-flats** | `json_embed` | `dynamic` | `flats-in-noida` (individual units) |
| **nobroker** | `json_embed` | `stealthy` | `property/sale/noida` (BLOCKED) |
| **housing** | `json_embed` | `stealthy` | `in/buy/noida` (BLOCKED) |
| **makaan** | `jsonld` | `stealthy` | `noida-residential-property` (BLOCKED) |

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

### Job 1 — `scrape` (runs inside `ghcr.io/d4vinci/scrapling:latest`):

1. Checkout repo + install runtime tools (`gh`, `awscli`, Python deps) inside the Scrapling Docker container
2. `actions/cache` restores adaptive fingerprints from `./.scrapling_cache`
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

---

## Deployment & Secrets

This project deploys to **Cloudflare Pages** (frontend) and **Cloudflare R2** (database backup). The CI/CD pipeline in `.github/workflows/scrape.yml` requires the following secrets configured in the GitHub repository (`Settings → Secrets and variables → Actions`):

| Secret | Required By | Purpose |
|--------|------------|---------|
| `CLOUDFLARE_API_TOKEN` | `wrangler pages deploy` | Cloudflare API token with `Cloudflare Pages` permission (`Write`) |
| `CLOUDFLARE_ACCOUNT_ID` | R2 `--endpoint-url` | Cloudflare account ID; used to construct `https://<account-id>.r2.cloudflarestorage.com` |
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | `aws s3 cp` | R2 S3-compatible credential key ID |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | `aws s3 cp` | R2 S3-compatible credential secret |
| `R2_BUCKET_NAME` | `aws s3 cp` | R2 bucket name where compressed DB snapshots are stored |

To set them:

```bash
gh secret set CLOUDFLARE_API_TOKEN --body "your-token"
gh secret set CLOUDFLARE_ACCOUNT_ID --body "your-account-id"
gh secret set CLOUDFLARE_R2_ACCESS_KEY_ID --body "your-r2-key-id"
gh secret set CLOUDFLARE_R2_SECRET_ACCESS_KEY --body "your-r2-secret"
gh secret set R2_BUCKET_NAME --body "your-bucket-name"
```

**Dashboard URL:** `https://noida-property-dashboard.pages.dev` (configured in `.github/workflows/scrape.yml` and Cloudflare Pages project settings).

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
| **housing.com** | Active | `stealthy` | BLOCKED | Next.js SPA — suspected captcha or bot detection on listing page. |
| **makaan.com** | Active | `stealthy` | BLOCKED | Server-rendered page blocks non-browser user-agents at CDN layer. |

Sites are configured with `fetcher: stealthy` and will attempt headless browser rendering on each run. If they yield 0 items for 3 consecutive runs, the auto-fallback in `BaseScraper._fallback_tracker` has already attempted stealthy mode (no further escalation).

---

## Data Validation & Locality Filtering

### Noida Locality Allowlist

**File:** `normalizer.py` (`NOIDA_LOCALITY_PATTERNS`)

Raw substring matching (`contains "Noida"`) has been replaced with a normalized token match against an explicit allowlist of Noida/Greater Noida locality tokens:

| Token | Example Matches |
|-------|----------------|
| `noida` | Any text containing "noida" |
| `noida extension` | Noida Extension, Noida Extn |
| `greater noida` | Greater Noida, Greater Noida West |
| `noida sector \d+` | Noida Sector 62, Sector 62 Noida |
| `sector \d+` | Sector 12, Sector 168 (standalone — scrapers already URL-filtered) |
| `yamuna expressway` | Yamuna Expressway area |
| Greek prefixes | Gamma, Zeta, Beta, Alpha, Delta, Omega + number |
| Landmarks | Knowledge Park, Film City, Ecotech, TechZone, Pari(Chowk) |

If the `locality` field is empty or unpopulated, the engine falls back to substring matching on `title` + `location` fields to catch listings where locality metadata is missing.

**Allowlist source:** Manual curation of residential sectors notified by Noida Authority, Greater Noida Authority, and YEIDA (Yamuna Expressway Industrial Development Authority) as of July 2026.

### Pre-Upsert Pydantic Validation

**Files:** `models/property.py`, `consolidate.py`

Every scraped record passes through two validation gates before reaching SQLite:

1. **Schema validation** (`PropertyListing` Pydantic v2 model):
   - `price_inr` > 0 or `None`
   - `area_sqft` > 0 or `None`
   - `bhk` between 0–10 or `None`
   - `url` must be HTTP/HTTPS and domain must match expected site domain
   - `title` must be a non-empty string

2. **Locality allowlist** — locality must match a Noida allowlist token or fallback substring.

Records that fail either gate are **not upserted**. They are streamed to `output/rejected/{date}.jsonl` with the rejection reason, the normalized fields, and the full raw record. Valid rows in the same batch continue to upsert without failing the pipeline.

### Rejection Log Format

```jsonl
{"rejected_at_utc": "2026-07-26T...", "reason": "price_inr must be > 0, got -100",
 "normalized": {...}, "raw": {...}}
```

---

## Compliance

- **robots.txt**: Checked by default (`respect_robots: true`). 99acres explicitly disabled (see `config/sites.yaml` for justification).
- **Rate limiting**: 3s between requests per site (minimum 1s enforced in code).
- **Proxy rotation**: Configured in `config/sites.yaml` per-site as `proxy.url` (default: `socks5://127.0.0.1:9050`) with `proxy.enabled: false`. Proxy rotation is **disabled by default** — sites currently use the machine's public IP. To enable, set `proxy.enabled: true` for the target site and ensure the proxy service (e.g., local Tor daemon) is running. No open-source proxy feed is configured; the local Tor binding is the recommended free option.
- **Responsibility**: Scraping legality depends on the target site's ToS, data type, and your jurisdiction.

---

## Security & AI Compliance

### XSS Defense

The dashboard (`dashboard/index.html`) renders property data using `innerHTML`. To prevent stored XSS attacks via poisoned listing fields, `dashboard/build-data.py` applies `html.escape()` to 23 free-text fields (`title`, `description`, `locality`, `seller_type`, `listing_url`, `site_name`, etc.) before serializing to `data.json`.

### PII Redaction

`normalizer.py` implements regex-based PII redaction across 12 known free-text fields (`title`, `description`, `contact_name`, `builder`, `full_address`, `sub_locality`, `building_name`, `society_name`, `locality`, `location`, `seller_type`, `user_type`) before they reach the database:

| PII Type | Pattern | Replacement |
|----------|---------|-------------|
| Indian mobile phone | `(?:\+91[\-\s]?\|0)?[6-9]\d{9}` | `[REDACTED]` |
| Email address | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` | `[REDACTED]` |

The phone regex handles all common Indian formats: `9876543210`, `+919876543210`, `+91-9876543210`, and `09876543210` (domestic `0`-prefix). The redaction runs inside `normalize()` on every raw record, so PII is stripped before Pydantic validation and SQLite upsert. The redacted values appear in both the normalized output and the `raw_data` JSON blob.

### LLM Prompt-Injection Warning

This scraper does **not** send scraped data to an LLM endpoint. If you extend it to do so (e.g., using Scrapling's `--ai-targeted` flag for AI-driven extraction), be aware that **adversarial content on the scraped page can influence LLM output**. Real-estate listings, for example, could contain hidden instructions in the description field that alter extraction behavior or leak internal prompt context.

Mitigations to apply if you enable AI-targeted extraction:

1. **Validate and sanitize all input** before it reaches the LLM prompt (already done for XSS and PII as described above).
2. **Isolate the extraction prompt** from user-facing content — never interpolate raw field values directly into system instructions.
3. **Scan for prompt-injection patterns** in description, title, and other free-text fields before passing to the model.
4. **Use output guards** — constrain the LLM's response schema and reject any output that deviates from the expected format.

When using Scrapling's `--ai-targeted` feature, review the `scrapling/scrapling/llm.py` source (specifically the prompt template construction) to ensure no raw page content is injected into the system-level instruction context.

---

## Site Config Schema Reference

**File:** `config/sites.yaml`

Each entry under `sites:` supports the following top-level keys:

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `name` | **Yes** | `string` | Site identifier (used in `--site` CLI arg and DB `source_site` column) |
| `parser` | **Yes** | `string` | Extraction method: `json_embed`, `jsonld`, or `css` |
| `start_urls` | **Yes** | `list[string]` | One or more starting URLs for the scrape |
| `fetcher` | **Yes** | `string` | Fetch mode: `dynamic` (real browser) or `stealthy` (anti-bot browser) |
| `wait_selector` | No | `string` | CSS selector to wait for before extracting (css parser only) |
| `respect_robots` | No | `bool` | Whether to obey robots.txt (default `true`). Disabled for 99acres (see Compliance) |
| `embed` | Conditional | `dict` | Required for `json_embed` / `jsonld` parsers. See sub-schema below |
| `selectors` | Conditional | `dict` | Required for `css` and `json_embed` parsers. Contains `item` (css parser) and `fields` |
| `pagination` | No | `dict` | Pagination config: `max_pages` (css parser only) |
| `rate_limit_seconds` | No | `float` | Min seconds between requests (default: `3.0`) |
| `output_format` | No | `string` | Default: `json` |
| `location_filter` | No | `dict` | Post-extraction filter: `field` + `contains` string. All sites use `field: locality` / `field: location`, `contains: "Noida"` |
| `adaptive_cache` | No | `string` | Cache directory path for adaptive DOM relocation (css parser only). E.g., `.scrapling_cache` |
| `proxy` | No | `dict` | Proxy config with `url` and `enabled` bool |

### Embed sub-schema

For `parser: json_embed`:
- `var` (required): JavaScript variable name containing the data
- `data_path` (required): Dot-separated path to the array within the parsed JSON

For `parser: jsonld`:
- `types` (required): JSON-LD types to extract (e.g., `Product`, `Apartment`)
- `filter` (optional): Field + contains filter applied before extraction

### Selectors sub-schema

For `parser: css`:
- `item` (required): CSS selector for the repeated listing element
- `fields` (required): Map of field names to CSS selectors with pseudo-selectors (`::text`, `::attr(href)`) and comma-separated fallbacks

For `parser: json_embed`:
- `fields` (required): Map of field names to JSON key paths

### Example

```yaml
sites:
  - name: example
    parser: css
    start_urls:
      - "https://example.com/listings"
    fetcher: dynamic
    wait_selector: ".listing-card"
    pagination:
      max_pages: 3
    selectors:
      item: ".listing-card"
      fields:
        title: ".listing-title::text"
        price: ".listing-price::text"
        url: "a::attr(href)"
    rate_limit_seconds: 3.0
    output_format: json
    location_filter:
      field: title
      contains: "Noida"
    adaptive_cache: ".scrapling_cache"
    proxy:
      url: "socks5://127.0.0.1:9050"
      enabled: false
```

---

### Compliance Justification: 99acres robots.txt

99acres `/robots.txt` issues a blanket `Disallow: /` for all user-agents. This project disables `respect_robots` for 99acres with the following mitigation:

| Concern | Mitigation |
|---------|------------|
| `Disallow: /` | Scrapes only the Noida listing SRP (search results page) — no user accounts, no auth-gated data |
| Request volume | `rate_limit_seconds: 3.0` — one request every 3 seconds (20 req/min), well below aggressive thresholds |
| Data type | Public listing metadata (price, area, locality, description) — no personal or private data |
| PII handling | Phone numbers and emails redacted in `normalizer.py` via regex before storage |
| XSS defense | All free-text fields escaped via `html.escape()` in `build-data.py` before dashboard display |

The scraper does **not** bypass login walls, access private APIs, or circumvent rate limits. It extracts the same public data that any browser viewing `https://www.99acres.com/property-in-noida-ffid` can see.
