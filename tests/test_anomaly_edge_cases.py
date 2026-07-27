"""Edge case tests for check_anomalies.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_anomalies as ca


@pytest.fixture(autouse=True)
def isolate_env():
    """Replace file paths with temp dirs for each test."""
    tmp = Path(tempfile.mkdtemp())
    ca.OUTPUT_DIR = tmp / "output"
    ca.HISTORY_PATH = tmp / "yield_history.json"
    ca.FALLBACK_PATH = tmp / "fallback_anomaly.json"
    ca.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Set GITHUB_STEP_SUMMARY to a temp file
    os.environ["GITHUB_STEP_SUMMARY"] = str(tmp / "summary.md")
    yield
    # Restore originals
    ca.OUTPUT_DIR = Path("output")
    ca.HISTORY_PATH = Path("data/yield_history.json")
    ca.FALLBACK_PATH = Path("data/fallback_anomaly.json")
    os.environ.pop("GITHUB_STEP_SUMMARY", None)


def make_output(site, count, date=None):
    d = ca.OUTPUT_DIR / site
    d.mkdir(parents=True, exist_ok=True)
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = d / f"{date}.json"
    f.write_text(json.dumps([{}] * count), encoding="utf-8-sig")


def run():
    # Persist history + fallback across calls (like real CI)
    sys.argv = ["check_anomalies.py"]
    rc = ca.main()
    fb = {}
    if ca.FALLBACK_PATH.exists():
        fb = json.loads(ca.FALLBACK_PATH.read_text())
    return rc, fb


class TestAnomalyEdgeCases:
    def test_first_run_normal_yield(self):
        make_output("testsite", 10)
        rc, fb = run()
        assert rc == 0
        assert fb == {}

    def test_zero_yield_with_baseline(self):
        make_output("testsite", 10)
        rc, _ = run()  # establish baseline
        make_output("testsite", 0)
        rc, fb = run()
        assert rc == 0
        assert "testsite" not in fb  # zero-yield not in anomaly file

    def test_drop_anomaly(self):
        make_output("testsite", 10)
        rc, _ = run()  # baseline
        make_output("testsite", 1)
        rc, fb = run()
        assert rc == 0
        assert fb.get("testsite") == 1

    def test_consecutive_drop_trigger(self):
        make_output("testsite", 10)
        rc, _ = run()  # baseline
        make_output("testsite", 1)
        rc, _ = run()  # first drop -> count 1
        make_output("testsite", 1)
        rc, fb = run()  # second drop -> count >= 2
        assert rc == 0
        assert fb.get("testsite", 0) >= 2

    def test_normal_run_resets_anomaly(self):
        make_output("testsite", 10)
        rc, _ = run()  # baseline
        make_output("testsite", 1)
        rc, _ = run()  # first drop -> count 1
        make_output("testsite", 10)
        rc, fb = run()  # normal -> reset
        assert rc == 0
        assert "testsite" not in fb

    def test_mixed_sites(self):
        make_output("site_anom", 10)
        make_output("site_ok", 10)
        rc, _ = run()  # baselines
        make_output("site_anom", 1)
        make_output("site_ok", 10)
        rc, fb = run()
        assert rc == 0
        assert "site_anom" in fb
        assert "site_ok" not in fb

    def test_exit_code_always_zero(self):
        make_output("testsite", 10)
        run()  # baseline
        make_output("testsite", 0)
        rc, _ = run()
        assert rc == 0  # never fails

    def test_first_run_zero_yield(self):
        make_output("testsite", 0)
        rc, fb = run()
        assert rc == 0
        assert fb == {}  # no baseline -> no anomaly
