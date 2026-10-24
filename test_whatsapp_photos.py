"""WhatsApp pictures, asked through the owner's always-on WhatsApp mirror: only people already in the graph without a
photo, at most 50 asks a day, each picture saved once here; this Machine's own WhatsApp reads stay read-only."""
import datetime as dt
import json
import subprocess
import unittest
from unittest import mock

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment
NOW = tg.NOW.isoformat()
JPEG = (b'\xff\xd8\xff' + b'0' * 50, 'image/jpeg')


def group(days_ago, phone, name):
    n = next(tg.counter)
    return {'account_id': 'chat', 'source_id': f'g{n}', 'kind': 'whatsapp.message', 'occurred_at': tg.at(days_ago), 'title': 'Book club', 'body': 'Hi',
            'deleted': False, 'metadata': {'chat_id': '120363000000000001@g.us', 'chat_name': 'Book club', 'from_me': False, 'from': name,
                                           'sender_id': phone + '@s.whatsapp.net'}}


class WhatsAppPhotos(unittest.TestCase):
    add, build = tg.Graph.add, tg.Graph.build

    def setUp(self):
        tg.Graph.setUp(self)
        self.asked = []

    def ask(self, answers):
        def ask(account, jid):
            self.asked.append((account, jid))
            return answers(jid) if callable(answers) else answers
        return ask

    def step(self, answers, now=NOW, **kwargs):
        return enrichment.whatsapp_photos_step(self.store.connect, now, self.ask(answers), ['chat'], fetch=lambda url: JPEG, **kwargs)

    def saved(self):
        with self.store.connect() as db:
            return {r[0] for r in db.execute('SELECT identifier FROM person_photos')}

    def test_people_in_the_graph_get_their_picture_once_strongest_first(self):
        self.add(*(tg.chat(1, '3255501', 'Ann Lee') for _ in range(4)), tg.chat(2, '3255502', 'Bo Kim'), group(1, '3255503', 'Cy Park'))
        self.build()
        saved = self.step({'id': '77', 'url': 'https://pps.whatsapp.net/v/t61/p.jpg'})
        self.assertEqual(saved, 3)
        self.assertEqual(self.asked[0], ('chat', '3255501@s.whatsapp.net'))  # most in touch first
        self.assertIn(('chat', '3255503@s.whatsapp.net'), self.asked)  # met only in a group: its address from their message
        self.assertTrue(all(not jid.endswith('@g.us') for _, jid in self.asked))  # a group is never asked
        self.assertEqual(self.saved(), {'wa:3255501', 'wa:3255502', 'wa:3255503'})
        self.asked.clear()
        self.assertEqual(self.step({'id': '77', 'url': 'https://pps.whatsapp.net/v/t61/p.jpg'}), 0)
        self.assertEqual(self.asked, [])  # saved here: never asked again

    def test_at_most_fifty_asks_a_day(self):
        self.add(*(tg.chat(1, str(3255600 + i), f'Person {i}') for i in range(60)))
        self.build()
        self.step({})
        self.assertEqual(len(self.asked), 50)
        self.step({}, now=(tg.NOW + dt.timedelta(hours=3)).isoformat())
        self.assertEqual(len(self.asked), 50)  # the same day: nothing left
        self.step({}, now=(tg.NOW + dt.timedelta(days=1)).isoformat())
        self.assertEqual(len(self.asked), 60)  # the next day: the rest

    def test_no_picture_is_asked_again_after_30_days(self):
        self.add(tg.chat(1, '3255701', 'Dee Fox'))
        self.build()
        self.step({})  # none, or hidden from the owner
        self.step({}, now=(tg.NOW + dt.timedelta(days=29)).isoformat())
        self.assertEqual(len(self.asked), 1)
        self.step({'id': '5', 'url': 'https://pps.whatsapp.net/v/t61/d.jpg'}, now=(tg.NOW + dt.timedelta(days=31)).isoformat())
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.saved(), {'wa:3255701'})
        with self.store.connect() as db:  # Delete permanently takes the picture and its asks too
            graph.delete_person(db, 'wa:3255701', tg.CONFIG, NOW)
            self.assertEqual(db.execute("SELECT count(*) FROM whatsapp_photo_asks").fetchone()[0], 0)
        self.assertEqual(self.saved(), set())

    def test_a_picture_that_didnt_download_is_asked_again_the_next_day(self):
        self.add(tg.chat(1, '3255711', 'Ivy Low'))
        self.build()
        enrichment.whatsapp_photos_step(self.store.connect, NOW, self.ask({'id': '5', 'url': 'https://pps.whatsapp.net/v/i.jpg'}), ['chat'], fetch=lambda url: None)
        self.step({'id': '5', 'url': 'https://pps.whatsapp.net/v/i.jpg'}, now=(tg.NOW + dt.timedelta(hours=12)).isoformat())
        self.assertEqual(len(self.asked), 1)
        self.step({'id': '5', 'url': 'https://pps.whatsapp.net/v/i.jpg'}, now=(tg.NOW + dt.timedelta(days=1, hours=1)).isoformat())
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.saved(), {'wa:3255711'})

    def test_someone_with_a_photo_or_deleted_is_not_asked(self):
        self.add(tg.chat(1, '3255801', 'Eve Ray'), tg.chat(1, '3255802', 'Flo Day'))
        self.build()
        with self.store.connect() as db:
            db.execute("INSERT INTO person_photos(identifier,key,type,data,saved_at) VALUES('wa:3255801','k1','image/jpeg',x'ffd8ff',?)", (NOW,))
        with mock.patch.object(graph, 'forgotten', return_value=lambda identifier: identifier == 'wa:3255802'):
            self.step({'id': '1', 'url': 'https://pps.whatsapp.net/v/x.jpg'})
        self.assertEqual(self.asked, [])

    def test_when_the_mirror_cant_answer_it_waits_and_spends_nothing(self):
        self.add(tg.chat(1, '3255901', 'Gil Moe'), tg.chat(1, '3255902', 'Hal Poe'))
        self.build()
        self.assertEqual(self.step(None), 0)
        self.assertEqual(len(self.asked), 1)  # stops at the first refusal
        self.step({'id': '1', 'url': 'https://pps.whatsapp.net/v/x.jpg'}, now=(tg.NOW + dt.timedelta(hours=1)).isoformat())
        self.assertEqual(len(self.asked), 1)  # paused for a while
        self.assertEqual(self.step({'id': '1', 'url': 'https://pps.whatsapp.net/v/x.jpg'}, now=(tg.NOW + dt.timedelta(hours=7)).isoformat()), 2)
        with self.store.connect() as db:  # the refusal didn't count against the day
            self.assertEqual(db.execute('SELECT count(*) FROM whatsapp_photo_asks').fetchone()[0], 2)


