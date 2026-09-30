"""Load theaters, sources, and settings from pipeline/config/*.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent / "config"

DEFAULT_SETTINGS = {
    # Any OpenAI-compatible chat endpoint works. Default: Google's free Gemini API.
    "llm_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    # Tried in order until one answers; the winner is remembered for a day.
    "llm_models": [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-lite",
    ],
    # Backups tried only when every model above is overloaded or gone: regular Flash is also free,
    # with a separate, smaller daily limit. Unknown names are skipped.
    "llm_fallback_models": ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash", "gemini-2.5-flash"],
    # Gemini's free Flash-Lite quota is about 500 requests/day; keep headroom.
    "daily_llm_calls": 470,
    "max_calls_per_run": 6,
    # Extraction stops when fewer than this many calls are left today, keeping room for the
    # situation brief (which is skipped when fewer than brief_min_calls are left).
    "extraction_reserve": 30,
    "brief_min_calls": 5,
    # The same-story check (at most one call an hour) is skipped below this.
    "dedupe_min_calls": 10,
    "seconds_between_calls": 7,
    "batch_max_items": 25,
    "batch_token_budget": 10000,
    "max_output_tokens": 6000,
    "max_item_age_hours": 36,
    "pending_max": 400,
    "geocode_per_run": 45,
    "event_retention_days": 7,
    "heat_retention_hours": 72,
    "max_events": 2500,
    "max_heat_cells": 4000,
    # GDELT counts as one extra independent source when 3+ news outlets report violence nearby.
    "gdelt": True,
}


@dataclass
class Config:
    theaters: list[dict]
    sources: dict
    settings: dict = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)  # event ids taken off the map by hand
    outlets: dict = field(default_factory=dict)  # domain -> outlet (see "outlets" in sources.yaml)
    alerts: dict = field(default_factory=dict)  # Telegram alert rules (alerts.yaml)

    @property
    def theater_ids(self) -> set[str]:
        return {t["id"] for t in self.theaters}


def load() -> Config:
    theaters = yaml.safe_load((CONFIG_DIR / "theaters.yaml").read_text(encoding="utf-8"))["theaters"]
    sources = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8")) or {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(sources.pop("settings", None) or {})
    outlets = {str(o["domain"]).lower(): o for o in (sources.pop("outlets", None) or []) if isinstance(o, dict) and o.get("domain")}
    for key in ("bluesky", "telegram", "rss"):
        sources[key] = [s for s in (sources.get(key) or []) if s and not s.get("disabled")]
    removed_file = CONFIG_DIR / "removed.yaml"
    removed = yaml.safe_load(removed_file.read_text(encoding="utf-8")) if removed_file.exists() else None
    removed_ids = {str(r["id"]) for r in ((removed or {}).get("removed") or []) if isinstance(r, dict) and r.get("id")}
    alerts_file = CONFIG_DIR / "alerts.yaml"
    alerts = (yaml.safe_load(alerts_file.read_text(encoding="utf-8")) or {}) if alerts_file.exists() else {}
    return Config(theaters=theaters, sources=sources, settings=settings, removed=removed_ids, outlets=outlets,
                  alerts=alerts)
