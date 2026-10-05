"""Imagery agent: before-and-after satellite pictures of a disputed settlement, compared by the model
(added 2026-10-05 at the owner's request for "satellite analysis by our own agents"; the heat agent
only counts fire detections). Black Bird Group and ISW lean on commercial imagery at under a metre;
the free equivalent is the European Space Agency's Sentinel-2: true colour at 10 m a pixel, every
few days. At that scale single vehicles, people and flags can't be seen, but a town's destruction,
burn scars, dense cratering and new trench or ditch lines can.

For up to PER_RUN settlements a run (changes waiting for the reviewer first, then places shown as
claimed or contested, newest evidence first; each at most every EVERY), a BOX_KM-square picture is
cut from the clearest pass of the last AFTER_DAYS and from one BEFORE_GAP earlier, both from
Microsoft's Planetary Computer (STAC search and its picture service; free, no key; Copernicus
Sentinel data, open licence). A crop more than CLOUDY bright (clouds) is passed over for the next
clearest pass. The model (Gemini: the outside providers can't read images; purpose
"frontline_imagery", share frontline_imagery_daily_max) lists the changes it can see, or says the
pair can't be compared. The result goes to the reviewer as "satellite_imagery", with the rule that
pictures show physical change, never who holds a place, and that nothing is confirmed or rejected
on imagery alone (review.py). No pictures are published or stored, only the model's description
(state["frontline"]["imagery"]).

Trial 2026-10-05 on GitHub's servers: Hulyaipole and Taiz (August vs late September) came back
"no large changes visible"; a Pokrovsk pair half covered by cloud came back "unusable".
"""
from __future__ import annotations

import base64
import io
import json
import math
from datetime import timedelta

from common import iso, log, parse_time

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
CROP = ("https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/{bbox}.png"
        "?collection=sentinel-2-l2a&item={item}&assets=visual&asset_bidx=visual|1,2,3&nodata=0&max_size={size}")
CREDIT = "Copernicus Sentinel-2 data, via Microsoft Planetary Computer"
PER_RUN = 1
EVERY = timedelta(days=5)          # Sentinel-2 passes every few days: no point looking again sooner
KEEP = timedelta(days=30)          # descriptions older than this are dropped
FRESH = timedelta(days=20)         # the reviewer is shown descriptions no older than this
BOX_KM = 6
SIZE = 640                         # pixels across: about 10 m a pixel for BOX_KM 6
AFTER_DAYS = 14
BEFORE_GAP = (25, 70)              # days before the "after" picture
MAX_SCENE_CLOUD = 40               # percent, for the whole 110 km scene
CLOUDY = 0.05                      # share of near-white pixels in the crop above which it is passed over
TRIES = 3                          # passes tried per window
TIMEOUT = 60

PROMPT = """You compare two Sentinel-2 satellite images (10 m per pixel, true colour) of the same area, about {km} km across, around a settlement on a front line: the first taken before, the second after. Reply with one JSON object and nothing else.
List only physical changes you can actually see between the two images: new burn scars or scorched ground, large areas of destroyed or damaged buildings, dense new cratering in fields, new earthworks, trench or anti-tank ditch lines, flooding. At 10 m per pixel single vehicles, people and flags cannot be seen. Say if clouds, haze, snow or seasonal change (harvested fields, autumn colour) make a comparison unreliable, and do not report seasonal change as damage.
Never say who controls the settlement or who caused a change: imagery cannot show that.
JSON: {{"usable": true|false, "why_not": "..." or null, "changes": [{{"what": "...", "where": "...", "confidence": "low"|"medium"|"high"}}], "summary": "one sentence, max 30 words"}}"""


def _store(fl: dict) -> dict:
    return fl.setdefault("imagery", {})


def _bbox(lat: float, lon: float, km: float = BOX_KM) -> tuple:
    dlat = km / 2 / 111.0
    dlon = km / 2 / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def candidates(fl: dict, pending: list[str], now) -> list[str]:
    """Settlements to look at: changes waiting for the reviewer, then places shown as claimed or
    contested (newest evidence first); placed, and not looked at within EVERY."""
    done = _store(fl)

    def due(k):
        p = fl["places"].get(k)
        last = parse_time((done.get(k) or {}).get("checked"))
        return p and p.get("lat") is not None and (not last or now - last >= EVERY)

    first = [k for k in pending if due(k)]
    disputed = sorted((k for k, p in fl["places"].items()
                       if (p.get("published") or {}).get("status") in ("claimed", "contested") and k not in first and due(k)),
                      key=lambda k: fl["places"][k]["claims"][-1]["time"] if fl["places"][k].get("claims") else "", reverse=True)
    return first + disputed


