"""Shared Graph collection preserves the stage list of custom boards, including the optional stalled row."""
import unittest
import test_graph as tg
import shared_graph as sg

class SharedBoardCollection(unittest.TestCase):
    setUp, add, build, mails = tg.Boards.setUp, tg.Boards.add, tg.Boards.build, tg.Boards.mails
    STAGES = tg.Boards.STAGES

    def test_custom_board_collects_with_and_without_the_stalled_row(self):
        _, agreed, _ = self.mails()
        with self.store.connect() as db:
            sg.ensure(db)
            tg.graph.set_stage(db, 'Paperkite', 'Agreed', 'review', tg.NOW.isoformat(), board='Listings',
                               reason='Ann agreed', event_id=agreed, next_step='Publish the listing')
        self.build()
        with self.store.connect() as db:
            sg.judge(db, [{'event_id': agreed, 'share': 'client', 'line': 'Ann agreed to the listing'}])
            for side in (0, 1):
                db.execute('UPDATE boards SET side=? WHERE name=?', (side, 'Listings'))
                scope = sg.Scope(db, ['work'], ['acme.com'], ['sam@acme.com'])
                records, _, _, _ = sg.collect(db, scope, b'k' * 32, b's' * 32, today=tg.NOW)
                project = next(r for r in records.values() if r['t'] == 'project')
                expected = ['Contacted', 'Agreed', 'Live'] + ([tg.graph.STALLED] if side else [])
                self.assertEqual(project['stages'], expected)
                self.assertEqual(project['stage'], 'Agreed')

if __name__ == '__main__':
    unittest.main()
