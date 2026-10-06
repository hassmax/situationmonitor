"""One-off probe: which Wikipedia maps show Israeli-held places in Lebanon and Syria."""
import re
from collections import Counter

import requests

S = requests.Session()
S.headers["User-Agent"] = "situationmonitor-probe/1.0 (https://github.com/hassmax/situationmonitor)"
API = "https://en.wikipedia.org/w/api.php"


def raw(title):
    r = S.get("https://en.wikipedia.org/w/index.php", params={"title": title, "action": "raw"}, timeout=30)
    return r.text if r.ok else ""


def search(q, ns):
    r = S.get(API, params={"action": "query", "list": "search", "srsearch": q, "srnamespace": ns, "srlimit": 15, "format": "json"}, timeout=30)
    return [x["title"] for x in r.json().get("query", {}).get("search", [])]


mods = set()
for q in ["Lebanon detailed map", "Israeli occupation Lebanon map", "Syria detailed map", "Syrian civil war detailed map",
          "Israeli invasion of Syria map", "Golan map", "Southern Lebanon map"]:
    for ns in (828, 10):
        t = search(q, ns)
        print(f"search {q!r} ns{ns}: {t}")
        mods.update(x for x in t if x.startswith("Module:") and "/doc" not in x)

for art in ["Israeli occupation of Southern Lebanon (2026)", "2026 Lebanon war", "Israeli invasion of Syria (2024–present)",
            "Israeli invasion of Syria (2024%E2%80%93present)", "Syrian civil war"]:
    txt = raw(art)
    print(f"\n== article {art}: {len(txt)} chars")
    for m in sorted(set(re.findall(r"\{\{\s*([^|{}\n]*(?:map|Map)[^|{}\n]*)", txt)))[:30]:
        print("   template:", m)
    for m in sorted(set(re.findall(r"Module:[^|\]}\n]+", txt)))[:20]:
        print("   module:", m)
        mods.add(m.strip())
    for m in sorted(set(re.findall(r"\[\[(?:File|Image):([^|\]]+)", txt)))[:30]:
        print("   file:", m)
    for line in txt.splitlines():
        if re.search(r"legend|Location dot|map-circle|Israeli[- ]held|Israeli control", line, re.I) and len(line) < 300:
            print("   line:", line.strip()[:250])

for m in sorted(mods):
    txt = raw(m)
    marks = Counter(re.findall(r"mark\s*=\s*\"?([^\",}\n]+)", txt))
    rev = S.get(API, params={"action": "query", "prop": "revisions", "titles": m, "rvprop": "timestamp", "format": "json"}, timeout=30).json()
    ts = [r.get("timestamp") for p in rev.get("query", {}).get("pages", {}).values() for r in p.get("revisions", [])]
    print(f"\n== {m}: {len(txt)} chars, last edit {ts}, labels {len(re.findall(r'label', txt))}")
    for k, n in marks.most_common(25):
        print(f"   {n:5d} {k}")
    print("   head:", txt[:600].replace("\n", " | "))
    for name in ["Khiam", "Maroun", "Naqoura", "Bint Jbeil", "Quneitra", "Hader", "Jubata", "Hermon", "Kfarchouba", "Shebaa"]:
        i = txt.find(name)
        if i >= 0:
            print(f"   {name}: {txt[max(0, i - 200):i + 200]!r}")
