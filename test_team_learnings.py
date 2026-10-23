"""Learnings for The Machine team (team_learnings.py): about once a week one turn distils what the Machine learned into a
few general learnings, the service strips anything personal, and the owner is asked. Nothing goes without their Send
on exactly what they saw; an error log only when ticked; sealed for the team's key and signed by a key for learnings
only, never the Machine's Network identity. The Network is a stand-in here: nothing is sent anywhere."""
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import network_crypto as nc
import team_learnings as tl

PINNED = tl.TEAM_KEY_FINGERPRINT  # as shipped: empty until the team key is made, then its fingerprint

spec = importlib.util.spec_from_file_location('learnings_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

PERSONAL = ('Sam Lee', 'Globex', 'sam@globex.example', 'Office move', 'https://globex.example/deal', '+32 470 12 34 56')


class Network:
    """The Network's learnings route, as the control plane answers it: the team's public key, and what was sent."""
    def __init__(self, team=None):
        self.team = team or nc.new_keys()
        self.sent, self.fail = [], False

    def call(self, method, path, body=None, timeout=40):
        assert (method, path) == ('POST', '/learnings'), (method, path)
        if body['action'] == 'key':
            return {'keys': nc.public_keys(self.team)}
        if self.fail:
            raise OSError('connection reset')
        self.sent.append(json.loads(json.dumps(body)))
        return {'ok': True}

    def opened(self, n=-1):
        body = self.sent[n]
        me = nc.check_keys(body['from'])['fingerprint']
        team = nc.check_keys(nc.public_keys(self.team))['fingerprint']
        return nc.open_sealed(body['sealed'], self.team, body['from'], {**tl.CONTEXT, 'f': me, 't': team, 'r': body['id']})


class TeamLearnings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Path(self.temp.name) / 'events.sqlite'
        self.store = a.Store(database)
        self.store.accounts = [{'id': 'work', 'service': 'gmail', 'email': 'owner@globex.example', 'display_name': 'Owner Work'}]
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': self.store.accounts, 'database': str(database), 'review_worker': {'enabled': True}}
        self.network = Network()
        pin = patch.object(tl, 'TEAM_KEY_FINGERPRINT', nc.check_keys(nc.public_keys(self.network.team))['fingerprint'])
        pin.start()
        self.addCleanup(pin.stop)
        self.app.fleet = types.SimpleNamespace(relay=types.SimpleNamespace(call=self.network.call))
        with self.store.connect() as db:
            for node, kind, name in (('email:sam@globex.example', 'person', 'Sam Lee'), ('org:globex.example', 'org', 'Globex'),
                                     ('project:1', 'project', 'Office move'), ('org:whatsapp.com', 'org', 'WhatsApp')):
                db.execute("INSERT INTO graph_nodes(id,kind,name,strength,signals,info) VALUES(?,?,?,1,1,'{}')", (node, kind, name))
            for i, note in enumerate(('Introduce yourself first, Sam Lee has never heard of us', 'Send it on WhatsApp instead',
                                      'Say who we are before the ask, they don’t know the context')):
                db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,status,created_at) "
                           "VALUES(?,?,'Reply','','[]','[\"work\"]','done',?)", (f's{i}', f's{i}', a.now()))
                db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,?,?,?)",
                           (f'd{i}', f's{i}', 'other', note, 'dashboard', a.now()))
            db.execute("INSERT INTO faults(fingerprint,day,kind,frames,location,version,first_at,last_at,count,message) "
                       "VALUES('fp1',?,'KeyError','x','graph.py:120 build','abc',?,?,7,'missing key \"…\"')", (a.now()[:10], a.now(), a.now()))

    fresh = True

    def distil(self, learnings):
        """One learnings run, as the turn does it: started by the service, read, saved, finished."""
        run_id = self.store.learnings_due()
        self.assertTrue(run_id)
        session = a.PROFILE_SESSION_PREFIX + run_id
        page = self.store.profile_evidence(1, session)
        self.assertEqual([f['fingerprint'] for f in page['faults']], ['fp1'] if self.fresh else [])
        self.assertIn('Send it on WhatsApp instead', json.dumps(page['rows']))  # the owner's own notes reach their own agent only
        def runner(request):
            if request['action'] == 'start':
                self.assertIn('Nothing personal', request['message'])
                self.assertIn('Judge by what the notes mean', request['message'])
                self.store.save_profile({'learnings': learnings}, session)
                return {'status': 'admitted'}
            return {'status': 'ok'}
        self.app.profile_step(runner)
        self.app.profile_step(runner)
        return self.app.learnings_view()['ask']

    def test_strip_takes_out_names_addresses_and_numbers_and_keeps_product_names(self):
        with self.store.connect() as db:
            names = tl.known_names(db, {'accounts': self.store.accounts})
        text, taken = tl.strip('Sam Lee at Globex wants the Office move update on WhatsApp: sam@globex.example, '
                               'https://globex.example/deal, +32 470 12 34 56, €1,200 by 2026-10-30. Sam said so.', names)
        for bit in PERSONAL + ('1,200', '2026-10-30', 'Sam'):
            self.assertNotIn(bit, text)
        self.assertIn('WhatsApp', text)  # what The Machine works with is the product, not a person
        self.assertEqual(text[:8], 'Someone ')
        self.assertEqual(tl.taken_line(taken), 'Took out an address, a link, a date, an amount, a number and 4 names.')
        # A single name only with its capital: "will" stays, "Will" the person doesn't
        self.assertEqual(tl.strip('It will help Will.', [('Will', 'person')])[0], 'It will help someone.')
        self.assertEqual(tl.strip('Drafts should introduce the context first.', names), ('Drafts should introduce the context first.', []))

    def test_a_week_of_notes_becomes_learnings_the_owner_reads_edits_and_sends_sealed(self):
        ask = self.distil([
            {'text': 'Drafts for people who don’t know Sam Lee or Globex yet should introduce the context first.',
             'example': 'A first email to a new supplier opens with who we are.', 'area': 'drafts'},
            {'text': 'People often switch a card’s channel to WhatsApp in chat; a button would save a step.', 'area': 'chat'},
            {'text': 'Graph updates fail now and then.', 'area': 'nonsense', 'fault': 'fp1'},
            {'text': 'short'}])  # too short to say anything: dropped
        self.assertEqual([i['n'] for i in ask['items']], [1, 2, 3])
        first, second, third = ask['items']
        self.assertNotIn('Sam', first['text'])
        self.assertNotIn('Globex', first['text'])
        self.assertEqual(first['taken'], 'Took out 2 names.')
        self.assertEqual((second['taken'], third['area']), ('', 'other'))
        self.assertIsNone(first['log'])
        self.assertIn('KeyError', third['log'])
        self.assertIn('graph.py:120', third['log'])
        self.assertEqual(self.network.sent, [])  # asked, never sent by itself
        self.assertIn('**For The Machine team**', a.weekly_recap_message(self.store, {'accounts': []})['text'])

        # An edit that puts a name back in is refused, so what goes is always what the owner saw
        with self.assertRaisesRegex(ValueError, 'take out a name first'):
            self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 1, 'text': 'Sam Lee should be introduced.'}]}, owner_dashboard=True)
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 2}]})
        self.assertEqual(self.network.sent, [])

        # The owner keeps two (one edited), ticks the log on the third, and leaves the first out
        done = self.app.action('learnings_share', {'id': ask['id'], 'items': [
            {'n': 2, 'text': 'People often switch a card’s channel to WhatsApp in chat.', 'log': True},
            {'n': 3, 'log': True}]}, owner_dashboard=True)
        self.assertEqual((done['status'], done['count']), ('sent', 2))
        self.assertIsNone(done['team_learnings']['ask'])
        body = self.network.sent[0]
        wire = json.dumps(body)
        for bit in PERSONAL + ('WhatsApp in chat', 'KeyError', 'owner@'):
            self.assertNotIn(bit, wire)  # sealed: the Network sees none of it
        self.assertFalse(body['named'])
        opened = self.network.opened()
        self.assertEqual([i['text'] for i in opened['items']],
                         ['People often switch a card’s channel to WhatsApp in chat.', 'Graph updates fail now and then.'])
        self.assertNotIn('log', opened['items'][0])  # it had none: a tick can't make one up
        self.assertIn('KeyError', opened['items'][1]['log'])
        # Signed with a key for learnings only, not the Machine's Network identity
        self.assertTrue((Path(self.temp.name) / 'learnings-keys.json').exists())
        self.assertFalse((Path(self.temp.name) / 'network-keys.json').exists())
        with self.assertRaisesRegex(ValueError, 'no longer waiting'):  # once
            self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 2}]}, owner_dashboard=True)
        self.assertEqual(self.app.learnings_view()['last_shared']['count'], 2)

    def test_logs_stay_home_unless_ticked_and_a_failed_send_keeps_the_batch(self):
        ask = self.distil([{'text': 'Graph updates fail now and then on a busy day.', 'fault': 'fp1'}])
        self.network.fail = True
        with self.assertRaisesRegex(ValueError, 'didn’t take it'):
            self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 1}]}, owner_dashboard=True)
        again = self.app.learnings_view()['ask']
        self.assertEqual((again['id'], again['error']), (ask['id'], 'not_sent'))
        self.network.fail = False
        self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 1}], 'named': True}, owner_dashboard=True)
        self.assertNotIn('log', self.network.opened()['items'][0])
        self.assertTrue(self.network.sent[0]['named'])

    def test_another_team_key_than_the_pinned_one_sends_nothing(self):
        ask = self.distil([{'text': 'Cards about invoices should show the due date first.'}])
        self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 1}]}, owner_dashboard=True)
        with self.store.connect() as db:
            db.execute("UPDATE profile_runs SET started_at='2000-01-01' WHERE kind='learnings'")
            db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('a','d0',?),('b','d1',?),('c','d2',?)", (a.now(),) * 3)
        self.network.team = nc.new_keys()  # the Network hands out another key than the one pinned in the code
        ask = self.distil([{'text': 'Cards about invoices should show the amount first.'}])
        with self.assertRaisesRegex(ValueError, 'isn’t the team’s key'):
            self.app.action('learnings_share', {'id': ask['id'], 'items': [{'n': 1}]}, owner_dashboard=True)
        self.assertEqual(len(self.network.sent), 1)

    def test_until_the_team_key_is_pinned_no_one_is_asked(self):
        with patch.object(tl, 'TEAM_KEY_FINGERPRINT', ''):
            self.assertIsNone(self.store.learnings_due())
            self.assertFalse(self.app.learnings_view()['network'])  # no card, no Settings switch
        self.assertRegex(tl.TEAM_KEY_FINGERPRINT, r'^([0-9a-f]{4} ){5}[0-9a-f]{4}$')  # the test's pin, in the form keygen prints
        self.assertTrue(PINNED == '' or re.fullmatch(r'([0-9a-f]{4} ){5}[0-9a-f]{4}', PINNED), PINNED)

    def test_not_now_never_ask_and_the_settings_switch(self):
        ask = self.distil([{'text': 'Drafts for new contacts should introduce the context first.'}])
        self.assertEqual(self.app.action('learnings_later', {'id': ask['id']}, owner_dashboard=True)['team_learnings']['ask'], None)
        self.assertIsNone(self.store.learnings_due())  # not again within the week
        with patch.object(a, 'now', return_value=(a.dt.datetime.fromisoformat(a.now()) + a.dt.timedelta(days=8)).isoformat()):
            with self.store.connect() as db:
                db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('a','d0',?),('b','d1',?),('c','d2',?)", (a.now(),) * 3)
            self.fresh = False  # the error was more than a week ago
            ask = self.distil([{'text': 'Drafts for new contacts should introduce the context first, again.'}])
            self.assertEqual(self.store.learnings_due(), None)  # one waits for the owner: no second one meanwhile
            view = self.app.action('learnings_never', {'id': ask['id']}, owner_dashboard=True)['team_learnings']
            self.assertEqual((view['offer'], view['ask']), (False, None))
        with patch.object(a, 'now', return_value=(a.dt.datetime.fromisoformat(a.now()) + a.dt.timedelta(days=30)).isoformat()):
            self.assertIsNone(self.store.learnings_due())  # Never ask: no run at all
            with self.store.connect() as db:
                db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('d','d0',?),('e','d1',?),('f','d2',?)", (a.now(),) * 3)
            self.assertIsNone(self.store.learnings_due())
            self.assertTrue(self.app.action('set_learnings_offer', {'on': True}, owner_dashboard=True)['team_learnings']['offer'])
            self.assertTrue(self.store.learnings_due())
        self.assertEqual(self.network.sent, [])
        self.assertTrue(a.LEARNING_ACTIONS <= a.OWNER_ACTIONS)

    def test_no_network_no_offer(self):
        self.app.fleet = None
        self.assertEqual(self.app.learnings_view()['network'], False)
        with self.assertRaisesRegex(ValueError, 'needs The Machine Network'):
            self.app.action('learnings_share', {'id': 'x', 'items': [{'n': 1}]}, owner_dashboard=True)
        with patch.object(self.store, 'learnings_due') as due:
            self.app.style_step()
        due.assert_not_called()


if __name__ == '__main__':
    unittest.main()
