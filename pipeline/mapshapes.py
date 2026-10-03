"""Shapes traced from a map picture (the owner asked for Yemen, 2026-10-03, where ISW publishes its
control map only as an image). Approximate by nature, and labeled so on the map.

How a map is turned into shapes, with no model involved:
1. Georeference: the pixel <-> lon/lat transform is fitted once to stored control points (town
   dots: pixel x, y in the 1638-wide rendition, plus lon, lat; affine on longitude and Mercator
   latitude, since ISW's maps are Web Mercator: under 1 px of error on 5 towns). Each new map is
   checked against its own dots (`find_dot`) and moved by the small shift 3+ of them agree on;
   a different layout fails the checks in step 5.
2. Classify: the source's fill colour for the class traced (`CLASSES`), plus its outline.
3. Clean (on the country's part of the picture): small gaps closed, and what strike circles drawn
   over the map hide filled from the nearest visible colours (the edge runs on straight through a
   circle). Holes in the traced area are kept only where the other side's colour fills most of
   them (`OTHER`: an enclave); holes left by labels, town dots and lines are filled.
4. Outline: filled contours (contourpy), pixel -> lon/lat, simplified; pieces outside the country's
   box or smaller than MIN_AREA_PX are dropped (logos, legend swatches, the inset map).
5. Check: places the source certainly shows inside (Sanaa) and outside (Aden) the traced class
   must come out that way, else nothing is used (the map's layout changed) and the previous
   copy stays.
"""
from __future__ import annotations

import io
import math

import numpy as np

AGREE = 3              # px; dots whose shift differs more than this from the others' are ignored
DOT_SEARCH = 18        # px around the predicted position
MIN_AREA_PX = 40
SIMPLIFY_PX = 0.7

# Colour rules per traced class, on RGB arrays (ints).
CLASSES = {
    # ISW and Critical Threats' "Houthi-Controlled Terrain": pale pink fill (249, 226, 225) with a
    # red outline; Saudi sand (248, 235, 192), sea and white boxes fall outside it.
    "isw_houthi": lambda R, G, B: ((R - G > 12) & (R - B > 12) & (R > 120) & (np.abs(G - B) < 25)),
}
# What the other side holds: a hole in the traced area is kept only if this fills most of it (a real
# enclave); holes left by labels, town dots and lines are filled.
OTHER = {
    "isw_houthi": lambda R, G, B: (G - R > 6) & (G >= B - 5),   # government-aligned light green
}
# Teal "Saudi and Yemeni government-aligned forces airstrike" circles drawn over the fill.
CIRCLE = lambda R, G, B: (G - R > 30) & (G > B)