class PictureRead(unittest.TestCase):
    ACCOUNT = {'id': 'chat', 'service': 'whatsapp', 'store': '/tmp/wa-store'}

    def run_with(self, stdout, returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout=json.dumps(stdout), stderr='')
        with mock.patch.object(a.subprocess, 'run', return_value=result) as run:
            try:
                return a.WhatsApp({}, self.ACCOUNT).picture('3255501@s.whatsapp.net'), run
            except a.SourceError as error:
                return error, run

    def test_the_picture_is_asked_read_only(self):
        got, run = self.run_with({'success': True, 'data': {'jid': '3255501@s.whatsapp.net', 'id': '77', 'url': 'https://pps.whatsapp.net/v/p.jpg'}, 'error': None})
        self.assertEqual(got, {'id': '77', 'url': 'https://pps.whatsapp.net/v/p.jpg'})
        args, kwargs = run.call_args
        self.assertIn('--read-only', args[0])
        self.assertEqual(args[0][-4:], ['profile', 'picture-info', '--jid', '3255501@s.whatsapp.net'])
        self.assertEqual(kwargs['env']['WACLI_READONLY'], '1')

    def test_none_hidden_or_refused(self):
        self.assertEqual(self.run_with({'success': True, 'data': {'jid': '3255501@s.whatsapp.net', 'none': True}, 'error': None})[0], {})
        self.assertIsInstance(self.run_with({'success': False, 'data': None, 'error': 'read-only mode'}, returncode=1)[0], a.SourceError)
        for jid in ('120363000000000001@g.us', 'status@broadcast', '--store=/x'):
            with self.assertRaises(a.SourceError):
                a.WhatsApp({}, self.ACCOUNT).picture(jid)
        with self.assertRaises(a.SourceError):
            a.WhatsApp({}, self.ACCOUNT).read(['profile', 'get-about', '--jid', '3255501@s.whatsapp.net'])

    def test_only_the_owners_own_accounts_are_asked(self):
        accounts, ask = a.whatsapp_photo_asker({'accounts': [self.ACCOUNT, {'id': 'team', 'service': 'whatsapp', 'store': '/x', 'shared': True}]})
        self.assertEqual(accounts, ['chat'])
        self.assertIsNone(ask('team', '3255501@s.whatsapp.net'))


if __name__ == '__main__':
    unittest.main()
