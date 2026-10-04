"""Temporary: which OSINT accounts and aggregators can be read for free from GitHub's servers."""
import re, time, json, requests, feedparser
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
s = requests.Session(); s.headers.update(UA)

def get(url, **kw):
    try:
        r = s.get(url, timeout=20, **kw); return r
    except Exception as e:
        class R: status_code = 0; text = str(e)[:100]
        return R()

print("== Telegram public previews (t.me/s)")
for name in ["OSINTtechnical", "osinttechnical", "Osinttechnical_official", "AuroraIntel", "FaytuksNetwork", "Faytuks", "IntelSky", "bnonews", "OSINTdefender", "osintdefender",
             "ClashReport", "clashreport", "sentdefender", "Global_Mil_Info", "ELINTNews", "Intel_Republic", "intelslava", "MilitaryAviation", "CIG_telegram",
             "nexta_live", "TheWarZone", "aviationist", "Itamilradar", "TWZ", "warmonitors", "Middle_East_Spectator", "MiddleEastSpectator", "IranIntl_En",
             "manniefabian", "QudsNen", "Megatron_Ron", "spectatorindex", "Spectatorindex", "conflict_news", "ConflictNews", "visegrad24", "noelreports", "Flash43191300",
             "CallsignSpotters", "AirLiveNet", "airlivenet", "IntelPointAlert", "Treadstone71", "the_military_watch", "GeoConfirmed", "geoconfirmed", "UAWeapons", "OSINTua"]:
    r = get(f"https://t.me/s/{name}")
    posts = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', r.text or "", re.S)
    times = re.findall(r'<time datetime="([^"]+)"', r.text or "")
    title = re.search(r'<meta property="og:title" content="([^"]*)"', r.text or "")
    print(f"{name:24} {r.status_code} posts={len(posts):2} last={times[-1][:16] if times else '-'} title={title.group(1)[:40] if title else '-'}")
    if posts and name in ("OSINTtechnical", "osinttechnical", "AuroraIntel", "FaytuksNetwork", "IntelSky", "OSINTdefender", "ClashReport", "sentdefender"):
        print("   last:", re.sub("<[^>]+>", " ", posts[-1])[:160])
    time.sleep(0.4)

print("== Bluesky")
for h in ["osinttechnical.bsky.social", "osinttechnical.com", "osintdefender.bsky.social", "auroraintel.bsky.social", "faytuks.bsky.social", "sentdefender.bsky.social",
          "clashreport.bsky.social", "intelsky.bsky.social", "thewarzone.bsky.social", "twz.com", "theaviationist.bsky.social", "aircraftspots.bsky.social",
          "itamilradar.bsky.social", "geoconfirmed.bsky.social", "noelreports.bsky.social", "visegrad24.bsky.social", "bnonews.com", "bnonews.bsky.social",
          "warmonitor.bsky.social", "osint613.bsky.social", "michaelkofman.bsky.social", "ralee85.bsky.social", "jimlaurie.bsky.social", "sentinelnow.bsky.social"]:
    r = get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed", params={"actor": h, "limit": 3})
    if r.status_code == 200:
        f = r.json().get("feed", [])
        last = f[0]["post"]["record"].get("createdAt", "")[:16] if f else "-"
        txt = f[0]["post"]["record"].get("text", "")[:100].replace("\n", " ") if f else ""
        print(f"{h:32} 200 last={last} {txt}")
    else:
        print(f"{h:32} {r.status_code} {(r.text or '')[:80]}")
for q in ["RAF Fairford", "B-1B", "osint"]:
    for host in ["public.api.bsky.app", "api.bsky.app"]:
        r = get(f"https://{host}/xrpc/app.bsky.feed.searchPosts", params={"q": q, "limit": 5, "sort": "latest"})
        print(f"search {q!r} on {host}: {r.status_code} {len(r.json().get('posts', [])) if r.status_code == 200 else (r.text or '')[:80]}")
r = get("https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors", params={"q": "osint", "limit": 25})
print("searchActors osint:", r.status_code, [a["handle"] for a in r.json().get("actors", [])] if r.status_code == 200 else r.text[:80])

print("== RSS aggregators")
for url in ["https://theaviationist.com/feed/", "https://www.twz.com/feed", "https://www.forcesnews.com/rss.xml", "https://www.forces.net/rss.xml", "https://www.airandspaceforces.com/feed/",
            "https://ukdefencejournal.org.uk/feed/", "https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml", "https://breakingdefense.com/feed/",
            "https://defence-blog.com/feed/", "https://www.stripes.com/arc/outboundfeeds/rss/?outputType=xml", "https://www.janes.com/feeds/news", "https://simpleflying.com/feed/",
            "https://www.thedrive.com/the-war-zone/feed", "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "https://www.flightglobal.com/rss",
            "https://www.airforce-technology.com/feed/", "https://avherald.com/h?subscribe=rss", "https://www.gloucestershirelive.co.uk/news/?service=rss",
            "https://bnonews.com/index.php/feed/", "https://www.understandingwar.org/rss.xml"]:
    r = get(url)
    f = feedparser.parse(r.text if r.status_code == 200 else "")
    hits = [e.title for e in f.entries if re.search(r"bomber|B-1|B-52|Fairford|Lakenheath|Mildenhall|tanker|F-35|redeploy", e.get("title", ""), re.I)]
    print(f"{r.status_code} entries={len(f.entries):3} {url[:60]:60} {hits[:2]}")

print("== Google News search for the post's story")
for q in ['"RAF Fairford" when:2d', '"B-1" bombers depart when:2d', 'bombers (depart OR leave OR arrive) (RAF OR "air base") when:2d']:
    r = get("https://news.google.com/rss/search", params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    f = feedparser.parse(r.text)
    print(q, r.status_code, len(f.entries), [e.title[:90] for e in f.entries[:5]])

print("== X bridges (only to know; not to be used without permission)")
for url in ["https://nitter.net/Osinttechnical/rss", "https://xcancel.com/Osinttechnical/rss", "https://nitter.privacydev.net/Osinttechnical/rss"]:
    r = get(url); print(url, r.status_code, len(feedparser.parse(r.text if r.status_code == 200 else "").entries))
