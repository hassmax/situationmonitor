"""Small helpers shared across the pipeline."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value) -> datetime | None:
    """Parse ISO-8601 strings (with or without Z / fractional seconds)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Trim over-long fractional seconds, e.g. 2026-09-26T12:00:00.1234567+00:00
        m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
        if not m:
            return None
        frac = (m.group(2) or "")[:7]
        try:
            dt = datetime.fromisoformat(m.group(1) + frac + (m.group(3) or ""))
        except ValueError:
            return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def hours_since(value, ref: datetime) -> float:
    t = parse_time(value)
    if not t:
        return 1e9
    return (ref - t).total_seconds() / 3600.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def short_hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def clean_text(text: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def user_agent() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "local")
    contact = os.environ.get("NOMINATIM_EMAIL", "").strip()
    ua = f"conflict-globe/1.0 (+https://github.com/{repo})"
    if contact:
        ua += f" {contact}"
    return ua


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent()})
    return s


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if pretty:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        else:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def make_item(src: dict, platform: str, source_id: str, url: str, text: str,
              published: datetime, uid: str | None = None) -> dict:
    """Normalise a post/article into the shape the extractor expects."""
    # A post can't be newer than the moment we read it. Some feeds give only a date, stamped as
    # midnight of a day that hasn't started yet in UTC (Taipei Times), which the map would show
    # as "just now" until the clock caught up.
    published = min(published if published.tzinfo else published.replace(tzinfo=UTC), now())
    return {
        "id": short_hash(platform, uid or url),
        "source_id": source_id,
        "source": src.get("name") or source_id,
        "platform": platform,
        "kind": src.get("kind", "osint"),
        "side": src.get("side"),
        "group": src.get("group") or source_id,
        "weight": int(src.get("weight", 1)),
        "prefilter": bool(src.get("prefilter", True)),
        "url": url,
        "text": clean_text(text)[:1500],
        "time": iso(published),
    }


def health_ok(name: str, platform: str, latest: datetime | None, count: int,
              previous: dict | None = None) -> dict:
    prev_last = (previous or {}).get("last_post")
    last = iso(latest) if latest else prev_last
    if latest and prev_last and prev_last > last:
        last = prev_last
    return {
        "name": name,
        "platform": platform,
        "ok": True,
        "checked": iso(now()),
        "last_post": last,
        "count": count,
        "error": None,
    }


def health_fail(name: str, platform: str, error: str, previous: dict | None = None) -> dict:
    prev = previous or {}
    return {
        "name": name,
        "platform": platform,
        "ok": False,
        "checked": iso(now()),
        "last_post": prev.get("last_post"),
        "count": 0,
        "error": str(error)[:200],
    }
