"""Security levels (security_levels.py): Standard is exactly today's defaults, Careful sets the stricter ones, a change
after shows Custom, and a Careful Work Fleet is a floor for this Machine's answers, notifications, lookups and the
Shared Graph's attachments. Sends need the owner's OK in both."""
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('levels_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
fleet, levels, retention, team_learnings, enrichment = a.fleet, a.security_levels, a.retention, a.team_learnings, a.enrichment
WORK = {'id': 'work', 'email': 'owner@acme.example', 'service': 'gmail', 'boundary': 'work:Acme'}
HOME = {'id': 'home', 'email': 'owner@gmail.com', 'service': 'gmail', 'boundary': 'personal:Home'}
CONFIG = {'accounts': [WORK, HOME], 'fleet': {'url': 'https://cp.example/api/fleet'}, 'credentials': {}, 'dashboard_host': 'm.example'}
MEMBERS = [{'member_id': 'me1', 'name': 'Owner', 'role': 'member', 'me': True, 'emails': ['owner@acme.example']},
           {'member_id': 'pat1', 'name': 'Pat', 'role': 'owner', 'emails': ['pat@acme.example']}]


class Relay:
    def __init__(self, level='standard', family=False, friends=False):
        work = {'id': 'fl_' + 'a' * 16, 'kind': 'work', 'name': 'Acme', 'secret': 's' * 64, 'asks': ['relationships', 'meeting'],
                'members': MEMBERS}
        if level:
            work['security'] = {'level': level}
        self.directory = {'fleets': [work] + ([{'id': 'fl_' + 'b' * 16, 'kind': 'family', 'name': 'Home', 'secret': 't' * 64,
                                                 'asks': [], 'members': [MEMBERS[0], {'member_id': 'kid1', 'name': 'Kid', 'role': 'member'}]}] if family else [])
                          + ([{'id': 'fr_' + 'c' * 16, 'kind': 'friends', 'name': 'Friends', 'members': [MEMBERS[0]]}] if friends else [])}

    def call(self, method, path, body=None, timeout=40):
        return self.directory if path == '/directory' else {'ok': True}


class Levels(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')

    def fleet(self, level='standard', **kw):
        found = fleet.Fleet(self.store.connect, CONFIG, lambda: 'token', relay=Relay(level, **kw))
        found.directory(refresh=True)
        return found

    def test_standard_is_exactly_todays_defaults(self):
        self.fleet(family=True, friends=True)
        with self.store.connect() as db:
            self.assertEqual(levels.label(db), 'standard')
            self.assertTrue(team_learnings.offer_on(db))
            self.assertTrue(enrichment.people_on(db))
            self.assertTrue(levels.notify_text(db, work=True))
            self.assertTrue(levels.save_logins(db))
            self.assertEqual(levels.keep_days(db), retention.PREPARATION_DAYS)
            now = levels.current(db)
        self.assertEqual(now['family_availability'], fleet.family.DEFAULT_RULES['availability'])
        self.assertEqual(now['friends_meeting'], 'ask')

    def test_careful_sets_its_values_and_a_change_after_is_custom(self):
        f = self.fleet(family=True, friends=True)
        with self.store.connect() as db:
            rules = levels.apply(db, 'careful')
        for ref, kind, rule in rules:
            f.set_rule(ref, fleet.ALL, kind, rule)
        with self.store.connect() as db:
            self.assertEqual(levels.label(db), 'careful')
            self.assertFalse(team_learnings.offer_on(db))
            self.assertFalse(enrichment.people_on(db))
            self.assertFalse(levels.notify_text(db))
            self.assertFalse(levels.save_logins(db))
            self.assertEqual(levels.keep_days(db), 30)
            self.assertEqual(f.rule(db, 'fl_' + 'b' * 16, 'kid1', 'availability')[0], 'ask')
            self.assertEqual(f.rule(db, 'fr_' + 'c' * 16, 'x', 'meeting')[0], 'never')
            levels.set_pref(db, 'save_logins', 'on')
            self.assertEqual(levels.label(db), 'custom')
            rules = levels.apply(db, 'standard')
        for ref, kind, rule in rules:
            f.set_rule(ref, fleet.ALL, kind, rule)
        with self.store.connect() as db:
            self.assertEqual(levels.label(db), 'standard')
        with self.assertRaises(ValueError):
            with self.store.connect() as db:
                levels.apply(db, 'paranoid')

    def test_a_careful_work_fleet_is_a_floor_that_keeps_the_members_own_choice(self):
        f = self.fleet('careful')
        ref = 'fl_' + 'a' * 16
        f.set_rule(ref, fleet.ALL, 'relationships', 'always', details=True)
        with self.store.connect() as db:
            self.assertEqual(f.rule(db, ref, 'pat1', 'relationships'), ('ask', False))
            self.assertEqual(f.rule(db, ref, 'pat1', 'relationships', raw=True), ('always', True))  # kept as stored
            self.assertEqual(f.rule(db, ref, 'pat1', 'meeting'), ('ask', False))
            self.assertTrue(f.scope(db, ref, 'pat1', 'relationships')['work_only'])
            self.assertFalse(enrichment.people_on(db))  # no lookups, whatever the owner's own switch
            self.assertFalse(levels.notify_text(db, work=True))
            self.assertTrue(levels.notify_text(db, work=False))  # the Fleet sets nothing for personal cards
            self.assertEqual(levels.label(db), 'standard')  # the owner's own level is their own
        never = f.set_rule(ref, fleet.ALL, 'relationships', 'never')
        self.assertEqual(never['rule'], 'never')  # stricter still works
        self.assertEqual(f.view()['fleets'][0]['security'], 'careful')
        # Back to Standard: the stored choice applies again.
        f.relay.directory['fleets'][0]['security'] = {'level': 'standard'}
        f.directory(refresh=True)
        f.set_rule(ref, fleet.ALL, 'relationships', 'always', details=True)
        with self.store.connect() as db:
            self.assertEqual(f.rule(db, ref, 'pat1', 'relationships'), ('always', True))
            self.assertTrue(enrichment.people_on(db))

    def test_a_directory_without_a_level_is_standard(self):
        f = self.fleet(None)
        self.assertEqual(f.level('work'), 'standard')
        self.assertEqual(levels.fleet_level({'kind': 'family', 'security': {'level': 'careful'}}), 'standard')

    def test_notifications_leave_out_card_text_when_careful(self):
        self.fleet('careful')
        with self.store.connect() as db:
            db.execute("INSERT INTO push_subscriptions(endpoint,p256dh,auth,created_at) VALUES('https://push.example/1','k','a','2026-10-24')")
        sent = []
        sender = lambda request: sent.append(request['payload']) or {'results': []}
        row = {'id': 's1', 'title': 'Reply to Lee about the 40k deal', 'body': 'Lee asks for a discount.', 'account_ids': json.dumps(['work'])}
        a.push_notify(self.store, CONFIG, row, sender)
        self.assertEqual((sent[-1]['title'], sent[-1]['body']), ('New card', ''))
        a.push_notify(self.store, CONFIG, {**row, 'account_ids': json.dumps(['home'])}, sender)
        self.assertEqual(sent[-1]['title'], row['title'])  # a personal card: the owner's own setting, on
        with self.store.connect() as db:
            levels.set_pref(db, 'notify_text', 'off')
        a.push_notify(self.store, CONFIG, {**row, 'account_ids': json.dumps(['home'])}, sender)
        self.assertNotIn('Lee', json.dumps(sent[-1]))

    def test_careful_keeps_do_it_details_30_days(self):
        with self.store.connect() as db:
            levels.set_pref(db, 'keep_days', '30')
        self.assertEqual(retention._keep_days(self.store.connect, retention.PREPARATION_DAYS), 30)

    def test_sends_need_the_owner_in_both_levels(self):
        # The levels never touch what needs the owner's OK: every send, calendar write and final step.
        self.assertEqual(a.next_actions.APPROVE_KINDS, a.next_actions.SEND_KINDS + ('merge', 'calendar'))
        source = Path(__file__).with_name('security_levels.py').read_text(encoding='utf-8')
        for word in ('APPROVE_KINDS', 'agent_jobs', 'confirm_action'):
            self.assertNotIn(word, source.split('"""', 2)[2])


if __name__ == '__main__':
    unittest.main()
