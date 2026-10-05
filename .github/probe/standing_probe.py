"""Temporary probe: real Google News headlines for the standing towns outside Ukraine, and which
the held-phrase matcher catches. Not part of the pipeline."""
import json, sys, time
from urllib.parse import quote_plus
sys.path.insert(0, "pipeline")
import feedparser, requests, yaml
from frontline import standing

conf = yaml.safe_load(open("pipeline/config/frontlines.yaml"))
conf = conf.get("conflicts") or conf
s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
EN = "&hl=en-US&gl=US&ceid=US%3Aen"
FR = "&hl=fr&gl=FR&ceid=FR%3Afr"
Q = {
    "held": "({n}) (occupied OR held OR controlled OR run OR stronghold) when:60d",
    "events": '({n}) (seized OR captured OR "under control" OR "fell to" OR "took control" OR "control of" OR retook OR recaptured OR besieged OR siege OR overran OR withdrew) when:60d',
}
QFR = {"fr": '({n}) (contrôle OR "aux mains" OR "tenue par" OR occupée OR "sous blocus" OR "s\'est emparé" OR "pris le contrôle" OR repris) when:60d'}
out = {}
for c in conf:
    if c["id"] == "ukraine":
        continue
    for t in standing.towns(c):
        names = " OR ".join(f'"{n}"' for n in t["names"][:3])
        qs = [(k, v, EN) for k, v in Q.items()] + ([(k, v, FR) for k, v in QFR.items()] if c["id"] in ("sahel", "drc") else [])
        for k, q, ed in qs:
            url = "https://news.google.com/rss/search?q=" + quote_plus(q.format(n=names)) + ed
            try:
                r = s.get(url, timeout=20)
                f = feedparser.parse(r.content)
            except Exception as exc:
                print("fail", c["id"], t["name"], k, exc)
                continue
            rows = []
            for e in f.entries[:60]:
                title = e.get("title", "")
                hit = standing.find(title, c, [t])
                rows.append({"t": title, "src": (e.get("source") or {}).get("title"), "d": e.get("published"),
                             "hit": [(a, w) for _, a, w in hit]})
            out.setdefault(c["id"], {}).setdefault(t["name"], {})[k] = rows
            time.sleep(0.6)
        got = out[c["id"]][t["name"]]
        print(c["id"], t["name"], {k: (len(v), sum(1 for x in v if x["hit"])) for k, v in got.items()}, flush=True)
json.dump(out, open("standing_probe.json", "w"), ensure_ascii=False)

import re
CTRL = re.compile(r"held|control|seiz|captur|\btook\b|\btake\b|retak|retook|fell|fall of|falls|overr|siege|besieg|stronghold|occup|withdr|enter|liberat|recaptur|under\b|-run\b|takeover|expel|driv|push|advance|aux mains|contrôle|tenue|prise|pris|repris|emparé", re.I)
for c, towns in out.items():
    seen = set()
    print(f"\n######## {c}")
    for town, qs in towns.items():
        for k, rows in qs.items():
            for x in rows:
                if x["t"] in seen or not CTRL.search(x["t"]):
                    continue
                seen.add(x["t"])
                print(f"{'HIT ' if x['hit'] else '    '}{town} | {x['t'][:170]} | {(x['d'] or '')[5:16]}")
