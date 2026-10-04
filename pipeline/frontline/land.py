"""Land of a set of countries, as a grid mask: the shaded areas stop at the coast and at the
borders of the conflict's own countries (no shading over the sea or a neighbouring country).
Uses the same country shapes the globe paints (site/assets/countries-110m.json, Natural Earth
1:110m), so the edges match what the viewer sees.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

SHAPES = Path(__file__).resolve().parents[2] / "site" / "assets" / "countries-110m.json"
REGIONS = Path(__file__).resolve().parent / "regions.json"


@lru_cache(maxsize=1)
def _rings() -> dict[str, list[np.ndarray]]:
    """ISO numeric id -> outer and inner rings, each an (n, 2) array of lon, lat."""
    topo = json.loads(SHAPES.read_text(encoding="utf-8"))
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        a = np.cumsum(np.array(arc, dtype=float), axis=0)
        arcs.append(np.column_stack([a[:, 0] * sx + tx, a[:, 1] * sy + ty]))

    def ring(ids):
        parts = [arcs[i] if i >= 0 else arcs[~i][::-1] for i in ids]
        return np.concatenate(parts)

    out: dict[str, list[np.ndarray]] = {}
    for g in topo["objects"]["countries"]["geometries"]:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        out.setdefault(str(g.get("id")), []).extend(ring(r) for poly in polys for r in poly)
    return out


def mask(xs: np.ndarray, ys: np.ndarray, numeric_ids: list[str]) -> np.ndarray:
    """Boolean grid (len(ys), len(xs)): True on the land of the given countries (even-odd rule,
    so lakes and enclaves cut out of a country stay out)."""
    return rings_mask(xs, ys, [r for i in numeric_ids for r in _rings().get(i, [])])


def rings_mask(xs: np.ndarray, ys: np.ndarray, rings: list) -> np.ndarray:
    """Boolean grid: True inside the rings (lon, lat), even-odd rule."""
    out = np.zeros((len(ys), len(xs)), dtype=bool)
    rings = [np.asarray(r, dtype=float) for r in rings if len(r) >= 3]
    if not rings:
        return out
    a = np.concatenate([r[:-1] for r in rings])
    b = np.concatenate([r[1:] for r in rings])
    keep = (np.maximum(a[:, 1], b[:, 1]) >= ys[0]) & (np.minimum(a[:, 1], b[:, 1]) <= ys[-1])
    a, b = a[keep], b[keep]
    for j, y in enumerate(ys):
        cross = (a[:, 1] <= y) != (b[:, 1] <= y)
        if not cross.any():
            continue
        p, q = a[cross], b[cross]
        x = np.sort(p[:, 0] + (y - p[:, 1]) * (q[:, 0] - p[:, 0]) / (q[:, 1] - p[:, 1]))
        out[j] = np.searchsorted(x, xs, side="right") % 2 == 1
    return out


@lru_cache(maxsize=1)
def _regions() -> dict:
    return json.loads(REGIONS.read_text(encoding="utf-8"))["regions"]


def regions(country: str) -> list[dict]:
    """A country's provinces ({name, rings}), from regions.json (Natural Earth admin-1, public
    domain): only the countries whose conflicts fill by province have any."""
    return [{"name": r["name"], "rings": [ring for poly in r["polygons"] for ring in poly]}
            for r in _regions().get(country, [])]


def inside(rings: list, lon: float, lat: float) -> bool:
    """Point in the rings (even-odd)."""
    hit = False
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:]):
            if (y1 <= lat) != (y2 <= lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit
