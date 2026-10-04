"""Temporary probe (removed before merging): which front-line sources answer from GitHub's servers."""
import re, json, requests
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
s = requests.Session(); s.headers.update(H)
def get(u, **kw):
    try:
        r = s.get(u, timeout=30, **kw); return r
    except Exception as e:
        print("  ERR", u, type(e).__name__, str(e)[:120]); return None
def locs(t): return re.findall(r"<loc>([^<]+)</loc>", t)

print("== ISW sitemap")
r = get("https://understandingwar.org/sitemap_index.xml")
if r is not None:
    print(" ", r.status_code, len(r.text))
    maps = [u for u in locs(r.text) if "post-sitemap" in u]
    for sm in maps[-2:]:
        rr = get(sm); urls = locs(rr.text) if rr is not None else []
        print("  ", sm, len(urls), "africa-file:", [u for u in urls if "africa-file" in u][-3:], "iran-update:", len([u for u in urls if "iran-update" in u]))

print("== Critical Threats")
for u in ["https://www.criticalthreats.org/robots.txt", "https://www.criticalthreats.org/sitemap.xml", "https://www.criticalthreats.org/analysis/africa-file"]:
    r = get(u)
    if r is None: continue
    print(" ", r.status_code, u, len(r.text))
    if u.endswith("robots.txt"): print("   ", [l for l in r.text.splitlines() if "itemap" in l][:5])
    if u.endswith("sitemap.xml"):
        L = locs(r.text); print("    locs", len(L), L[:5])
        af = [x for x in L if "africa-file" in x]; print("    africa-file in index:", af[-5:])
        for sub in [x for x in L if x.endswith(".xml")][:3]:
            rr = get(sub); LL = locs(rr.text) if rr is not None else []
            print("    sub", sub, rr.status_code if rr is not None else None, len(LL), [x for x in LL if "africa-file" in x][-3:])
    if u.endswith("africa-file"):
        links = sorted(set(re.findall(r'href="(/analysis/[^"]*africa-file[^"]*)"', r.text)))
        print("    africa-file links on page:", links[:8])
        if links:
            p = get("https://www.criticalthreats.org" + links[-1])
            if p is not None:
                txt = re.sub(r"<[^>]+>", " ", p.text); sents = re.split(r"(?<=[.!?])\s+", txt)
                hits = [x.strip()[:160] for x in sents if re.search(r"\b(seiz|captur|control|took|advanc|withdr)", x, re.I)]
                print("    page", p.status_code, "sentences", len(sents), "control-ish", len(hits)); [print("     -", h) for h in hits[:6]]

print("== Bluesky analyst accounts")
for actor in ["blackbirdgroup.bsky.social", "deepstatemap.bsky.social", "sudanwarmonitor.bsky.social", "geoconfirmed.bsky.social"]:
    r = get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed", params={"actor": actor, "limit": 8})
    if r is None: continue
    print(" ", actor, r.status_code)
    if r.ok:
        for it in r.json().get("feed", [])[:8]:
            print("    -", it["post"]["record"].get("createdAt", "")[:10], it["post"]["record"].get("text", "").replace("\n", " ")[:170])

print("== Feeds")
for u in ["https://sudanwarmonitor.com/feed", "https://www.sudanspost.com/feed/", "https://myanmar-now.org/en/feed/", "https://www.irrawaddy.com/feed", "https://www.criticalthreats.org/feed"]:
    r = get(u)
    if r is None: continue
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?([^<\]]+)", r.text)
    print(" ", r.status_code, u, len(titles), titles[1:5])

print("== Wikipedia detailed maps")
for t in ["Template:Myanmar civil war detailed map", "Template:Sudanese civil war (2023–present) detailed map", "Template:Mali War detailed map",
          "Template:Somali Civil War (2009–present) detailed map", "Template:Kivu conflict detailed map", "Template:Tigray war detailed map"]:
    r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "format": "json", "titles": t, "prop": "revisions", "rvprop": "timestamp|content", "rvslots": "main"})
    if r is None: continue
    pages = r.json().get("query", {}).get("pages", {})
    for p in pages.values():
        if "missing" in p: print("  missing", t); continue
        rev = p["revisions"][0]; c = rev["slots"]["main"]["*"]
        print("  ", t, rev["timestamp"], "len", len(c), "marks", len(re.findall(r"mark\s*=", c)), "| sample:", re.sub(r"\s+", " ", c[:300]))
r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "format": "json", "list": "search", "srnamespace": 10, "srsearch": "detailed map civil war", "srlimit": 30})
if r is not None and r.ok:
    print("  templates found:", [x["title"] for x in r.json()["query"]["search"]])

print("== FIRMS")
r = get("https://firms.modaps.eosdis.nasa.gov/api/country/"); print(" ", r.status_code if r is not None else None)