def _merc(lat: float) -> float:
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _unmerc(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def fit(points: list) -> tuple[np.ndarray, float]:
    """Affine (lon, mercator y) -> (px, py) from [[px, py, lon, lat], ...]; and its rms error in px."""
    A = np.array([[lon, _merc(lat), 1.0] for _, _, lon, lat in points])
    P = np.array([[x, y] for x, y, _, _ in points], float)
    sol, *_ = np.linalg.lstsq(A, P, rcond=None)
    res = A @ sol - P
    return sol, float(np.sqrt((res ** 2).sum(axis=1).mean()))


def to_px(sol: np.ndarray, lon: float, lat: float) -> tuple[float, float]:
    x, y = np.array([lon, _merc(lat), 1.0]) @ sol
    return float(x), float(y)


def to_lonlat(sol: np.ndarray, x: float, y: float) -> tuple[float, float]:
    M = sol[:2].T  # [[a, b], [c, d]] with px = M @ [lon, my] + t
    lon, my = np.linalg.solve(M, np.array([x, y]) - sol[2])
    return float(lon), _unmerc(float(my))


def find_dot(lum: np.ndarray, x: float, y: float, search: int = DOT_SEARCH):
    """The centre of a town dot (white disc, dark ring) near (x, y), or None."""
    h, w = lum.shape
    best = None
    ang = np.linspace(0, 2 * math.pi, 24, endpoint=False)
    for r in (5, 6, 7):
        dx, dy = np.round(r * np.cos(ang)).astype(int), np.round(r * np.sin(ang)).astype(int)
        for cy in range(int(y) - search, int(y) + search + 1):
            if cy - r - 1 < 0 or cy + r + 1 >= h:
                continue
            for cx in range(int(x) - search, int(x) + search + 1):
                if cx - r - 1 < 0 or cx + r + 1 >= w:
                    continue
                score = lum[cy - 1:cy + 2, cx - 1:cx + 2].mean() - lum[cy + dy, cx + dx].mean()
                if best is None or score > best[0]:
                    best = (score, cx, cy)
    return (best[1], best[2]) if best and best[0] > 120 else None


def georeference(lum: np.ndarray, points: list) -> tuple[np.ndarray, float, int]:
    """The stored fit, moved by the shift the map's own dots agree on. A dot is looked for near
    each stored point; the median offset of those found is used only if 3 or more agree with it
    (within AGREE px), so one mistaken match (a letter of a label taken for a dot) can't pull
    the map askew. Fewer than 3 agreeing: the stored fit, unshifted."""
    sol, rms = fit(points)
    found = [(at[0] - x, at[1] - y) for x, y, _, _ in points if (at := find_dot(lum, x, y))]
    if len(found) >= 3:
        mx = float(np.median([d[0] for d in found]))
        my = float(np.median([d[1] for d in found]))
        agree = [d for d in found if abs(d[0] - mx) <= AGREE and abs(d[1] - my) <= AGREE]
        if len(agree) >= 3:
            dx = float(np.mean([d[0] for d in agree]))
            dy = float(np.mean([d[1] for d in agree]))
            sol = sol.copy()
            sol[2] += [dx, dy]
            return sol, rms, len(agree)
    return sol, rms, 0


def _grow(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def _shrink(m: np.ndarray, r: int) -> np.ndarray:
    return ~_grow(~m, r)


def _under_circles(m: np.ndarray, circles: np.ndarray, steps: int = 40) -> np.ndarray:
    """Fill what the strike circles hide from the nearest visible pixels, step by step inward
    (each hidden pixel takes the majority of its already decided neighbours), so the edge runs on
    through a circle instead of leaving a bite or a block."""
    hidden = _grow(circles, 3)  # whole discs: rims and anti-aliased edges too
    known, val = ~hidden, m & ~hidden
    shifts = ((0, 1), (0, -1), (1, 0), (-1, 0))
    for _ in range(steps):
        todo = hidden & ~known
        if not todo.any():
            break
        n_known = sum(np.roll(np.roll(known, dy, 0), dx, 1).astype(int) for dy, dx in shifts)
        n_true = sum(np.roll(np.roll(val & known, dy, 0), dx, 1).astype(int) for dy, dx in shifts)
        step = todo & (n_known > 0)
        val = val | (step & (2 * n_true > n_known))
        known = known | step
    return val


def _simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker on a closed ring of (x, y)."""
    if len(pts) < 5:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        far, idx = -1.0, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > far:
                far, idx = d, i
        if idx is not None and far > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def _mostly(other: np.ndarray, ring: list) -> bool:
    """Whether the other side's colour fills most of the area inside a ring (pixel coordinates)."""
    from PIL import Image, ImageDraw
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    x0, y0 = max(0, int(min(xs))), max(0, int(min(ys)))
    x1, y1 = min(other.shape[1], int(max(xs)) + 1), min(other.shape[0], int(max(ys)) + 1)
    if x1 <= x0 or y1 <= y0:
        return False
    img = Image.new("1", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(img).polygon([(x - x0, y - y0) for x, y in ring], fill=1)
    inside = np.asarray(img, dtype=bool)
    return inside.any() and other[y0:y1, x0:x1][inside].mean() > 0.5


def _area(ring) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _inside(polys: list, lon: float, lat: float) -> bool:
    hit = False
    for poly in polys:
        for ring in poly:
            for (xi, yi), (xj, yj) in zip(ring, ring[1:] + ring[:1]):
                if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    hit = not hit
    return hit


def trace(image_bytes: bytes, spec: dict) -> dict:
    """Polygons ([[ring, holes...], ...] of [lon, lat]) of one class on one map picture.
    spec: {"class", "points": [[px, py, lon, lat], ...] (1638-wide rendition), "bbox": [w, s, e, n],
    "inside": [[lon, lat], ...], "outside": [[lon, lat], ...]}. Raises ValueError when the checks fail."""
    import contourpy
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if img.width != 1638:
        img = img.resize((1638, round(img.height * 1638 / img.width)))
    rgb = np.asarray(img).astype(int)
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    sol, rms, dots = georeference(rgb.mean(axis=2), spec["points"])

    # work on the country's part of the picture only (with a margin)
    w, s_, e, n = spec["bbox"]
    corners = [to_px(sol, lon, lat) for lon in (w, e) for lat in (s_, n)]
    x0 = max(0, int(min(c[0] for c in corners)) - 30)
    x1 = min(rgb.shape[1], int(max(c[0] for c in corners)) + 30)
    y0 = max(0, int(min(c[1] for c in corners)) - 30)
    y1 = min(rgb.shape[0], int(max(c[1] for c in corners)) + 30)
    R, G, B = R[y0:y1, x0:x1], G[y0:y1, x0:x1], B[y0:y1, x0:x1]
    mask = CLASSES[spec["class"]](R, G, B)
    mask = _shrink(_grow(mask, 2), 2)
    circles = CIRCLE(R, G, B)
    mask = _under_circles(mask, circles)
    other = OTHER[spec["class"]](R, G, B) & ~_grow(circles, 3)  # teal circles aren't the other side

    gen = contourpy.contour_generator(z=mask.astype(float), fill_type=contourpy.FillType.OuterOffset)
    polys = []
    for points, offsets in zip(*gen.filled(0.5, 1.5)):
        rings = [points[offsets[i]:offsets[i + 1]].tolist() for i in range(len(offsets) - 1)]
        if not rings or _area(rings[0]) < MIN_AREA_PX:
            continue
        out = []
        for k, ring in enumerate(rings):
            if k and (_area(ring) < MIN_AREA_PX or not _mostly(other, ring)):
                continue  # a hole left by a label or a dot, not an enclave
            ring = _simplify(ring, SIMPLIFY_PX)
            ll = [[round(v, 3) for v in to_lonlat(sol, x + x0, y + y0)] for x, y in ring]
            if len(ll) >= 4:
                out.append(ll)
        if not out:
            continue
        cx = sum(p[0] for p in out[0]) / len(out[0])
        cy = sum(p[1] for p in out[0]) / len(out[0])
        if w <= cx <= e and s_ <= cy <= n:
            polys.append(out)
    wrong = [f"{p} not inside" for p in spec.get("inside", []) if not _inside(polys, *p)]
    wrong += [f"{p} inside" for p in spec.get("outside", []) if _inside(polys, *p)]
    if not polys or wrong:
        raise ValueError(f"traced shapes failed the checks ({'; '.join(wrong) or 'nothing traced'}); "
                         f"fit {rms:.1f} px from {dots or 'stored'} dots")
    return {"polygons": polys, "fit_px": round(rms, 2), "dots": dots}
