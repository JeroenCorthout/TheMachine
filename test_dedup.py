"""Same person? Identities join on strong evidence only; a likely match is a question; a merge keeps everything and can be undone."""
import json
import unittest
from pathlib import Path

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment
STAMP = tg.NOW.isoformat()


class SamePerson(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node
    ids = tg.Graph.ids

    def setUp(self):
        tg.Graph.setUp(self)

    def people(self):
        with self.store.connect() as db:
            return {r['id']: json.loads(r['info'])['identifiers'] for r in db.execute("SELECT id,info FROM graph_nodes WHERE kind='person'")}

    def same_pairs(self):
        with self.store.connect() as db:
            return {(r['a'], r['b']) for r in db.execute('SELECT a,b FROM graph_same')}

    def say(self, x, y, same):
        return self.app.action('graph_same', {'a': x, 'b': y, 'same': same}, owner_dashboard=True)

    def test_work_mail_with_her_own_address_in_cc_is_one_person_and_her_whatsapp_follows(self):
        self.add(tg.mail(3, 'Rae Moreno <rae@lab.example>', 'sam@acme.com', cc='Rae Moreno <rae.moreno@gmail.com>', thread='plans'),
                 tg.mail(2, 'Sam <sam@acme.com>', 'Rae Moreno <rae.moreno@gmail.com>', ('SENT',), thread='weekend'),
                 tg.chat(1, '00000000021', 'Rae Moreno'), tg.chat(1, '00000000021', 'Rae Moreno', mine=True))
        self.build()
        people = self.people()
        self.assertEqual(len(people), 1)
        node, = people
        self.assertEqual(sorted(people[node]), ['email:rae.moreno@gmail.com', 'email:rae@lab.example', 'wa:00000000021'])
        whys = {j['by'] for j in self.node(node)['joined']}
        self.assertEqual(whys, {'message', 'name'})  # her CC, then one Rae Moreno per channel
        # An old link to one of her identities opens the merged person.
        self.assertEqual(self.app.graph_action({'node_id': 'wa:00000000021'})['id'], node)

    def test_two_people_with_one_name_never_merge_by_themselves(self):
        self.add(tg.mail(3, 'Sam Rivera <sam.rivera@one.example>', 'sam@acme.com'),
                 tg.mail(2, 'Sam Rivera <rivera@two.example>', 'sam@acme.com'),
                 tg.chat(1, '00000000031', 'Sam Rivera'))
        self.build()
        self.assertEqual(len(self.people()), 3)  # two addresses carry the name: nobody is joined on the name
        pairs = self.same_pairs()
        self.assertIn(('email:rivera@two.example', 'email:sam.rivera@one.example'), pairs)
        self.assertEqual(len(pairs), 3)  # a question for each pair, nothing more
        view = self.app.graph_action({})
        self.assertEqual({s['why'] for s in view['same_person']}, {'Same name'})
        # Not the same is remembered: no question again, also after a rebuild.
        self.say('email:rivera@two.example', 'email:sam.rivera@one.example', False)
        with self.assertRaisesRegex(ValueError, 'their word stays'):
            self.app.graph_action({'merge': ['email:sam.rivera@one.example', 'email:rivera@two.example']})  # a review can't overrule it
        self.build()
        self.assertNotIn(('email:rivera@two.example', 'email:sam.rivera@one.example'), self.same_pairs())
        self.assertEqual(len(self.people()), 3)

    def test_a_similar_name_needs_something_shared_and_is_only_asked(self):
        self.add(tg.mail(3, 'Kit Lane <kit@globex.example>', 'sam@acme.com'),
                 tg.mail(2, 'Kit <k.lane@globex.example>', 'sam@acme.com'),
                 tg.mail(2, 'Kit <kit@initech.example>', 'sam@acme.com'))
        self.build()
        self.assertEqual(len(self.people()), 3)
        asked = self.node('email:kit@globex.example')['same']
        self.assertEqual([(s['id'], s['why']) for s in asked], [('email:k.lane@globex.example', 'Similar name, both at globex.example')])
        self.assertNotIn('email:kit@initech.example', {s['id'] for s in asked})  # nothing shared: not even a question

    def test_her_number_in_her_own_mail_joins_a_colleagues_does_not(self):
        own = tg.mail(3, 'Ann Ito <ann@client.example>', 'sam@acme.com')
        own['body'] = 'Thanks!\n\nAnn Ito\nClient Co\n+32 470 00 00 41'
        quoted = tg.mail(2, 'Bo Berg <bo@client.example>', 'sam@acme.com')
        quoted['body'] = 'Sure.\n\nOn Mon, Ann Ito <ann@client.example> wrote:\n> call Lou on +32 470 00 00 42'
        self.add(own, quoted, tg.chat(1, '32470000041', 'Ann'), tg.chat(1, '32470000042', 'Lou'), tg.chat(1, '32470000043', 'Bo Berg'))
        self.build()
        people = self.people()
        self.assertIn('wa:32470000041', people['email:ann@client.example'])
        self.assertEqual(self.node('email:ann@client.example')['joined'][0]['by'], 'signature')
        self.assertIn('wa:32470000042', people)  # another person's number, quoted: not hers
        self.assertIn('email:bo@client.example', people)

    def test_the_same_linkedin_profile_joins(self):
        self.add(tg.mail(3, 'Jo Park <jo@hooli.example>', 'sam@acme.com'), tg.mail(2, 'J. Park <jpark@pied.example>', 'sam@acme.com'))
        with self.store.connect() as db:
            for who in ('email:jo@hooli.example', 'email:jpark@pied.example'):
                enrichment.save_person(db, who, 'found', {'title': 'Engineer', 'linkedin_url': 'https://www.linkedin.com/in/jo-park-0/'}, STAMP)
        self.build()
        self.assertEqual(len(self.people()), 1)

    def test_merge_keeps_everything_and_undo_splits_for_good(self):
        first = tg.mail(4, 'Kit Lane <kit@globex.example>', 'sam@acme.com', thread='deal')
        self.add(first, tg.mail(3, 'Sam <sam@acme.com>', 'Kit Lane <kit@globex.example>', ('SENT',), thread='deal'),
                 tg.chat(2, '00000000051', 'Kitty'), tg.chat(2, '00000000051', 'Kitty', mine=True))
        self.build()
        work, chat = 'email:kit@globex.example', 'wa:00000000051'
        deal, = self.ids([first['source_id']])
        with self.store.connect() as db:
            graph.set_role(db, chat, 'old friend', 'owner', STAMP)
            graph.label(db, [deal], 'Globex renewal', 'owner', STAMP)
            db.execute("INSERT INTO person_photos(identifier,key,type,data,saved_at) VALUES(?,?,?,?,?)", (chat, 'a' * 32, 'image/png', b'png', STAMP))
            fact = db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('Kit likes long walks.','d1',?)", (STAMP,)).lastrowid
            db.execute("INSERT INTO fact_about(fact_id,node_id,by,created_at) VALUES(?,?,'review',?)", (fact, chat, STAMP))
        self.build()
        before = {n: self.node(n)['signals'] for n in (work, chat)}
        self.say(work, chat, True)
        merged = self.app.graph_action({'node_id': work})
        self.assertEqual(sorted(self.people()[work]), [work, chat])
        self.assertEqual(merged['signals'], sum(before.values()))  # all history
        self.assertEqual(merged['role'], 'old friend')
        self.assertEqual(merged['profile']['photo'], 'api/person-photo?p=' + 'a' * 32)
        self.assertIn('project:', ' '.join(l['id'] for l in merged['links'] if l['kind'] == 'project'))
        self.assertEqual([k['id'] for k in merged['open']['known']], [fact])
        self.assertEqual(merged['joined'][0]['why'], 'merged by you')
        # Undo: apart again, remembered, never asked or joined again.
        self.say(work, chat, False)
        self.build()
        self.assertEqual(set(self.people()), {work, chat})
        self.assertNotIn((chat, work), self.same_pairs())
        self.assertEqual(self.node(chat)['role'], 'old friend')

    def test_an_automatic_join_can_be_undone(self):
        self.add(tg.mail(3, 'Rae Moreno <rae@lab.example>', 'sam@acme.com', cc='Rae Moreno <rae.moreno@gmail.com>'))
        self.build()
        node, = self.people()
        edge = self.node(node)['joined'][0]
        self.say(edge['a'], edge['b'], False)
        self.build()
        self.assertEqual(len(self.people()), 2)
        self.add(tg.mail(1, 'Rae Moreno <rae@lab.example>', 'sam@acme.com', cc='Rae Moreno <rae.moreno@gmail.com>'))
        self.build()
        self.assertEqual(len(self.people()), 2)  # new evidence doesn't overrule the owner

    def test_only_the_owner_merges_on_the_dashboard_and_never_a_deleted_person(self):
        self.add(tg.mail(3, 'Rae Moreno <rae@lab.example>', 'sam@acme.com', cc='Rae Moreno <rae.moreno@gmail.com>'),
                 tg.mail(2, 'Kit <kit@globex.example>', 'sam@acme.com'))
        self.build()
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_same', {'a': 'email:rae@lab.example', 'b': 'email:kit@globex.example', 'same': True})
        node, = [n for n, ids in self.people().items() if 'email:rae@lab.example' in ids]
        self.app.delete_person(node, workspace=Path(self.temp.name))
        self.assertEqual(set(self.people()), {'email:kit@globex.example'})  # both her addresses gone, and forgotten
        with self.assertRaisesRegex(ValueError, 'deleted permanently'):
            self.say('email:rae.moreno@gmail.com', 'email:kit@globex.example', True)
        self.add(tg.mail(1, 'Rae Moreno <rae.moreno@gmail.com>', 'sam@acme.com'))
        self.build()
        self.assertEqual(set(self.people()), {'email:kit@globex.example'})

    def test_a_domain_or_a_placeholder_is_not_a_name(self):
        self.assertIsNone(graph.plain_name('HelloShop.example'))
        self.assertIsNone(graph.plain_name('Unknown sender'))
        self.assertEqual(graph.name_words(None, 'email:jane.doe@mail.example'), ['jane', 'doe'])
        self.assertEqual(graph.name_match(['alex', 'lee'], ['alexander', 'lee']), 'similar')
        self.assertIsNone(graph.name_match(['ann', 'lee'], ['ann', 'berg']))


if __name__ == '__main__':
    unittest.main()
