"""Cartographer: no model. Turns the reviewed settlement assessments (and, where the agents have
nothing, the one-time Wikipedia snapshot, wikipedia.py) into one broad shape per side in each
conflict (the owner, 2026-10-05: "Just use one broad shape (outlined by the moving dashes) for each
territorial control, and then adjust when agents or news indicates territorial control shift"):

- Only corroborated control counts: a settlement's `controller` is its assessed holder; one side's
  claim, or fighting inside, leaves it with that holder until a capture is corroborated.
- Each patch of land goes to the nearest controlled settlement on the same country's land, within
  the conflict's `broad_km` (so the line between two sides runs halfway between their towns);
  whole provinces go to their towns where the conflict fills provinces (`fill: regions`); the `rest`
  side gets what nobody else holds in its countries (Ukraine: everything not Russian-held).
- Specks under `min_km2` go to whoever surrounds them, enclosed gaps are filled, and each side's
  ground is smoothed and outlined (contourpy). Ground the agents themselves published is never
  folded away, and a capture they confirmed always shows.
- Shading stays on land. Ground a side holds in its own country (`home`) isn't shown; `counts_as`
  puts Crimea, which the globe's map draws inside Russia, back in Ukraine.
- Fighting inside settlements is hatched ("Contested") only where it forms a sizeable zone.

The settlements themselves are published too (`places`), with the Wikipedia credits.
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
MAX_CELLS = 250_000     # a bigger grid is made coarser
STYLE = {"assessed": "occupied", "claimed": "claimed", "contested": "infiltration"}
DEFAULT_AREA_KM = 10
DEFAULT_FILL_KM = 120
DEFAULT_BROAD_KM = 60   # how far a settlement's side holds the ground around it, where nobody nearer does
SMOOTH_PER_AREA = 1.5   # outlines are smoothed over this many area_km
DEFAULT_MIN_KM2 = 1500  # a side's parts smaller than this go to whoever surrounds them
KEEP_SHARE = 0.03       # ... as do parts under this share of the side's largest part
GAP_SHARE = 0.25        # gaps one side encloses are filled up to this share of its ground
CONTESTED_PER_AREA = 1.5   # fighting inside a settlement marks the ground this many area_km around it
CONTESTED_MIN = 3          # ... hatched only where it forms a zone of this many min_km2 or more
MIN_STEP_KM, MAX_STEP_KM = 3, 8
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


def _country(p: dict, conflict: dict) -> str | None:
    """The country a settlement lies in under international law: the globe's map draws Crimea
    inside Russia, so Wikipedia's Crimean towns come out as Russia's; `counts_as` boxes put them
    back in Ukraine."""
    for c, boxes in (conflict.get("counts_as") or {}).items():
        if any(w <= p["lon"] <= e and s <= p["lat"] <= n for w, s, e, n in boxes):
            return c
    return p.get("country")


def _home(p: dict, conflict: dict) -> bool:
    """Held by a side in its own country (nothing to show) and not fought over."""
    a = ledger.actor(conflict, p.get("holder"))
    return bool(a and p["status"] != "contested" and _country(p, conflict) in (a.get("home") or []))


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


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Mean over 2r+1 cells along one axis (zeros beyond the edge)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad), axis=axis)
    n = a.shape[axis]
    return (np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis) - np.take(c, np.arange(n), axis=axis)) / (2 * r + 1)


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three box passes each way: close to a Gaussian of about r cells."""
    a = a.astype(float)
    for axis in (0, 1):
        for _ in range(3):
            a = _box(a, r, axis) if r >= 1 else a
    return a


def _components(m: np.ndarray, piece: np.ndarray | None = None) -> list[np.ndarray]:
    """Connected parts (4-neighbour) of a boolean grid, as arrays of flat cell indices; with
    `piece`, cells join only cells of the same land piece (Crimea doesn't join Russia's mainland
    across the Kerch Strait)."""
    ny, nx = m.shape
    flat = m.ravel()
    pc = piece.ravel() if piece is not None else np.zeros(flat.shape, dtype=int)
    seen = np.zeros(flat.shape, dtype=bool)
    out = []
    for start in np.flatnonzero(flat):
        if seen[start]:
            continue
        seen[start] = True
        stack, cells = [int(start)], []
        while stack:
            i = stack.pop()
            cells.append(i)
            y, x = divmod(i, nx)
            for j, ok in ((i - nx, y > 0), (i + nx, y < ny - 1), (i - 1, x > 0), (i + 1, x < nx - 1)):
                if ok and flat[j] and not seen[j] and pc[j] == pc[i]:
                    seen[j] = True
                    stack.append(j)
        out.append(np.array(cells))
    return out


