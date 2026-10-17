"""Make the country-ID maps the globe uses to light countries without repainting its land picture.

Each pixel of site/assets/country-ids-<width>.png holds the ISO numeric id of the country there
(red = id % 256, green = id // 256; 0 = sea, Antarctica, or a territory without an id), in the same
equirectangular layout as the land picture site/app.js paints (longitude -180..180 left to right,
latitude 90..-90 top to bottom). Unlike a canvas fill, the edges are not blended, so every pixel is
one country's id exactly. The globe reads it with a small table of colours (app.js, landIds).

Run once, and again only if site/assets/countries-110m.json changes:
    python scripts/make_country_ids.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TOPO = ROOT / "site/assets/countries-110m.json"
WIDTHS = (4096, 2048)  # desktop, phones (the land picture's own widths, app.js landCanvas)


def decode(topo: dict) -> list[dict]:
    """TopoJSON countries -> [{"id", "name", "polygons": [[ring, ...], ...]}], rings in (lon, lat)."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(indexes):
        out = []
        for i in indexes:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        return out

    feats = []
    for g in topo["objects"]["countries"]["geometries"]:
        if g.get("type") == "Polygon":
            polys = [[ring(r) for r in g["arcs"]]]
        elif g.get("type") == "MultiPolygon":
            polys = [[ring(r) for r in p] for p in g["arcs"]]
        else:
            continue
        # as app.js sanitize: a polygon whose outer ring has fewer than 3 distinct points is dropped
        polys = [p for p in polys if p and len({(round(a, 4), round(b, 4)) for a, b in p[0]}) >= 3]
        feats.append({"id": g.get("id"), "name": (g.get("properties") or {}).get("name"), "polygons": polys})
    return feats


def unwrap(ring):
    """As app.js countryPath: longitudes made continuous across the 180° line."""
    out, prev = [], ring[0][0]
    for lon, lat in ring:
        while lon - prev > 180:
            lon -= 360
        while lon - prev < -180:
            lon += 360
        prev = lon
        out.append((lon, lat))
    return out


def draw(feats: list[dict], width: int) -> Image.Image:
    height = width // 2
    img = Image.new("RGB", (width, height), (0, 0, 0))
    X = lambda lon: (lon + 180) / 360 * width  # noqa: E731
    Y = lambda lat: (90 - lat) / 180 * height  # noqa: E731
    for f in feats:
        if f["name"] == "Antarctica" or not str(f["id"] or "").isdigit():
            continue  # app.js leaves Antarctica off; a territory without an id is never lit
        n = int(f["id"])
        # even-odd, like the canvas: each ring flips what it covers, so holes stay open
        mask = Image.new("1", (width, height), 0)
        for poly in f["polygons"]:
            for r in poly:
                pts = unwrap(r)
                for shift in (-360, 0, 360):
                    one = Image.new("1", (width, height), 0)
                    ImageDraw.Draw(one).polygon([(X(lon + shift), Y(lat)) for lon, lat in pts], fill=1)
                    mask = ImageChops.logical_xor(mask, one)
        img.paste((n % 256, n // 256, 0), mask=mask)
    return img


def main() -> None:
    feats = decode(json.loads(TOPO.read_text(encoding="utf-8")))
    for width in WIDTHS:
        out = ROOT / f"site/assets/country-ids-{width}.png"
        draw(feats, width).save(out, optimize=True)
        print(f"{out.relative_to(ROOT)}: {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