def _search(session, lat: float, lon: float, start, end) -> list[dict]:
    body = {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{iso(start)}/{iso(end)}", "limit": 20, "query": {"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}}}
    r = session.post(STAC, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("features") or []
    return sorted(items, key=lambda it: (it["properties"].get("eo:cloud_cover") or 100, it["properties"].get("datetime") or ""))


def cloudy(png: bytes) -> float:
    """Share of near-white pixels (cloud), and of no-data black (the edge of a pass), in a crop."""
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((160, 160))
    raw = im.tobytes()
    px = [raw[i:i + 3] for i in range(0, len(raw), 3)]
    bad = sum(1 for r, g, b in px if (r > 200 and g > 200 and b > 200) or (r < 3 and g < 3 and b < 3))
    return bad / max(1, len(px))


def _clear(session, items: list[dict], bbox: tuple):
    """The first of the clearest passes whose crop isn't clouded: (item, png) or (None, None)."""
    b = ",".join(f"{v:.5f}" for v in bbox)
    for it in items[:TRIES]:
        r = session.get(CROP.format(bbox=b, item=it["id"], size=SIZE), timeout=TIMEOUT)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            continue
        if cloudy(r.content) <= CLOUDY:
            return it, r.content
    return None, None


def _clean(got) -> dict | None:
    if not isinstance(got, dict) or not isinstance(got.get("usable"), bool):
        return None
    changes = []
    for c in got.get("changes") or []:
        if isinstance(c, dict) and c.get("what"):
            changes.append({"what": str(c["what"])[:120], "where": str(c.get("where") or "")[:80],
                            "confidence": c.get("confidence") if c.get("confidence") in ("low", "medium", "high") else "low"})
    return {"usable": got["usable"], "why_not": str(got.get("why_not") or "")[:160] or None,
            "changes": changes[:6], "summary": str(got.get("summary") or "")[:240]}


def look(p: dict, session, state: dict, settings: dict, now, ask) -> dict:
    """Search, cut and compare one settlement's pictures; returns the record to store."""
    rec = {"checked": iso(now), "source": CREDIT}
    bbox = _bbox(p["lat"], p["lon"])
    after_item, after = _clear(session, _search(session, p["lat"], p["lon"], now - timedelta(days=AFTER_DAYS), now), bbox)
    if not after_item:
        return {**rec, "none": f"no clear picture in the last {AFTER_DAYS} days"}
    taken = parse_time(after_item["properties"]["datetime"]) or now
    before_item, before = _clear(session, _search(session, p["lat"], p["lon"], taken - timedelta(days=BEFORE_GAP[1]),
                                                  taken - timedelta(days=BEFORE_GAP[0])), bbox)
    if not before_item:
        return {**rec, "none": "no clear earlier picture to compare with"}
    rec.update(before=before_item["properties"]["datetime"][:10], after=after_item["properties"]["datetime"][:10])
    text = json.dumps({"settlement": p["name"], "first_image": rec["before"], "second_image": rec["after"]}, ensure_ascii=False)
    images = ["data:image/png;base64," + base64.b64encode(x).decode() for x in (before, after)]
    got = _clean(ask(PROMPT.format(km=BOX_KM), text, state, settings, now, max_tokens=2000, purpose="frontline_imagery", images=images))
    if got is None:
        return {**rec, "none": "the model gave no usable answer", "retry": True}
    return {**rec, **got}


def run(conflicts: list[dict], fl: dict, state: dict, settings: dict, session, now, ask, budget: int, pending: list[str]) -> int:
    """Look at up to PER_RUN settlements (at most `budget` model calls). Returns how many were compared."""
    store = _store(fl)
    for k in [k for k, v in store.items() if not parse_time(v.get("checked")) or now - parse_time(v["checked"]) > KEEP or k not in fl["places"]]:
        del store[k]
    if budget <= 0:
        return 0
    done = 0
    for k in candidates(fl, pending, now)[:min(PER_RUN, budget)]:
        p = fl["places"][k]
        try:
            rec = look(p, session, state, settings, now, ask)
        except Exception as exc:  # noqa: BLE001 - the picture service is down or slow: try again next run
            log(f"[frontline] imagery: {p['name']}: {exc}")
            break
        if rec.get("retry"):
            log(f"[frontline] imagery: {p['name']}: {rec['none']}")
            break  # keep it due, without a record
        store[k] = rec
        if rec.get("none"):
            log(f"[frontline] imagery: {p['name']}: {rec['none']}")
            continue
        done += 1
        what = "; ".join(c["what"] for c in rec["changes"]) or "no visible change"
        log(f"[frontline] imagery: {p['name']} {rec['before']} -> {rec['after']}: "
            f"{'usable' if rec['usable'] else 'unusable (' + (rec.get('why_not') or '') + ')'}; {what}")
    return done


def near(fl: dict, k: str, now) -> dict | None:
    """What the reviewer sees for one settlement, if pictures were compared within FRESH."""
    rec = (fl.get("imagery") or {}).get(k)
    if not rec or rec.get("none") or not parse_time(rec.get("checked")) or now - parse_time(rec["checked"]) > FRESH:
        return None
    return {"before": rec["before"], "after": rec["after"], "usable": rec["usable"], "why_not": rec.get("why_not"),
            "changes": rec["changes"], "summary": rec["summary"]}
