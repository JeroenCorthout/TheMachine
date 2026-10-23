import json
import sqlite3
import unittest
from pathlib import Path

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment


class DeletePermanently(unittest.TestCase):
    """Delete permanently: the person and what is tied to them go, cards stay without them, and they never come back."""
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node
    ids = tg.Graph.ids

    def setUp(self):
        tg.Graph.setUp(self)
        self.app.config = tg.CONFIG

    def dump(self):
        """Every text in every table, by table."""
        with sqlite3.connect(self.store.path) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {t: json.dumps([list(map(str, r)) for r in db.execute(f'SELECT * FROM "{t}"')]) for t in tables}

    def test_deleted_means_gone_everywhere_and_a_new_message_does_not_bring_them_back(self):
        lee = tg.mail(3, 'Lee Parker <lee@globex.com>', 'sam@acme.com', title='Quote for Lee Parker')
        reply = tg.mail(2, 'Sam <sam@acme.com>', 'Lee Parker <lee@globex.com>', ('SENT',), title='Re: quote')
        group = tg.mail(1, 'Ann Doe <ann@hooli.example>', 'sam@acme.com', cc='Lee Parker <lee@globex.com>', title='Planning')  # Ann's: stays
        ann = tg.mail(1, 'Sam <sam@acme.com>', 'Ann Doe <ann@hooli.example>', ('SENT',), title='Hi Ann')
        self.add(lee, reply, group, ann)
        self.build()
        node = 'email:lee@globex.com'
        self.assertIsNotNone(self.node(node))
        lee_id, reply_id, group_id = self.ids([lee['source_id'], reply['source_id'], group['source_id']])
        stamp = tg.NOW.isoformat()
        with self.store.connect() as db:
            enrichment.save_person(db, node, 'found', {'title': 'Head of Sales', 'photo': 'https://media.example/lee.jpg'}, stamp)
            graph.set_role(db, node, 'customer', 'owner', stamp)
            graph.rename(db, node, 'Lee Parker', stamp)
            db.execute('INSERT INTO identity_names(identifier,name,email,checked_at) VALUES(?,?,?,?)', ('li:abc', 'Lee Parker', 'lee@globex.com', stamp))
            db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,status,created_at) VALUES('c1','k1','Reply to Lee Parker',"
                       "'Draft: Hi Lee, the quote is attached. Write to lee@globex.com.',?,'[\"work\"]','pending',?)", (json.dumps([lee_id]), stamp))
            db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,status,created_at) VALUES('c2','k2','Plan with Ann','Hi Ann, Lee may join.',?,'[\"work\"]','pending',?)",
                       (json.dumps([group_id]), stamp))
            about = db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('Prefers calls in the morning','d1',?)", (stamp,)).lastrowid
            db.execute("INSERT INTO fact_about(fact_id,node_id,by,created_at) VALUES(?,?,'review',?)", (about, node, stamp))
            db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('Lee Parker signs off on budgets','d2',?)", (stamp,))
            db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('Ann likes short emails','d3',?)", (stamp,))
            db.execute("INSERT INTO promises(direction,who,what,check_at,event_id,account_id,status,created_at,updated_at) "
                       "VALUES('mine','Lee Parker','send Lee Parker the quote',?,?,'work','open',?,?)", (stamp, reply_id, stamp, stamp))
        notes = Path(self.temp.name) / 'workspace' / 'memory'
        notes.mkdir(parents=True)
        (notes / '2026-10-15.md').write_text('# Notes\n- Lee Parker wants the quote by Friday\n- Ann is on holiday\n- lee@globex.com bounced once\n')
        self.build()

        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_person_delete', {'node_id': node})
        done = self.app.delete_person(node, workspace=Path(self.temp.name) / 'workspace')
        self.assertEqual((done['events'], done['facts'], done['cards'], done['notes']), (2, 2, 1, 2))

        dump = self.dump()
        kept = {'events'}  # Ann's message, with Lee on copy, is Ann's and the owner's: it stays
        for table, text in dump.items():
            if table in kept:
                continue
            for needle in ('lee@globex.com', 'Lee Parker', 'li:abc', 'Head of Sales', 'media.example', 'Prefers calls'):
                self.assertNotIn(needle, text, f'{needle} still in {table}')
        with self.store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT id FROM events')}, set(self.ids([group['source_id'], ann['source_id']])))
            cards = {r['id']: (r['title'], r['body']) for r in db.execute('SELECT id,title,body FROM suggestions')}
            self.assertEqual(db.execute('SELECT count(*) FROM forgotten_people').fetchone()[0], 2)  # email:lee@globex.com and li:abc, hashed
            self.assertEqual(db.execute("SELECT count(*) FROM facts WHERE text='Ann likes short emails'").fetchone()[0], 1)
        self.assertEqual(cards['c1'], ('Reply to someone', 'Draft: Hi someone, the quote is attached. Write to someone.'))  # the card stays
        self.assertEqual(cards['c2'], ('Plan with Ann', 'Hi Ann, Lee may join.'))  # names someone else only by a first name: left alone
        self.assertEqual((notes / '2026-10-15.md').read_text(), '# Notes\n- Ann is on holiday\n')
        self.assertIsNone(self.node(node))
        self.assertIsNotNone(self.node('email:ann@hooli.example'))

        # New messages from them, or only to them, are not kept; they are not added, looked up or learned about again.
        again = tg.mail(0, 'Lee Parker <lee@globex.com>', 'sam@acme.com', title='Following up')
        to_them = tg.mail(0, 'Sam <sam@acme.com>', 'lee@globex.com', ('SENT',), title='Sure')
        self.add(again, to_them)
        self.build()
        self.assertIsNone(self.node(node))
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM events WHERE source_id IN (?,?)', (again['source_id'], to_them['source_id'])).fetchone())
            self.assertEqual(enrichment.people_due(db, tg.NOW.isoformat(), 10), [{'identifier': 'email:ann@hooli.example', 'email': 'ann@hooli.example', 'name': 'Ann Doe', 'domain': 'hooli.example'}])
        self.assertNotIn('lee@globex.com', json.dumps(self.dump()['graph_nodes']))
        # The tombstone is a keyed hash: no address in it.
        self.assertNotIn('globex', self.dump()['forgotten_people'])


if __name__ == '__main__':
    unittest.main()
