"""Temporary probe (removed before the PR): candidate social sources and free Sentinel-2 imagery."""
import base64, io, json, os, re, sys, time
import requests
sys.path.insert(0, "pipeline")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
OUT = "probe-out"
os.makedirs(OUT, exist_ok=True)
step = sys.argv[1] if len(sys.argv) > 1 else "all"

if step in ("all", "social"):
    B = "https://api.bsky.app/xrpc/"
    queries = ["geoconfirmed", "geolocation", "geolocated", "war mapper", "defmon", "control map", "suriyak", "moklasen",
               "jompy", "clement molin", "andrew perpetua", "tatarigami", "kastehelmi", "black bird group", "deepstate",
               "militaryland", "frontline map", "ukraine war map", "osint ukraine", "sudan war", "myanmar war", "rakhine",
               "m23 congo", "yemen war", "sahel conflict", "somalia conflict", "noel reports", "ukraine battle map", "osint"]
    seen = {}
    for q in queries:
        r = requests.get(B + "app.bsky.actor.searchActors", params={"q": q, "limit": 8}, timeout=20)
        if r.status_code != 200:
            print("BSKY search", q, r.status_code, r.text[:100]); continue
        for a in r.json().get("actors", []):
            seen[a["did"]] = q
        time.sleep(0.3)
    dids = list(seen)
    profiles = []
    for i in range(0, len(dids), 25):
        r = requests.get(B + "app.bsky.actor.getProfiles", params=[("actors", d) for d in dids[i:i + 25]], timeout=20)
        profiles += r.json().get("profiles", []) if r.ok else []
    for p in sorted(profiles, key=lambda p: -p.get("followersCount", 0)):
        if p.get("followersCount", 0) < 1500:
            continue
        f = requests.get(B + "app.bsky.feed.getAuthorFeed", params={"actor": p["did"], "limit": 10, "filter": "posts_no_replies"}, timeout=20)
        feed = f.json().get("feed", []) if f.ok else []
        own = [x for x in feed if x["post"]["author"]["did"] == p["did"]]
        last = own[0]["post"]["record"].get("createdAt", "")[:16] if own else "-"
        geo = sum(1 for x in own if re.search(r"geolocat|geoconfirm|📍", x["post"]["record"].get("text", ""), re.I))
        print(f"BSKY {p['handle']} | {p.get('displayName')} | {p.get('followersCount')} fol | {p.get('postsCount')} posts | last {last} | "
              f"geo {geo}/{len(own)} | q={seen[p['did']]!r} | {(p.get('description') or '')[:80]!r}")
        time.sleep(0.2)

    chans = ["DeepStateUA", "dva_majors", "milinfolive", "wargonzo", "boris_rozhin", "yurasumy", "voenacher", "notes_veterans",
             "RVvoenkor", "sashakots", "rusich_army", "GeneralStaffZSU", "operativnoZSU", "exilenova_plus", "ab3army",
             "Tsaplienko", "geoconfirmed", "GeoConfirmed", "warmapper", "militarysummary", "intelslava", "RSFSudan", "rsf_sudan",
             "RSF_Sudan", "SudanTribune", "almasirah", "AlMasirahNet", "Sudan_War_Monitor", "sudanwarmonitor", "ukraine_map",
             "mapukraine", "Militarnyi", "militarnyi", "kpszsu", "ShtefanMedia", "ArakanArmy", "nugmyanmar", "M23_ARC", "DefenceU",
             "ukrinform_news", "Tatarigami", "MAKS23_NAFO", "war_monitor", "warmonitors", "UkraineNow", "dronbomber", "chicken_kyiv"]
    for c in chans:
        try:
            r = requests.get(f"https://t.me/s/{c}", headers=UA, timeout=20)
        except Exception as exc:
            print("TG", c, "error", exc); continue
        title = re.search(r'<meta property="og:title" content="([^"]*)"', r.text)
        times = re.findall(r'<time datetime="([^"]+)"', r.text)
        subs = re.search(r'tgme_header_counter">([^<]+)<', r.text) or re.search(r'counter_value">([^<]+)</span>\s*<span class="counter_type">subscribers', r.text)
        posts = len(re.findall(r'tgme_widget_message_text', r.text))
        print(f"TG {c} | {r.status_code} | {title.group(1) if title else '-'} | subs {subs.group(1) if subs else '-'} | last {times[-1][:16] if times else '-'} | {posts} text posts on page")
        time.sleep(0.5)

