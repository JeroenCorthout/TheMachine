from pathlib import Path
import sys,unittest,struct,json,io
from unittest.mock import patch
from contextlib import redirect_stdout
root=Path(__file__).resolve().parent;sys.path.insert(0,str(root))
import test_graph as tg,test_promises as tp,cli
class GraphFixes(unittest.TestCase):
    setUp=tg.Graph.setUp;add=tg.Graph.add;build=tg.Graph.build;node=tg.Graph.node;ids=tg.Graph.ids;vectors=tg.Graph.vectors
    def test_current_vectors_only(self):
        e=tg.mail(1,'Ann <ann@client.com>','sam@acme.com');self.add(e);self.vectors([(e['source_id'],[1,0])]);eid=self.ids([e['source_id']])[0]
        with self.store.connect() as db:
            db.execute('UPDATE events SET version=version+1 WHERE id=?',(eid,))
            self.assertEqual(tg.graph.load_vectors(db,{eid}),{})
    def test_selected_model_only(self):
        e=tg.mail(1,'Ann <ann@client.com>','sam@acme.com');self.add(e);self.vectors([(e['source_id'],[1,0])]);eid=self.ids([e['source_id']])[0]
        with self.store.connect() as db:self.assertEqual(tg.graph.load_vectors(db,{eid},{'model':'new','dims':2}),{})
    def test_owner_role_survives_identity_merge(self):
        self.add(tg.chat(1,'32470000001','Ann Bell'));self.build()
        with self.store.connect() as db:tg.graph.set_role(db,'wa:32470000001','friend','owner',tg.NOW.isoformat())
        self.add(tg.mail(1,'Ann Bell <ann@client.com>','sam@acme.com'));self.build()
        with self.store.connect() as db:result=tg.graph.set_role(db,'email:ann@client.com','customer','review',tg.NOW.isoformat())
        self.build();self.assertEqual(self.node('email:ann@client.com')['role'],'friend');self.assertIn('kept',result)
    def test_failed_review_does_not_consume_role_context(self):
        self.add(tg.mail(1,'Ann <ann@client.com>','sam@acme.com'));self.build()
        with self.store.connect() as db:
            db.execute("UPDATE graph_nodes SET strength=1 WHERE kind='person'")
            db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at) VALUES('r1','starting',?,?)",(tg.at(0),tg.at(0)))
        first=self.store.role_text('r1');self.assertTrue(first)
        self.assertEqual(self.store.role_text('r1'),first)
        with self.store.connect() as db:db.execute("UPDATE review_runs SET status='error' WHERE run_id='r1'")
        self.assertEqual(self.store.role_text('r2'),first)
    def test_failed_review_does_not_consume_project_context(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at) VALUES('r1','starting',?,?)",(tg.at(0),tg.at(0)))
            db.execute("INSERT INTO graph_unnamed VALUES(1,'[1,2,3]','[\"Ann\"]','[\"Project\"]',3,?)",(tg.at(0),))
        first=self.store.unnamed_text('r1');self.assertTrue(first)
        self.assertEqual(self.store.unnamed_text('r1'),first)
        with self.store.connect() as db:db.execute("UPDATE review_runs SET status='error' WHERE run_id='r1'")
        self.assertEqual(self.store.unnamed_text('r2'),first)
    def test_board_check_offered_once_a_day_until_a_review_answers_it(self):
        with self.store.connect() as db:
            for name in ('Hire: designer','Hire: backend','Hire: support lead'):tg.graph.project_id(db,name,tg.at(0))
            db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at) VALUES('r1','starting',?,?)",(tg.at(0),tg.at(0)))
        first=self.store.boards_text('r1');self.assertIn('Hire: backend (project:',first)
        self.assertEqual(self.store.boards_text('r1'),first)  # a retry of the same run sees it again
        with self.store.connect() as db:db.execute("UPDATE review_runs SET status='error',finished_at=? WHERE run_id='r1'",(tg.at(0),))
        self.assertEqual(self.store.boards_text('r2'),first)  # r1 never answered it
        with self.store.connect() as db:tg.graph.board_check_answer(db,'r2','none: different kinds of work')
        self.assertEqual(self.store.boards_text('r3'),'')  # answered today
        with self.store.connect() as db:db.execute("UPDATE board_offers SET offered_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.store.boards_text('r4'),first)  # a day later
        with self.store.connect() as db:db.execute("UPDATE projects SET closed_at=? WHERE name='Hire: backend'",(tg.at(0),));db.execute("DELETE FROM board_offers")
        self.assertEqual(self.store.boards_text('r5'),'')  # fewer than three on no board
    def test_a_review_with_a_board_check_answers_it_before_it_finishes(self):
        with self.store.connect() as db:
            for name in ('Hire: designer','Hire: backend','Hire: support lead'):tg.graph.project_id(db,name,tg.at(0))
        self.assertTrue(self.store.boards_text('r1'))
        done=[]
        with patch.object(self.store,'finish_analysis',lambda *a,**k:done.append(a) or {'ok':True}):
            finish=lambda **k:self.app.action('finish_analysis',{'analysis_id':'x','_review_run':'r1',**k})
            with self.assertRaisesRegex(ValueError,'Boards to suggest'):finish()
            with self.assertRaisesRegex(ValueError,'suggest_board first'):finish(boards='suggested')
            self.assertEqual(done,[])  # nothing saved until it answers
            finish(boards='none: three different kinds of work')
            finish()  # answered once is enough
            self.app.action('finish_analysis',{'analysis_id':'y','_review_run':'r2'})  # another run has no check
        self.assertEqual(len(done),3)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT answer FROM board_offers').fetchone()[0],'none: three different kinds of work')
    def test_owner_clear_survives_rebuild_and_later_review(self):
        self.add(tg.mail(1,'Ann <ann@client.com>','sam@acme.com'));self.build()
        with self.store.connect() as db:tg.graph.set_role(db,'email:ann@client.com','friend','owner',tg.NOW.isoformat())
        self.build()
        with self.store.connect() as db:tg.graph.set_role(db,'email:ann@client.com','','owner',tg.NOW.isoformat())
        self.build()
        with self.store.connect() as db:kept=tg.graph.set_role(db,'email:ann@client.com','customer','review',tg.NOW.isoformat())
        self.build();self.assertIsNone(self.node('email:ann@client.com')['role']);self.assertIn('kept',kept)
        self.assertEqual(self.store.role_text('r1'),'')
    def test_role_evidence_follows_merged_alias(self):
        e=tg.chat(1,'32470000001','Ann Bell');self.add(e);self.build();eid=self.ids([e['source_id']])[0]
        with self.store.connect() as db:tg.graph.set_role(db,'wa:32470000001','friend','review',tg.NOW.isoformat(),eid,'Hi')
        self.add(tg.mail(1,'Ann Bell <ann@client.com>','sam@acme.com'));self.build()
        with self.store.connect() as db:
            detail=tg.graph.detail(db,'email:ann@client.com');who=tg.graph.unconfirmed(db)
        self.assertEqual(detail['role_event_id'],eid);self.assertEqual(detail['role_quote'],'Hi');self.assertEqual(who[0]['id'],'email:ann@client.com')
    def test_graph_build_does_not_publish_mixed_snapshot(self):
        import threading
        self.add(tg.mail(1,'Ann <ann@client.com>','sam@acme.com'));self.build()
        started,done=threading.Event(),threading.Event();fail=[];threads=[]
        def change():
            started.set()
            try:
                with self.store.connect() as db:tg.graph.set_role(db,'email:ann@client.com','friend','owner',tg.NOW.isoformat())
                self.build()
            except Exception as e:fail.append(e)
            done.set()
        original=tg.graph.Owner.learn
        def learn(owner,signals):
            original(owner,signals)
            if not threads:
                t=threading.Thread(target=change);threads.append(t);t.start();self.assertTrue(started.wait(1));self.assertFalse(done.wait(.08))
        with patch.object(tg.graph.Owner,'learn',learn):self.build()
        threads[0].join(3);self.assertTrue(done.is_set());self.assertFalse(fail);self.assertEqual(self.node('email:ann@client.com')['role'],'friend')
    def test_recent_roles_run_is_checked_inside_transaction(self):
        def evidence():
            with self.store.connect() as db:db.execute("INSERT INTO profile_runs(run_id,status,started_at,updated_at,finished_at,kind) VALUES('other','ok',?,?,?,'roles')",(tg.at(0),tg.at(0),tg.at(0)))
            return ([{'name':'Ann'}],[])
        with patch.object(self.store,'role_evidence',side_effect=evidence):saved=self.store.roles_start()
        self.assertEqual(saved['run_id'],'other');self.assertTrue(saved['attached'])
    def test_enrichment_error_retains_last_known_profile(self):
        self.build()
        with self.store.connect() as db:
            tg.a.enrichment.save(db,'client.com','found',{'name':'Client Ltd'},tg.at(1))
            tg.a.enrichment.save(db,'client.com','error',None,tg.at(0))
            self.assertEqual(json.loads(db.execute('SELECT profile FROM org_profiles').fetchone()[0]),{'name':'Client Ltd'})
class PromiseFixes(unittest.TestCase):
    setUp=tp.Promises.setUp;at=tp.Promises.at;app=tp.Promises.app;record=tp.Promises.record;row=tp.Promises.row
    def test_closed_promise_cannot_publish_stale_card(self):
        pid=self.record(due='2026-10-19')['id'];self.at('2026-10-21T09:00:00+00:00')
        p={**self.row(pid),'made_at':'2026-10-19T08:00:00+00:00'}
        self.store.update_promise({'id':pid,'status':'kept'},source='owner')
        self.app().promise_card(p)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0],0)
    def test_promise_card_and_link_roll_back_together(self):
        pid=self.record(due='2026-10-19')['id'];self.at('2026-10-21T09:00:00+00:00')
        p={**self.row(pid),'made_at':'2026-10-19T08:00:00+00:00'}
        original=self.store.suggest
        def fail(*args,**kw):
            original(*args,**kw)
            raise RuntimeError('fixture crash after insert')
        with patch.object(self.store,'suggest',side_effect=fail),self.assertRaises(RuntimeError):self.app().promise_card(p)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM preparations').fetchone()[0],0)
        self.app().promise_card(p);self.assertEqual(self.row(pid)['status'],'carded')
    def test_legacy_promise_migration_preserves_every_field(self):
        pid=self.record()['id'];before=self.row(pid)
        with self.store.connect() as db:
            sql=db.execute("SELECT sql FROM sqlite_master WHERE name='promises'").fetchone()[0]
            db.execute(sql.replace('promises(', 'old_promises(',1).replace('UNIQUE(event_id,direction,who,what)','UNIQUE(event_id,direction,who)'))
            db.execute('INSERT INTO old_promises SELECT * FROM promises');db.execute('DROP TABLE promises');db.execute('ALTER TABLE old_promises RENAME TO promises')
        reopened=tp.a.Store(self.store.path)
        with reopened.connect() as db:self.assertEqual(dict(db.execute('SELECT * FROM promises').fetchone()),before)
        self.assertNotEqual(self.record(what='send the pricing sheet')['id'],pid)
    def test_moved_promise_cannot_publish_stale_card(self):
        pid=self.record(due='2026-10-19')['id'];self.at('2026-10-21T09:00:00+00:00');p={**self.row(pid),'made_at':'2026-10-19T08:00:00+00:00'}
        self.store.update_promise({'id':pid,'due':'2026-11-01','reason':'New deadline'})
        self.app().promise_card(p)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0],0)
    def test_unrelated_email_does_not_close_promise(self):
        pid=self.record(due='2026-10-23')['id']
        self.store.commit_page('work',{},[tp.email('unrelated','2026-10-19T08:50:00+00:00',thread='other',title='Lunch')])
        self.app().promise_step();self.assertEqual(self.row(pid)['status'],'open')
    def test_two_distinct_promises_from_one_message(self):
        one=self.record()['id'];two=self.record(what='send the pricing sheet')['id'];self.assertNotEqual(one,two)
class CliFixes(unittest.TestCase):
    def test_roles_json_is_json_only(self):
        from types import SimpleNamespace
        def call(*args):return {'run_id':'r','status':'ok','roles':[]}
        out=io.StringIO()
        with patch.object(cli,'call',side_effect=call),redirect_stdout(out):cli.cmd_roles({},SimpleNamespace(again=False,json=True))
        self.assertEqual(json.loads(out.getvalue())['status'],'ok')
if __name__=='__main__':
    unittest.main(verbosity=2)
