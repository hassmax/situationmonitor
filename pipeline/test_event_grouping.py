"""Run with: python -m unittest discover -s pipeline -p 'test_event_grouping.py'.

No network or model calls. Classifier replies are fixtures; candidate discovery, identity,
report merging, confidence, archive restoration and budget enforcement use production code.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import archive
from common import iso, parse_time
import dedupe
import merge


NOW = parse_time("2026-10-07T18:00:00Z")


def event(key, age, summary, kind="explosion", updated_age=None):
    time = iso(NOW - timedelta(days=age))
    updated = iso(NOW - timedelta(days=age if updated_age is None else updated_age))
    return {"id": key, "type": kind, "summary": summary, "time": time, "updated": updated,
            "theater": "indo-pacific", "country": "KR", "place": "Korean Demilitarized Zone",
            "lat": 38.0, "lon": 127.0, "approx": False, "severity": 2,
            "parties": ["KP", "KR"], "attacker": None, "killed": None, "injured": None,
            "reports": [{"url": f"https://example.org/{key}", "time": updated, "summary": summary,
                         "source": key, "group": key, "weight": 1, "side": None}]}


BLAST = "A mine explosion in the Korean Demilitarized Zone injured two South Korean soldiers."
FOLLOW = "South Korea's investigation attributes the DMZ landmine blast to North Korea."


class EventGroupingTests(unittest.TestCase):
    def test_working_set_event_older_than_72_hours_is_a_candidate(self):
        old, new = event("original", 6, BLAST), event("update", 0.1, FOLLOW, "hybrid")
        questions, _ = dedupe.cases([old, new], [], {}, NOW)
        self.assertTrue(any(e["id"] == "update" and any(f["id"] == "original" for _, f in fs)
                            for e, fs in questions))

    def test_fresh_report_with_original_occurrence_date_is_checked(self):
        old = event("original", 6, BLAST)
        new = event("update", 6, FOLLOW, "hybrid", updated_age=0.1)
        questions, _ = dedupe.cases([old, new], [], {}, NOW)
        self.assertTrue(any(e["id"] == "update" for e, _ in questions))

    def test_original_report_survives_a_changed_headline_for_matching(self):
        old = event("original", 6, "Officials say tensions remain elevated.")
        old["reports"][0]["summary"] = BLAST
        new = event("update", 0.1, FOLLOW)
        questions, _ = dedupe.cases([old, new], [], {}, NOW)
        self.assertTrue(any(any(f["id"] == "original" for _, f in fs) for _, fs in questions))

    def test_aliases_retrieve_dmz_reports_across_types(self):
        original = event("original", 3, "A mine blast in the Demilitarized Zone fueled tensions between North and South Korea.")
        denial = event("denial", 0.1, "North Korea dismisses a DMZ mine blast as a farce and vows a powerful response if the border is violated.", "diplomacy")
        cases, _ = dedupe.cases([original, denial], [], {}, NOW)
        self.assertTrue(any(any(f["id"] == "original" for _, f in fs) for _, fs in cases))

    def test_fold_keeps_id_date_and_reports(self):
        old, new = event("original", 6, BLAST), event("update", 0.1, FOLLOW)
        old_time = old["time"]
        result, gone = dedupe._fold([old, new], [("original", "update")], set())
        self.assertEqual([e["id"] for e in result], ["original"])
        self.assertEqual(result[0]["time"], old_time)
        self.assertEqual(result[0]["updated"], new["updated"])
        self.assertEqual(len(result[0]["reports"]), 2)
        self.assertEqual([e["id"] for e in gone], ["update"])

    def test_earlier_date_in_followup_does_not_replace_original_id(self):
        old = event("original", 6, BLAST)
        new = event("update", 7, FOLLOW, updated_age=0.1)
        result, _ = dedupe._fold([old, new], [("original", "update")], set())
        self.assertEqual(result[0]["id"], "original")
        self.assertEqual(result[0]["time"], new["time"])

    def test_shared_excerpt_cannot_automatically_join_distinct_actions(self):
        a = event("a", 0.1, "South Korea imposed sanctions after the border incident.", "diplomacy")
        b = event("b", 0.2, "North Korea moved artillery units to border positions.", "deployment")
        a["reports"].append({**a["reports"][0], "summary": BLAST})
        b["reports"].append({**b["reports"][0], "summary": BLAST})
        self.assertFalse(dedupe.automatic(a, b, 1.0))

    def test_landmine_blast_does_not_become_an_air_attack_wave(self):
        e = event("mine", 0.1, BLAST)
        e["attacker"] = "KP"
        e["report"] = e.pop("reports")[0]
        result = merge.merge([], [e])
        self.assertEqual(result[0]["type"], "explosion")
        self.assertFalse(result[0].get("wave"))

    def test_existing_mine_wave_keeps_id_and_reports_when_repaired(self):
        e = event("mine", 6, BLAST, "missile_drone")
        e.update(wave=True, targets=[], wave_key="old")
        merge.mine_incidents([e])
        self.assertEqual(e["id"], "mine")
        self.assertEqual(e["type"], "explosion")
        self.assertFalse(e.get("wave"))
        self.assertEqual(len(e["reports"]), 1)

    def test_missile_wave_is_not_repaired_as_a_mine_incident(self):
        e = event("wave", 0.1, "North Korean missiles hit a military landmine store.", "missile_drone")
        e["wave"] = True
        merge.mine_incidents([e])
        self.assertTrue(e["wave"])

    def test_mine_blast_does_not_join_a_nearby_air_attack_wave(self):
        wave = event("wave", 0.1, "North Korean missiles hit a military site.", "missile_drone")
        wave.update(wave=True, targets=[], attacker="KP", origins=[])
        e = event("mine", 0.1, BLAST)
        e["report"] = e.pop("reports")[0]
        result = merge.merge([wave], [e])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["type"], "explosion")

    def test_archive_restore_keeps_identity_history_and_archive_unchanged(self):
        old, new = event("original", 10, BLAST), event("update", 0.1, FOLLOW)
        original = deepcopy(old)
        result, gone = dedupe._fold([new], [("original", "update")], set(), [old])
        self.assertEqual(result[0]["id"], "original")
        self.assertEqual(result[0]["time"], old["time"])
        self.assertEqual(result[0]["updated"], new["updated"])
        self.assertEqual(len(result[0]["reports"]), 2)
        self.assertEqual(old, original)
        self.assertEqual(gone[0]["id"], "update")
        merge.apply_status(result, [])  # restored reports retain their independence metadata
        self.assertEqual(result[0]["status"], "corroborated")

    def test_hidden_archive_identity_is_not_restored(self):
        old, new = event("original", 10, BLAST), event("update", 0.1, FOLLOW)
        result, gone = dedupe._fold([new], [("original", "update")], {"original"}, [old])
        self.assertEqual(result, [new])
        self.assertFalse(gone)

    def test_unrelated_archive_pair_cannot_restore_an_event(self):
        old, new = event("original", 10, BLAST), event("update", 0.1, FOLLOW)
        result, _ = dedupe._fold([new], [("original", "other")], set(), [old])
        self.assertEqual(result, [new])

    def test_same_answer_reconnects_archive_without_another_model_call(self):
        old, new = event("original", 10, BLAST), event("update", 0.1, FOLLOW)
        state = {"dedupe": {"version": dedupe.VERSION, "judged": {
            "original|update": {"same": True, "at": iso(NOW), "n": 1}}}}
        def unexpected(*args, **kwargs):
            self.fail("A known match should not need a model call")
        result, _ = dedupe.run([new], state, {}, NOW, unexpected, 0, set(), [old], share=0)
        self.assertEqual(result[0]["id"], "original")

    def test_repeated_report_url_is_not_added_twice(self):
        old, new = event("original", 6, BLAST), event("update", 0.1, FOLLOW)
        new["reports"] = deepcopy(old["reports"])
        result, _ = dedupe._fold([old, new], [("original", "update")], set())
        self.assertEqual(len(result[0]["reports"]), 1)

    def test_casualty_updates_are_not_summed(self):
        old, new = event("original", 6, BLAST), event("update", 0.1, FOLLOW)
        old["injured"], new["injured"] = 2, 3
        result, _ = dedupe._fold([old, new], [("original", "update")], set())
        self.assertEqual(result[0]["injured"], 3)

    def test_separate_strikes_far_apart_are_excluded(self):
        a, b = event("a", 0.1, BLAST), event("b", 0.2, BLAST)
        b["lat"] = 34.0
        self.assertTrue(dedupe.apart(a, b))
        self.assertFalse(dedupe.candidates(a, [b], dedupe._vectors([a, b])))

    def test_alerts_and_two_waves_do_not_join(self):
        a, b = event("a", 0.1, BLAST), event("b", 0.2, BLAST)
        b["alert"] = True
        self.assertFalse(dedupe.candidates(a, [b], dedupe._vectors([a, b])))
        b.pop("alert")
        a["wave"] = b["wave"] = True
        self.assertFalse(dedupe.candidates(a, [b], dedupe._vectors([a, b])))

    def test_budget_exhaustion_defers_model_judgment(self):
        old, new = event("original", 6, BLAST), event("update", 0.1, FOLLOW)
        def unexpected(*args, **kwargs):
            self.fail("The existing dedupe budget must be respected")
        state = {}
        result, _ = dedupe.run([old, new], state, {}, NOW, unexpected, 100, set(), share=0)
        self.assertEqual(len(result), 2)
        self.assertTrue(state["dedupe"]["backlog"])

    def test_classifier_confirmed_followup_restores_archive(self):
        old, new = event("original", 10, BLAST), event("update", 0.1, FOLLOW, "hybrid")
        calls = []
        def answer(prompt, payload, *args, **kwargs):
            calls.append(kwargs["purpose"])
            case = json.loads(payload)["cases"][0]
            self.assertIn("reports", case["candidates"][0])
            return {"results": [{"i": 0, "same": ["original"], "type": "explosion", "summary": BLAST}]}
        result, _ = dedupe.run([new], {}, {}, NOW, answer, 100, set(), [old], share=1)
        self.assertEqual(calls, ["dedupe"])
        self.assertEqual(result[0]["id"], "original")
        self.assertEqual(result[0]["type"], "explosion")
        self.assertEqual(result[0]["time"], old["time"])

    def test_classifier_rejection_keeps_recurring_incidents_separate(self):
        a, b = event("a", 0.1, BLAST), event("b", 0.2, BLAST)
        def answer(prompt, payload, *args, **kwargs):
            return {"results": [{"i": i, "same": []} for i, _ in enumerate(json.loads(payload)["cases"])]}
        result, gone = dedupe.run([a, b], {}, {}, NOW, answer, 100, set(), share=1)
        self.assertEqual(len(result), 2)
        self.assertFalse(gone)

    def test_report_date_sets_updated_without_changing_occurrence(self):
        old = event("original", 6, BLAST)
        candidate = {**event("update", 6, BLAST, updated_age=0.1)}
        candidate["report"] = candidate.pop("reports")[0]
        result = merge.merge([old], [candidate])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], old["time"])
        self.assertEqual(result[0]["updated"], candidate["report"]["time"])

    def test_first_report_of_old_incident_sets_reporting_activity(self):
        candidate = event("update", 6, BLAST, updated_age=0.1)
        candidate["report"] = candidate.pop("reports")[0]
        result = merge.merge([], [candidate])
        self.assertEqual(result[0]["time"], candidate["time"])
        self.assertEqual(result[0]["updated"], candidate["report"]["time"])

    def test_recent_reports_retain_old_event_then_event_expires(self):
        old = event("original", 10, BLAST, updated_age=0.1)
        self.assertEqual(merge.prune([old], NOW, 7, 2500), [old])
        self.assertFalse(merge.prune([old], NOW + timedelta(days=8), 7, 2500))

    def test_archive_roundtrip_retains_metadata_and_respects_report_removal(self):
        e = event("original", 10, BLAST)
        e["reports"][0]["group"] = "shared-newsroom"
        e["reports"].append({**e["reports"][0], "url": "https://example.org/removed"})
        published = merge.public_event(e)
        published["reports"] = published["reports"][:1]
        with TemporaryDirectory() as folder:
            archive.update(Path(folder), [published], {}, [], NOW, [e])
            restored = archive.recent(Path(folder), NOW, 14)
        self.assertEqual(len(restored[0]["reports"]), 1)
        self.assertEqual(restored[0]["reports"][0]["group"], "shared-newsroom")

    def test_legacy_archive_recovers_known_groups_conservatively(self):
        e = event("original", 10, BLAST)
        e["reports"].append({**e["reports"][0], "source": "syndicated", "url": "https://example.org/second"})
        with TemporaryDirectory() as folder:
            archive.update(Path(folder), [merge.public_event(e)], {}, [], NOW)
            restored = archive.recent(Path(folder), NOW, 14)
            merge.apply_status(restored, [])
            self.assertEqual(restored[0]["sources_count"], 1)
            self.assertEqual(restored[0]["status"], "unconfirmed")
            restored = archive.recent(Path(folder), NOW, 14, [{"name": "original (via Google News)", "group": "agency"}])
            self.assertEqual(restored[0]["reports"][0]["group"], "agency")


if __name__ == "__main__":
    unittest.main()
