"""Phase 3 tests: Pydantic validation (Item 10) + Locality allowlist (Item 11)."""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.property import PropertyListing, SITE_DOMAINS
from normalizer import matches_noida_locality, matches_noida_fallback, normalize
from consolidate import write_rejection, REJECTED_DIR
from pydantic import ValidationError


# ------------------------------------------------------------------ #
#  Item 10 — Pydantic Validation Layer                               #
# ------------------------------------------------------------------ #

class TestPropertyModelBasic:
    """PropertyListing schema validators."""

    def test_valid_minimal(self):
        p = PropertyListing(source_site="99acres", title="Flat in Noida")
        assert p.source_site == "99acres"
        assert p.title == "Flat in Noida"
        assert p.city == "Noida"

    def test_price_inr_must_be_positive(self):
        with pytest.raises(ValidationError, match="price_inr must be > 0"):
            PropertyListing(source_site="99acres", title="Flat", price_inr=0)

    def test_price_inr_negative_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres", title="Flat", price_inr=-100)

    def test_price_inr_none_allowed(self):
        p = PropertyListing(source_site="99acres", title="Flat", price_inr=None)
        assert p.price_inr is None

    def test_price_inr_positive_allowed(self):
        p = PropertyListing(source_site="99acres", title="Flat", price_inr=50_00_000)
        assert p.price_inr == 50_00_000

    def test_area_sqft_must_be_positive(self):
        with pytest.raises(ValidationError, match="area_sqft must be > 0"):
            PropertyListing(source_site="99acres", title="Flat", area_sqft=0)

    def test_area_sqft_negative_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres", title="Flat", area_sqft=-50)

    def test_area_sqft_none_allowed(self):
        p = PropertyListing(source_site="99acres", title="Flat", area_sqft=None)
        assert p.area_sqft is None

    def test_bhk_zero_allowed(self):
        p = PropertyListing(source_site="99acres", title="Studio", bhk=0)
        assert p.bhk == 0

    def test_bhk_ten_allowed(self):
        p = PropertyListing(source_site="99acres", title="Villa", bhk=10)
        assert p.bhk == 10

    def test_bhk_eleven_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres", title="Villa", bhk=11)

    def test_bhk_negative_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres", title="Flat", bhk=-1)

    def test_bhk_none_allowed(self):
        p = PropertyListing(source_site="99acres", title="Flat", bhk=None)
        assert p.bhk is None

    def test_title_must_be_non_empty(self):
        with pytest.raises(ValidationError, match="title must be a non-empty string"):
            PropertyListing(source_site="99acres", title="")

    def test_title_whitespace_only_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres", title="   ")

    def test_title_stripped(self):
        p = PropertyListing(source_site="99acres", title="  Flat in Noida  ")
        assert p.title == "Flat in Noida"

    def test_url_none_allowed(self):
        p = PropertyListing(source_site="99acres", title="Flat", url=None)
        assert p.url is None

    def test_url_valid_https(self):
        p = PropertyListing(source_site="99acres", title="Flat", url="https://www.99acres.com/property")
        assert p.url == "https://www.99acres.com/property"

    def test_url_invalid_scheme(self):
        with pytest.raises(ValidationError, match="url must be HTTP/HTTPS"):
            PropertyListing(source_site="99acres", title="Flat", url="ftp://files.com")

    def test_url_missing_scheme(self):
        with pytest.raises(ValidationError, match="url must be HTTP/HTTPS"):
            PropertyListing(source_site="99acres", title="Flat", url="not-a-url")


