"""Same-story check: fold events that are one incident reported in different words, places or kinds.

The merge rules join reports by place, time, and wording. Some stories still end up as several
events, and on 2026-10-06 the map was full of them, mostly because the story was filed under
different kinds of event: one Israeli strike in Gaza as an airstrike, a missile attack and shelling;
Kim Jong Un's AI missile test as a missile launch, arms production and an arms transfer; the US
pulling its B-1 bombers out of RAF Fairford as ten arms transfers, deployments and diplomacy
events; one UKMTO tanker report as a naval incident and a missile attack. The check that ran until
then compared only events of one kind in one country, left arms transfers and production out, and
had judged 38,000 pairs while those were never shown to it.

So the check is built around each event and the few events most like it:

- Candidates: for every recent event, the CANDIDATES events most similar to it in wording (TF-IDF
  over the summaries: rare words such as "Aselsan", "B-1B" or "$2.27 billion" weigh most), of any
  kind, within PAIR_WINDOW of it: in the same country, or at sea within SEA_KM of each other, or
  among statements and deals naming a party in common, from NEAR_FLOOR similarity; anywhere from
  FAR_FLOOR (one cockpit attack on a flydubai flight was pinned to four countries); never strikes,
  fighting or incidents at sea farther apart than INCIDENT_KM unless one is pinned only to a whole
  country or sea. Alert groups
  have their own grouping and are left out; an attack wave can take in others but two waves are
  never folded together.
- Near-identical reports of things other than strikes and fighting ("All 12 U.S. B-1B Lancer
  bombers have left RAF Fairford" and "... have departed RAF Fairford"), at AUTO similarity or more,
  within AUTO_HOURS and giving no different figures, are folded without asking.
- The rest are asked: each event with its candidates not judged yet, CASES_PER_CALL a call, newest
  events first; the model lists which candidates describe the same specific incident, names the
  kind that fits it best (of the kinds the events were filed under), and writes one combined
  headline. Each pair is asked once; a "different" for a close pair (SECOND_LOOK_MIN) gets one
  second look after SECOND_LOOK, since a busy preferred model means a weaker backup answers.
- Late follow-ups: events from the last NEW_HOURS are also compared with older ones, up to LATE_DAYS
  back (working set and archive), from LATE_FLOOR similarity: folded into the working-set event
  (it keeps its first date), or, when the older one is only in the archive, the new event takes
  its date, so an old story isn't shown as new.
- Every run while questions wait (RETRY after a failure), up to MAX_CALLS_PER_RUN calls, within the paced daily share (`share`), never below dedupe_min_calls
  calls left today (extraction keeps priority), the extra call only with EXTRA_CALLS_FLOOR left.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import timedelta

from common import iso, log, parse_time
from merge import FAMILY, _absorb

VERSION = 2              # 2: each event with its most similar events, of any kind (2026-10-06)
RETRY = timedelta(minutes=15)
LOOKBACK = timedelta(hours=72)
PAIR_WINDOW = timedelta(hours=48)
SECOND_LOOK = timedelta(hours=6)
SECOND_LOOK_MIN = 0.6   # a "different" for a pair this similar gets a second look
CANDIDATES = 4          # most similar events shown with each event
NEAR_FLOOR = 0.25       # similarity for a candidate in the same country, nearby at sea, or with a shared party
FAR_FLOOR = 0.5         # ... anywhere else
LATE_FLOOR = 0.45       # ... for an older event (late follow-ups), which must also be near
SEA_KM = 800            # events at sea (no country) this close together are near
AUTO = 0.8              # folded without asking: near-identical wording ...
AUTO_HOURS = 36         # ... this close in time, and not strikes or fighting
CASES_PER_CALL = 15
MAX_CALLS_PER_RUN = 2
EXTRA_CALLS_FLOOR = 100  # the second call in one run only while this many calls are left today
NEW_HOURS = 36           # events this recent are compared with older ones (late follow-ups)
LATE_DAYS = 14           # how far back
KEEP_DAYS = 7
VIOLENCE = {"strike", "ground"}   # never folded without the model: a city at war has many strikes a day
# Incidents happen in one place: strikes and fighting this far apart, or incidents at sea, are
# different ones unless either is pinned only to a whole country or sea (approximate). A trial on
# 2026-10-06 joined the Ukrainian air force's guided-bomb reports for Dnipropetrovsk, Chernihiv and
# Kharkiv, and an attack off Yemen with one in the Strait of Hormuz.
INCIDENT_KM = {"strike": 150, "ground": 150, "naval": 600}
STATEMENTS = {"diplomacy", "legal", "hybrid", "deployment", "transfer", "production"}

PROMPT = """You check a live conflict map for duplicates. Each case is one event from the map and a few candidate events that look like it, each with an id, its kind, summary, place and time. Events are often filed under different kinds and places by different outlets: one strike as an airstrike, a missile attack and shelling; a missile test as arms production; an arms sale as diplomacy; a city, the base it is about, the capital that spoke, or the whole country. The kind and the place don't decide it.

