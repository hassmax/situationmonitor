"""Temporary probe (removed before merging), round 3."""
import re, json, requests, datetime
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
s = requests.Session(); s.headers.update(H)
def get(u, **kw):
    try: return s.get(u, timeout=30, **kw)
    except Exception as e: print("  ERR", u, type(e).__name__); return None
print("== CTP list")
r = get("https://www.criticalthreats.org/analysis/africa-file")
m = re.search(r"var INI_LIST = (\[.*?\]);?\s*</script>", r.text, re.S)
items = json.loads(m.group(1)) if m else []
print("  INI_LIST", len(items))
items.sort(key=lambda x: -x.get("published_timestamp", 0))
for it in items[:6]: print("   ", datetime.datetime.utcfromtimestamp(it["published_timestamp"]).date(), it["slug"][:110])
hrefs = sorted({h.split("#")[0] for h in re.findall(r'href="(https://www\.criticalthreats\.org/analysis/[^"#]*africa-file-[a-z]+-\d+-2026)', r.text)})
print("  2026 hrefs", len(hrefs), hrefs[-4:])
newest = "https://www.criticalthreats.org/analysis/" + items[0]["slug"] if items else (hrefs[-1] if hrefs else None)
if newest:
    p = get(newest); txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", p.text, flags=re.S); txt = re.sub(r"<[^>]+>", " ", txt)
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", txt)) if 40 < len(x) < 600]
    hits = [x for x in sents if re.search(r"\b(seiz|captur|control|took|advanc|withdr|retook|recaptur)", x, re.I)]
    print("  newest", newest, p.status_code, "sentences", len(sents), "control-ish", len(hits))
    for h in hits[:10]: print("    -", h[:220])
for prefix in ["iran-update", "russian-offensive-campaign-assessment"]:
    rr = get(f"https://www.criticalthreats.org/analysis/{prefix}")
    print("  ", prefix, rr.status_code if rr is not None else None)
print("== Regional feeds")
for u in ["https://www.radiookapi.net/rss.xml", "https://actualite.cd/feed", "https://www.garoweonline.com/en/rss", "https://www.hiiraan.com/rss.xml", "https://www.somaliguardian.com/feed/",
          "https://english.dvb.no/feed/", "https://www.mizzima.com/rss.xml", "https://www.thedefensepost.com/feed/", "https://www.addisstandard.com/feed/", "https://borkena.com/feed/",
          "https://www.bnionline.net/en/rss.xml", "https://www.khitthitnews.com/feed/", "https://www.dabangasudan.org/en/feed", "https://www.alnilin.com/feed", "https://lefaso.net/spip.php?page=backend",
          "https://www.studio-tamani.org/feed/", "https://www.rfi.fr/fr/afrique/rss", "https://www.voaafrique.com/api/zrqiteuuir"]:
    rr = get(u)
    if rr is None: continue
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?([^<\]]+)", rr.text)
    print("  ", rr.status_code, u, len(titles), [t[:80] for t in titles[1:5]])
