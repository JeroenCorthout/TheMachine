"""Notes about one kind of task become a skill, only when the owner says so on a card.

A review that sees 5 or more notes about one scenario suggests a skill (remember_fact kind=skill): a card with the
notes, Create skill or Add to <one of the owner's own skills>, and Not now. Not now isn't asked again until the same
notes grew. On yes the skill is written and the notes move into it: kept, shown with the skill, gone from the list
every task gets. A teammate's skill is never changed. All local; nothing is sent."""
import json
from pathlib import Path
import secrets
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import skills
from test_voices import a, mail


class SkillsFromNotes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.accounts = [{'id': 'work', 'service': 'gmail', 'email': 'owner@example.com'}]
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': self.store.accounts}
        self.app.skills = skills.Skills(self.store.connect, Path(self.temp.name) / 'workspace')
        self.store.commit_page('work', {}, [mail(f'in{i}', '2026-10-01T09:00:00+00:00', f'Listing swap {i}.', thread=f't{i}') for i in range(12)])
        self.notes = []
        for i in range(12):
            sid = self.store.suggest(f'swap{i}', f'Answer listing swap {i}', 'They agreed.', [i + 1])['id']
            self.store.decide(sid, 'other', f'note {i}', f'n{i}', 'dashboard')
            with self.store.connect() as db:
                decision = db.execute("SELECT id FROM decisions WHERE suggestion_id=? AND feedback!=''", (sid,)).fetchone()['id']
            self.notes.append(self.store.remember_fact(f'For listing exchanges, rule number {i} holds.', decision, 'draft')['id'])

    def propose(self, ids, **more):
        return self.app.action('remember_fact', {'kind': 'skill', 'name': 'Listing exchanges', 'description': 'When a listing exchange is agreed.',
                                                 'text': '## Steps\n' + '\n'.join(f'- Rule {i}.' for i in range(len(ids))), 'replaces': ids, **more})

    def card(self, sid):
        return next(c for c in self.store.snapshot([])['suggestions'] if c['id'] == sid)

    def test_a_cluster_becomes_a_card_and_create_skill_moves_the_notes(self):
        with self.assertRaisesRegex(ValueError, '5 to 20'):
            self.propose(self.notes[:4])
        out = self.propose(self.notes[:6])
        card = self.card(out['suggestion_id'])
        self.assertEqual(card['title'], 'Turn 6 notes into a skill: Listing exchanges')
        self.assertIn('rule number 5 holds', '\n'.join(card['skill_proposal']['notes']))
        self.assertIn('## Steps', card['skill_proposal']['body'])
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM suggestions WHERE id=?',(card['id'],)).fetchone());row.update(self.store.suggestion_result(row,db))
        buttons=[b for block in a.controls(row,'https://fixture.invalid/')['blocks'] for b in block['buttons']]
        self.assertTrue(any(b['label']=='Create skill' for b in buttons))
        self.assertTrue(any(b['label']=='Read instructions' for b in buttons))
        self.assertEqual(card['labels'], {'accept': 'Create skill', 'reject': 'Not now'})
        self.assertEqual(self.propose(self.notes[:6])['duplicate'], True)  # asked once
        self.assertEqual(len(self.store.facts()), 12)  # nothing moves before the owner says so
        self.app.action('decide', {'suggestion_id': out['suggestion_id'], 'choice': 'accept', 'decision_id': 'yes1'}, owner_dashboard=True)
        with self.store.connect() as db:  # no Do it run: it's done here
            self.assertIsNone(db.execute('SELECT 1 FROM preparations WHERE suggestion_id=?', (out['suggestion_id'],)).fetchone())
            self.assertEqual(db.execute('SELECT status FROM suggestions WHERE id=?', (out['suggestion_id'],)).fetchone()[0], 'completed')
        made = next(s for s in self.app.skills.view()['skills'] if s['name'] == 'Listing exchanges')
        self.assertEqual((made['status'], made['origin'], len(made['notes'])), ('on', 'mine', 6))
        self.assertEqual(len(self.store.facts()), 6)  # out of the list every task gets
        self.assertEqual(self.store.snapshot([])['moved'], [{'skill_id': made['id'], 'name': 'Listing exchanges', 'notes': 6}])
        self.assertEqual(self.app.action('unmerge_fact', {'skill': made['id']}, owner_dashboard=True), {'restored': 6})  # Put back
        self.assertEqual(len(self.store.facts()), 12)

    def test_not_now_waits_until_the_notes_grew_and_only_own_skills_take_notes(self):
        out = self.propose(self.notes[:6])
        self.app.action('decide', {'suggestion_id': out['suggestion_id'], 'choice': 'reject', 'decision_id': 'no1'}, owner_dashboard=True)
        with self.assertRaisesRegex(ValueError, 'Not now'):
            self.propose(self.notes[:8])
        self.assertIn('suggestion_id', self.propose(self.notes[:9]))  # grew by 3: asked again
        # Add to an existing skill of the owner's own; never a teammate's.
        self.app.skills.save(None, 'Partner listings', 'When partners swap listings.', 'Be generous with partners, always.')
        with self.store.connect() as db:
            db.execute("INSERT INTO skills(id,slug,name,description,body,status,origin,sha,author,created_at,updated_at) "
                       "VALUES('sk_theirs','theirs','Team listings','theirs','Theirs.','on','shared',?, 'Sam',?,?)", (secrets.token_hex(32), a.now(), a.now()))
        with self.assertRaisesRegex(ValueError, "teammate's"):
            self.propose(self.notes[9:12] + self.notes[6:8], skill='Team listings')
        with self.assertRaisesRegex(ValueError, 'Partner listings'):
            self.propose(self.notes[9:12] + self.notes[6:8], skill='Nope')
        with self.store.connect() as db:  # the pending one is decided first
            db.execute("UPDATE skill_clusters SET status='done'")
        out = self.propose(self.notes[9:12] + self.notes[6:8], skill='partner listings')
        self.assertEqual(self.card(out['suggestion_id'])['labels']['accept'], 'Add to Partner listings')
        self.app.action('decide', {'suggestion_id': out['suggestion_id'], 'choice': 'accept', 'decision_id': 'yes2'}, owner_dashboard=True)
        mine = next(s for s in self.app.skills.view()['skills'] if s['name'] == 'Partner listings')
        self.assertEqual(len(mine['notes']), 5)
        self.assertIn('Be generous with partners', self.app.skills.get(mine['id'])['body'])
        self.assertIn('- Rule 4.', self.app.skills.get(mine['id'])['body'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT body FROM skills WHERE id='sk_theirs'").fetchone()[0], 'Theirs.')

    def test_long_notes_remain_reviewable_and_skill_write_retries_once(self):
        with self.store.connect() as db:
            db.execute("UPDATE facts SET text=text||?", (' More exact details to keep. ' * 8,))
        self.app.skills.save(None, 'Existing', 'When this scenario happens.', 'Original steps for this existing skill.')
        out=self.propose(self.notes[:6], skill='Existing')
        card=self.card(out['suggestion_id'])
        self.assertLessEqual(len(card['body']),700)
        self.assertTrue(all(len(n)>200 for n in card['skill_proposal']['notes']))
    def test_interrupted_skill_database_write_rolls_back_and_retry_adds_once(self):
        self.app.skills.save(None, 'Existing', 'When this scenario happens.', 'Original steps for this existing skill.')
        out=self.propose(self.notes[:6], skill='Existing')
        self.store.decide(out['suggestion_id'],'accept','', 'yes-retry','dashboard')
        real=self.app.skills.save
        def interrupted(*args,**kwargs):
            real(*args,**kwargs)
            raise sqlite3.OperationalError('simulated interruption after skill write')
        with patch.object(self.app.skills,'save',side_effect=interrupted):
            with self.assertRaises(sqlite3.OperationalError): self.app.apply_skill_clusters()
        self.assertEqual(next(s for s in self.app.skills.view()['skills'] if s['name']=='Existing')['version'],1)
        self.app.apply_skill_clusters();self.app.apply_skill_clusters()
        made=next(s for s in self.app.skills.view()['skills'] if s['name']=='Existing')
        self.assertEqual(made['version'],2)
        self.assertEqual(self.app.skills.get(made['id'])['body'].count('## Steps'),1)
        self.assertEqual(len(made['notes']),6)

    def test_snoozed_skill_unsnooze_only_returns_the_card(self):
        out=self.propose(self.notes[:6])
        self.store.decide(out['suggestion_id'],'snooze','', 'later','dashboard')
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM suggestions WHERE id=?',(out['suggestion_id'],)).fetchone());row.update(self.store.suggestion_result(row,db))
        labels=[b['label'] for block in a.controls(row,'https://fixture.invalid/')['blocks'] for b in block['buttons']]
        self.assertIn('Unsnooze',labels)
        self.assertNotIn('Create skill',labels)
        self.app.action('decide',{'suggestion_id':out['suggestion_id'],'choice':'accept','decision_id':'back'},owner_dashboard=True)
        self.assertEqual(self.card(out['suggestion_id'])['status'],'pending')
        self.assertEqual(self.app.skills.view()['skills'],[])
        self.assertEqual(len(self.store.facts()),12)
        self.app.action('decide',{'suggestion_id':out['suggestion_id'],'choice':'accept','decision_id':'create'},owner_dashboard=True)
        self.assertEqual(len(self.app.skills.view()['skills']),1)


if __name__ == '__main__':
    unittest.main()
