"""Temporary probe (removed before the PR): the social media and imagery agents on a copy of the state."""
import json, sys
sys.path.insert(0, "pipeline")
from datetime import datetime, timezone
import config, extract, frontline
from frontline import imagery, ledger, social
from common import load_json, save_json, http_session
from pathlib import Path

state = load_json("state/state.json", {})
settings = config.load().settings
cfg = config.load()
now = datetime.now(timezone.utc)
fl = ledger.state_of(state)
print("social queue after the run:", len(fl.get("social", {}).get("queue", [])))
claims = [(k, c) for k, p in fl["places"].items() for c in p["claims"] if c.get("via") == "social"]
print("claims from social posts in the ledger:", len(claims))
for k, c in claims[:40]:
    print("  ", k, "|", c["change"], c["actor"], "| basis", c["basis"], "| aligned", c["aligned"], "|", c["summary"][:120])
# more imagery: three disputed places
imagery.PER_RUN = 3
sess = http_session()
n = imagery.run(cfg.frontlines, fl, state, settings, sess, now, extract.ask_json, 3, [])
print("imagery compared:", n)
for k, v in fl.get("imagery", {}).items():
    print("  ", k, json.dumps(v, ensure_ascii=False)[:500])
print("budget:", state.get("llm_calls", {}).get("by"), state.get("providers", {}).get("cerebras", {}).get("by"))
