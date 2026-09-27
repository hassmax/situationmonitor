"""Telegram alerts, sent from the update job through the Telegram Bot API.

Rules are in config/alerts.yaml. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; without them
alerts are skipped (and nothing is marked as seen, so turning them on later starts cleanly).

- Never alert the same thing twice: sent alerts are remembered by (event, rule). An event
  alerts again only when it matches a rule it did not match before.
- First run: everything currently on the map is recorded as seen, and the only message is
  "Alerts are on". No backlog.
- At most max_messages_per_run messages per run; more than that go out as one digest.
- Quiet hours hold alerts until the quiet period ends.
The bot token is never logged.
"""
from __future__ import annotations

from datetime import timedelta

import requests

from common import haversine_km, iso, log, parse_time

TYPES = {
    "airstrike": "Airstrike", "missile_drone": "Drone or missile attack", "artillery": "Shelling",
    "ground": "Ground fighting", "territory": "Territorial change", "air_defense": "Air defense",
    "naval": "Naval incident", "explosion": "Explosion", "deployment": "Deployment or exercise",
    "diplomacy": "Diplomacy", "ceasefire": "Diplomacy", "hybrid": "Sabotage or hybrid attack",
    "incursion": "Airspace or border incursion", "arms_transfer": "Arms transfer", "legal": "Legal step",
}
CONFIDENCE = {"corroborated": "Corroborated", "unconfirmed": "Single source", "claimed": "One side's claim"}
REASON = {"major": "major corroborated event", "wave": "large attack wave", "legal": "new legal step",
          "tg_pair": "reported by two OSINT Telegram channels"}
KEEP_DAYS = 30


