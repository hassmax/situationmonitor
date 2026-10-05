"""Temporary probe 2: the overview map module behind Ukraine's detailed map, and what mk.* stand for."""
import collections, re, requests
S = requests.Session()
S.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
def raw(t): return S.get("https://en.wikipedia.org/w/index.php", params={"title": t, "action": "raw"}).text
r = raw("Module:Russo-Ukrainian war overview map")
print("overview chars", len(r))
print("HEAD:", r[:3500])
print("MARKS:", collections.Counter(re.findall(r"mark\s*=\s*([^,}\n]+)", r)).most_common(40))
ents = re.findall(r"\{[^{}]*\blat\s*=[^{}]*\}", r)
print("ENTRIES", len(ents))
for e in ents[:4] + ents[100:104] + ents[-3:]:
    print("   ", re.sub(r"\s+", " ", e)[:240])
for line in r.splitlines():
    if re.search(r"\bmk\s*[.=\[]|^\s*(ukr|rus|con|grz)\s*=", line) and "lat" not in line:
        print("DEF:", line.strip()[:200])
for t in ["Template:Russo-Ukrainian war detailed map/doc", "Module:Russo-Ukrainian war detailed map/doc"]:
    d = raw(t)
    for line in d.splitlines():
        if ".svg" in line and ("control" in line.lower() or "held" in line.lower() or "contested" in line.lower() or "pressure" in line.lower()):
            print("LEGEND:", line.strip()[:220])
