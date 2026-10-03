"""Territorial control: who holds the ground, as a named source maps it (config/control.yaml).

Two kinds of layer:
- ArcGIS (the default): the source's own shapes (ISW publishes Ukraine as feature layers), fetched
  as they are and simplified for the globe, with the source's last-edit date.
- "isw_map": where the source publishes its control map only as a picture (Yemen), the owner
  asked for the shapes anyway (2026-10-03): the newest matching map in ISW's Map Room is traced
  by colour (mapshapes.py), and the layer is published as approximate, with the map it came from.
A layer older than max_age_days is left off the map rather than shown as current. No model calls.

- Fetched at most every FETCH_EVERY; a failed fetch keeps the previous copy and its date.
- Coordinates are simplified on the server (OFFSET degrees, about 300 m) and rounded to 3 decimals:
  the globe is painted at about 5 km per pixel, so finer detail is never seen.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import mapshapes
from common import health_fail, health_ok, iso, log, parse_time
from sources import maproom

FETCH_EVERY = timedelta(hours=3)
OFFSET = 0.003       # degrees; maxAllowableOffset for the server's simplification
TIMEOUT = 30
STYLES = {"occupied", "advance", "infiltration"}
KINDS = {"arcgis", "isw_map"}
MAP_CANDIDATES = 3   # newest matching maps tried when the newest fails the checks


def _get(session, url: str) -> dict:
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(f"ArcGIS error: {data['error'].get('message') or data['error']}")
    return data


def _layer_url(session, url: str) -> str:
    """A FeatureServer layer URL; a service URL resolves to its first polygon layer."""
    url = url.rstrip("/")
    if not url.endswith("FeatureServer"):
        return url
    svc = _get(session, url + "?f=json")
    for lyr in svc.get("layers") or []:
        if lyr.get("geometryType") in (None, "esriGeometryPolygon"):
            return f"{url}/{lyr['id']}"
    raise ValueError("the service has no polygon layer")


def _polygons(geojson: dict) -> list:
    """[[ring, ...], ...] with [lon, lat] rounded to 3 decimals; empty rings dropped."""
    out = []
    for f in geojson.get("features") or []:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            polys = [g.get("coordinates")]
        elif g.get("type") == "MultiPolygon":
            polys = g.get("coordinates") or []
        else:
            polys = []
        for poly in polys:
            rings = [[[round(x, 3), round(y, 3)] for x, y, *_ in ring] for ring in poly or [] if len(ring) >= 4]
            if rings:
                out.append(rings)
    return out


def fetch_layer(session, layer: dict) -> dict:
    """{"as_of": iso date of the source's last edit, "polygons": [...]} for one configured layer."""
    url = _layer_url(session, layer["url"])
    meta = _get(session, url + "?f=json")
    ed = meta.get("editingInfo") or {}
    ms = ed.get("dataLastEditDate") or ed.get("lastEditDate")
    if meta.get("geometryType") not in (None, "esriGeometryPolygon"):
        raise ValueError(f"not a polygon layer ({meta.get('geometryType')})")
    q = {"where": "1=1", "outFields": "OBJECTID", "returnGeometry": "true", "outSR": 4326, "f": "geojson",
         "maxAllowableOffset": OFFSET, "geometryPrecision": 3}
    data = _get(session, f"{url}/query?{urlencode(q)}")
    as_of = iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc)) if ms else None
    return {"as_of": as_of, "polygons": _polygons(data)}


def _map_as_of(m: dict) -> str:
    """The map's own date ("as of October 1, 2026 at 2:00 PM ET": 18:00 UTC), else when it was posted."""
    days = maproom._dates(m["title"])
    return iso(max(days).replace(hour=18, tzinfo=timezone.utc)) if days else m["date"]


