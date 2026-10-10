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
    def test_nearby_different_events_are_new_alerts(self):
        old = event("original", 0.1, "A mine explosion injured soldiers at a border checkpoint.")
        new = event("new", 0.05, "A warehouse fire destroyed equipment near the border checkpoint.")
        candidate = {**new, "report": new["reports"][0]}
        result = merge.merge([old], [candidate])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["reports"], old["reports"])
        self.assertEqual(result[1]["time"], new["time"])

    def test_recurring_strikes_at_same_place_need_same_event_confirmation(self):
        old = event("first", 0.2, BLAST)
        new = event("second", 0.1, BLAST)
        result = merge.merge([old], [{**new, "report": new["reports"][0]}])
        self.assertEqual(len(result), 2, "Matching words and nearby times cannot establish one occurrence")
        # The existing classifier can still group them if it confirms the same incident.
        result, _ = dedupe._fold(result, [(result[0]["id"], result[1]["id"])], set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], old["time"])
        self.assertEqual(len(result[0]["reports"]), 2)

    def test_different_statements_by_same_parties_stay_separate(self):
        old = event("talks", 0.1, "North and South Korea held a border security meeting.", "diplomacy")
        new = event("sanctions", 0.05, "South Korea imposed financial sanctions on North Korean officials.", "diplomacy")
        result = merge.merge([old], [{**new, "report": new["reports"][0]}])
        self.assertEqual(len(result), 2)

    def test_recurring_shipping_incidents_are_not_automatically_folded(self):
        old = event("first", 0.2, "A cargo vessel reported an explosion near the coast.", "naval")
        new = event("second", 0.1, old["summary"], "naval")
        result = merge.merge([old], [{**new, "report": new["reports"][0]}])
        self.assertEqual(len(result), 2)
        self.assertFalse(dedupe.automatic(old, new, 1.0))
        # A broad country/sea pin cannot bypass the occurrence check either.
        broad = {**new, "lat": old["lat"] + 8, "approx": True}
        result = merge.merge([old], [{**broad, "report": broad["reports"][0]}])
        self.assertEqual(len(result), 2)

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



