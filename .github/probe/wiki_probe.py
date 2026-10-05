"""Temporary probe: Wikipedia's conflict 'detailed map' modules, their markers and legends."""
import collections, re, requests
S = requests.Session()
S.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
API = "https://en.wikipedia.org/w/api.php"
TERMS = ["Russo-Ukrainian War", "Sudanese civil war", "Myanmar civil war", "Somali Civil War", "Mali War", "Sahel",
         "Burkina Faso", "Niger", "Kivu", "M23", "Yemeni Civil War", "Yemen", "Tigray", "Amhara", "Ethiopia"]
seen = set()
for t in TERMS:
    r = S.get(API, params={"action": "query", "format": "json", "list": "search", "srlimit": 8,
                           "srsearch": f'intitle:"detailed map" {t}', "srnamespace": "10|828"}).json()
    hits = [h["title"] for h in r.get("query", {}).get("search", [])]
    print(f"## {t}: {hits}")
    seen.update(hits)
mark = re.compile(r"mark\s*=\s*[\"']([^\"']+)[\"']")
for title in sorted(seen):
    raw = S.get("https://en.wikipedia.org/w/index.php", params={"title": title, "action": "raw"}).text
    info = S.get(API, params={"action": "query", "format": "json", "prop": "revisions", "titles": title, "rvprop": "timestamp"}).json()
    ts = next(iter(info["query"]["pages"].values())).get("revisions", [{}])[0].get("timestamp")
    marks = collections.Counter(mark.findall(raw))
    lat = len(re.findall(r"\blat\s*=", raw))
    print(f"\n==== {title} | {len(raw)} chars | last edit {ts} | {lat} lat= | marks {marks.most_common(14)}")
    if lat:
        m = re.search(r"\{[^{}]*\blat\s*=[^{}]*\}", raw)
        print("   sample:", (m.group(0) if m else "")[:300].replace("\n", " "))
    legend = [l.strip() for l in raw.splitlines() if ".svg" in l and not re.search(r"\blat\s*=", l) and len(l) < 300]
    for l in legend[:25]:
        print("   legend:", l[:220])