def fetch_traced(session, layer: dict, prev: dict) -> dict | None:
    """Trace the newest map in ISW's Map Room matching the layer; None when the newest is the one
    already traced. A map failing the checks is remembered and skipped (its layout differs)."""
    r = session.get(maproom.API, params={"search": layer["search"], "per_page": 10, "_fields": maproom.FIELDS},
                    headers=maproom.HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    title_re = re.compile(layer.get("title") or re.escape(layer["search"]), re.I)
    maps = []
    for m in r.json():
        title = maproom._plain((m.get("title") or {}).get("rendered"))
        image = maproom._image((m.get("content") or {}).get("rendered"))
        if m.get("id") and image and title_re.search(title) and m.get("date_gmt"):
            maps.append({"id": str(m["id"]), "title": title, "link": m.get("link") or "", "image": image,
                         "date": iso(parse_time(m["date_gmt"] + "Z"))})
    maps.sort(key=lambda m: m["date"], reverse=True)
    failed = set(prev.get("failed") or [])
    errors = []
    for m in maps[:MAP_CANDIDATES]:
        if m["id"] == prev.get("map_id"):
            prev["failed"] = sorted(failed)[-20:]
            return None
        if m["id"] in failed:
            continue
        img = session.get(m["image"], headers={**maproom.HEADERS, "Accept": "image/*"}, timeout=TIMEOUT)
        img.raise_for_status()
        try:
            got = mapshapes.trace(img.content, layer["trace"])
        except ValueError as exc:
            log(f"[control] {layer['id']}: {m['title']}: {exc}; not used")
            failed.add(m["id"])
            errors.append(str(exc))
            continue
        return {"as_of": _map_as_of(m), "polygons": got["polygons"], "map_id": m["id"], "map_title": m["title"],
                "map_link": m["link"], "fit_px": got["fit_px"], "dots": got["dots"], "failed": sorted(failed)[-20:]}
    if errors:
        raise ValueError(f"no recent map could be traced: {errors[0]}")
    if not maps:
        raise ValueError("no matching map in the Map Room")
    return None


def update(state: dict, session, layers: list[dict], now) -> None:
    """Refresh stored layers (at most every FETCH_EVERY). Failures keep the previous copy."""
    st = state.setdefault("control", {})
    health = state.setdefault("health", {})
    ids = {l["id"] for l in layers}
    for gone in [k for k in st if k not in ids]:
        st.pop(gone)
        health.pop(f"control:{gone}", None)
    for layer in layers:
        prev = st.get(layer["id"]) or {}
        fetched = parse_time(prev.get("fetched"))
        if fetched and now - fetched < FETCH_EVERY and prev.get("url") == layer["url"]:
            continue
        key = f"control:{layer['id']}"
        name = f"{layer.get('source', 'Control map')}: {layer['label']}"
        try:
            if layer.get("kind") == "isw_map":
                got = fetch_traced(session, layer, prev)
                if got is None:  # the newest map is the one already traced
                    st[layer["id"]] = {**prev, "fetched": iso(now), "url": layer["url"]}
                    continue
            else:
                got = fetch_layer(session, layer)
        except Exception as exc:  # noqa: BLE001 - keep the previous copy; the source panel shows the error
            log(f"[control] {layer['id']}: {exc}")
            health[key] = health_fail(name, "map", str(exc)[:200], health.get(key))
            continue
        st[layer["id"]] = {**got, "fetched": iso(now), "url": layer["url"]}
        latest = parse_time(got["as_of"]) if got["as_of"] else None
        health[key] = health_ok(name, "map", latest, len(got["polygons"]), health.get(key))
        log(f"[control] {layer['id']}: {len(got['polygons'])} polygons, source edited {(got['as_of'] or '?')[:10]}")


def public(state: dict, layers: list[dict], now) -> list[dict]:
    """The layers to draw: current enough, in config order (later ones paint on top)."""
    st = state.get("control") or {}
    out = []
    for layer in layers:
        got = st.get(layer["id"])
        if not got or not got.get("polygons"):
            continue
        max_age = layer.get("max_age_days")
        as_of = parse_time(got.get("as_of"))
        if max_age is not None and (not as_of or now - as_of > timedelta(days=float(max_age))):
            log(f"[control] {layer['id']}: source last edited {(got.get('as_of') or 'never')[:10]}, "
                f"over {max_age} days ago; left off the map")
            continue
        row = {"id": layer["id"], "label": layer["label"], "style": layer.get("style", "occupied"),
               "country": layer.get("country"), "source": layer.get("source"), "link": layer.get("link"),
               "as_of": got.get("as_of"), "polygons": got["polygons"]}
        if layer.get("kind") == "isw_map":  # traced by this site from the source's picture
            row.update(approx=True, link=got.get("map_link") or layer.get("link"), map_title=got.get("map_title"))
        out.append(row)
    return out


def validate(layers: list[dict]) -> list[dict]:
    """Layers with the fields this module needs (bad entries are logged and skipped)."""
    ok = []
    for l in layers or []:
        if not isinstance(l, dict) or not l.get("id") or not l.get("url") or not l.get("label"):
            log(f"[control] skipping a layer without id, url and label: {l!r}"[:200])
            continue
        if l.get("kind", "arcgis") not in KINDS or (l.get("kind") == "isw_map" and not (l.get("search") and l.get("trace"))):
            log(f"[control] {l['id']}: unknown kind, or a traced layer without search and trace; skipped")
            continue
        if l.get("style", "occupied") not in STYLES:
            log(f"[control] {l['id']}: unknown style {l.get('style')!r}; skipped")
            continue
        ok.append(l)
    return ok

