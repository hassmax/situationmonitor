"""Load theaters, sources, and settings from pipeline/config/*.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent / "config"

DEFAULT_SETTINGS = {
    # GitHub Models model id. Low-tier models get the most free requests per day.
    "model": "openai/gpt-4.1-mini",
    # Free tier is ~150 requests/day on low-tier models; keep headroom.
    "daily_llm_calls": 140,
    "max_calls_per_run": 4,
    "batch_max_items": 18,
    "batch_token_budget": 5200,
    "max_item_age_hours": 36,
    "pending_max": 400,
    "geocode_per_run": 45,
    "event_retention_days": 7,
    "heat_retention_hours": 72,
    "max_events": 2500,
    "max_heat_cells": 4000,
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