if step in ("all", "imagery"):
    from PIL import Image
    towns = {"pokrovsk": (48.282, 37.176), "hulyaipole": (47.663, 36.256), "taiz": (13.579, 44.021)}
    PC = "https://planetarycomputer.microsoft.com/api"

    def search(url, lat, lon, when, cloud=40):
        body = {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
                "datetime": when, "limit": 20, "query": {"eo:cloud_cover": {"lt": cloud}},
                "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
        r = requests.post(url, json=body, timeout=40)
        if r.status_code != 200:
            body.pop("sortby")
            r = requests.post(url, json=body, timeout=40)
        print("  search", url.split("/")[2], when, r.status_code, len(r.json().get("features", [])) if r.ok else r.text[:200])
        return r.json().get("features", []) if r.ok else []

    for name, (lat, lon) in towns.items():
        print("TOWN", name)
        bbox = (lon - 0.045, lat - 0.03, lon + 0.045, lat + 0.03)
        for label, when in (("after", "2026-09-15T00:00:00Z/2026-10-05T00:00:00Z"), ("before", "2026-07-15T00:00:00Z/2026-08-31T00:00:00Z")):
            items = search(f"{PC}/stac/v1/search", lat, lon, when)
            for it in items[:4]:
                print("   PC item", it["id"], it["properties"].get("datetime"), "cloud", it["properties"].get("eo:cloud_cover"))
            got = None
            if items:
                it = items[0]
                b = ",".join(f"{v:.5f}" for v in bbox)
                tries = [f"{PC}/data/v1/item/crop/{b}.png?collection=sentinel-2-l2a&item={it['id']}&assets=visual&asset_bidx=visual|1,2,3&nodata=0&max_size=640",
                         f"{PC}/data/v1/item/bbox/{b}.png?collection=sentinel-2-l2a&item={it['id']}&assets=visual&asset_bidx=visual|1,2,3&nodata=0&max_size=640",
                         f"{PC}/data/v1/item/crop/{b}.png?collection=sentinel-2-l2a&item={it['id']}&assets=B04&assets=B03&assets=B02&rescale=0,3000&nodata=0&max_size=640"]
                for u in tries:
                    r = requests.get(u, timeout=60)
                    print("   data api", u.split("/data/v1/")[1][:40], r.status_code, r.headers.get("content-type"), len(r.content), r.text[:120] if r.status_code != 200 else "")
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                        got = r.content
                        break
            if got:
                path = f"{OUT}/{name}-{label}-pc.png"
                open(path, "wb").write(got)
                im = Image.open(io.BytesIO(got))
                print("   saved", path, im.size, "date", items[0]["properties"].get("datetime"))
            # Earth Search (AWS) as a second source
            es = search("https://earth-search.aws.element84.com/v1/search", lat, lon, when)
            for it in es[:3]:
                print("   ES item", it["id"], it["properties"].get("datetime"), "cloud", it["properties"].get("eo:cloud_cover"),
                      "visual", (it["assets"].get("visual") or {}).get("href", "")[:90])
            if es and not got:
                try:
                    import rasterio
                    from rasterio.warp import transform_bounds
                    from rasterio.windows import from_bounds
                    href = es[0]["assets"]["visual"]["href"]
                    t0 = time.time()
                    with rasterio.open(href) as src:
                        bb = transform_bounds("EPSG:4326", src.crs, *bbox)
                        win = from_bounds(*bb, transform=src.transform)
                        arr = src.read([1, 2, 3], window=win, out_shape=(3, 640, 640))
                    Image.fromarray(arr.transpose(1, 2, 0)).save(f"{OUT}/{name}-{label}-es.png")
                    print("   rasterio window read ok in", round(time.time() - t0, 1), "s", arr.shape)
                except Exception as exc:
                    print("   rasterio failed:", exc)

if step in ("all", "compare"):
    import extract, config
    from datetime import datetime, timezone
    from common import load_json
    state = load_json("state/state.json", {})
    settings = config.load().settings
    now = datetime.now(timezone.utc)
    PROMPT = open("probe_prompt.txt").read()
    for name in ("pokrovsk", "hulyaipole", "taiz"):
        imgs = []
        for label in ("before", "after"):
            for src in ("pc", "es"):
                p = f"{OUT}/{name}-{label}-{src}.png"
                if os.path.exists(p):
                    imgs.append("data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode())
                    break
        if len(imgs) != 2:
            print("COMPARE", name, "missing images"); continue
        text = json.dumps({"settlement": name, "box_km": 6.6, "first_image": "before (late July to August)", "second_image": "after (late September)"})
        reply = extract.ask_json(PROMPT, text, state, settings, now, max_tokens=2000, purpose="probe", images=imgs)
        print("COMPARE", name, json.dumps(reply, ensure_ascii=False)[:900])
