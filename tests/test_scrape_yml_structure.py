"""Validate scrape.yml structure matches Phase 2 spec."""

import yaml
from pathlib import Path

YML_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scrape.yml"


def load():
    with open(YML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_valid_yaml():
    data = load()
    assert data is not None


def test_concurrency_lock():
    data = load()
    c = data["concurrency"]
    assert c["group"] == "scrape-db"
    assert c["cancel-in-progress"] is False


def test_docker_container():
    data = load()
    container = data["jobs"]["scrape"]["container"]
    assert "ghcr.io/d4vinci/scrapling" in container["image"]


def test_playwright_browser_cache():
    data = load()
    steps = data["jobs"]["scrape"]["steps"]
    cache = [s for s in steps if s.get("name") == "Install Playwright browsers"]
    assert len(cache) == 1
    assert cache[0].get("if") is not None  # conditional on cache miss


def test_adaptive_cache_step():
    data = load()
    steps = data["jobs"]["scrape"]["steps"]
    cache = [s for s in steps if "cache adaptive" in s.get("name", "").lower()]
    assert len(cache) == 1
    c = cache[0]
    assert c["with"]["path"] == "./.scrapling_cache"
    assert "runner.os" in c["with"]["key"]
    assert "github.sha" in c["with"]["key"]


def test_r2_step_after_consolidate():
    data = load()
    steps = [s.get("name", "") for s in data["jobs"]["scrape"]["steps"]]
    consolidate_idx = steps.index("Consolidate to SQLite")
    r2_idx = steps.index("Sync DB to Cloudflare R2")
    assert r2_idx > consolidate_idx, "R2 sync must be after consolidate"
    # R2 should be before build-data
    build_idx = steps.index("Build dashboard data from DB")
    assert r2_idx < build_idx, "R2 sync should be before build-data"


def test_r2_required_secrets():
    data = load()
    steps = data["jobs"]["scrape"]["steps"]
    r2 = [s for s in steps if s.get("name") == "Sync DB to Cloudflare R2"][0]
    env = r2.get("env", {})
    required = ["CLOUDFLARE_ACCOUNT_ID", "AWS_ACCESS_KEY_ID",
                 "AWS_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]
    for s in required:
        assert s in env, f"Missing secret: {s}"


def test_anomaly_step_present():
    data = load()
    steps = [s.get("name", "") for s in data["jobs"]["scrape"]["steps"]]
    assert "Run anomaly detection" in steps


def test_scraper_state_artifact():
    data = load()
    steps = data["jobs"]["scrape"]["steps"]
    state_upload = [s for s in steps if "fallback" in str(s.get("with", {}).get("path", "")).lower()]
    assert len(state_upload) >= 1
    u = [s for s in steps if "Upload fallback" in s.get("name", "")]
    assert len(u) == 1
    path = u[0]["with"]["path"]
    assert "yield_history.json" in str(path)
    assert "fallback_anomaly.json" in str(path)
