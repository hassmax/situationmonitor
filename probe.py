"""Temporary probe (removed before merging): the new readers against the live sites, no model."""
import sys, re
sys.path.insert(0, "pipeline")
from datetime import datetime, timezone
import config, extract
from common import http_session
from frontline import isw
from sources import rss
now = datetime.now(timezone.utc)
s = http_session()
reps = isw.africa_file(s, now)
for r in reps: print(" ", r["date"][:10], r["url"])
if reps:
    from sources.maproom import HEADERS
    page = s.get(reps[0]["url"], headers=HEADERS, timeout=30)
    sents = isw.sentences(page.text); todo = [x for x in sents if isw.CONTROL_RE.search(x)]
    print("  newest report:", page.status_code, len(sents), "sentences,", len(todo), "about ground"); [print("   -", x[:170]) for x in todo[:5]]
cfg = config.load()
new = {"sudanwarmonitor", "sudanspost", "dabanga", "myanmarnow", "dvb", "bni", "somaliguardian", "radiookapi", "actualitecd", "lefaso", "rfi-afrique"}
srcs = [x for x in cfg.sources["rss"] if x.get("id") in new]
health = {}
items = rss.fetch(srcs, s, health, 0, cfg.outlets)
by = {}
for it in items:
    by.setdefault(it["source"], [0, 0, []])
    by[it["source"]][0] += 1
    if extract.is_candidate(it):
        by[it["source"]][1] += 1; by[it["source"]][2].append(it["text"][:90])
for k, (n, c, ex) in by.items(): print(f"  {k}: {n} items, {c} pass the filter; e.g. {ex[:2]}")
print("  health:", {k: (v.get('ok'), v.get('error')) for k, v in health.items()})
