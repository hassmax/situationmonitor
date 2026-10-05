"""Temporary probe 3: Sudan 2023 module, Kivu template, map files' dates and licences; sample names."""
import collections, json, re, requests
S = requests.Session()
S.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
API = "https://en.wikipedia.org/w/api.php"
mark = re.compile(r"mark\s*=\s*[\"']([^\"']+)[\"']")
def raw(t): return S.get("https://en.wikipedia.org/w/index.php", params={"title": t, "action": "raw"}).text
def edited(t):
    j = S.get(API, params={"action": "query", "format": "json", "prop": "revisions", "titles": t, "rvprop": "timestamp"}).json()
    return next(iter(j["query"]["pages"].values())).get("revisions", [{}])[0].get("timestamp")
for t in ["Module:2023 Sudanese Clashes detailed map", "Template:Kivumap"]:
    r = raw(t)
    print(f"==== {t} {len(r)} edited {edited(t)} marks {collections.Counter(mark.findall(r)).most_common(30)}")
    for l in r.splitlines():
        if ".svg" in l and "lat" not in l and len(l) < 260:
            print("   legend:", l.strip()[:200])
    print("   head:", r[:600].replace("\n", " | "))
for f in ["File:Sudanese Civil War Composite Map (2025).svg", "File:War in Sudan (2023).svg", "File:War in Sudan (2024).svg",
          "File:Zone-de-controle-M23.svg", "File:DRC-Rwanda Conflict (2022-present).svg", "File:M23 Offensive Map.svg"]:
    j = S.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "format": "json", "titles": f, "prop": "imageinfo",
              "iiprop": "timestamp|size|extmetadata|url"}).json()
    p = next(iter(j["query"]["pages"].values()))
    ii = (p.get("imageinfo") or [{}])[0]
    em = ii.get("extmetadata") or {}
    print(f"==== {f}: {ii.get('timestamp')} {ii.get('width')}x{ii.get('height')} {ii.get('size')} bytes licence={em.get('LicenseShortName', {}).get('value')} url={ii.get('url')}")
    print("   desc:", re.sub('<[^>]+>', '', em.get('ImageDescription', {}).get('value', ''))[:400])
    if ii.get("url") and f.endswith(".svg"):
        svg = S.get(ii["url"]).text
        fills = collections.Counter(re.findall(r"fill[:=]\s*\"?(#[0-9a-fA-F]{3,6})", svg))
        print("   svg", len(svg), "paths", svg.count("<path"), "fills", fills.most_common(12), "viewBox", re.findall(r'viewBox="([^"]+)"', svg)[:1])
for t, names in [("Module:Ethiopian wars and insurgencies detailed map", ["Mekelle", "Adigrat", "Shire", "Axum", "Alamata", "Humera", "Gondar", "Lalibela"])]:
    r = raw(t)
    for n in names:
        m = re.search(r"\{[^{}]*" + re.escape(n) + r"[^{}]*\}", r)
        print("  ", n, "->", re.sub(r"\s+", " ", m.group(0))[:200] if m else None)