class TestPropertyModelDomainValidation:
    """url_matches_site_domain model_validator."""

    def test_url_domain_matches_source_site(self):
        p = PropertyListing(
            source_site="99acres",
            title="Flat",
            url="https://www.99acres.com/property/123",
        )
        assert p.url == "https://www.99acres.com/property/123"

    def test_url_domain_mismatch_rejected(self):
        with pytest.raises(ValidationError, match="does not match expected domain"):
            PropertyListing(
                source_site="99acres",
                title="Flat",
                url="https://www.magicbricks.com/property",
            )

    def test_url_domain_mismatch_magicbricks(self):
        with pytest.raises(ValidationError):
            PropertyListing(
                source_site="magicbricks",
                title="Flat",
                url="https://www.99acres.com/property",
            )

    def test_unknown_source_site_no_domain_check(self):
        p = PropertyListing(
            source_site="unknown_site",
            title="Flat",
            url="https://example.com/property",
        )
        assert p.url == "https://example.com/property"

    def test_all_config_sites_have_domains(self):
        """Every site in SITE_DOMAINS has a non-empty domain."""
        for name, domain in SITE_DOMAINS.items():
            assert domain, f"{name} has empty domain"
            assert "." in domain, f"{name} domain '{domain}' looks invalid"

    def test_url_domain_valid_for_nobroker(self):
        p = PropertyListing(
            source_site="nobroker",
            title="Flat",
            url="https://www.nobroker.in/property",
        )
        assert p.url == "https://www.nobroker.in/property"


# ------------------------------------------------------------------ #
#  Item 11 — Locality Allowlist Precision                            #
# ------------------------------------------------------------------ #

class TestLocalityAllowlist:
    """matches_noida_locality strict pattern matching."""

    def test_sector_pattern(self):
        assert matches_noida_locality("Sector 62, Noida")
        assert matches_noida_locality("Sector 128")

    def test_noida_extension(self):
        assert matches_noida_locality("Noida Extension")

    def test_greater_noida(self):
        assert matches_noida_locality("Greater Noida West")

    def test_yamuna_expressway(self):
        assert matches_noida_locality("Yamuna Expressway")

    def test_gamma_zone(self):
        assert matches_noida_locality("Gamma 1")
        assert matches_noida_locality("Gamma 2")

    def test_zeta_zone(self):
        assert matches_noida_locality("Zeta 1")

    def test_beta_zone(self):
        assert matches_noida_locality("Beta 2")

    def test_alpha_zone(self):
        assert matches_noida_locality("Alpha 1")

    def test_delta_zone(self):
        assert matches_noida_locality("Delta 3")

    def test_omega_zone(self):
        assert matches_noida_locality("Omega 1")

    def test_pari_chowk(self):
        assert matches_noida_locality("Pari Chowk")
        assert matches_noida_locality("Parichowk")

    def test_knowledge_park(self):
        assert matches_noida_locality("Knowledge Park 3")

    def test_film_city(self):
        assert matches_noida_locality("Film City")

    def test_ecotech(self):
        assert matches_noida_locality("Ecotech 3")

    def test_techzone(self):
        assert matches_noida_locality("Techzone 4")

    def test_non_noida_rejected(self):
        assert not matches_noida_locality("Mumbai")
        assert not matches_noida_locality("Gurgaon")
        assert not matches_noida_locality("Dwarka, Delhi")

    def test_none_rejected(self):
        assert not matches_noida_locality(None)

    def test_empty_string_rejected(self):
        assert not matches_noida_locality("")

    def test_locality_with_noida_word(self):
        assert matches_noida_locality("Noida")

    def test_locality_with_sector_alone(self):
        """Standalone sector numbers match because scrapers already filter."""
        assert matches_noida_locality("Sector 44")
        assert matches_noida_locality("Sector 12")

    def test_sector_without_number_not_match(self):
        assert not matches_noida_locality("Sector Road")


class TestLocalityFallback:
    """matches_noida_fallback substring matching (when locality empty)."""

    def test_noida_substring(self):
        assert matches_noida_fallback("Flat in Noida Sector 62")

    def test_greater_noida_substring(self):
        assert matches_noida_fallback("2BHK in Greater Noida")

    def test_sector_substring(self):
        assert matches_noida_fallback("2BHK in Sector 62")

    def test_expressway_substring(self):
        assert matches_noida_fallback("Flat on Yamuna Expressway")

    def test_ecotech_substring(self):
        assert matches_noida_fallback("Plot in Ecotech 3")

    def test_non_noida_rejected(self):
        assert not matches_noida_fallback("Flat in Mumbai Andheri")

    def test_none_rejected(self):
        assert not matches_noida_fallback(None)

    def test_empty_rejected(self):
        assert not matches_noida_fallback("")


