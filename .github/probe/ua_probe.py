"""Temporary probe: how Ukraine's Wikipedia detailed map encodes its towns."""
import collections, re, requests
S = requests.Session()
S.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
def raw(t): return S.get("https://en.wikipedia.org/w/index.php", params={"title": t, "action": "raw"}).text
for t in ["Module:Russo-Ukrainian war detailed map", "Module:Russo-Ukrainian war detailed map (oblasts)",
          "Module:Russo-Ukrainian war detailed map (Donetsk Oblast)", "Template:Russo-Ukrainian war detailed map"]:
    r = raw(t)
    print(f"\n==== {t} ({len(r)} chars)")
    print("HEAD:", r[:2500])
    marks = collections.Counter(re.findall(r"mark\s*=\s*([^,}\n]+)", r))
    print("MARK VALUES:", marks.most_common(40))
    entries = re.findall(r"\{[^{}]*\blat\s*=[^{}]*\}", r)
    print("ENTRIES:", len(entries))
    for e in entries[200:206] + entries[600:603]:
        print("   ", re.sub(r"\s+", " ", e)[:260])
    print("TAIL:", r[-1500:])
