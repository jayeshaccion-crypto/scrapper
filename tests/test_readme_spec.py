"""Verify README.md contains all Phase 1 spec-required documentation."""

from pathlib import Path

README = Path(__file__).parent.parent / "README.md"
CONTENT = README.read_text(encoding="utf-8")


class TestItem2StealthyFetcherDocs:
    """README must document StealthyFetcher results in Blocked Sites table (Item 2)."""

    def test_blocked_sites_table_exists(self):
        assert "## Blocked / Attempted Sites" in CONTENT

    def test_nobroker_documented(self):
        assert "nobroker" in CONTENT

    def test_stealthy_result_column(self):
        assert "StealthyFetcher Result" in CONTENT


class TestItem3AdaptiveCacheDocs:
    """README must document adaptive cache location (Item 3)."""

    def test_cache_path_documented(self):
        assert ".scrapling_cache" in CONTENT

    def test_cache_persistence_mentioned(self):
        assert "CI persistence" in CONTENT or "actions/cache" in CONTENT


class TestItem4JsonldLimitationDocs:
    """README must explain JSON/LD sites lack adaptive DOM protection (Item 4)."""

    def test_adaptive_limitation_stated(self):
        assert "do NOT receive DOM adaptive protection" in CONTENT or "do not pass through the adaptive parser" in CONTENT

    def test_json_embed_sites_listed(self):
        assert "99acres" in CONTENT
        assert "magicbricks" in CONTENT
        assert "proptiger-flats" in CONTENT

    def test_css_sites_distinguished(self):
        assert "olx" in CONTENT
        assert "proptiger" in CONTENT

    def test_adaptive_cache_path_documented(self):
        assert "adaptive_cache" in CONTENT


class TestItem5ProxyDocs:
    """README must state proxy posture (Item 5)."""

    def test_proxy_section_exists(self):
        assert "Proxy rotation" in CONTENT or "proxy" in CONTENT.lower()

    def test_proxy_disabled_stated(self):
        assert "disabled by default" in CONTENT
