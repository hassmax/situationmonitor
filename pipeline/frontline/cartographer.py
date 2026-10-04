"""Cartographer: no model. Turns the reviewed settlement assessments into shaded areas of control,
the way the globe already shows territorial control:

- Every published settlement shades the ground around it, out to its conflict's `area_km`, in its
  holder's colour: solid where control is assessed, light where only one side claims it, hatched
  where fighting is reported inside. A town on the conflict's `standing` list (frontlines.yaml)
  whose control is assessed reaches further, to `reach_km`: those are the established towns and
  cities behind the lines, and together they fill the ground a side has held for a long time.
- Shading stays on the land of the country the settlement lies in (the globe's own country
  shapes, land.py): never over the sea or across a border. Ground a side holds in its own country
  (`home`: Russian-held villages in Russia) isn't shaded.
- Where two zones of different holders or status overlap, the line runs halfway between their
  settlements (each point goes to the nearest settlement), which is how the front line is drawn.
- The outline comes from a smooth distance field on a grid (GRID_STEPS cells per area_km), outlined
  with contourpy, so the edges are circles and straight halfway lines, not pixel steps.

The areas are approximate by nature (labelled so on the map) and never reach beyond area_km
(reach_km for a listed town) of a settlement the evidence names. Settlements with no evidence in SHOW_DAYS are left out. The
settlements themselves are published too, for the hover ("nearest assessed place").
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np

from common import iso

from geo import ISO_NUMERIC

from . import land, ledger, standing

SHOW_DAYS = 45
CHANGED_DAYS = 7
MAX_PLACES = 1500
GRID_STEPS = 4          # grid cells per area_km
MAX_CELLS = 600_000     # a bigger grid is made coarser
STYLE = {"assessed": "occupied", "claimed": "claimed", "contested": "infiltration"}
DEFAULT_AREA_KM = 10
BIG = 1e6   # "out of reach" in the distance fields (finite, so differences stay numbers)


def _places(fl: dict, conflicts: list[dict], now) -> list[dict]:
    cutoff = iso(now - timedelta(days=SHOW_DAYS))
    changed = iso(now - timedelta(days=CHANGED_DAYS))
    out = []
    listed = {t["key"]: t["reach_km"] for c in conflicts for t in standing.towns(c) if t["key"]}
    for key, p in fl.get("places", {}).items():
        pub = p.get("published")
        if not pub or p.get("lat") is None or not p["claims"] or p["claims"][-1]["time"] < cutoff:
            continue
        conflict = ledger.conflict_for(conflicts, p["country"], p["conflict"])
        if not conflict:
            continue
        holder = ledger.actor(conflict, pub.get("holder"))
        prev = ledger.actor(conflict, pub.get("previous"))
        live = [c for c in p["claims"] if not c.get("rejected")]
        out.append({
            "name": p["name"], "region": p.get("region"), "country": p["country"], "conflict": conflict["id"],
            "lat": p["lat"], "lon": p["lon"], "status": pub["status"],
            "holder": pub.get("holder"), "holder_name": holder["name"] if holder else None,
            "color": holder["color"] if holder else None, "previous_name": prev["name"] if prev else None,
            "since": pub.get("since"), "basis": pub.get("basis"), "sources": pub.get("sources"),
            "last": live[-1]["time"] if live else p["claims"][-1]["time"], "event": pub.get("event"),
            "changed": bool(pub.get("since") and pub["since"] >= changed),
            "standing": key in listed, **({"reach_km": listed[key]} if listed.get(key) else {}),
        })
    out.sort(key=lambda x: x["last"], reverse=True)
    return out[:MAX_PLACES]


def _klass(p: dict) -> tuple:
    return ("contested", None) if p["status"] == "contested" else (p["status"], p["holder"])


def _reach(p: dict, conflict: dict) -> float:
    area = float(conflict.get("area_km") or DEFAULT_AREA_KM)
    if p.get("standing") and p["status"] == "assessed":
        return max(area, float(p.get("reach_km") or conflict.get("reach_km") or area))
    return area


def _home(p: dict, conflict: dict) -> bool:
    """Held by a side in its own country (nothing to show) and not fought over."""
    a = ledger.actor(conflict, p.get("holder"))
    return bool(a and p["status"] != "contested" and p.get("country") in (a.get("home") or []))


def areas(places: list[dict], conflict: dict) -> list[dict]:
    """Shaded areas for one conflict's published settlements: one layer per (status, holder)."""
    pts = [p for p in places if p["conflict"] == conflict["id"] and not _home(p, conflict)]
    if not pts:
        return []
    import contourpy
    area = float(conflict.get("area_km") or DEFAULT_AREA_KM)
    reach = [_reach(p, conflict) for p in pts]
    lat0 = sum(p["lat"] for p in pts) / len(pts)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57          # km per degree
    pad_lon, pad_lat = (max(reach) * 1.2) / kx, (max(reach) * 1.2) / ky
    w, e = min(p["lon"] for p in pts) - pad_lon, max(p["lon"] for p in pts) + pad_lon
    s, n = min(p["lat"] for p in pts) - pad_lat, max(p["lat"] for p in pts) + pad_lat
    step = area / GRID_STEPS
    nx, ny = int((e - w) * kx / step) + 2, int((n - s) * ky / step) + 2
    if nx * ny > MAX_CELLS:
        f = math.sqrt(nx * ny / MAX_CELLS)
        nx, ny = int(nx / f) + 2, int(ny / f) + 2
    xs, ys = np.linspace(w, e, nx), np.linspace(s, n, ny)
    gx, gy = np.meshgrid(xs, ys)
    # the land of each of the conflict's countries: a settlement shades only the country it lies in
    masks = {c: land.mask(xs, ys, [ISO_NUMERIC[c]]) for c in conflict["countries"] if c in ISO_NUMERIC}
    on_land = np.logical_or.reduce(list(masks.values())) if masks else None
    klass = [_klass(p) for p in pts]
    # per class: distance (km) to its nearest settlement on the same land, and how far inside the
    # nearest settlement's reach a cell is (<= 0 inside)
    near: dict[tuple, np.ndarray] = {}
    inside: dict[tuple, np.ndarray] = {}
    for i, (p, k) in enumerate(zip(pts, klass)):
        d = np.hypot((gx - p["lon"]) * kx, (gy - p["lat"]) * ky)
        if on_land is not None and on_land.any():
            j = np.unravel_index(np.argmin(np.where(on_land, d, np.inf)), d.shape)
            if d[j] > reach[i]:
                continue  # no land of the conflict within reach
            own = next(m for m in masks.values() if m[j])
            d = np.where(own, d, BIG)
        r = d - reach[i]
        near[k] = np.minimum(near[k], d) if k in near else d
        inside[k] = np.minimum(inside[k], r) if k in inside else r
    by_class = near
    layers = []
    for k, d in by_class.items():
        others = [v for kk, v in by_class.items() if kk != k]
        nearest_other = np.minimum.reduce(others) if others else np.full_like(d, np.inf)
        # inside where within reach and nearer to this class than to any other: g <= 0
        g = np.maximum(inside[k], (d - nearest_other) / 2)
        gen = contourpy.contour_generator(xs, ys, g, fill_type=contourpy.FillType.OuterOffset)
        polys = []
        for points, offsets in zip(*gen.filled(-1e9, 0.0)):
            rings = [[[round(float(x), 3), round(float(y), 3)] for x, y in points[offsets[j]:offsets[j + 1]]]
                     for j in range(len(offsets) - 1)]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                polys.append(rings)
        if not polys:
            continue
        status, holder = k
        a = ledger.actor(conflict, holder)
        members = [p for p, kk in zip(pts, klass) if kk == k]
        label = {"assessed": f"Held by {a['name']}" if a else "Held",
                 "claimed": f"Claimed by {a['name']}" if a else "Claimed",
                 "contested": "Contested: fighting reported"}[status]
        layers.append({"id": f"fl-{conflict['id']}-{status}-{holder or 'none'}", "label": label, "style": STYLE[status],
                       "color": a["color"] if a else None, "country": None, "conflict": conflict["id"],
                       "source": "This site's assessment", "assessment": True, "area_km": area,
                       "reach_km": max(_reach(p, conflict) for p in members),
                       "as_of": max(p["last"] for p in members), "settlements": len(members), "polygons": polys})
    # paint order: claims first (lightest), then held ground, then contested hatching on top
    order = {"claimed": 0, "occupied": 1, "infiltration": 2}
    return sorted(layers, key=lambda L: order.get(L["style"], 3))


def public(fl: dict, conflicts: list[dict], now) -> dict:
    places = _places(fl, conflicts, now)
    return {"conflicts": [{"id": c["id"], "name": c["name"], "area_km": c.get("area_km") or DEFAULT_AREA_KM,
                           "reach_km": c.get("reach_km") or c.get("area_km") or DEFAULT_AREA_KM,
                           "actors": [{"id": a["id"], "name": a["name"], "color": a["color"]} for a in c["actors"]]}
                          for c in conflicts],
            "areas": [L for c in conflicts for L in areas(places, c)],
            "places": places}
