"""Temporary: did the pipeline see the B-1 Fairford posts, and what does the model make of them?"""
import json, os, sys
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import config, extract
from common import http_session, short_hash, make_item
from sources import bluesky
cfg = config.load()
s = http_session()
state = json.load(open("/tmp/state.json"))
seen = state.get("seen", {})
found = {}
for q in ["B-1B Fairford", "B-1 Fairford", "Lancer Fairford", "B-1s Fairford", "bombers Fairford home"]:
    r = s.get(bluesky.SEARCH_URL, params={"q": q, "sort": "latest", "limit": 25}, timeout=20)
    for p in r.json().get("posts", []):
        found[p["uri"]] = p
print("posts found:", len(found))
items = []
for uri, p in found.items():
    rec, author = p["record"], p["author"]
    text = rec.get("text", "")
    iid = short_hash("bluesky", uri)
    print(f"- {rec.get('createdAt')} @{author['handle']} followers?  seen={iid in seen} | {text[:150]!r}")
    if "Fairford" in text and ("B-1" in text or "Lancer" in text):
        src = {"name": f"{author.get('displayName') or author['handle']} (@{author['handle']}, Bluesky)", "kind": "osint", "group": "bluesky-search", "weight": 1}
        items.append(make_item(src, "bluesky", "bsky-search", f"https://bsky.app/profile/{author['handle']}/post/{uri.rsplit('/',1)[-1]}", text,
                               datetime.fromisoformat(rec["createdAt"].replace("Z", "+00:00")), uid=uri))
prof = s.get(bluesky.PROFILES_URL, params=[("actors", p["author"]["handle"]) for p in list(found.values())[:25]], timeout=20).json()
print("followers:", {x["handle"]: x.get("followersCount") for x in prof.get("profiles", [])})
print("candidate items:", len(items), [extract.is_candidate(i) for i in items])
token = os.environ.get("LLM_API_KEY", "")
chosen = state.get("llm_model")
if items and token and chosen:
    out = extract._call_model(items[:6], token, chosen, cfg.settings)
    print("MODEL RAW:", json.dumps(out, ensure_ascii=False)[:3000])
    for obj in out.get("events", []):
        i = obj.get("i")
        if isinstance(i, int) and i < len(items):
            print("CLEANED:", json.dumps(extract._clean_record(obj, items[i]), ensure_ascii=False)[:600])
