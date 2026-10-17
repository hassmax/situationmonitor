"""Make the terrain relief the globe blends into its land picture (site/assets/relief-<width>.jpg).

Source: Natural Earth's shaded relief (public domain), as shipped in matplotlib basemap's data
package (basemap-data 2.0.0, `mpl_toolkits/basemap_data/shadedrelief.jpg`, 10800 x 5400, under its
MIT licence). That picture mixes the shading of hills and mountains (lit from the northwest) with
colours for height and climate; only the shading is kept: its brightness minus the brightness around
each point (a high-pass), measured over land only, so coasts get no halo and broad colour changes
(desert to forest) don't show as relief. Grey 128 means flat, lighter and darker the lit and shaded
slopes. Land is the country shapes the globe paints (countries-110m.json, without Antarctica)
where the picture isn't sea or lake blue; elsewhere it is 128.

Run once:
    pip download basemap-data==2.0.0 --no-deps -d /tmp/bm
    python scripts/make_relief.py /tmp/bm/basemap_data-2.0.0-py3-none-any.whl
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_country_ids import TOPO, decode, unwrap  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (4096, 2048)   # desktop, phones (the land picture's widths, app.js landCanvas)
SPREAD_KM = 60          # the "around each point" of the high-pass: hills and ranges stay, regions go
Image.MAX_IMAGE_PIXELS = None


def source(path: str) -> Image.Image:
    p = Path(path)
    if p.suffix == ".whl":
        with zipfile.ZipFile(p) as z:
            return Image.open(io.BytesIO(z.read("mpl_toolkits/basemap_data/shadedrelief.jpg"))).convert("RGB")
    return Image.open(p).convert("RGB")


def land_mask(width: int) -> np.ndarray:
    """1 where the globe paints land (every country shape but Antarctica), else 0."""
    height = width // 2
    X = lambda lon: (lon + 180) / 360 * width  # noqa: E731
    Y = lambda lat: (90 - lat) / 180 * height  # noqa: E731
    mask = Image.new("1", (width, height), 0)
    for f in decode(json.loads(TOPO.read_text(encoding="utf-8"))):
        if f["name"] == "Antarctica":
            continue
        for poly in f["polygons"]:
            for r in poly:
                pts = unwrap(r)
                for shift in (-360, 0, 360):
                    one = Image.new("1", (width, height), 0)
                    ImageDraw.Draw(one).polygon([(X(lon + shift), Y(lat)) for lon, lat in pts], fill=1)
                    mask = ImageChops.logical_xor(mask, one)
    return np.asarray(mask, dtype=np.float32)


def blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three box blurs of radius r (close to a Gaussian), wrapping east-west, clamped north-south."""
    for _ in range(3):
        for axis, mode in ((1, "wrap"), (0, "edge")):
            pad = np.pad(a, [(r, r) if i == axis else (0, 0) for i in range(2)], mode=mode)
            c = np.cumsum(pad, axis=axis, dtype=np.float64)
            c = np.concatenate([np.zeros_like(c.take([0], axis=axis)), c], axis=axis)
            n = a.shape[axis]
            a = ((c.take(range(2 * r + 1, 2 * r + 1 + n), axis=axis) - c.take(range(0, n), axis=axis)) / (2 * r + 1)).astype(np.float32)
    return a


def relief(src: Image.Image, width: int) -> Image.Image:
    height = width // 2
    rgb = np.asarray(src.resize((width, height), Image.LANCZOS), dtype=np.float32) / 255
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    # the country shapes are coarser than the picture: where they reach over its sea or lakes (blue,
    # brighter than any shading), that is flat too, or every coast would get a bright rim
    # (pale shallow water near coasts too, and the blended pixels at its edge: two pixels around it)
    water = (rgb[..., 2] - rgb[..., 0] > 18 / 255) & (rgb[..., 2] - rgb[..., 1] > 6 / 255)
    for _ in range(2):
        water = water | np.roll(water, 1, 0) | np.roll(water, -1, 0) | np.roll(water, 1, 1) | np.roll(water, -1, 1)
    land = land_mask(width) * ~water
    r = max(1, round(SPREAD_KM / (40075 / width) / 2))   # three passes of radius r spread about 2r
    around = blur(lum * land, r) / np.maximum(blur(land, r), 1e-3)
    detail = (lum - around) * land
    scale = np.percentile(np.abs(detail[land > 0]), 99) or 1
    grey = 128 + 127 * np.clip(detail / scale, -1, 1)
    grey[land == 0] = 128
    return Image.fromarray(grey.round().astype(np.uint8), "L")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = source(sys.argv[1])
    for width in WIDTHS:
        out = ROOT / f"site/assets/relief-{width}.jpg"
        relief(src, width).save(out, quality=82, optimize=True)
        print(f"{out.relative_to(ROOT)}: {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
