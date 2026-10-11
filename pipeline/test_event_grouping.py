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

    def test_fresh_followup_cannot_join_older_same_site_sibling(self):
        parts = self.parts()
        older, newer = parts[0], parts[2]
        older['time'] = iso(NOW - timedelta(days=2))
        newer['time'] = iso(NOW - timedelta(hours=4))
        coverage = event('coverage', 0.01, 'Many injured in Riyadh airport strike.', 'missile_drone')
        coverage.update(place=older['place'], country='SA', injured=80)
        out, _ = dedupe._fold([older, newer, coverage], [(older['id'], 'coverage'),
                              (newer['id'], 'coverage')], set())
        self.assertEqual(len(out), 2)
        self.assertEqual(older['killed'], 3)
        self.assertIsNone(older['injured'])
        self.assertEqual(newer['injured'], 80)
        self.assertIsNone(newer['killed'])

    def test_explicit_original_occurrence_still_reaches_older_sibling(self):
        parts = self.parts()
        older, newer = parts[0], parts[2]
        older['time'] = iso(NOW - timedelta(days=2))
        newer['time'] = iso(NOW - timedelta(hours=4))
        coverage = event('coverage', 2, 'Casualty update on the original airport strike.', 'missile_drone', updated_age=0.01)
        coverage.update(place=older['place'], country='SA')
        self.assertFalse(dedupe.older_partition(older, coverage, [older, newer, coverage]))
        out, _ = dedupe._fold([older, newer, coverage], [(older['id'], 'coverage')], set())
        self.assertEqual(len(out), 2)
        self.assertEqual(len(older['reports']), 2)
        self.assertEqual(len(newer['reports']), 1)

    def test_same_site_family_is_repaired_once_and_invalid_reply_preserves_every_member(self):
        parts = self.parts()
        parts[0]['time'] = iso(NOW - timedelta(days=2))
        parts[2]['time'] = iso(NOW - timedelta(hours=4))
        state = {}
        self.assertEqual(self.incidents.repair(parts, state, lambda *a, **k: None, {}, NOW,
                                               self.geo, self.theaters, set()), parts)
        self.assertNotIn('campaign', state['incident_episode_repaired'])
        out = self.incidents.repair(parts, state, lambda *a, **k: self.reply, {}, NOW,
                                   self.geo, self.theaters, set())
        self.assertEqual(len(out), 3)
        self.assertIn('campaign', state['incident_episode_repaired'])
        self.assertEqual(sorted(r['url'] for e in out for r in e['reports']),
                         sorted(r['url'] for e in parts for r in e['reports']))
        def forbidden(*a, **k):
            self.fail('chronology repair must only happen once')
        self.assertEqual(self.incidents.repair(out, state, forbidden, {}, NOW, self.geo, self.theaters, set()), out)

    def test_invalid_event_type_is_not_silently_retyped(self):
        reply = deepcopy(self.reply)
        reply['groups'][0]['event']['type'] = 'new_alert_category'
        self.assertIsNone(self.parts(reply))

    def test_today_airport_followups_become_one_incident_with_all_sources(self):
        parts = self.parts()
        parts[0]['time'] = iso(NOW - timedelta(days=2))
        parts[2]['time'] = iso(NOW - timedelta(hours=4))
        coverage = event('evacuation', 0.01, 'Terminal evacuated after today\'s Riyadh airport attack.', 'missile_drone')
        coverage.update(country='SA', theater='mideast', place='Riyadh', lat=24.6389, lon=46.716)
        injured = deepcopy(coverage)
        injured.update(id='injuries', reports=[{**coverage['reports'][0], 'url': 'https://example.org/injuries',
                       'summary': '80 injured in today\'s Riyadh airport attack.', 'source': 'hospital', 'group': 'hospital'}])
        reply = deepcopy(self.reply)
        reply['groups'][2]['reports'] = [2, 3, 4]
        reply['groups'][2]['event']['injured'] = 80
        state = {}
        out = self.incidents.repair(parts + [coverage, injured], state, lambda *a, **k: reply, {}, NOW,
                                   self.geo, self.theaters, set())
        self.assertEqual(len(out), 3)
        current = next(e for e in out if e['injured'] == 80)
        self.assertEqual(len(current['reports']), 3)
        self.assertIsNone(current['killed'])
        self.assertEqual(next(e for e in out if e['killed'] == 3)['injured'], None)
        self.assertEqual(sorted(r['url'] for e in out for r in e['reports']),
                         sorted(r['url'] for e in parts + [coverage, injured] for r in e['reports']))
        self.assertIn('evacuation', state['incident_replaced_ids'])
        merge.apply_status(out, [])
        self.assertEqual(current['sources_count'], 3)

    def test_omitted_report_is_classified_explicitly_before_atomic_validation(self):
        reply = deepcopy(self.reply)
        reply['groups'][2]['reports'] = []
        seen = []
        def ask(prompt, payload, *args, **kwargs):
            seen.append(json.loads(payload))
            return {'groups': [{'reports': [2], 'group': 2}]}
        completed = self.incidents.complete_partition(self.parent, reply, ask, {}, {}, NOW)
        self.assertEqual([r['r'] for r in seen[0]['reports']], [2])
        self.assertEqual(len(self.parts(completed)), 3)
        self.assertEqual(reply['groups'][2]['reports'], [])
        # A duplicate, invented index or invalid destination never changes the proposal.
        for patch in ({'groups': [{'reports': [2, 2], 'group': 2}]},
                      {'groups': [{'reports': [3], 'group': 2}]},
                      {'groups': [{'reports': [2], 'group': 99}]}):
            self.assertEqual(self.incidents.complete_partition(self.parent, reply, lambda *a, **k: patch,
                                                               {}, {}, NOW), reply)

    def test_facility_review_groups_followups_across_partition_roots(self):
        parts = self.parts()
        older, oil, current = parts
        older['reports'][0]['time'] = iso(NOW - timedelta(days=2))
        current['reports'][0].update(time=iso(NOW - timedelta(hours=2)),
                                    incident={'injured': 80, 'killed': None})
        current['time'] = iso(NOW - timedelta(days=1))  # an unsupported inherited occurrence date
        follow = deepcopy(current)
        follow.update(id='evacuation', incident_split='other-partition', reports=[{
            **current['reports'][0], 'url': 'https://example.org/evacuation',
            'summary': 'Terminal evacuated after today\'s airport attack.', 'incident': {}}])
        reply = {'groups': [
            {'ids': [older['id']], 'summary': 'Three killed in the earlier airport attack.', 'happened': older['reports'][0]['time'], 'killed': 3, 'injured': None},
            {'ids': [current['id'], 'evacuation'], 'summary': 'Today\'s airport attack injured 80 people and prompted evacuation.', 'happened': current['time'], 'killed': None, 'injured': 80}]}
        from common import short_hash
        state = {'facility_episode_reviews': {short_hash('SA', 'King Khalid International Airport'):
                 short_hash(*sorted([older['id'], current['id'], 'evacuation']))}}
        out, folded = self.incidents.group_facility_episodes(parts + [follow], state, lambda *a, **k: reply, {}, NOW, set())
        self.assertEqual(len(out), 3)  # oil field untouched, two airport episodes
        self.assertEqual(len(folded), 1)
        new = next(e for e in out if e.get('injured') == 80)
        self.assertEqual(new['time'], current['reports'][0]['time'])
        self.assertIsNone(new['killed'])
        self.assertEqual(len(new['reports']), 2)
        self.assertEqual(sorted((r['url'], r['summary']) for e in out for r in e['reports']),
                         sorted((r['url'], r['summary']) for e in parts + [follow] for r in e['reports']))
        def forbidden(*a, **k):
            self.fail('unchanged identities should not need another review')
        self.assertEqual(self.incidents.group_facility_episodes(out, state, forbidden, {}, NOW, set())[0], out)
        bad = deepcopy(reply)
        bad['groups'][1]['ids'].remove('evacuation')
        self.assertEqual(self.incidents.group_facility_episodes(parts + [follow], {}, lambda *a, **k: bad, {}, NOW, set()),
                         (parts + [follow], []))

    def test_reviewed_episode_routes_continuing_updates_without_another_model_call(self):
        old, oil, current = self.parts()
        old.update(time=iso(NOW - timedelta(days=2)), killed=3)
        current.update(time=iso(NOW - timedelta(hours=2)), injured=80)
        registry = {e['id']: {'country': 'SA', 'day': e['time'][:10], 'lat': e['lat'], 'lon': e['lon'],
                             'attacker': 'YE', 'killed': e['killed'], 'aliases': ['riyadh', 'king khalid']}
                    for e in (old, current)}
        def update(eid, text, **changes):
            item = deepcopy(current)
            item.update(id=eid, summary=text, time=iso(NOW), incident_split=None, killed=None, injured=None,
                        reports=[{**current['reports'][0], 'url': 'https://example.org/' + eid, 'summary': text, 'time': iso(NOW)}], **changes)
            return item
        coverage = [update('footage', 'New video shows the attack on Riyadh airport.'),
                    update('suspension', 'Riyadh airport suspended operations following the attack.', type='diplomacy'),
                    update('warning', 'The UK warns citizens to avoid Riyadh airport after the attack.', type='diplomacy'),
                    update('again', 'Riyadh airport is attacked again hours after earlier blasts.')]
        # A city-centroid error must not split an explicitly identified airport update.
        centroid = update('centroid', 'Officials say the airport in Saudi Arabia\'s capital was attacked again.',
                          place='Riyadh', lat=25.2663, lon=47.7789)
        coverage.append(centroid)
        prior = update('prior-count', 'Three killed in the Riyadh airport strike.')
        prior['killed'] = 3
        policy = update('airspace', 'EASA expanded its airspace warning following Riyadh airport attacks.', type='diplomacy')
        other = update('other-actor', 'A US strike hit Riyadh airport.', attacker='US')
        state = {'facility_episodes': registry}
        out, folded = self.incidents.route_facility_updates([old, oil, current] + coverage + [prior, policy, other], state, set())
        self.assertEqual({e['id'] for e in folded}, {e['id'] for e in coverage})
        self.assertEqual(len(current['reports']), 6)
        self.assertEqual(current['injured'], 80)
        self.assertIsNone(current['killed'])
        self.assertEqual(current['time'], iso(NOW - timedelta(hours=2)))
        self.assertTrue({'prior-count', 'airspace', 'other-actor'} <= {e['id'] for e in out})


