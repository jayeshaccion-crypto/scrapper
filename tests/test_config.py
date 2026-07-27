"""Validate config/sites.yaml against Phase 1 spec requirements."""

import yaml
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sites.yaml"


def load_sites():
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    return data["sites"]


class TestConfigStructure:
    """Every site must have these fields."""

    REQUIRED_FIELDS = {"name", "parser", "start_urls", "fetcher", "selectors", "rate_limit_seconds", "output_format"}

    def test_all_sites_have_required_fields(self):
        sites = load_sites()
        for s in sites:
            missing = self.REQUIRED_FIELDS - set(s.keys())
            assert not missing, f"{s['name']} missing: {missing}"

    def test_selectors_have_fields(self):
        sites = load_sites()
        for s in sites:
            assert "fields" in s.get("selectors", {}), f"{s['name']} missing selectors.fields"

    def test_location_filter_on_all_sites(self):
        sites = load_sites()
        for s in sites:
            lf = s.get("location_filter", {})
            assert lf.get("field"), f"{s['name']} missing location_filter.field"
            assert lf.get("contains"), f"{s['name']} missing location_filter.contains"

    def test_proxy_block_on_all_sites(self):
        sites = load_sites()
        for s in sites:
            proxy = s.get("proxy", {})
            assert "url" in proxy, f"{s['name']} missing proxy.url"
            assert "enabled" in proxy, f"{s['name']} missing proxy.enabled"
            assert proxy["enabled"] is False, f"{s['name']} proxy should be disabled by default"

    def test_valid_fetcher_values(self):
        sites = load_sites()
        for s in sites:
            assert s["fetcher"] in ("dynamic", "stealthy"), f"{s['name']} invalid fetcher: {s['fetcher']}"

    def test_valid_parser_values(self):
        sites = load_sites()
        for s in sites:
            assert s["parser"] in ("css", "json_embed", "jsonld"), f"{s['name']} invalid parser: {s['parser']}"

    def test_rate_limit_positive(self):
        sites = load_sites()
        for s in sites:
            assert s["rate_limit_seconds"] >= 1.0, f"{s['name']} rate_limit too low"


class TestAdaptiveCache:
    """Adaptive cache must be on CSS sites only (Item 3, Item 4)."""

    def test_css_sites_have_adaptive_cache(self):
        sites = load_sites()
        css_sites = [s for s in sites if s["parser"] == "css"]
        for s in css_sites:
            assert "adaptive_cache" in s, f"{s['name']} (CSS) missing adaptive_cache"
            assert s["adaptive_cache"] == ".scrapling_cache"

    def test_json_sites_omit_adaptive_cache(self):
        sites = load_sites()
        non_css = [s for s in sites if s["parser"] != "css"]
        for s in non_css:
            assert "adaptive_cache" not in s, (
                f"{s['name']} ({s['parser']}) should NOT have adaptive_cache"
            )


class TestStealthyFetcher:
    """StealthyFetcher must be configured for blocked sites (Item 2)."""

    REQUIRED_STEALTHY = {"nobroker", "99acres", "housing", "makaan"}

    def test_blocked_sites_use_stealthy(self):
        sites = load_sites()
        names = {s["name"] for s in sites if s["fetcher"] == "stealthy"}
        for site in self.REQUIRED_STEALTHY:
            assert site in names, f"{site} must use fetcher: stealthy"

    def test_dynamic_sites_not_stealthy(self):
        sites = load_sites()
        active = {"magicbricks", "squareyards", "olx", "proptiger", "proptiger-flats"}
        for s in sites:
            if s["name"] in active:
                assert s["fetcher"] == "dynamic", f"{s['name']} should use dynamic, not {s['fetcher']}"


class TestSiteDomains:
    """Every config site must have a domain entry in PropertyListing."""

    def test_all_sites_have_domain_in_model(self):
        from models.property import SITE_DOMAINS
        sites = load_sites()
        for s in sites:
            assert s["name"] in SITE_DOMAINS, (
                f"{s['name']} missing from SITE_DOMAINS in models/property.py"
            )
            domain = urlparse(s["start_urls"][0]).netloc
            assert SITE_DOMAINS[s["name"]] == domain, (
                f"{s['name']}: SITE_DOMAINS has '{SITE_DOMAINS[s['name']]}' "
                f"but config has '{domain}'"
            )

    def test_no_extra_domains(self):
        from models.property import SITE_DOMAINS
        sites = load_sites()
        config_names = {s["name"] for s in sites}
        for name in SITE_DOMAINS:
            assert name in config_names, (
                f"SITE_DOMAINS has '{name}' but no matching config site"
            )


class TestSiteCount:
    """Config must have all 9 sites."""

    def test_total_sites(self):
        sites = load_sites()
        assert len(sites) == 9, f"Expected 9 sites, got {len(sites)}"

    EXPECTED = {"magicbricks", "99acres", "squareyards", "olx", "proptiger", "proptiger-flats", "nobroker", "housing", "makaan"}

    def test_all_sites_present(self):
        sites = load_sites()
        names = {s["name"] for s in sites}
        missing = self.EXPECTED - names
        extra = names - self.EXPECTED
        assert not missing, f"Missing sites: {missing}"
        assert not extra, f"Unexpected sites: {extra}"