def _around(cells: np.ndarray, shape: tuple) -> tuple[np.ndarray, bool]:
    """The cells bordering a part (flat indices), and whether it touches the grid's edge."""
    ny, nx = shape
    y, x = np.divmod(cells, nx)
    edge = bool((y == 0).any() or (y == ny - 1).any() or (x == 0).any() or (x == nx - 1).any())
    ring = np.concatenate([cells[y > 0] - nx, cells[y < ny - 1] + nx, cells[x > 0] - 1, cells[x < nx - 1] + 1])
    return np.setdiff1d(ring, cells), edge


def _pieces(xs: np.ndarray, ys: np.ndarray, conflict: dict) -> np.ndarray:
    """Which separate piece of land (a ring of the globe's country shapes) each cell lies on."""
    out = np.zeros((len(ys), len(xs)), dtype=int)
    n = 0
    for c in conflict["countries"]:
        for r in land._rings().get(ISO_NUMERIC.get(c, ""), []):
            if r[:, 0].max() < xs[0] or r[:, 0].min() > xs[-1] or r[:, 1].max() < ys[0] or r[:, 1].min() > ys[-1]:
                continue
            n += 1
            out[land.rings_mask(xs, ys, [r]) & (out == 0)] = n
    return out


def _ring_km2(ring: list, kx: float, ky: float) -> float:
    a = np.asarray(ring, dtype=float)
    x, y = a[:, 0] * kx, a[:, 1] * ky
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2


NONE, HIDDEN = -1, -2    # grid labels: nobody's ground; ground a side holds at home (not shown)