For each case, list the candidates that describe the same specific incident or statement as the event: the same strike, arrests, seizure, test, exercise, announcement, deal, meeting, visit or call, vote, filing or ruling, including follow-up coverage of it over the following days (new details, reactions, denials, a rising death toll).
Leave out candidates that are separate incidents that resemble it (two strikes on the same city on the same day, two drills, two meetings between the same countries), that are only background to it, or when you are unsure. A response by another government or body is its own event.
Times matter. Reports on different days are the same incident only when they clearly describe it again (follow-up coverage of the same strike, deal or move). Many kinds of report recur and are separate incidents each time: an air force's daily report of guided bombs or drones on a region, strikes on the same front, aircraft landing at the same airport, attacks on ships in the same waters. Different regions or provinces in the summaries mean different incidents.

When you list any, also give:
- "type": the kind, of those the event and the listed candidates were filed under, that best fits what happened (a missile test is "missile_drone", not arms production; an approved arms sale is "arms_transfer"; a strike that killed people is "airstrike" or "missile_drone" over "diplomacy").
- "summary": one neutral sentence of at most 25 words stating what they agree on, with the most recent figures; only facts in the summaries, keeping attributions ("Russian MoD claims", "reportedly"); never add a number, place, or name they don't give.

Reply with one JSON object and nothing else, one entry for every case; a case with none is just {"i": <case number>, "same": []}:
{"results": [{"i": <case number>, "same": ["<candidate id>", ...], "type": "<kind>", "summary": "<one sentence>"}]}"""

_STOP = set("""the a an of in on at to for and or with by from as is are was were be been being has have had that this
these those its it his her their them they there here after over amid near into says said say claims claimed claim
reported reports report according against during about while would could will more than which who what when where
also new following other some any all not but per via within without since until under upon onto out off""".split())


def _tokens(text: str | None) -> list[str]:
    """Words and figures of a summary ("2.27", "b-1b"), plural "s" dropped, small words left out."""
    out = []
    for w in re.findall(r"\$?\d+(?:[.,]\d+)?|[a-z][a-z0-9'\-]+", (text or "").lower()):
        w = w.strip("'-$").replace(",", "")
        if not w or w in _STOP or (len(w) < 3 and not w[0].isdigit()):
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _numbers(text: str | None) -> set[str]:
    return {t for t in _tokens(text) if t[0].isdigit()}


def _vectors(events: list[dict]) -> dict[str, dict[str, float]]:
    """TF-IDF vectors of the summaries, normalised, keyed by event id."""
    toks = {e["id"]: _tokens(e.get("summary")) for e in events}
    df = Counter(w for ws in toks.values() for w in set(ws))
    n = max(1, len(toks))
    out = {}
    for i, ws in toks.items():
        tf = Counter(ws)
        v = {w: (1 + math.log(c)) * (1 + math.log(n / df[w])) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[i] = {w: x / norm for w, x in v.items()}
    return out


def _cos(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _km(e: dict, f: dict) -> float:
    if e.get("lat") is None or f.get("lat") is None:
        return float("inf")
    kx = 111.32 * math.cos(math.radians((e["lat"] + f["lat"]) / 2))
    return math.hypot((e["lon"] - f["lon"]) * kx, (e["lat"] - f["lat"]) * 110.57)


def _family(e: dict) -> str:
    return FAMILY.get(e.get("type"), e.get("type") or "")


def near(e: dict, f: dict) -> bool:
    """Same country; at sea (no country) and close; or statements and deals with a party in common."""
    if e.get("country") and e.get("country") == f.get("country"):
        return True
    if not e.get("country") or not f.get("country"):
        return _km(e, f) <= SEA_KM
    if _family(e) in STATEMENTS and _family(f) in STATEMENTS:
        return bool(set(e.get("parties") or []) & set(f.get("parties") or []))
    return False


def apart(e: dict, f: dict) -> bool:
    """Two incidents too far apart to be one (INCIDENT_KM), when both are pinned to a place."""
    limits = [INCIDENT_KM[x] for x in (_family(e), _family(f)) if x in INCIDENT_KM]
    if not limits or e.get("approx") or f.get("approx"):
        return False
    return _km(e, f) > max(limits)


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _gap(e: dict, f: dict) -> timedelta:
    return abs((parse_time(e.get("time")) or parse_time(f.get("time"))) - (parse_time(f.get("time")) or parse_time(e.get("time"))))


def candidates(e: dict, pool: list[dict], vec: dict, late: bool = False) -> list[tuple[float, dict]]:
    """The events most like e (score, event), best first: within PAIR_WINDOW, or for late
    follow-ups older than that, up to LATE_DAYS."""
    out = []
    for f in pool:
        if f["id"] == e["id"] or (e.get("wave") and f.get("wave")) or f.get("alert") or apart(e, f):
            continue
        gap = _gap(e, f)
        if late:
            if gap <= PAIR_WINDOW or gap > timedelta(days=LATE_DAYS) or f["time"] > e["time"] or not near(e, f):
                continue
            floor = LATE_FLOOR
        else:
            if gap > PAIR_WINDOW:
                continue
            floor = NEAR_FLOOR if near(e, f) else FAR_FLOOR
        s = _cos(vec.get(e["id"], {}), vec.get(f["id"], {}))
        if s >= floor:
            out.append((s, f))
    out.sort(key=lambda x: -x[0])
    return out[:CANDIDATES]


def automatic(e: dict, f: dict, score: float) -> bool:
    """Near-identical reports of something other than a strike or fighting, close in time, giving
    no different figures: folded without asking."""
    if score < AUTO or e.get("wave") or f.get("wave") or _gap(e, f) > timedelta(hours=AUTO_HOURS):
        return False
    fa, fb = _family(e), _family(f)
    if fa in VIOLENCE or fb in VIOLENCE or not (fa == fb or (fa in STATEMENTS and fb in STATEMENTS)):
        return False
    a, b = _numbers(e.get("summary")), _numbers(f.get("summary"))
    return not a or not b or a <= b or b <= a


def _answers(v: dict | None) -> int:
    return int(v.get("n", 1)) if v else 0


def _settled(v: dict | None, score: float, now) -> bool:
    """A pair needs no (further) question: judged the same; judged different twice; judged
    different once and not close enough for a second look, or less than SECOND_LOOK ago."""
    if not v:
        return False
    if v.get("same") or _answers(v) >= 2 or score < SECOND_LOOK_MIN:
        return True
    return now - (parse_time(v.get("at")) or now) < SECOND_LOOK


def cases(events: list[dict], history: list[dict], judged: dict, now) -> tuple[list[tuple[dict, list]], list]:
    """(questions, automatic folds). A question is (event, [(score, candidate), ...]) with only the
    candidates not settled yet; events of the last day first, newest first, then the rest."""
    since = now - LOOKBACK
    live = [e for e in events if not e.get("alert") and (parse_time(e.get("time")) or since) > since]
    old = [h for h in history if not h.get("alert")]
    vec = _vectors(live + old)
    asked: set[str] = set()
    out, auto = [], []
    day = now - timedelta(hours=24)
    order = sorted(live, key=lambda e: (parse_time(e["time"]) < day, -(parse_time(e["time"]).timestamp())))
    new_since = now - timedelta(hours=NEW_HOURS)
    for e in order:
        if e.get("wave"):
            continue  # a wave is a candidate for others; its own reports are grouped by merge
        found = candidates(e, live, vec)
        if parse_time(e["time"]) >= new_since:
            found += candidates(e, live + old, vec, late=True)
        todo = []
        for s, f in found:
            k = _key(e["id"], f["id"])
            if k in asked or _settled(judged.get(k), s, now):
                continue
            asked.add(k)
            if automatic(e, f, s) and not judged.get(k):
                auto.append((e, f, s))
            else:
                todo.append((s, f))
        if todo:
            out.append((e, todo))
    return out, auto


def _fold(events: list[dict], same: list[tuple[str, str]], skip: set[str], history: list[dict] | None = None,
          kinds: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Fold events judged the same: into an attack wave if one is among them, else the earliest.
    An event judged the same as an archived one (no longer in the working set) takes the archived
    event's date instead: the story is old. `kinds`: the kind chosen for a folded set, by member id."""
    archived = {h["id"]: h for h in history or []}
    by_id = {e["id"]: e for e in events}
    for a, b in same:
        old, new = (a, b) if a in archived else (b, a) if b in archived else (None, None)
        if old and new in by_id and by_id[new]["time"] > archived[old]["time"]:
            log(f"[dedupe] {by_id[new]['summary'][:70]!r} is a late report of {archived[old]['summary'][:70]!r} "
                f"({archived[old]['time'][:10]}); dated to then")
            by_id[new]["time"] = archived[old]["time"]
    parent = {i: i for i in by_id}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def rank(i):
        return (not by_id[i].get("wave"), by_id[i]["time"], i)

    for a, b in same:
        if a in by_id and b in by_id and a not in skip and b not in skip:
            ra, rb = root(a), root(b)
            if ra != rb and not (by_id[ra].get("wave") and by_id[rb].get("wave")):
                first, second = sorted((ra, rb), key=rank)
                parent[second] = first
    folded = []
    for i, e in by_id.items():
        r = root(i)
        if r == i:
            continue
        keep = by_id[r]
        urls = {x["url"] for x in keep["reports"]}
        keep["reports"] += [x for x in e["reports"] if x["url"] not in urls]
        _absorb(keep, e)
        if not keep.get("transfer") and e.get("transfer"):
            keep["transfer"] = e["transfer"]
        keep["updated"] = max(keep["updated"], e["updated"])
        folded.append(e)
    for i, kind in (kinds or {}).items():
        if i in by_id and root(i) in by_id:
            keep = by_id[root(i)]
            if not keep.get("wave") and kind != keep.get("type"):
                log(f"[dedupe] {keep['summary'][:70]!r}: kind {keep.get('type')} -> {kind}")
                keep["type"] = kind
    gone = {e["id"] for e in folded}
    return [e for e in events if e["id"] not in gone], folded


