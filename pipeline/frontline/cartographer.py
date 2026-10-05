"""Cartographer: no model. Turns the reviewed settlement assessments into shaded areas of control,
the way the globe already shows territorial control:

- Every settlement whose control is assessed (corroborated) shades the ground around it, out to
  its conflict's `area_km`, in its holder's colour, labelled simply "Russian-controlled",
  "Houthi-controlled" (the actor's `controlled`). One side's claim, or fighting inside, doesn't
  change it: the shading moves only when a capture is corroborated (the owner's choice, 2026-10-04).
  Fighting inside a settlement is hatched over it ("Contested").
- Where a conflict fills whole provinces (`fill: regions`: Yemen, Ethiopia, Sudan, eastern DRC), a
  province holding an established town whose control is assessed is shaded whole, each part going
  to the nearest controlled settlement in it (so a province held by both sides is split halfway
  between their towns), no further than the conflict's `fill_km` from it: the Houthi-held north,
  Tigray, Darfur (regions.json, Natural Earth). A town on the conflict's `standing` list (frontlines.yaml)
  whose control is assessed reaches further, to `reach_km`: those are the established towns and
  cities behind the lines, and together they fill the ground a side has held for a long time.
- Shading stays on the land of the country the settlement lies in (the globe's own country
  shapes, land.py): never over the sea or across a border. Ground a side holds in its own country
  (`home`: Russian-held villages in Russia) isn't shaded.
- Where two zones of different holders or status overlap, the line runs halfway between their
  settlements (each point goes to the nearest settlement), which is how the front line is drawn.
- The outline comes from a smooth distance field on a grid (GRID_STEPS cells per area_km), outlined
  with contourpy, so the edges are circles and straight halfway lines, not pixel steps.

Where the agents have nothing, a one-time snapshot of Wikipedia's conflict maps fills the gaps
(wikipedia.py): its towns shade ground the same way, credited in the Sources list.

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

from . import land, ledger, standing, wikipedia

SHOW_DAYS = 45
CHANGED_DAYS = 7
MAX_PLACES = 1500
GRID_STEPS = 4          # grid cells per area_km
MAX_CELLS = 600_000     # a bigger grid is made coarser
STYLE = {"assessed": "occupied", "claimed": "claimed", "contested": "infiltration"}
DEFAULT_AREA_KM = 10
DEFAULT_FILL_KM = 120
BIG = 1e6   # "out of reach" in the distance fields (finite, so differences stay numbers)


def _places(fl: dict, conflicts: list[dict], now) -> list[dict]:
    cutoff = iso(now - timedelta(days=SHOW_DAYS))
    changed = iso(now - timedelta(days=CHANGED_DAYS))
    out = []
    listed = {t["key"]: t for c in conflicts for t in standing.towns(c) if t["key"]}
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
            "holder": pub.get("holder"), "holder_name": holder["name"] if holder else None, "previous": pub.get("previous"),
            "color": holder["color"] if holder else None, "previous_name": prev["name"] if prev else None,
            "since": pub.get("since"), "basis": pub.get("basis"), "sources": pub.get("sources"),
            "last": live[-1]["time"] if live else p["claims"][-1]["time"], "event": pub.get("event"),
            "changed": bool(pub.get("since") and pub["since"] >= changed),
            "standing": key in listed, **({"reach_km": listed[key]["reach_km"]} if key in listed and listed[key]["reach_km"] else {}),
            "fills": key in listed and not listed[key]["front"], "provinces": listed[key]["provinces"] if key in listed else [],
        })
    out.sort(key=lambda x: x["last"], reverse=True)
    return out[:MAX_PLACES]


def _klass(p: dict) -> tuple:
    return ("contested", None) if p["status"] == "contested" else (p["status"], p["holder"])


def _reach(p: dict, conflict: dict) -> float:
    area = float(conflict.get("area_km") or DEFAULT_AREA_KM)
    if p.get("baseline"):   # Wikipedia's towns: their map's own reach, where set (Ukraine's villages)
        reach = (conflict.get("wikipedia") or {}).get("reach_km") or area
        if isinstance(reach, dict):   # per side
            reach = reach.get(p.get("holder")) or area
        return max(area, float(reach))
    if p.get("standing") and p["status"] == "assessed":
        return max(area, float(p.get("reach_km") or conflict.get("reach_km") or area))
    return area


def _home(p: dict, conflict: dict) -> bool:
    """Held by a side in its own country (nothing to show) and not fought over."""
    a = ledger.actor(conflict, p.get("holder"))
    return bool(a and p["status"] != "contested" and p.get("country") in (a.get("home") or []))


def controller(p: dict) -> str | None:
    """Who the map shades a settlement for: the side whose control is assessed (corroborated). A
    newer claim by the other side, or fighting inside, leaves it with that side until the capture
    is corroborated too; a place no side's control was ever assessed for isn't shaded."""
    if p["status"] == "claimed":
        return p.get("previous")
    return p.get("holder")


def _simplify(ring: list, tol: float) -> list:
    """Douglas-Peucker on a closed ring: drops points within tol (degrees) of the line through their
    neighbours (contour points sit one per grid cell, mostly along straight halfway lines)."""
    if len(ring) <= 8 or tol <= 0:
        return ring
    pts = np.asarray(ring, dtype=float)
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    split = int(np.argmax(np.hypot(*(pts - pts[0]).T)))   # far point: two open halves
    keep[split] = True
    stack = [(0, split), (split, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = b - a
        norm = math.hypot(*seg)
        rel = pts[i + 1:j] - a
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm if norm else np.hypot(*rel.T)
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    out = [ring[i] for i in np.flatnonzero(keep)]
    return out if len(out) >= 4 else ring


def _polygons(xs, ys, g, tol: float = 0.0) -> list:
    """Outlines of g <= 0, simplified within tol degrees."""
    import contourpy
    gen = contourpy.contour_generator(xs, ys, g, fill_type=contourpy.FillType.OuterOffset)
    polys = []
    for points, offsets in zip(*gen.filled(-1e9, 0.0)):
        rings = [_simplify([[round(float(x), 3), round(float(y), 3)] for x, y in points[offsets[j]:offsets[j + 1]]], tol)
                 for j in range(len(offsets) - 1)]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            polys.append(rings)
    return polys


def _provinces(ctl: list[dict], conflict: dict) -> list[tuple[dict, list[int]]]:
    """For a conflict that fills whole provinces (`fill: regions`): each province holding an
    established (listed, not front-line) town whose control is assessed, or named in such a
    town's `provinces`, with the controlled settlements in it (and that town)."""
    if conflict.get("fill") != "regions":
        return []
    out = []
    for c in conflict["countries"]:
        for r in land.regions(c):
            members = [i for i, p in enumerate(ctl) if land.inside(r["rings"], p["lon"], p["lat"])]
            covers = [i for i, p in enumerate(ctl) if r["name"] in (p.get("provinces") or []) and i not in members]
            if any(ctl[i].get("fills") for i in members) or covers:
                out.append((r, members + covers))
    return out