class CampaignRepairTests(unittest.TestCase):
    def setUp(self):
        import config
        import incidents
        self.incidents = incidents
        self.theaters = config.load().theaters
        self.parent = event('campaign', 0.1, 'Campaign aggregate', 'missile_drone')
        self.parent.update(wave=True, theater='mideast', country='SA', attacker='YE', killed=9,
                           status='corroborated', sources_count=17, targets=[], headline='Mixed headline')
        summaries = ['Three killed in a Riyadh airport strike.',
                     'Smoke reported at Ghawar after a missile strike.',
                     'Riyadh airport attacked again, two days after the strike that killed three.']
        self.parent['reports'] = [{**self.parent['reports'][0], 'summary': text, 'url': f'https://example.org/report-{i}',
                                   'source': f'source-{i}', 'group': f'group-{i}'} for i, text in enumerate(summaries)]
        self.reply = {'groups': [{'reports': [i], 'event': {'type': 'missile_drone', 'summary': text,
                      'place': 'Ghawar' if i == 1 else 'King Khalid International Airport', 'country': 'SA',
                      'theater': 'mideast', 'attacker': 'YE', 'lat': 25 if i == 1 else 24.9586,
                      'lon': 49.1667 if i == 1 else 46.711, 'severity': 2, 'happened': None,
                      'killed': 3 if i == 0 else None, 'injured': None}}
                      for i, text in enumerate(summaries)]}
        class Places:
            def locate(self, place, admin1, country, hint):
                return hint
        self.geo = Places()

    def parts(self, reply=None):
        return self.incidents.replacements(self.parent, self.reply if reply is None else reply,
                                           self.geo, self.theaters, {self.parent['id']})

    def test_airport_oilfield_and_renewed_airport_are_separate(self):
        parts = self.parts()
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0]['id'], 'campaign')
        self.assertEqual([e['killed'] for e in parts], [3, None, None])
        self.assertTrue(all(not e.get('wave') and 'headline' not in e and 'status' not in e for e in parts))
        self.assertEqual(sorted(r['url'] for e in parts for r in e['reports']),
                         sorted(r['url'] for r in self.parent['reports']))
        merge.apply_status(parts, [])
        self.assertTrue(all(e['sources_count'] == 1 and e['status'] == 'unconfirmed' for e in parts))
        self.assertTrue(all('incident_split' not in merge.public_event(e) for e in parts))

    def test_missing_duplicate_boolean_or_out_of_range_report_rejects_whole_partition(self):
        for indices in ([[0], [1]], [[0], [1], [1, 2]], [[False], [1], [2]], [[0], [1], [3]]):
            reply = deepcopy(self.reply)
            reply['groups'] = [{**self.reply['groups'][min(i, 2)], 'reports': g} for i, g in enumerate(indices)]
            self.assertIsNone(self.parts(reply))

    def test_unplaceable_group_preserves_whole_original(self):
        reply = deepcopy(self.reply)
        reply['groups'][1]['event'].update(place=None, lat=None, lon=None)
        self.assertIsNone(self.parts(reply))

    def test_invalid_or_unavailable_reply_retries_without_mutation(self):
        for answer in (None, {'groups': []}):
            original = deepcopy(self.parent)
            state = {}
            out = self.incidents.repair([self.parent], state, lambda *a, **k: answer, {}, NOW,
                                        self.geo, self.theaters, set())
            self.assertEqual(out, [original])
            self.assertEqual(state['incident_repair']['remaining'], 1)

    def test_success_is_stable_idempotent_and_hidden_campaigns_are_untouched(self):
        parts = self.parts()
        self.assertEqual([e['id'] for e in parts], [e['id'] for e in self.parts()])
        def forbidden(*a, **k):
            self.fail('completed or hidden repair must not call the model')
        self.assertEqual(self.incidents.repair(parts, {}, forbidden, {}, NOW, self.geo, self.theaters, set()), parts)
        self.assertEqual(self.incidents.repair([self.parent], {}, forbidden, {}, NOW, self.geo, self.theaters,
                                              {'campaign'}), [self.parent])

    def test_cached_duplicates_and_transitive_bridges_cannot_refold_siblings(self):
        parts = self.parts()
        bridge = event('bridge', 0.1, parts[0]['summary'], 'missile_drone')
        out, _ = dedupe._fold(parts + [bridge], [(parts[0]['id'], 'bridge'),
                            ('bridge', parts[2]['id']), (parts[0]['id'], parts[1]['id'])], set())
        self.assertEqual(len(out), 3)
        out, folded = merge.consolidate(out, set())
        self.assertEqual(len(out), 3)
        self.assertFalse(folded)

    def test_known_attacker_no_longer_bypasses_incident_identity(self):
        parts = self.parts()
        candidates = [{**e, 'report': e['reports'][0]} for e in parts]
        out = merge.merge([], candidates)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(not e.get('wave') for e in out))

    def test_archive_preserves_partition_to_prevent_late_refolding(self):
        parts = self.parts()
        merge.apply_status(parts, [])
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive.update(root, [merge.public_event(e) for e in parts], {}, [], NOW, parts)
            archived = archive.recent(root, NOW, 14, set())
            self.assertEqual(len(archived), 3)
            self.assertTrue(all(e.get('incident_split') == 'campaign' for e in archived))
            out, folded = dedupe._fold([parts[0]], [(parts[0]['id'], parts[2]['id'])], set(), archived)
            self.assertEqual(len(out), 1)
            self.assertFalse(folded)

    def test_multiple_incidents_in_one_input_keep_the_same_source_reference(self):
        import extract
        item = {**self.parent['reports'][0], 'id': 'input', 'platform': 'rss', 'text': 'Airport and oil-field attacks'}
        reply = {'events': [{**g['event'], 'i': 0, 'relevant': True} for g in self.reply['groups'][:2]]}
        records, carriers, done = [], [], set()
        extract._absorb(reply, [item], {}, NOW, records, carriers, done)
        self.assertEqual(len(records), 2)
        self.assertEqual(done, {'input'})
        self.assertEqual(records[0]['item']['url'], records[1]['item']['url'])

    def test_flat_repair_schema_keeps_full_validation(self):
        reply = {'groups': [{'reports': g['reports'], **g['event']} for g in self.reply['groups']]}
        self.assertEqual(self.parts(reply), self.parts())
        reply['groups'][1]['reports'] = [0]
        self.assertIsNone(self.parts(reply))

    def test_repair_invalidates_old_campaign_duplicate_links_only(self):
        state = {'dedupe': {'judged': {'alias|campaign': {'same': True},
                                      'campaign|other': {'same': False},
                                      'other|unrelated': {'same': True}}}}
        out = self.incidents.repair([self.parent], state, lambda *a, **k: self.reply, {}, NOW,
                                    self.geo, self.theaters, set())
        self.assertEqual(len(out), 3)
        self.assertEqual(state['dedupe']['judged'], {'other|unrelated': {'same': True}})

    def test_invalid_event_type_is_not_silently_retyped(self):
        reply = deepcopy(self.reply)
        reply['groups'][0]['event']['type'] = 'new_alert_category'
        self.assertIsNone(self.parts(reply))


if __name__ == "__main__":
    unittest.main()
