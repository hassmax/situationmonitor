"""Temporary: which flight-tracking sources answer GitHub's servers, what they return, and their terms."""
import collections, re, time, requests
s = requests.Session(); s.headers["User-Agent"] = "GlobalSituationMonitor/1.0 (+https://github.com/hassmax/situationmonitor)"

def get(url, **kw):
    try:
        return s.get(url, timeout=30, **kw)
    except Exception as e:
        class R: status_code = 0; text = str(e)[:120]; content = b""
        return R()

def region(lat, lon):
    if lat is None: return "no position"
    if 24 < lat < 50 and -125 < lon < -66: return "US mainland"
    if 34 < lat < 72 and -25 < lon < 45: return "Europe"
    if 12 < lat < 42 and 25 < lon < 63: return "Middle East"
    if -10 < lat < 50 and 63 <= lon < 150: return "Asia/Pacific"
    return "other"

for name, url in [("adsb.lol mil", "https://api.adsb.lol/v2/mil"), ("airplanes.live mil", "https://api.airplanes.live/v2/mil"),
                  ("adsb.one mil", "https://api.adsb.one/v2/mil"), ("adsb.fi mil", "https://opendata.adsb.fi/api/v2/mil")]:
    r = get(url)
    print(f"== {name}: HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print("   ", r.text[:200]); continue
    try:
        j = r.json()
    except Exception as e:
        print("   not json", r.text[:200]); continue
    ac = j.get("ac") or j.get("aircraft") or []
    print("   keys:", sorted(j.keys())[:12], "aircraft:", len(ac), "now:", j.get("now"))
    if ac:
        print("   sample:", {k: ac[0].get(k) for k in ("hex", "flight", "r", "t", "desc", "ownOp", "lat", "lon", "alt_baro", "gs", "track", "seen_pos", "dbFlags", "type", "squawk")})
    types = collections.Counter(a.get("t") for a in ac)
    print("   types:", types.most_common(45))
    reg = collections.Counter(region(a.get("lat"), a.get("lon")) for a in ac)
    print("   regions:", reg.most_common())
    for want in ("B52", "B1", "B2", "K35R", "KC46", "R135", "E3TF", "E3CF", "P8", "Q4", "C17", "C5M", "E6", "B742", "TU95", "T160", "IL20", "A332", "U2"):
        hits = [a for a in ac if a.get("t") == want]
        if hits:
            print(f"   {want}: {len(hits)}", [(a.get("flight", "").strip(), a.get("r"), region(a.get("lat"), a.get("lon")), a.get("ownOp")) for a in hits[:6]])
    time.sleep(1.5)

for name, url in [("adsb.lol sqk 7500", "https://api.adsb.lol/v2/sqk/7500"), ("adsb.lol sqk 7700", "https://api.adsb.lol/v2/sqk/7700"),
                  ("adsb.lol point Fairford 50nm", "https://api.adsb.lol/v2/point/51.68/-1.79/50"), ("adsb.lol callsign RCH", "https://api.adsb.lol/v2/callsign/RCH"),
                  ("airplanes.live point Fairford", "https://api.airplanes.live/v2/point/51.68/-1.79/50")]:
    r = get(url)
    try:
        ac = r.json().get("ac") or []
    except Exception:
        ac = []
    print(f"== {name}: HTTP {r.status_code} aircraft={len(ac)}", [(a.get("flight", "").strip(), a.get("t"), a.get("dbFlags")) for a in ac[:8]])
    time.sleep(1.5)

print("== OpenSky")
r = get("https://opensky-network.org/api/states/all", params={"lamin": 45, "lomin": 30, "lamax": 50, "lomax": 40})
print("states box (anonymous):", r.status_code, len(r.content), (r.text or "")[:150])
print("rate-limit headers:", {k: v for k, v in getattr(r, "headers", {}).items() if "rate" in k.lower() or "limit" in k.lower()})

print("== Terms")
for name, url, words in [("adsb.lol", "https://www.adsb.lol/docs/open-data/api/", r"ODbL|licen[cs]e|attribut|commercial"),
                         ("adsb.lol home", "https://www.adsb.lol/", r"ODbL|licen[cs]e|open data"),
                         ("airplanes.live api", "https://airplanes.live/api-guide/", r"commercial|licen[cs]e|attribut|terms|rate"),
                         ("airplanes.live terms", "https://airplanes.live/terms-of-service/", r"commercial|licen[cs]e|attribut|redistribut|publish"),
                         ("opensky terms", "https://opensky-network.org/about/terms-of-use", r"commercial|licen[cs]e|attribut|redistribut|publish|research"),
                         ("flightradar24 terms", "https://www.flightradar24.com/terms-and-conditions", r"scrap|automat|commercial|reproduc|redistribut")]:
    r = get(url)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text or ""))
    print(f"-- {name}: HTTP {r.status_code}")
    seen = 0
    for m in re.finditer(words, text, re.I):
        snippet = text[max(0, m.start() - 160): m.end() + 200]
        print("   ...", snippet)
        seen += 1
        if seen >= 5: break