def run(events: list[dict], state: dict, settings: dict, now, ask, remaining: int,
        skip: set[str], history: list[dict] | None = None, share: int | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (events, the events folded away). `ask` is extract.ask_json; `history` holds archived
    events no longer in the working set (compared with new events as late follow-ups); `share` is
    how many calls this check may still make now under its paced daily share (extract.share_left)."""
    st = state.setdefault("dedupe", {})
    if st.get("version") != VERSION:
        # the group-by-kind check's "different" answers were given in big mixed groups: start over,
        # keeping what it found to be the same
        st["judged"] = {k: v for k, v in (st.get("judged") or {}).items() if v.get("same")}
        st["version"] = VERSION
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    st["judged"] = judged = {k: v for k, v in (st.get("judged") or {}).items() if v.get("at", "") >= cutoff}
    # pairs already judged the same are folded again if both came back (e.g. restored by a check)
    events, folded = _fold(events, [tuple(k.split("|")) for k, v in judged.items() if v.get("same")], skip)
    have = {e["id"] for e in events}
    history = [h for h in (history or []) if h.get("id") not in have and h.get("id") not in skip]
    live = [e for e in events if e["id"] not in skip]
    questions, auto = cases(live, history, judged, now)
    if auto:
        for e, f, s in auto:
            judged[_key(e["id"], f["id"])] = {"same": True, "at": iso(now), "n": 1, "auto": round(s, 2)}
            log(f"[dedupe] same story (near-identical, {s:.2f}): {e['summary'][:60]!r} + {f['summary'][:60]!r}")
        events, more = _fold(events, [(e["id"], f["id"]) for e, f, _ in auto], skip, history)
        folded += more
        gone = {x["id"] for x in more}
        questions = [(e, [(s, f) for s, f in c if f["id"] not in gone]) for e, c in questions if e["id"] not in gone]
        questions = [(e, c) for e, c in questions if c]
    failed = parse_time(st.get("failed"))
    # A question exists only for a pair not answered yet, so every run with questions asks; after a
    # failed call (the model down), it waits RETRY.
    if not questions or (failed and now - failed < RETRY):
        st["backlog"] = bool(questions)
        return events, folded
    for call in range(MAX_CALLS_PER_RUN):
        batch, questions = questions[:CASES_PER_CALL], questions[CASES_PER_CALL:]
        if not batch:
            break
        if remaining - call < int(settings.get("dedupe_min_calls", 10)):
            log(f"[dedupe] skipped: only {remaining - call} model calls left today")
            questions = batch + questions
            break
        if call and remaining - call < EXTRA_CALLS_FLOOR:
            questions = batch + questions
            break  # the rest waits for the next run rather than eat into extraction
        if share is not None and call >= share:
            if not call:
                log("[dedupe] waiting: its share of today's model calls is used for now")
            questions = batch + questions
            break
        payload = [{"i": n, "event": _show(e), "candidates": [_show(f) for _, f in c]} for n, (e, c) in enumerate(batch)]
        # reasoning models (the outside providers' gpt-oss) spend part of max_tokens thinking: 3,000
        # cut a 15-case answer off at 1,400 characters in a trial (2026-10-06)
        reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=8000,
                    purpose="dedupe")
        if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
            st["failed"] = iso(now)
            log(f"[dedupe] no model answer; will try again in {int(RETRY.total_seconds() // 60)} minutes")
            questions = batch + questions
            break
        st["attempt"] = iso(now)
        st.pop("failed", None)
        same, heads, kinds = _read(reply, batch, judged, now)
        events, more = _fold(events, same, skip, history, kinds)
        _headlines(events, heads)
        log(f"[dedupe] asked about {len(batch)} events ({sum(len(c) for _, c in batch)} candidates), folded {len(more)}")
        folded += more
        gone = {x["id"] for x in more}
        questions = [(e, [(s, f) for s, f in c if f["id"] not in gone]) for e, c in questions if e["id"] not in gone]
        questions = [(e, c) for e, c in questions if c]
    st["backlog"] = bool(questions)
    if questions:
        log(f"[dedupe] {len(questions)} events wait for a question")
    return events, folded


def _show(e: dict) -> dict:
    return {"id": e["id"], "kind": e.get("type"), "summary": e.get("summary"), "place": e.get("place"),
            "time": (e.get("time") or "")[:16]}


def _read(reply: dict, batch: list, judged: dict, now) -> tuple[list[tuple[str, str]], list, dict[str, str]]:
    """Record the model's answers. Returns (pairs judged the same, [(member events, headline)],
    {member id: kind chosen})."""
    same, heads, kinds = [], [], {}
    answered = set()
    for res in reply["results"]:
        n = _case_number(res)
        if n is None or not 0 <= n < len(batch) or n in answered:
            continue
        answered.add(n)
        e, cands = batch[n]
        ids = {str(i).strip() for i in (res.get("same") or res.get("ids") or []) if isinstance(i, (str, int))}
        members = [e]
        for _, f in cands:
            hit = f["id"] in ids
            k = _key(e["id"], f["id"])
            judged[k] = {"same": hit, "at": iso(now), "n": _answers(judged.get(k)) + 1}
            if hit:
                same.append((e["id"], f["id"]))
                members.append(f)
        if len(members) > 1:
            log("[dedupe] same story: " + " + ".join(f"{m.get('type')} {m['summary'][:55]!r}" for m in members))
            text = res.get("summary") if isinstance(res.get("summary"), str) else None
            heads.append((members, text))
            kind = res.get("type")
            if isinstance(kind, str) and kind in {m.get("type") for m in members}:
                kinds[e["id"]] = kind
    if len(answered) < len(batch):
        sample = json.dumps(reply["results"][:1], ensure_ascii=False)[:200]
        log(f"[dedupe] {len(batch) - len(answered)} of {len(batch)} events got no usable answer "
            f"(asked again later); first result looked like: {sample}")
        for n, (e, cands) in enumerate(batch):
            if n not in answered:
                for _, f in cands:
                    judged.pop(_key(e["id"], f["id"]), None)
    return same, heads, kinds


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _headlines(events: list[dict], heads: list) -> None:
    """Give each folded event the model's combined headline, when it keeps to the members' facts
    (every number in it appears in one of their summaries or casualty counts)."""
    by_id = {e["id"]: e for e in events}
    for members, text in heads:
        keep = next((by_id[m["id"]] for m in members if m["id"] in by_id), None)
        text = " ".join(str(text or "").split())
        if keep is None or not text or len(text) > 220:
            continue
        known = " ".join([m.get("summary") or "" for m in members]
                         + [str(m.get(k)) for m in members for k in ("killed", "injured") if m.get(k) is not None])
        if all(n in _NUMBER.findall(known) for n in _NUMBER.findall(text)):
            keep["headline"] = text
        else:
            log(f"[dedupe] combined headline dropped (a number the events don't give): {text[:90]!r}")


def _case_number(res) -> int | None:
    """The case a result is about: "i" as a number or a numeric string (models differ)."""
    if not isinstance(res, dict):
        return None
    v = next((res[k] for k in ("i", "case", "index") if k in res), None)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        return int(v.strip().lstrip("#"))
    return None
