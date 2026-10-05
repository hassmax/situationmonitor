"""Temporary probe 2: Sudan and DRC maps; full legends and mark counts."""
import collections, re, requests
S = requests.Session()
S.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
API = "https://en.wikipedia.org/w/api.php"
def search(q, ns, n=20):
    r = S.get(API, params={"action": "query", "format": "json", "list": "search", "srlimit": n, "srsearch": q, "srnamespace": ns}).json()
    return [h["title"] for h in r.get("query", {}).get("search", [])]
print("## all detailed-map modules:", search('intitle:"detailed map"', "828", 100))
for q in ["Sudanese civil war map", "Sudan war control map", "M23 offensive map", "Kivu conflict map", "eastern Congo control map"]:
    print(f"## files {q}:", search(q, "6", 10))
for q in ["Sudanese civil war (2023–present)", "M23 offensive (2022–present)", "Kivu conflict"]:
    r = S.get(API, params={"action": "parse", "format": "json", "page": q, "prop": "templates|images"}).json()
    p = r.get("parse", {})
    print(f"## page {q}: templates", [t["*"] for t in p.get("templates", []) if "map" in t["*"].lower()][:20])
    print(f"   images", [i for i in p.get("images", []) if re.search(r"map|control|situation|svg", i, re.I)][:25])
mark = re.compile(r"mark\s*=\s*[\"']([^\"']+)[\"']")
for title in ["Module:Ethiopian wars and insurgencies detailed map", "Module:Myanmar Civil War detailed map",
              "Module:Somali Civil War detailed map", "Module:Mali War detailed map", "Module:Yemeni Civil War detailed map",
              "Module:Sudanese Internal Conflict detailed map"]:
    raw = S.get("https://en.wikipedia.org/w/index.php", params={"title": title, "action": "raw"}).text
    print(f"\n==== {title} {len(raw)}")
    print("   marks:", collections.Counter(mark.findall(raw)).most_common(60))
    for l in raw.splitlines():
        if ".svg" in l and "lat" not in l and ("control" in l.lower() or "**" in l or l.strip().startswith("*")):
            print("   legend:", l.strip()[:200])
