"""One-off probe: the full legend of Wikipedia's Israeli-Palestinian conflict detailed map, and its
dots in Lebanon and Syria as the configured reader places them."""
import json
import re
import sys
from collections import Counter

import requests
import yaml

sys.path.insert(0, "pipeline")
from frontline import wikipedia  # noqa: E402

S = requests.Session()
S.headers["User-Agent"] = wikipedia.UA
doc = S.get(wikipedia.RAW, params={"title": "Module:Israeli-Palestinian conflict detailed map/doc", "action": "raw"}, timeout=30).text
for line in doc.splitlines():
    if "Control" in line or "purple" in line.lower():
        print("LEGEND:", line.strip())
text = S.get(wikipedia.RAW, params={"title": "Module:Israeli-Palestinian conflict detailed map", "action": "raw"}, timeout=30).text
c = next(x for x in yaml.safe_load(open("pipeline/config/frontlines.yaml"))["conflicts"] if x["id"] == "israel")
pts = wikipedia._placed(wikipedia._near_only(wikipedia.parse(text, c["wikipedia"]), c["wikipedia"].get("near")), c)
print("COUNTS", Counter((p["country"], p["holder"] or "contested") for p in pts))
print("POINTS", json.dumps(pts, ensure_ascii=False))
purple = [m.group(0)[:200] for m in re.finditer(r"\{[^{}]*purple[^{}]*\}", text)]
print("PURPLE", len(purple), purple[:8])

# the dots, compact, as annotations (readable through GitHub's API): name|lat|lon|holder-or-contested;...
rows = ";".join(f"{p['name'].replace(';', ',').replace('|', '/')}|{p['lat']:.5f}|{p['lon']:.5f}|{p['holder'] or 'contested'}|{p['country']}" for p in pts)
for i in range(0, len(rows), 3500):
    print(f"::notice title=dots {i // 3500}::{rows[i:i + 3500]}")
