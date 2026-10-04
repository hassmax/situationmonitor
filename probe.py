"""Temporary probe (removed before merging), round 2."""
import re, requests
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
s = requests.Session(); s.headers.update(H)
def get(u, **kw):
    try: return s.get(u, timeout=30, **kw)
    except Exception as e: print("  ERR", u, type(e).__name__); return None

print("== CTP africa-file page links")
r = get("https://www.criticalthreats.org/analysis/africa-file")
if r is not None:
    hrefs = re.findall(r'href="([^"]+)"', r.text)
    af = sorted({h for h in hrefs if "africa" in h.lower()})
    print("  hrefs", len(hrefs), "with africa:", af[:15])
    print("  any 2026 analysis links:", sorted({h for h in hrefs if "/analysis/" in h and "2026" in h})[:10])
    i = r.text.find("Africa File,"); print("  context:", re.sub(r"\s+", " ", r.text[max(0, i-400):i+200]) if i >= 0 else "no 'Africa File,' text")
for u in ["https://www.criticalthreats.org/analysis", "https://www.criticalthreats.org/sitemap_index.xml", "https://www.criticalthreats.org/rss.xml", "https://www.criticalthreats.org/analysis.rss", "https://www.criticalthreats.org/api/search?q=africa%20file"]:
    rr = get(u); print("  ", rr.status_code if rr is not None else None, u, (rr.headers.get("content-type") if rr is not None else ""), len(rr.text) if rr is not None else 0)
    if rr is not None and rr.ok:
        L = sorted({h for h in re.findall(r'(?:href="|<loc>|<link>)([^"<]+)', rr.text) if "africa-file" in h})
        print("     africa-file links:", L[-6:])

print("== Wikipedia detailed-map modules")
for t in ["Module:Myanmar Civil War detailed map", "Module:Somali Civil War detailed map", "Module:Yemeni Civil War detailed map", "Module:Mali War detailed map",
          "Module:Sudanese civil war detailed map", "Module:Sudanese civil war (2023–present) detailed map", "Template:Sudanese civil war (2023–present) detailed map",
          "Module:Kivu conflict detailed map", "Module:M23 offensive detailed map", "Module:Russo-Ukrainian War detailed map", "Module:Tigray War detailed map", "Module:Ethiopian civil conflict detailed map"]:
    r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "format": "json", "titles": t, "prop": "revisions", "rvprop": "timestamp|content", "rvslots": "main"})
    if r is None: continue
    for p in r.json().get("query", {}).get("pages", {}).values():
        if "missing" in p: print("  missing", t); continue
        rev = p["revisions"][0]; c = rev["slots"]["main"]["*"]
        marks = re.findall(r"mark\s*=\s*\"([^\"]+)\"", c)
        from collections import Counter
        print("  ", t, rev["timestamp"], "len", len(c), "places", len(re.findall(r"lat\s*=", c)), "marks", Counter(marks).most_common(8))
        m = re.search(r"\{[^{}]*lat\s*=[^{}]*\}", c); print("     example:", re.sub(r"\s+", " ", m.group(0))[:300] if m else c[:300])
r = get("https://en.wikipedia.org/w/api.php", params={"action": "query", "format": "json", "list": "search", "srnamespace": 828, "srsearch": "detailed map", "srlimit": 50})
if r is not None and r.ok: print("  modules:", [x["title"] for x in r.json()["query"]["search"]])

print("== Regional feeds")
for u in ["https://www.radiookapi.net/rss.xml", "https://actualite.cd/feed", "https://www.garoweonline.com/en/rss", "https://www.hiiraan.com/rss.xml", "https://www.somaliguardian.com/feed/",
          "https://www.dvb.no/feed", "https://www.mizzima.com/rss.xml", "https://english.dvb.no/feed/", "https://www.thedefensepost.com/feed/", "https://www.sahel-intelligence.com/feed", "https://www.addisstandard.com/feed/", "https://borkena.com/feed/"]:
    rr = get(u)
    if rr is None: continue
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?([^<\]]+)", rr.text)
    print("  ", rr.status_code, u, len(titles), [t[:70] for t in titles[1:4]])