class TestNormalizeLocalityRejection:
    """normalize() sets _rejected for non-Noida localities."""

    def test_noida_locality_passes(self):
        result = normalize({
            "site_name": "99acres",
            "locality": "Sector 62, Noida",
            "title": "Flat",
        })
        assert "_rejected" not in result or not result["_rejected"]

    def test_non_noida_locality_rejected(self):
        result = normalize({
            "site_name": "99acres",
            "locality": "Mumbai",
            "title": "Flat in Mumbai",
        })
        assert result.get("_rejected") is True
        assert "not in Noida allowlist" in result["_rejected_reason"]

    def test_empty_locality_with_noida_title_passes(self):
        result = normalize({
            "site_name": "99acres",
            "title": "Flat in Noida Sector 62",
            "locality": "",
        })
        assert "_rejected" not in result or not result["_rejected"]

    def test_empty_locality_without_noida_rejected(self):
        result = normalize({
            "site_name": "99acres",
            "title": "Flat in Mumbai",
            "locality": "",
        })
        assert result.get("_rejected") is True
        assert "no Noida substring" in result["_rejected_reason"]

    def test_locality_with_noida_via_title_location(self):
        """Fallback uses title + location + locality when locality empty."""
        result = normalize({
            "site_name": "99acres",
            "title": "2BHK Apartment",
            "location": "Sector 62, Noida",
        })
        assert "_rejected" not in result or not result["_rejected"]

    def test_non_noida_with_noida_in_non_locality_field(self):
        """Only locality field is checked first; if locality has non-noida, rejected."""
        result = normalize({
            "site_name": "99acres",
            "locality": "Mumbai",
            "title": "Flat in Noida Sector 62",
        })
        assert result.get("_rejected") is True
        assert "not in Noida allowlist" in result["_rejected_reason"]


# ------------------------------------------------------------------ #
#  Consolidation Pipeline — Rejection Logging                        #
# ------------------------------------------------------------------ #

class TestWriteRejection:
    """write_rejection outputs to output/rejected/{date}.jsonl."""

    @pytest.fixture(autouse=True)
    def isolate_rejected_dir(self, tmp_path):
        original = REJECTED_DIR
        import consolidate
        consolidate.REJECTED_DIR = tmp_path / "rejected"
        yield
        consolidate.REJECTED_DIR = original

    def test_writes_jsonl_entry(self, isolate_rejected_dir):
        import consolidate
        raw = {"site_name": "99acres", "title": "Bad"}
        norm = {"source_site": "99acres", "title": "Bad", "_private": "hidden"}
        write_rejection(raw, norm, "price_inr must be > 0")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log = consolidate.REJECTED_DIR / f"{date_str}.jsonl"
        assert log.exists()

        lines = log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["reason"] == "price_inr must be > 0"
        assert entry["raw"] == raw
        assert "_private" not in entry["normalized"]
        assert "rejected_at_utc" in entry

    def test_appends_multiple_entries(self, isolate_rejected_dir):
        import consolidate
        raw = {"site_name": "99acres", "title": "Bad"}
        norm = {"source_site": "99acres", "title": "Bad"}
        write_rejection(raw, norm, "error 1")
        write_rejection(raw, norm, "error 2")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log = consolidate.REJECTED_DIR / f"{date_str}.jsonl"
        lines = log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


