"""Writing the public data for the dashboard.

The reports behind each event (source, link, summary, time) are four fifths of the published data,
and the page needs them only when an event is opened. So events.json carries the events without
them (plus the distinct source names, for search), and the reports go into REPORT_BUCKETS small
files, data/reports/NN.json, chosen by a hash of the event id that site/app.js computes the same way
(bucketOf). The page fetches one of them when an event is opened.
"""
from __future__ import annotations

from pathlib import Path

from common import save_json

REPORT_BUCKETS = 64  # site/app.js: REPORT_BUCKETS


def report_bucket(event_id: str) -> int:
    """31-based string hash, 32 bits, as in site/app.js (bucketOf)."""
    h = 0
    for ch in str(event_id):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % REPORT_BUCKETS


def split_reports(published: list[dict]) -> tuple[list[dict], list[dict]]:
    """Events without their reports (with `src`, the distinct source names), and the reports by bucket."""
    events, buckets = [], [{} for _ in range(REPORT_BUCKETS)]
    for e in published:
        reports = e.get("reports") or []
        buckets[report_bucket(e["id"])][e["id"]] = reports
        out = {k: v for k, v in e.items() if k != "reports"}
        out["src"] = sorted({r["source"] for r in reports if r.get("source")})
        events.append(out)
    return events, buckets


def write(out_dir: Path, public: dict) -> int:
    """events.json plus every reports file (all of them, so none is ever missing). Returns the bytes
    of events.json."""
    events, buckets = split_reports(public["events"])
    for i, bucket in enumerate(buckets):
        save_json(out_dir / "reports" / f"{i:02d}.json", {"generated_at": public["generated_at"], "reports": bucket})
    save_json(out_dir / "events.json", {**public, "events": events})
    return (out_dir / "events.json").stat().st_size