if __name__ == "__main__":
    unittest.main()


class FacilitySourceProtocolTests(unittest.TestCase):
    def fixture(self):
        helper = CampaignRepairTests()
        helper.setUp()
        old, _, current = helper.parts()
        old['time'] = iso(NOW - timedelta(days=2))
        old['reports'][0]['time'] = old['time']
        current['time'] = iso(NOW)
        current['reports'][0]['time'] = current['time']
        current['reports'][0]['summary'] = 'Riyadh airport attacked again two days after three died; 80 injured today.'
        current['reports'][0]['incident'] = {'injured': 80, 'killed': None}
        current['injured'] = 80
        mixed = deepcopy(old)
        mixed['reports'] += deepcopy(current['reports'])
        mixed['injured'] = 80
        follow = deepcopy(current)
        follow.update(id='follow-up', reports=[{**current['reports'][0], 'url': 'https://example.org/footage',
            'summary': 'Footage of the ongoing Riyadh airport attack today.'}])
        prototypes = []
        for key, e in [('old', old), ('today', current)]:
            prototypes.append({'key': key, **{k: e.get(k) for k in ('type', 'summary', 'place', 'country',
                'theater', 'attacker', 'severity', 'killed', 'injured', 'lat', 'lon')}, 'happened': e['time']})
        return helper, mixed, follow, prototypes

    def test_mixed_casualties_are_repaired_and_followup_joins_current_episode(self):
        helper, mixed, follow, prototypes = self.fixture()
        calls = []
        def ask(prompt, payload, *args, **kwargs):
            b = json.loads(payload)
            calls.append(b)
            if 'episodes' not in b:
                return {'groups': prototypes}
            return {'assignments': [{'r': r['r'], 'episode': 'old' if 'Three killed' in r['summary'] else 'today'} for r in b['reports']]}
        state = {}
        out, folded = helper.incidents.repair_facility_sources([mixed, follow], [mixed, follow], mixed,
            state, ask, {}, NOW, helper.geo, helper.theaters)
        self.assertEqual(len(out), 2)
        old = next(e for e in out if e['killed'] == 3)
        today = next(e for e in out if e['injured'] == 80)
        self.assertIsNone(old['injured'])
        self.assertIsNone(today['killed'])
        self.assertEqual(old['id'], mixed['id'])
        self.assertEqual(today['id'], follow['id'])
        self.assertEqual(sum(len(e['reports']) for e in out), 3)
        self.assertEqual({r['url'] for e in out for r in e['reports']},
                         {r['url'] for e in [mixed, follow] for r in e['reports']})
        self.assertTrue(all(v['source_reviewed'] for v in state['facility_episodes'].values()))
        # A later model answer cannot undo this source-level episode partition.
        def bad_merge(*args, **kwargs):
            return {'groups': [{'ids': [e['id'] for e in out], 'summary': 'Combined airport attack.',
                'happened': None, 'killed': 3, 'injured': 80}]}
        preserved, _ = helper.incidents.group_facility_episodes(out, state, bad_merge, {}, NOW, set())
        self.assertEqual(preserved, out)

    def test_incomplete_batch_preserves_all_originals_and_registry(self):
        helper, mixed, follow, prototypes = self.fixture()
        before = deepcopy([mixed, follow])
        state = {'facility_episodes': {'existing': {'source_reviewed': True}}}
        saved = deepcopy(state)
        def ask(prompt, payload, *args, **kwargs):
            return {'assignments': [{'r': 0, 'episode': 'old'}]} if 'episodes' in json.loads(payload) else {'groups': prototypes}
        out, folded = helper.incidents.repair_facility_sources(before, before, mixed, state, ask, {}, NOW,
            helper.geo, helper.theaters)
        self.assertEqual(out, before)
        self.assertEqual(state["facility_episodes"], saved["facility_episodes"])
        self.assertEqual(state["facility_source_error"]["stage"], "batch")
        self.assertEqual(folded, [])

    def test_confirmed_episode_keeps_identity_date_and_known_count(self):
        helper, mixed, follow, prototypes = self.fixture()
        confirmed = deepcopy(follow)
        confirmed.update(id='confirmed', time=iso(NOW), injured=80)
        update = deepcopy(follow)
        update.update(id='ambiguous', time=iso(NOW - timedelta(days=1)))
        update['reports'][0]['url'] = 'https://example.org/ambiguous'
        update['reports'][0]['time'] = iso(NOW - timedelta(days=1))
        state = {'facility_episodes': {'confirmed': {'country': 'SA', 'day': iso(NOW)[:10],
            'lat': confirmed['lat'], 'lon': confirmed['lon'], 'aliases': ['king khalid'],
            'first_report': iso(NOW), 'source_reviewed': True}}}
        def ask(*args, **kwargs):
            return {'groups': [{'ids': ['ambiguous', 'confirmed'], 'summary': 'Reports of the continuing airport attack.',
                'happened': iso(NOW - timedelta(days=1)), 'injured': None, 'killed': None}]}
        out, folded = helper.incidents.group_facility_episodes([confirmed, update], state, ask, {}, NOW, set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['id'], 'confirmed')
        self.assertEqual(out[0]['time'], iso(NOW))
        self.assertEqual(out[0]['injured'], 80)
        self.assertEqual([e['id'] for e in folded], ['ambiguous'])

    def test_repeated_prior_fatalities_cannot_contaminate_renewed_attack(self):
        helper, mixed, follow, prototypes = self.fixture()
        older = deepcopy(mixed)
        older.update(id='older', reports=[mixed['reports'][0]], time=iso(NOW - timedelta(days=1)), injured=None)
        newer = deepcopy(follow)
        newer.update(id='newer', killed=3, injured=80,
            reports=[mixed['reports'][1], {**mixed['reports'][0], 'time': iso(NOW),
                'url': 'https://example.org/repeated-toll', 'incident': {'killed': 3}}])
        registry = {e['id']: {'country': 'SA', 'day': e['time'][:10], 'killed': 3,
            'aliases': ['king khalid', 'riyadh'], 'source_reviewed': True} for e in [older, newer]}
        before = {r['url'] for e in [older, newer] for r in e['reports']}
        out = helper.incidents.protect_prior_casualties([older, newer], {'facility_episodes': registry})
        self.assertIsNone(newer['killed'])
        self.assertEqual(newer['injured'], 80)
        self.assertEqual(older['killed'], 3)
        self.assertEqual(older['time'][:10], iso(NOW - timedelta(days=1))[:10])
        self.assertEqual({r['url'] for e in out for r in e['reports']}, before)
        self.assertEqual(len(newer['reports']), 1)
        self.assertEqual(len(older['reports']), 2)
        self.assertIn('renewed', newer['summary'])
        # A new, explicitly dated fatality report is not assumed to repeat the earlier toll.
        newer['reports'].append({**newer['reports'][0], 'url': 'https://example.org/new-deaths',
            'summary': 'The renewed attack today killed three people.', 'incident': {'killed': 3}})
        newer['killed'] = 3
        helper.incidents.protect_prior_casualties(out, {'facility_episodes': registry})
        self.assertEqual(newer['killed'], 3)

    def test_two_confirmed_days_keep_their_ids_when_reports_are_published_today(self):
        helper, mixed, follow, prototypes = self.fixture()
        yesterday = deepcopy(mixed)
        yesterday.update(id='yesterday', time=iso(NOW - timedelta(days=1)), injured=None,
                         reports=[{**mixed['reports'][0], 'time': iso(NOW),
                                   'summary': "Yesterday's attack on Riyadh airport killed three people."}])
        today = deepcopy(follow)
        today.update(id='today', time=iso(NOW), killed=None, injured=80)
        confirmed = [{"key": e['id'], **{k: e.get(k) for k in ('type', 'summary', 'place', 'country',
                'theater', 'attacker', 'severity', 'killed', 'injured', 'lat', 'lon')},
                'happened': e['time'], 'occurrence_day': e['time'][:10]} for e in [yesterday, today]]
        def ask(prompt, payload, *args, **kwargs):
            body = json.loads(payload)
            self.assertIn('episodes', body, 'Reviewed episodes must not be replanned')
            return {'assignments': [{'r': r['r'], 'episode': 'yesterday' if 'Yesterday' in r['summary'] else 'today'} for r in body['reports']]}
        out, _ = helper.incidents.repair_facility_sources([yesterday, today], [yesterday, today], yesterday,
            {}, ask, {}, NOW, helper.geo, helper.theaters, confirmed=confirmed)
        self.assertEqual({e['id']: e['time'] for e in out}, {'yesterday': yesterday['time'], 'today': today['time']})
        self.assertEqual(sum(len(e['reports']) for e in out), 2)
        self.assertEqual({e['id']: e['occurrence_day'] for e in out}, {'yesterday': yesterday['time'][:10], 'today': today['time'][:10]})

    def test_publication_today_cannot_assign_undated_footage_to_todays_attack(self):
        helper, mixed, follow, _ = self.fixture()
        older = deepcopy(mixed)
        older.update(id='older', time=iso(NOW - timedelta(days=1)), reports=[mixed['reports'][0]], injured=None)
        newer = deepcopy(follow)
        newer.update(id='newer')
        footage = deepcopy(follow)
        footage.update(id='undated', summary='Video shows the missile strike on Riyadh airport.')
        footage['reports'][0]['summary'] = footage['summary']
        state = {'facility_episodes': {e['id']: {'country': 'SA', 'day': e['time'][:10],
            'lat': e['lat'], 'lon': e['lon'], 'aliases': ['riyadh', 'king khalid'], 'source_reviewed': True}
            for e in [older, newer]}}
        out, folded = helper.incidents.route_facility_updates([older, newer, footage], state, set())
        self.assertEqual(len(out), 3)
        self.assertEqual(folded, [], 'This source needs identity review, not a publication-day merge')

    def test_pending_two_episode_repair_precedes_a_more_crowded_other_facility(self):
        helper, mixed, follow, _ = self.fixture()
        older = deepcopy(mixed)
        older.update(id='older', time=iso(NOW - timedelta(days=1)), reports=[mixed['reports'][0]], injured=None)
        newer = deepcopy(follow)
        newer.update(id='newer')
        unrelated = [event(f'cargo-{i}', 0, 'Transport aircraft arrived at Ramstein Air Base.', 'arms_transfer') for i in range(8)]
        for e in unrelated:
            e.update(country='DE', place='Ramstein Air Base', lat=49.44, lon=7.60)
        state = {'facility_episodes': {e['id']: {'country': 'SA', 'day': e['time'][:10],
            'lat': e['lat'], 'lon': e['lon'], 'aliases': ['king khalid', 'riyadh'], 'source_reviewed': True}
            for e in [older, newer]}}
        calls = []
        def ask(prompt, payload, *args, **kwargs):
            body = json.loads(payload)
            calls.append(body)
            self.assertEqual({p['key'] for p in body['episodes']}, {'older', 'newer'})
            return None
        helper.incidents.group_facility_episodes([older, newer] + unrelated, state, ask, {}, NOW,
            set(), helper.geo, helper.theaters)
        self.assertEqual(len(calls), 1)