def areas(places: list[dict], conflict: dict) -> list[dict]:
    """Shaded areas for one conflict: one layer per side, around the settlements it controls (the
    whole province, where the conflict fills provinces), and hatching where fighting is reported
    inside a settlement."""
    ctl = [{**p, "status": "assessed", "holder": controller(p)} for p in places
           if p["conflict"] == conflict["id"] and controller(p)]
    ctl = [p for p in ctl if not _home(p, conflict)]
    fought = [p for p in places if p["conflict"] == conflict["id"] and p["status"] == "contested"]
    if not ctl and not fought:
        return []
    area = float(conflict.get("area_km") or DEFAULT_AREA_KM)
    reach = [_reach(p, conflict) for p in ctl]
    provinces = _provinces(ctl, conflict)
    fill_km = float(conflict.get("fill_km") or DEFAULT_FILL_KM)
    pts = ctl + fought
    lat0 = sum(p["lat"] for p in pts) / len(pts)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57          # km per degree
    pad = max(reach + [area]) * 1.2
    lons = [p["lon"] for p in pts] + [x for r, _ in provinces for ring in r["rings"] for x, _ in ring]
    lats = [p["lat"] for p in pts] + [y for r, _ in provinces for ring in r["rings"] for _, y in ring]
    w, e = min(lons) - pad / kx, max(lons) + pad / kx
    s, n = min(lats) - pad / ky, max(lats) + pad / ky
    step = area / GRID_STEPS
    nx, ny = int((e - w) * kx / step) + 2, int((n - s) * ky / step) + 2
    if nx * ny > MAX_CELLS:
        f = math.sqrt(nx * ny / MAX_CELLS)
        nx, ny = int(nx / f) + 2, int(ny / f) + 2
    xs, ys = np.linspace(w, e, nx), np.linspace(s, n, ny)
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    tol = min(dx, dy) / 2           # outlines are simplified within half a grid cell
    gx, gy = np.meshgrid(xs, ys)
    # the land of each of the conflict's countries: a settlement shades only the country it lies in
    masks = {c: land.mask(xs, ys, [ISO_NUMERIC[c]]) for c in conflict["countries"] if c in ISO_NUMERIC}
    on_land = np.logical_or.reduce(list(masks.values())) if masks else None

    def window(lon, lat, km):
        """The part of the grid within km of a point (each settlement works only on its own patch:
        hundreds of settlements over a whole-country grid took seconds)."""
        x0, x1 = int((lon - km / kx - w) / dx), int((lon + km / kx - w) / dx) + 2
        y0, y1 = int((lat - km / ky - s) / dy), int((lat + km / ky - s) / dy) + 2
        return slice(max(0, y0), min(ny, y1)), slice(max(0, x0), min(nx, x1))

    span = max(reach + [area] + ([fill_km] if provinces else [])) + area

    def dist(p, limit):
        """(window, distance in km from every cell of it to p, only on the land of the country p
        lies in), or None when no land of the conflict is within limit."""
        # as wide as the longest reach (or province fill): a rival settlement nearer than a cell's
        # own settlement is always within it, so the halfway line comes out as on the whole grid
        sl = window(p["lon"], p["lat"], span)
        d = np.hypot((gx[sl] - p["lon"]) * kx, (gy[sl] - p["lat"]) * ky)
        if d.size == 0:
            return None
        if on_land is not None and on_land.any():
            here = on_land[sl]
            if not here.any():
                return None
            j = np.unravel_index(np.argmin(np.where(here, d, np.inf)), d.shape)
            if d[j] > limit:
                return None
            own = next(m[sl] for m in masks.values() if m[sl][j])
            d = np.where(own, d, BIG)
        return sl, d

    klass = [_klass(p) for p in ctl]
    # per side: distance (km) to its nearest settlement on the same land, and how far inside the
    # nearest settlement's reach a cell is (<= 0 inside); BIG beyond a settlement's own patch
    near: dict[tuple, np.ndarray] = {}
    inside: dict[tuple, np.ndarray] = {}
    for i, (p, k) in enumerate(zip(ctl, klass)):
        got = dist(p, reach[i])
        if got is None:
            continue  # no land of the conflict within reach
        sl, d = got
        if k not in near:
            near[k], inside[k] = np.full(gx.shape, BIG), np.full(gx.shape, BIG)
        near[k][sl] = np.minimum(near[k][sl], d)
        inside[k][sl] = np.minimum(inside[k][sl], d - reach[i])
    # whole provinces: every cell of the province goes to its nearest controlled settlement in it
    for prov, members in provinces:
        plon = [x for ring in prov["rings"] for x, _ in ring]
        plat = [y for ring in prov["rings"] for _, y in ring]
        sy = slice(max(0, int((min(plat) - s) / dy)), min(ny, int((max(plat) - s) / dy) + 2))
        sx = slice(max(0, int((min(plon) - w) / dx)), min(nx, int((max(plon) - w) / dx) + 2))
        if sy.start >= sy.stop or sx.start >= sx.stop:
            continue
        pm = land.rings_mask(xs[sx], ys[sy], prov["rings"])
        if not pm.any():
            continue
        best = np.full(pm.shape, np.inf)
        side = np.full(pm.shape, -1)
        for i in members:
            d = np.hypot((gx[sy, sx] - ctl[i]["lon"]) * kx, (gy[sy, sx] - ctl[i]["lat"]) * ky)
            closer = d < best
            best[closer], side[closer] = d[closer], i
        fill = pm & (best <= fill_km)       # a province never hangs on one far-off town
        for i in members:
            k = klass[i]
            if k in inside:
                inside[k][sy, sx] = np.where(fill & (side == i), np.minimum(inside[k][sy, sx], -1.0), inside[k][sy, sx])
    layers = []
    for k, d in near.items():
        others = [v for kk, v in near.items() if kk != k]
        nearest_other = np.minimum.reduce(others) if others else np.full_like(d, np.inf)
        # inside where within reach (or its province) and nearer to this side than to any other
        polys = _polygons(xs, ys, np.maximum(inside[k], (d - nearest_other) / 2), tol)
        if not polys:
            continue
        status, holder = k
        a = ledger.actor(conflict, holder)
        members = [p for p, kk in zip(ctl, klass) if kk == k]
        label = (a.get("controlled") or f"Held by {a['name']}") if a else "Held"
        layers.append({"id": f"fl-{conflict['id']}-{status}-{holder or 'none'}", "label": label, "style": STYLE[status],
                       "color": a["color"] if a else None, "country": None, "conflict": conflict["id"],
                       "source": _source(members), "assessment": True, "area_km": area,
                       "reach_km": max(_reach(p, conflict) for p in members),
                       "as_of": max(p["last"] for p in members), "settlements": len(members), "polygons": polys})
    # fighting inside a settlement: hatched over whoever holds it, within area_km
    if fought:
        g = np.full(gx.shape, BIG)
        for p in fought:
            got = dist(p, area)
            if got is not None:
                sl, d = got
                g[sl] = np.minimum(g[sl], d - area)
        polys = _polygons(xs, ys, g, tol)
        if polys:
            layers.append({"id": f"fl-{conflict['id']}-contested", "label": "Contested", "style": STYLE["contested"],
                           "color": None, "country": None, "conflict": conflict["id"], "source": _source(fought),
                           "assessment": True, "area_km": area, "reach_km": area, "as_of": max(p["last"] for p in fought),
                           "settlements": len(fought), "polygons": polys})
    return layers


def _source(members: list[dict]) -> str:
    if all(p.get("baseline") for p in members):
        return "Wikipedia's conflict map"
    return "This site's assessment" + (", with Wikipedia's conflict map" if any(p.get("baseline") for p in members) else "")


def public(fl: dict, conflicts: list[dict], now) -> dict:
    places = _places(fl, conflicts, now)
    shaded = places + wikipedia.points(fl, conflicts, places)
    return {"conflicts": [{"id": c["id"], "name": c["name"], "area_km": c.get("area_km") or DEFAULT_AREA_KM,
                           "reach_km": c.get("reach_km") or c.get("area_km") or DEFAULT_AREA_KM,
                           "actors": [{"id": a["id"], "name": a["name"], "color": a["color"]} for a in c["actors"]]}
                          for c in conflicts],
            "areas": [L for c in conflicts for L in areas(shaded, c)],
            "places": places, "credits": wikipedia.credits(fl, conflicts)}
