"""One-off probe: the legend of Wikipedia's Israeli-Palestinian conflict detailed map, and its dots
in Lebanon and Syria as this site's reader would place them."""
import re
import sys
from collections import Counter, defaultdict

import requests

sys.path.insert(0, "pipeline")
from frontline import wikipedia  # noqa: E402

S = requests.Session()
S.headers["User-Agent"] = wikipedia.UA


def raw(title):
    r = S.get(wikipedia.RAW, params={"title": title, "action": "raw"}, timeout=30)
    return r.text if r.ok else ""


for t in ["Template:Israeli-Palestinian conflict detailed map", "Module:Israeli-Palestinian conflict detailed map/doc",
          "Template:Israeli-Palestinian conflict detailed map/doc"]:
    txt = raw(t)
    print(f"== {t}: {len(txt)} chars")
    for line in txt.splitlines():
        if re.search(r"\.(svg|png|gif)|legend|caption|color|colour", line, re.I):
            print("   ", line.strip()[:300])

text = raw("Module:Israeli-Palestinian conflict detailed map")
for m in re.finditer(r"--[^\n]{3,120}", text):
    if re.search(r"leban|syria|golan|hermon|buffer|quneitra|israel|occup|control|legend|hezb", m.group(0), re.I):
        print("comment:", m.group(0)[:150])
marks = sorted(set(re.findall(r"mark\s*=\s*\"([^\"]+)\"", text)))
cfg = {"marks": {m: m for m in marks}}
conflict = {"countries": ["LB", "SY"], "center": [33.2, 35.7], "radius_km": 120}
pts = wikipedia._placed(wikipedia.parse(text, cfg), conflict)
by = defaultdict(list)
for p in pts:
    by[(p["country"], p["holder"] or "contested")].append(p)
for (c, h), ps in sorted(by.items()):
    print(f"\n{c} {h}: {len(ps)}")
    print("   ", "; ".join(f"{p['name']} ({p['lat']:.3f},{p['lon']:.3f})" for p in ps[:60]))
