"""Temporary: run the Bluesky patrol and the new feeds live, from GitHub's servers."""
import sys
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import config, extract
from common import http_session
from sources import bluesky, rss
cfg = config.load()
s = http_session()
now = datetime.now(timezone.utc)
health = {}
items = bluesky.search(cfg.sources["bluesky_search"], cfg.sources["bluesky"], s, health, now)
print("patrol items:", len(items), "passing filter:", sum(bool(extract.is_candidate(i)) for i in items))
for i in items[:40]:
    print(" ", extract.is_candidate(i), i["source"][:50], "|", i["text"][:110].replace("\n", " "))
new = [f for f in cfg.sources["rss"] if f["id"] in ("twz", "ukdefencejournal", "theaviationist", "airandspaceforces", "gnews-airpower", "gnews-airbases")]
got = rss.fetch(new, s, health, 1, cfg.outlets)
print("feeds:", {k: (v.get("count"), v.get("error")) for k, v in health.items() if k.startswith("rss:")})
for i in got:
    if extract.is_candidate(i) and any(w in i["text"] for w in ("B-1", "bomber", "Bomber", "Fairford")):
        print(" ", i["source"][:40], "|", i["text"][:120].replace("\n", " "))
# rerun