def areas(places: list[dict], conflict: dict) -> list[dict]:
    """Shaded areas for one conflict: one broad shape per side (the owner, 2026-10-05: "Just use
    one broad shape ... for each territorial control, and then adjust when agents or news indicates
    territorial control shift"), and hatching where fighting inside settlements forms a zone.

    1. Every patch of land goes to its nearest controlled settlement on the same country's land,
       within the conflict's `broad_km` (or the settlement's own longer reach); a whole province
       goes to its towns where the conflict fills provinces; the `rest` side gets what is left of
       its country (Ukraine: everything not Russian-held).
    2. Parts of one side smaller than `min_km2` (or KEEP_SHARE of its largest part) go to whoever
       surrounds them, and gaps enclosed by one side are filled: no specks or lone circles.
    3. Each side's ground is smoothed over `smooth_km` and outlined; where two sides meet the line
       is shared (each point goes to the side most of its neighbourhood belongs to)."""
    ctl = [{**p, "status": "assessed", "holder": controller(p)} for p in places
           if p["conflict"] == conflict["id"] and controller(p)]
    fought = [p for p in places if p["conflict"] == conflict["id"] and p["status"] == "contested"]
    rest = conflict.get("rest") or {}
    if not [p for p in ctl if not _home(p, conflict)] and not fought and not rest:
        return []
    area = float(conflict.get("area_km") or DEFAULT_AREA_KM)
    broad = float(conflict.get("broad_km") or DEFAULT_BROAD_KM)
    smooth = float(conflict.get("smooth_km") or area * SMOOTH_PER_AREA)
    min_km2 = float(conflict.get("min_km2") or DEFAULT_MIN_KM2)
    reach = [max(broad, _reach(p, conflict)) for p in ctl]
    provinces = _provinces(ctl, conflict)
    fill_km = float(conflict.get("fill_km") or DEFAULT_FILL_KM)
    rest_rings = [r for c in rest.get("countries") or [] for r in land._rings().get(ISO_NUMERIC.get(c, ""), [])]
    pts = ctl + fought
    lons = [p["lon"] for p in pts] + [x for r, _ in provinces for ring in r["rings"] for x, _ in ring] \
        + [float(x) for r in rest_rings for x in r[:, 0]]
    lats = [p["lat"] for p in pts] + [y for r, _ in provinces for ring in r["rings"] for _, y in ring] \
        + [float(y) for r in rest_rings for y in r[:, 1]]
    lat0 = (min(lats) + max(lats)) / 2
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57          # km per degree
    pad = max(reach + [area]) + 3 * smooth
    w, e = min(lons) - pad / kx, max(lons) + pad / kx
    s, n = min(lats) - pad / ky, max(lats) + pad / ky
    step = min(max(area / 2, MIN_STEP_KM), MAX_STEP_KM)
    nx, ny = int((e - w) * kx / step) + 2, int((n - s) * ky / step) + 2
    if nx * ny > MAX_CELLS:
        f = math.sqrt(nx * ny / MAX_CELLS)
        nx, ny = int(nx / f) + 2, int(ny / f) + 2
    xs, ys = np.linspace(w, e, nx), np.linspace(s, n, ny)
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    cell_km2 = dx * kx * dy * ky
    tol = min(dx, dy) / 2           # outlines are simplified within half a grid cell
    gx, gy = np.meshgrid(xs, ys)
    # the land of each of the conflict's countries: a settlement holds ground only in its own country
    masks = {c: land.mask(xs, ys, [ISO_NUMERIC[c]]) for c in conflict["countries"] if c in ISO_NUMERIC}
    on_land = np.logical_or.reduce(list(masks.values())) if masks else np.ones(gx.shape, dtype=bool)
    span = max(reach + [area])

    def window(lon, lat, km):
        """The part of the grid within km of a point (each settlement works only on its own patch)."""
        x0, x1 = int((lon - km / kx - w) / dx), int((lon + km / kx - w) / dx) + 2
        y0, y1 = int((lat - km / ky - s) / dy), int((lat + km / ky - s) / dy) + 2
        return slice(max(0, y0), min(ny, y1)), slice(max(0, x0), min(nx, x1))

    def dist(p, limit):
        """(window, km from every cell of it to p, BIG off the land of the country p lies in), or
        None when no land of the conflict is within limit. The window is as wide as the longest
        reach: a rival nearer than a cell's own settlement is always within it."""
        sl = window(p["lon"], p["lat"], span)
        d = np.hypot((gx[sl] - p["lon"]) * kx, (gy[sl] - p["lat"]) * ky)
        if d.size == 0 or not on_land[sl].any():
            return None
        here = on_land[sl]
        j = np.unravel_index(np.argmin(np.where(here, d, np.inf)), d.shape)
        if d[j] > limit:
            return None
        own = next(m[sl] for m in masks.values() if m[sl][j]) if masks else here
        return sl, np.where(own, d, BIG)

    # a side's settlements in its own country (Russian-held villages in Russia) hold ground like any
    # other, so the line against the other side comes out right, but that ground isn't shown
    side_of = [(p["holder"], _home(p, conflict)) for p in ctl]
    sides = sorted(set(side_of) | ({(rest["actor"], False)} if rest.get("actor") else set()))
    near = {h: np.full(gx.shape, BIG) for h in sides}      # km to the side's nearest settlement
    inside = {h: np.full(gx.shape, BIG) for h in sides}    # <= 0 within a settlement's reach
    for i, p in enumerate(ctl):
        got = dist(p, reach[i])
        if got is None:
            continue
        sl, d = got
        h = side_of[i]
        near[h][sl] = np.minimum(near[h][sl], d)
        inside[h][sl] = np.minimum(inside[h][sl], d - reach[i])
    for prov, members in provinces:   # whole provinces: each cell to its nearest controlled town in it
        plon = [x for ring in prov["rings"] for x, _ in ring]
        plat = [y for ring in prov["rings"] for _, y in ring]
        sy = slice(max(0, int((min(plat) - s) / dy)), min(ny, int((max(plat) - s) / dy) + 2))
        sx = slice(max(0, int((min(plon) - w) / dx)), min(nx, int((max(plon) - w) / dx) + 2))
        if sy.start >= sy.stop or sx.start >= sx.stop:
            continue
        pm = land.rings_mask(xs[sx], ys[sy], prov["rings"])
        if not pm.any():
            continue
        best, side = np.full(pm.shape, np.inf), np.full(pm.shape, -1)
        for i in members:
            d = np.hypot((gx[sy, sx] - ctl[i]["lon"]) * kx, (gy[sy, sx] - ctl[i]["lat"]) * ky)
            closer = d < best
            best[closer], side[closer] = d[closer], i
        fill = pm & (best <= fill_km)
        for i in members:
            h = side_of[i]
            inside[h][sy, sx] = np.where(fill & (side == i), np.minimum(inside[h][sy, sx], -1.0), inside[h][sy, sx])
    # 1. each cell to the nearest side whose reach covers it
    labels = np.full(gx.shape, NONE)
    best = np.full(gx.shape, np.inf)
    for k, h in enumerate(sides):
        win = (inside[h] <= 0) & (near[h] < best) & on_land
        labels[win], best[win] = k, near[h][win]
    for k, h in enumerate(sides):
        if h == (rest.get("actor"), False):
            labels[(labels == NONE) & land.rings_mask(xs, ys, rest_rings)] = k
        elif h[1]:
            labels[labels == k] = HIDDEN
    # 2. specks go to whoever surrounds them, except ground around a settlement the agents
    # themselves published (a capture they confirmed must show); gaps one side encloses are filled
    flat = labels.ravel()
    own = {k: np.zeros(gx.shape, dtype=bool) for k in range(len(sides))}   # land near the agents' settlements
    taken = np.zeros(gx.shape, dtype=bool)    # ... that the agents confirmed changed hands
    for i, p in enumerate(ctl):
        got = None if p.get("baseline") else dist(p, area)
        if got is not None:
            sl, d = got
            k = sides.index(side_of[i])
            own[k][sl] |= d <= area
            if p.get("previous") and p["previous"] != p["holder"]:
                taken[sl] |= (d <= area) & (labels[sl] == k)
    kept = taken.copy()     # kept for the agents' evidence: not smoothed away
    for k in range(len(sides)):
        parts = _components(labels == k)
        if not parts:
            continue
        floor = max(min_km2, KEEP_SHARE * max(len(c) for c in parts) * cell_km2)
        for cells in parts:
            if len(cells) * cell_km2 >= floor:
                continue
            if own[k].ravel()[cells].any():
                kept.ravel()[cells] = True
                continue
            ring, _ = _around(cells, labels.shape)
            ring = ring[on_land.ravel()[ring]]
            got = flat[ring][flat[ring] >= 0]
            flat[cells] = np.bincount(got).argmax() if got.size else NONE
    piece = _pieces(xs, ys, conflict)
    pflat = piece.ravel()
    for cells in _components((labels == NONE) & on_land, piece):
        ring, edge = _around(cells, labels.shape)
        ring = ring[on_land.ravel()[ring] & (pflat[ring] == pflat[cells[0]])]
        got = set(flat[ring].tolist()) - {NONE}
        if edge or len(got) != 1 or HIDDEN in got:
            continue
        k = got.pop()
        if len(cells) <= max(min_km2, GAP_SHARE * int((labels == k).sum())) / cell_km2:
            flat[cells] = k
    # 3. smooth each side's ground and outline it
    r = max(1, round(smooth / step - 0.5))   # three box passes of r cells spread about r + 0.5 cells
    valid = on_land & (labels != HIDDEN)
    norm = _blur(valid, r)
    norm[norm < 1e-6] = 1.0
    share = {k: np.where(valid, _blur(labels == k, r) / norm, 0.0) for k in range(len(sides)) if (labels == k).any()}
    layers = []
    for k, b in share.items():
        others = [v for kk, v in share.items() if kk != k]
        rival = np.maximum.reduce(others) if others else np.zeros_like(b)
        g = np.where(valid, np.maximum(0.5 - b, rival - b), 1.0)
        g = np.where(kept, np.where(labels == k, -0.5, 0.5), g)
        mine = kept & (labels == k)
        polys = _keep(_polygons(xs, ys, g, tol), min_km2 / 2, kx, ky, list(zip(gx[mine], gy[mine])))
        if not polys:
            continue
        h = sides[k][0]
        a = ledger.actor(conflict, h)
        members = [p for p, sd in zip(ctl, side_of) if sd == sides[k]]
        label = (a.get("controlled") or f"Held by {a['name']}") if a else "Held"
        layers.append({"id": f"fl-{conflict['id']}-assessed-{h}", "label": label, "style": STYLE["assessed"],
                       "color": a["color"] if a else None, "country": None, "conflict": conflict["id"],
                       "source": _source(members) if members else "This site's assessment",
                       "assessment": True, "area_km": area, "reach_km": broad,
                       "as_of": max((p["last"] for p in members), default=None), "settlements": len(members),
                       "polygons": polys})
    # fighting inside settlements: hatched where it forms a zone of min_km2 or more
    if fought:
        zone = np.zeros(gx.shape, dtype=bool)
        for p in fought:
            got = dist(p, area)
            if got is not None:
                sl, d = got
                zone[sl] |= d <= area * CONTESTED_PER_AREA
        zone &= valid
        for cells in _components(zone):
            if len(cells) * cell_km2 < CONTESTED_MIN * min_km2:
                zone.ravel()[cells] = False
        if zone.any():
            g = np.where(valid, 0.5 - _blur(zone, r) / norm, 1.0)
            polys = _keep(_polygons(xs, ys, g, tol), min_km2, kx, ky)
            if polys:
                layers.append({"id": f"fl-{conflict['id']}-contested", "label": "Contested", "style": STYLE["contested"],
                               "color": None, "country": None, "conflict": conflict["id"], "source": _source(fought),
                               "assessment": True, "area_km": area, "reach_km": area,
                               "as_of": max(p["last"] for p in fought), "settlements": len(fought), "polygons": polys})
    return layers


def _keep(polys: list, floor: float, kx: float, ky: float, keep: list = ()) -> list:
    """Polygons of floor km² or more (or holding one of the `keep` points), without holes smaller
    than floor: smoothing leftovers."""
    out = []
    for rings in polys:
        if _ring_km2(rings[0], kx, ky) < floor and not any(land.inside([rings[0]], x, y) for x, y in keep):
            continue
        out.append([rings[0]] + [h for h in rings[1:] if _ring_km2(h, kx, ky) >= floor])
    return out


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
