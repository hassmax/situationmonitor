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
    # Gemini's free Flash-Lite quota is about 500 requests/day; keep headroom.
    "daily_llm_calls": 400,
    "max_calls_per_run": 6,
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

    @property
    def theater_ids(self) -> set[str]:
        return {t["id"] for t in self.theaters}


def load() -> Config:
    theaters = yaml.safe_load((CONFIG_DIR / "theaters.yaml").read_text(encoding="utf-8"))["theaters"]
    sources = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8")) or {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(sources.pop("settings", None) or {})
    for key in ("bluesky", "telegram", "rss"):
        sources[key] = [s for s in (sources.get(key) or []) if s and not s.get("disabled")]
    return Config(theaters=theaters, sources=sources, settings=settings)
