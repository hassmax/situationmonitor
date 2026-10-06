"""Temporary trial (removed before the PR): the new same-story check and date check on the live
state, nothing saved."""
import copy
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "pipeline")
import archive  # noqa: E402
import config as config_mod  # noqa: E402
import datecheck  # noqa: E402
import dedupe  # noqa: E402
import extract  # noqa: E402
import recency  # noqa: E402
from common import http_session, load_json  # noqa: E402

cfg = config_mod.load()
settings = cfg.settings
state = load_json(Path("state/state.json"), {})
events = load_json(Path("state/events.json"), {})["events"]
now = datetime.now(timezone.utc)
session = http_session()
print(f"== {len(events)} events; held under the new rule: {sum(1 for e in events if recency.held(e))}")

print("== date check (one run's worth, then a second)")
ev = copy.deepcopy(events)
st = {}
for _ in range(2):
    before = {e["id"] for e in ev}
    ev = datecheck.check(ev, session, st, now)
    print("   dropped this run:", len(before - {e["id"] for e in ev}))
print("   how dated:", Counter((e.get("dated") or {}).get("how") for e in ev if (e.get("dated") or {}).get("published")))
for d in st.get("dropped_as_old", []):
    print(f"   OLD {d['match']} | listed {d['listed'][:10]} | {d['summary'][:110]}")

print("== same-story check (3 calls)")
dedupe.MAX_CALLS_PER_RUN = 3
dedupe.EXTRA_CALLS_FLOOR = 0
st2 = copy.deepcopy(state)
q, auto = dedupe.cases([e for e in events], archive.recent(Path("state"), now, dedupe.LATE_DAYS), {}, now)
print(f"   {len(q)} events to ask about, {sum(len(c) for _, c in q)} candidates, {len(auto)} automatic folds")
out, folded = dedupe.run(copy.deepcopy(events), st2, settings, now, extract.ask_json,
                         extract.calls_remaining(st2, settings, now), set(),
                         archive.recent(Path("state"), now, dedupe.LATE_DAYS), share=3)
print(f"   folded {len(folded)}; events {len(events)} -> {len(out)}")
