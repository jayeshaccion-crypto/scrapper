"""
Anomaly detection for property scraper.

Compares per-site yield against a 7-day rolling median, flags 0 or >80% drop
anomalies, and tracks consecutive anomalies to trigger StealthyFetcher fallback.
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict


OUTPUT_DIR = Path("output")
HISTORY_PATH = Path("data/yield_history.json")
FALLBACK_PATH = Path("data/fallback_anomaly.json")


def get_site_yields() -> dict[str, int]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yields: dict[str, int] = {}
    for site_dir in OUTPUT_DIR.iterdir():
        if not site_dir.is_dir():
            continue
        count = 0
        # Only read today's output file(s) to avoid counting stale data
        for f in site_dir.glob(f"{today}.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, list):
                count += len(data)
        yields[site_dir.name] = count
    return yields


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def compute_median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 0:
        return (s[n // 2 - 1] + s[n // 2]) / 2
    return float(s[n // 2])


def main():
    parser = argparse.ArgumentParser(description="Anomaly detection for property scraper")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""),
                        help="Path to GITHUB_STEP_SUMMARY file")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")

    current = get_site_yields()
    history = load_json(HISTORY_PATH)

    # Append current yields to history with timestamp
    for site, count in current.items():
        history.setdefault(site, []).append({"ts": now.isoformat(), "count": count})

    # 7-day window for median calculation
    cutoff_ts = now.timestamp() - 7 * 24 * 3600

    summary_lines = [
        "## Anomaly Detection",
        f"**Run:** {date_str}",
        "",
        "| Site | Yield | 7-day Median | Drop % | Status |",
        "|------|-------|-------------|--------|--------|",
    ]

    anomalies: list[str] = []

    for site in sorted(current.keys()):
        count = current[site]
        entries = history.get(site, [])

        # Filter to 7-day window
        recent = [
            e["count"]
            for e in entries
            if datetime.fromisoformat(e["ts"]).timestamp() >= cutoff_ts
        ]
        # Exclude current run from median
        past = recent[:-1] if len(recent) > 1 else recent
        median = compute_median(past)

        if median > 0:
            drop_pct = max(0.0, (1 - count / median) * 100)
        else:
            drop_pct = 100.0 if count == 0 else 0.0

        if count == 0 and median > 0:
            status = "ZERO"
            anomalies.append(site)
        elif median > 0 and drop_pct > 80:
            status = "DROP"
            anomalies.append(site)
        else:
            status = "OK"

        summary_lines.append(
            f"| {site} | {count} | {median:.0f} | {drop_pct:.0f}% | {status} |"
        )

    summary_lines.append("")

    # Update persistent fallback state
    fallback = load_json(FALLBACK_PATH)

    for site in anomalies:
        # Only count non-zero-yield anomalies (zero-yield is handled by BaseScraper)
        if current.get(site, 0) > 0:
            fallback[site] = fallback.get(site, 0) + 1
    for site in current:
        if site not in anomalies:
            fallback.pop(site, None)

    # Sites with 2+ consecutive anomalies get trigger warning
    trigger_sites = [s for s, c in fallback.items() if c >= 2]

    if anomalies:
        summary_lines.append(f"**Anomalies detected:** {', '.join(anomalies)}")
        if trigger_sites:
            summary_lines.append(
                f"**StealthyFetcher trigger:** {', '.join(trigger_sites)}"
                " — 2+ consecutive anomalies, next run will activate stealthy fallback"
            )
    else:
        summary_lines.append("**No anomalies detected.**")

    summary_lines.append("")

    # Prune history older than 14 days
    old_cutoff = now.timestamp() - 14 * 24 * 3600
    for site in list(history.keys()):
        history[site] = [
            e for e in history[site]
            if datetime.fromisoformat(e["ts"]).timestamp() >= old_cutoff
        ]
        if not history[site]:
            del history[site]

    save_json(HISTORY_PATH, history)
    save_json(FALLBACK_PATH, fallback)

    output = "\n".join(summary_lines)
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(output + "\n")

    print(output)
    return 0  # alert-only, never fail the pipeline


if __name__ == "__main__":
    sys.exit(main())