def dashboard_url(env) -> str:
    url = (env.get("DASHBOARD_URL") or "").strip()
    if url:
        return url.rstrip("/") + "/"
    repo = (env.get("GITHUB_REPOSITORY") or "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def _rule(rules: dict, name: str) -> dict:
    r = (rules.get("rules") or {}).get(name) or {}
    return r if r.get("enabled", True) else {}


def event_matches(e: dict, rules: dict) -> list[str]:
    """The rules this event matches right now (hidden events never reach this point)."""
    hits = []
    if _rule(rules, "major_corroborated") and (e.get("severity") or 0) >= 3 and e.get("status") == "corroborated":
        hits.append("major")
    wave = _rule(rules, "attack_wave")
    if wave and e.get("wave") and ((e.get("launched") or 0) >= int(wave.get("min_launched", 100))
                                   or len(e.get("targets") or []) >= int(wave.get("min_locations", 8))):
        hits.append("wave")
    if _rule(rules, "legal") and e.get("type") == "legal":
        hits.append("legal")
    pair = _rule(rules, "telegram_osint_pair")
    if pair and (e.get("severity") or 0) >= int(pair.get("min_severity", 1)):
        prefix = f"https://t.me/{str(pair.get('channel', '')).lstrip('@').lower()}/"
        osint = [r for r in e.get("reports") or [] if r.get("platform") == "telegram" and r.get("kind") == "osint"]
        if (any(r.get("url", "").lower().startswith(prefix) for r in osint)
                and any(not r.get("url", "").lower().startswith(prefix) for r in osint)):
            hits.append("tg_pair")
    return hits


def carrier_matches(c: dict, rules: dict) -> list[tuple[str, str]]:
    """(key, what happened) for a carrier; keys change only when a new move is reported."""
    rule = _rule(rules, "carrier")
    if not rule:
        return []
    hull, out = c.get("hull"), []
    if c.get("status") == "departed" and c.get("departed_at"):
        out.append((f"cvn:{hull}:departed:{c['departed_at']}", f"departed{' ' + c['place'] if c.get('place') else ''}"))
    h = c.get("heading_to")
    if h and (h.get("place") or h.get("lat") is not None):
        where = h.get("place") or f"{h.get('lat')},{h.get('lon')}"
        out.append((f"cvn:{hull}:heading:{where}", f"is heading to {h.get('place') or 'a stated destination'}"))
    p = c.get("prev")
    if p and c.get("moved_at") and p.get("lat") is not None and c.get("lat") is not None:
        km = haversine_km(p["lat"], p["lon"], c["lat"], c["lon"])
        if km >= float(rule.get("min_move_km", 500)):
            out.append((f"cvn:{hull}:moved:{c['moved_at']}", f"moved about {round(km, -1):.0f} km"))
    return out


def active_bridges(events: list[dict], rules: dict, now) -> dict[str, list[dict]]:
    """Supplier>recipient routes with enough reported deliveries in the window right now."""
    rule = _rule(rules, "supply_bridge")
    if not rule:
        return {}
    since = now - timedelta(hours=float(rule.get("window_hours", 72)))
    routes: dict[str, list[dict]] = {}
    for e in events:
        t = e.get("transfer") or {}
        when = parse_time(e.get("time"))
        if e.get("type") != "arms_transfer" or t.get("kind", "delivery") != "delivery" or not when or when < since:
            continue
        if t.get("supplier") and t.get("recipient"):
            routes.setdefault(f"{t['supplier']}>{t['recipient']}", []).append(e)
    need = int(rule.get("min_deliveries", 3))
    return {k: v for k, v in routes.items()
            if sum(max(1, (e.get("transfer") or {}).get("flights") or 1) for e in v) >= need}


def _in_theaters(e: dict, rules: dict) -> bool:
    want = rules.get("theaters", "all")
    return want in (None, "all") or e.get("theater") in (want if isinstance(want, list) else [want])


def _quiet(rules: dict, now) -> bool:
    q = rules.get("quiet_hours")
    if not isinstance(q, dict) or q.get("start") is None or q.get("end") is None:
        return False
    start, end, h = int(q["start"]) % 24, int(q["end"]) % 24, now.hour
    return start <= h < end if start < end else (h >= start or h < end)


def _event_text(e: dict, why: list[str], names: dict, base: str) -> str:
    what = "Drone and missile attack wave" if e.get("wave") else TYPES.get(e.get("type"), "Event")
    where = f"{e.get('attacker')} → {e.get('country')}" if e.get("wave") else (e.get("place") or "unnamed place")
    lines = [f"{what}: {where}, {names.get(e.get('theater'), e.get('theater'))}", e.get("summary") or ""]
    if e.get("wave"):
        facts = [f"{e['launched']} launched (reported)" if e.get("launched") else "",
                 f"{len(e.get('targets') or [])} locations" if e.get("targets") else ""]
        lines.append(", ".join(f for f in facts if f))
    n = e.get("sources_count") or 1
    lines.append(f"{CONFIDENCE.get(e.get('status'), e.get('status'))}, {n} {'source' if n == 1 else 'sources'}")
    lines.append("Alert: " + ", ".join(REASON[w] for w in why))
    if base:
        lines.append(f"{base}#{e['id']}")
    return "\n".join(x for x in lines if x)


def _carrier_text(c: dict, whats: list[str], base: str) -> str:
    lines = [f"{c.get('name')} ({c.get('hull')}) " + " and ".join(whats),
             f"Last reported: {c.get('place') or 'position given in report'}"
             + (f", {c['as_of'][:10]}" if c.get("as_of") else ""),
             f"Source: {c.get('source') or 'reporting'}"]
    if base:
        lines.append(f"{base}#{c.get('hull')}")
    return "\n".join(lines)


def _bridge_text(route: str, evs: list[dict], hours, base: str) -> str:
    supplier, recipient = route.split(">", 1)
    n = sum(max(1, (e.get("transfer") or {}).get("flights") or 1) for e in evs)
    modes = sorted({(e.get("transfer") or {}).get("mode") for e in evs} - {None, "unspecified"})
    best = max(evs, key=lambda e: ("claimed", "unconfirmed", "corroborated").index(e.get("status", "claimed")))
    latest = max(evs, key=lambda e: e.get("time") or "")
    lines = [f"New arms bridge: {supplier} → {recipient}",
             f"{n} deliveries reported in {hours:g} hours" + (f" (by {', '.join(modes)})" if modes else ""),
             f"Best confidence: {CONFIDENCE.get(best.get('status'), best.get('status'))}"]
    if base:
        lines.append(f"{base}#{latest['id']}")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                          json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
    except Exception as exc:  # noqa: BLE001 - the message could contain the URL with the token
        log(f"[alerts] Telegram send failed ({type(exc).__name__})")
        return False
    if r.status_code != 200:
        try:
            desc = r.json().get("description", "")
        except ValueError:
            desc = ""
        log(f"[alerts] Telegram send failed: HTTP {r.status_code} {str(desc).replace(token, '***')[:120]}")
        return False
    return True


def run(state: dict, events: list[dict], fleet: list[dict], rules: dict, names: dict, now, env,
        send=send_telegram) -> None:
    token, chat = (env.get("TELEGRAM_BOT_TOKEN") or "").strip(), (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        log("[alerts] Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping alerts")
        return
    if _quiet(rules, now):
        log("[alerts] quiet hours; alerts held until they end")
        return
    base = dashboard_url(env)
    st = state.setdefault("alerts", {})
    sent: dict = st.setdefault("sent", {})

    due: list[tuple[list[str], str, str | None]] = []  # (keys, message, bridge route)
    for e in events:
        if not _in_theaters(e, rules):
            continue
        new = [w for w in event_matches(e, rules) if f"{e['id']}:{w}" not in sent]
        if new:
            due.append(([f"{e['id']}:{w}" for w in new], _event_text(e, new, names, base), None))
    for c in fleet:
        hits = [(k, w) for k, w in carrier_matches(c, rules) if k not in sent]
        if hits:
            due.append(([k for k, _ in hits], _carrier_text(c, [w for _, w in hits], base), None))
    bridges = active_bridges(events, rules, now)
    was_active = set(st.get("bridges", []))
    hours = float((_rule(rules, "supply_bridge") or {}).get("window_hours", 72))
    for route, evs in bridges.items():
        if route not in was_active:
            due.append(([f"bridge:{route}:{iso(now)}"], _bridge_text(route, evs, hours, base), route))

    done: list[tuple[list[str], str, str | None]] = []
    if not st.get("started"):
        # First run: everything already on the map counts as seen. One message, no backlog.
        if not send(token, chat, "Alerts are on. You will get a message here when a new event matches the "
                                 "rules in pipeline/config/alerts.yaml." + (f"\n{base}" if base else "")):
            return
        done = due
        st["started"] = iso(now)
        log(f"[alerts] alerts are on; {len(due)} current matches recorded as already seen")
    elif len(due) > int(rules.get("max_messages_per_run", 8)):
        lines = [f"{len(due)} new alerts:"]
        for _, m, _ in due[:20]:
            first, last = m.splitlines()[0], m.splitlines()[-1]
            lines.append(f"• {first}" + (f" {last}" if base and last.startswith(base) else ""))
        if len(due) > 20:
            lines.append(f"…and {len(due) - 20} more on the dashboard" + (f": {base}" if base else ""))
        if send(token, chat, "\n".join(lines)):
            done = due
        log(f"[alerts] {len(due)} due; digest {'sent' if done else 'failed'}")
    else:
        done = [d for d in due if send(token, chat, d[1])]
        if due:
            log(f"[alerts] {len(due)} due, {len(done)} sent")

    for keys, _, _ in done:
        for k in keys:
            sent[k] = iso(now)
    # A bridge alerts once while it stays active; once it lapses, a restart alerts again.
    st["bridges"] = sorted((was_active & set(bridges)) | {r for _, _, r in done if r})
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    st["sent"] = {k: v for k, v in sent.items() if v >= cutoff}