class TestConsolidateValidation:
    """consolidate.py validates records before upsert."""

    def test_model_accepts_none_bhk(self):
        """None should pass for optional fields."""
        p = PropertyListing(source_site="magicbricks", title="Flat", bhk=None)
        assert p.bhk is None

    def test_model_accepts_none_price(self):
        p = PropertyListing(source_site="magicbricks", title="Flat", price_inr=None)
        assert p.price_inr is None

    def test_model_accepts_all_fields(self):
        p = PropertyListing(
            source_site="99acres",
            title="  Luxury 4BHK in Noida  ",
            listing_id="prop_123",
            url="https://www.99acres.com/property/123",
            price_inr=1_50_00_000,
            price_per_sqft_inr=7500.0,
            area_sqft=2000.0,
            bhk=4,
            property_type="apartment",
            furnishing="semi",
            floor="5th",
            total_floors=15,
            age_years=3,
            locality="Sector 62, Noida",
            city="Noida",
            seller_type="Owner",
            latitude=28.62,
            longitude=77.39,
            amenities=["parking", "gym"],
            scraped_at_utc="2026-07-26T10:00:00Z",
            schema_version="1.0",
        )
        assert p.title == "Luxury 4BHK in Noida"
        assert p.price_inr == 1_50_00_000
        assert p.bhk == 4
        assert p.city == "Noida"

    def test_validation_error_contains_field_name(self):
        try:
            PropertyListing(source_site="99acres", title="Flat", bhk=15)
        except ValidationError as e:
            errors = e.errors()
            assert any("bhk" in str(err["loc"]) for err in errors)

    def test_missing_title_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(source_site="99acres")

    def test_missing_source_site_rejected(self):
        with pytest.raises(ValidationError):
            PropertyListing(title="Flat")


# ------------------------------------------------------------------ #
#  Storage — Double-Normalization Fix                                #
# ------------------------------------------------------------------ #

class TestStorageNormalizedParam:
    """Storage.upsert_listing accepts normalized= param to skip re-normalize."""

    def test_upsert_listing_with_normalized(self):
        from storage import Storage
        import tempfile
        tmp_db = Path(tempfile.mktemp(suffix=".db"))
        try:
            store = Storage(str(tmp_db))
            record = {"site_name": "99acres", "listing_url": "test-1", "title": "Raw"}
            norm = {
                "source_site": "99acres",
                "listing_id": "test-1",
                "title": "Normalized Flat",
                "url": None,
                "price_inr": None,
                "price_per_sqft_inr": None,
                "area_sqft": None,
                "bhk": None,
                "property_type": None,
                "furnishing": None,
                "floor": None,
                "total_floors": None,
                "age_years": None,
                "locality": None,
                "city": "Noida",
                "seller_type": None,
                "latitude": None,
                "longitude": None,
                "amenities": [],
                "schema_version": "1.0",
            }
            lid = store.upsert_listing(record, normalized=norm)
            assert lid is not None

            rows = store.get_all_listings()
            assert len(rows) == 1
            assert rows[0]["title"] == "Normalized Flat"
        finally:
            store.close()
            tmp_db.unlink(missing_ok=True)

    def test_upsert_many_with_normalized(self):
        from storage import Storage
        import tempfile
        tmp_db = Path(tempfile.mktemp(suffix=".db"))
        try:
            store = Storage(str(tmp_db))
            norm_base = {
                "source_site": "99acres",
                "url": None, "price_inr": None, "price_per_sqft_inr": None,
                "area_sqft": None, "bhk": None, "property_type": None,
                "furnishing": None, "floor": None, "total_floors": None,
                "age_years": None, "locality": None, "city": "Noida",
                "seller_type": None, "latitude": None, "longitude": None,
                "amenities": [], "schema_version": "1.0",
            }
            records = [
                {"site_name": "99acres", "listing_url": "a1", "title": "Raw A"},
                {"site_name": "99acres", "listing_url": "a2", "title": "Raw B"},
            ]
            norms = [
                {**norm_base, "listing_id": "a1", "title": "Norm A"},
                {**norm_base, "listing_id": "a2", "title": "Norm B"},
            ]
            ids = store.upsert_many(records, normalized=norms)
            assert len(ids) == 2

            rows = store.get_all_listings()
            titles = {r["title"] for r in rows}
            assert titles == {"Norm A", "Norm B"}
        finally:
            store.close()
            tmp_db.unlink(missing_ok=True)
