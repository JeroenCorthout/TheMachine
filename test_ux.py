from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_assistant import assistant as a, event

class UX(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=a.Store(self.root/'events.sqlite')
        self.store.commit_page('work',{},[event()])
        self.sid=self.store.suggest('task','Prepare a draft','I can draft this.',[1])['id']
    def row(self):return self.store.snapshot([])['suggestions'][0]
    def job(self):
        with self.store.connect() as db:return dict(db.execute('SELECT * FROM preparations').fetchone())
    def decide(self,c,id=None,feedback=''):return self.store.decide(self.sid,c,feedback,id or c,'dashboard')
    def test_undo_done_keeps_prepared_result_and_is_idempotent(self):
        self.decide('accept')
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='running'")
        self.store.prepare(self.sid,'Actual draft',self.job()['job_id'])
        self.decide('complete');self.assertEqual(a.controls(self.row())['blocks'][0]['buttons'][0]['label'],'Undo')
        self.decide('undo');self.assertEqual(self.row()['status'],'prepared');self.assertEqual(self.row()['body'],'Actual draft')
        self.assertEqual(self.job()['status'],'done');self.assertTrue(self.decide('undo')['duplicate'])
    def test_undo_dismiss_restores_snooze_and_delivery(self):
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='42'")
        self.decide('snooze');before=self.row();self.decide('reject');self.decide('undo')
        after=self.row();self.assertEqual((after['status'],after['snoozed_until'],after['resume_status']),('snoozed',before['snoozed_until'],before['resume_status']))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'sent')
    def test_undo_close_requeues_still_running_preparation_in_same_session(self):
        self.decide('accept');before=self.job()['job_id']
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='running'")
        self.decide('reject');self.decide('undo');self.assertNotEqual(self.job()['job_id'],before);self.assertEqual(self.job()['status'],'queued')
        self.assertEqual(self.job()['session_job_id'],before)

    def test_undo_keeps_a_blocked_preparation_blocked_instead_of_starting_over(self):
        self.decide('accept');before=self.job()
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='blocked',reason='Needs a browser'")
        self.decide('complete');self.decide('undo')
        self.assertEqual(self.row()['status'],'accepted')
        self.assertEqual((self.job()['job_id'],self.job()['status'],self.job()['reason']),(before['job_id'],'blocked','Needs a browser'))
        self.assertEqual(self.row()['preparation_status'],'blocked')

    def test_undo_expiration(self):
        self.decide('reject')
        with self.store.connect() as db:db.execute("UPDATE suggestions SET undo_until='2020-01-01'")
        with self.assertRaises(ValueError):self.decide('undo')
        self.store.wake_snoozed();self.assertIsNone(self.row()['undo_until']);self.assertEqual(a.controls(self.row())['blocks'][0]['buttons'],[])
    def test_stale_telegram_undo_unblocks_ingress_without_changing_owner_state(self):
        self.decide('other',feedback='Keep my exact instructions')
        self.decide('complete');self.decide('undo');before=self.row();job=self.job()
        with self.store.connect() as db:
            decisions=[tuple(r) for r in db.execute('SELECT * FROM decisions')]
            delivery=dict(db.execute('SELECT * FROM deliveries').fetchone())
        app=object.__new__(a.App);app.store=self.store;app.config={'accounts':[]}
        args={'suggestion_id':self.sid,'choice':'undo','decision_id':'telegram:delayed-second-click'}
        for _ in range(2):
            result=app.action('decide',args)
            self.assertTrue(result['skipped']);self.assertTrue(result['duplicate'])
            self.assertFalse(result['decision_saved']);self.assertEqual(result['status'],'instructions')
        self.assertEqual(self.row(),before);self.assertEqual(self.job(),job)
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM decisions')],decisions)
            self.assertEqual(dict(db.execute('SELECT * FROM deliveries').fetchone()),delivery)
        # A following real Change callback may now request and bind its prompt.
        self.store.request_feedback(self.sid,'next-change',card_message_id='123')
        self.store.bind_feedback_prompt(self.sid,'next-change','124')
        self.assertTrue(self.store.feedback_reply('Make it shorter','125',reply_to_id='124')['handled'])
        self.assertEqual(self.row()['latest_feedback']['feedback'],'Make it shorter')
    def test_expired_telegram_undo_does_not_close_or_reopen_item(self):
        self.decide('reject')
        with self.store.connect() as db:db.execute("UPDATE suggestions SET undo_until='2020-01-01'")
        before=self.row()
        result=self.store.decide(self.sid,'undo','','telegram:expired','telegram')
        self.assertTrue(result['skipped']);self.assertEqual(self.row(),before)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM decisions').fetchone()[0],1)
    def test_retry_only_blocked_preserves_instructions_fences_old_job(self):
        self.decide('other',feedback='Please make it shorter');old=self.job()['job_id']
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='blocked',reason='Could not connect'")
        self.decide('retry');new=self.job()['job_id'];self.assertNotEqual(old,new)
        self.assertEqual(self.row()['latest_feedback']['feedback'],'Please make it shorter')
        self.assertTrue(self.decide('retry')['duplicate'])
        with self.assertRaises(ValueError):self.decide('retry','retry-again')
        with self.assertRaises(ValueError):self.store.prepare(self.sid,'Stale',old)
    def test_unconfirmed_preparation_retry_needs_terminal_proof(self):
        self.decide('accept');job=self.job()['job_id']
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='blocked',error='completion_unconfirmed'")
        app=object.__new__(a.App);app.store=self.store;app.config={'accounts':[]}
        args={'suggestion_id':self.sid,'choice':'retry','decision_id':'owner-click'}
        with patch.object(app,'preparation_gateway',return_value={'status':'pending'}) as gateway:
            app.action('decide',args,owner_dashboard=True)
        # UI responds immediately; the worker owns cancellation and terminal proof.
        gateway.assert_not_called()
        self.assertNotEqual(self.job()['job_id'],job)
        self.assertEqual(self.job()['session_job_id'],job)

    def test_ended_preparation_retry_needs_no_stop(self):
        self.decide('accept');job=self.job()['job_id']
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='blocked',error='completion_unconfirmed'")
        app=object.__new__(a.App);app.store=self.store;app.config={'accounts':[]}
        args={'suggestion_id':self.sid,'choice':'retry','decision_id':'owner-click'}
        with patch.object(app,'preparation_gateway',return_value={'status':'aborted'}) as gateway:
            app.action('decide',args,owner_dashboard=True)
        gateway.assert_not_called()
        self.assertNotEqual(self.job()['job_id'],job)

    def test_change_captures_only_exact_prompt_reply(self):
        self.store.request_feedback(self.sid,'req',card_message_id='41');self.store.bind_feedback_prompt(self.sid,'req','42')
        self.assertFalse(self.store.feedback_reply('Ordinary chat','43')['handled'])
        self.assertFalse(self.store.feedback_reply('Another reply','44',reply_to_id='41')['handled'])
        self.assertTrue(self.store.feedback_reply('Make it shorter','45',reply_to_id='42')['handled'])
        self.assertEqual(self.row()['latest_feedback']['feedback'],'Make it shorter')
    def test_double_change_tap_reuses_bound_prompt(self):
        self.store.request_feedback(self.sid,'first',card_message_id='41')
        self.store.bind_feedback_prompt(self.sid,'first','42');before=self.row()
        self.assertTrue(self.store.request_feedback(self.sid,'second',card_message_id='41')['duplicate'])
        self.assertEqual(self.row(),before)
        self.assertTrue(self.store.feedback_reply('Shorter','43',reply_to_id='42')['handled'])
        self.assertFalse(self.store.request_feedback(self.sid,'third',card_message_id='41').get('duplicate',False))
    def test_received_change_edits_existing_card_only_and_keeps_real_request(self):
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
        self.store.request_feedback(self.sid,'first',card_message_id='40');self.store.bind_feedback_prompt(self.sid,'first','41')
        self.store.feedback_reply('Make it *funny* [please]','42',reply_to_id='41')
        with self.store.connect() as db:
            delivery=dict(db.execute('SELECT * FROM deliveries').fetchone());job=dict(db.execute('SELECT * FROM preparations').fetchone())
        self.assertEqual((delivery['status'],delivery['message_id'],delivery['needs_edit']),('sent','40',1))
        with patch.object(a,'telegram_request',side_effect=lambda config,args:args):
            row={**self.row(),'preparation_status':'queued','requested_feedback':'Make it *funny* [please]','reminder':1}
            payload=a.telegram({'telegram_owner':'1000001'},row,'edit','40')
        self.assertEqual((payload['action'],payload['messageId']),('edit','40'))
        self.assertIn(r'Make it \*funny\* \[please\]',payload['message'])
        self.assertIn('I’m preparing your update',payload['message']);self.assertNotIn('I can draft this.',payload['message'])
        self.assertEqual(self.row()['body'],'I can draft this.')
        self.assertTrue(self.store.feedback_reply('Make it *funny* [please]','42',reply_to_id='41')['duplicate'])
        with self.store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM deliveries').fetchone()),delivery)
            self.assertEqual(dict(db.execute('SELECT * FROM preparations').fetchone()),job)
    def test_change_does_not_replace_uncertain_or_inflight_send(self):
        for status in ('uncertain','sending'):
            with self.store.connect() as db:db.execute('UPDATE deliveries SET status=?',(status,))
            self.store.request_feedback(self.sid,'request-'+status,card_message_id='40')
            self.store.bind_feedback_prompt(self.sid,'request-'+status,'41')
            self.store.feedback_reply('Update '+status,'42' if status=='uncertain' else '43',reply_to_id='41')
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],status)
    def test_result_finishing_during_feedback_card_edit_still_notifies(self):
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
        self.store.request_feedback(self.sid,'first',card_message_id='40');self.store.bind_feedback_prompt(self.sid,'first','41')
        self.store.feedback_reply('Funnier','42',reply_to_id='41')
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='running'")
        def finish_during_edit(*args):
            if args[2]=='edit':
                self.assertEqual(args[3],'40')
                self.store.prepare(self.sid,'The actual funnier result',self.job()['job_id'])
                return {'messageId':'40'}
            self.assertEqual(args[1]['status'],'prepared')
            return {'messageId':'43'}
        class Daytime(a.dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,11,14,0,tzinfo=tz or a.dt.timezone.utc)
        with patch.object(a.dt,'datetime',Daytime),patch.object(a,'telegram',side_effect=finish_during_edit) as send:
            a.deliver_one(self.store,{'telegram_owner':'1000001'},edit_cards=True)
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM deliveries').fetchone())
            self.assertEqual((row['status'],row['reminder'],row['message_id']),('sent',0,'43'))
        self.assertEqual([c.args[2] for c in send.call_args_list],['edit','send'])
    def test_feedback_progress_never_sends_another_card(self):
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
        self.store.request_feedback(self.sid,'change',card_message_id='40');self.store.bind_feedback_prompt(self.sid,'change','41')
        self.store.feedback_reply('Make it shorter','42',reply_to_id='41')
        class Daytime(a.dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,11,14,0,tzinfo=tz or a.dt.timezone.utc)
        with patch.object(a.dt,'datetime',Daytime),patch.object(a,'telegram',return_value={'messageId':'40'}) as send:
            a.deliver(self.store,{'telegram_owner':'1000001'})
            a.deliver(self.store,{'telegram_owner':'1000001'})
            self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[2:4],('edit','40'))
        self.assertEqual(self.job()['status'],'queued');self.assertEqual(self.row()['latest_feedback']['feedback'],'Make it shorter')
    def test_completion_edits_one_full_card_and_sends_only_short_notice(self):
        self.decide('accept')
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
            db.execute("UPDATE preparations SET status='running'")
        self.store.prepare(self.sid,'Unique complete result content',self.job()['job_id'])
        config={'telegram_owner':'1000001','dashboard_host':'machine.example','dashboard_path':'/assistant/'}
        payloads=[]
        def transport(config,payload):
            payloads.append(payload);return {'messageId':'40' if payload['action']=='edit' else '43'}
        class Daytime(a.dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,11,14,0,tzinfo=tz or a.dt.timezone.utc)
        with patch.object(a.dt,'datetime',Daytime),patch.object(a,'telegram_request',side_effect=transport):
            a.deliver(self.store,config);a.deliver(self.store,config)
        self.assertEqual([p['action'] for p in payloads],['edit','send'])
        self.assertIn('Unique complete result content',payloads[0]['message'])
        self.assertNotIn('Unique complete result content',payloads[1]['message'])
        self.assertEqual(payloads[1]['replyTo'],'40');self.assertEqual(payloads[1]['presentation']['blocks'],[])
        self.assertIn('Ready:',payloads[1]['message'])
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM deliveries').fetchone())
            self.assertEqual((row['message_id'],row['notification_message_id'],row['status']),('40','43','sent'))
            self.assertEqual(row['previous_message_ids'],'[]')
    def test_failed_completion_edit_does_not_announce_ready(self):
        self.decide('accept')
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
        self.store.prepare(self.sid,'Actual result')
        config={'telegram_owner':'1000001','dashboard_host':'machine.example'}
        with patch.object(a,'telegram',side_effect=ValueError('Disconnected')) as send:
            a.deliver_one(self.store,config)
        self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[2],'edit')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'queued')
    def test_plain_change_prompt_when_native_prompt_failed_is_sent_once(self):
        app=object.__new__(a.App);app.store=self.store;app.config={'telegram_owner':'1000001','accounts':[]}
        args={'suggestion_id':self.sid,'request_id':'cb1','card_message_id':'40'}
        app.config['prompt_receipts']=str(self.root/'receipts')
        app.action('request_feedback',{**args,'managed_prompt':True})
        with patch.object(a,'now',return_value=(a.dt.datetime.fromisoformat(a.now())+a.dt.timedelta(seconds=11)).isoformat()):
            app.feedback_prompt_step(sender=lambda row:{'definitely_not_sent':True})
        sent=[]
        with patch.object(a,'now',return_value=(a.dt.datetime.fromisoformat(a.now())+a.dt.timedelta(seconds=25)).isoformat()),patch.object(a,'telegram_request',side_effect=lambda config,payload:(sent.append(payload),{'messageId':'41'})[1]):
            app.feedback_prompt_step()
            app.action('request_feedback',{**args,'managed_prompt':True})  # a retried callback
            app.feedback_prompt_step()
        self.assertEqual(len(sent),1);self.assertEqual(sent[0]['replyTo'],'40')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT feedback_prompt_message_id FROM suggestions').fetchone()[0],'41')

    def test_card_that_cannot_be_edited_does_not_hold_the_ready_notice_forever(self):
        self.decide('accept')
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='sent',message_id='40',needs_edit=0")
        self.store.prepare(self.sid,'Actual result')
        with self.store.connect() as db:db.execute("UPDATE deliveries SET needs_edit=1,edit_failures=3,edit_retry_at='2999-01-01'")
        class Daytime(a.dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,11,14,0,tzinfo=tz or a.dt.timezone.utc)
        config={'telegram_owner':'1000001','dashboard_host':'machine.example'}
        with patch.object(a.dt,'datetime',Daytime),patch.object(a,'telegram_request',return_value={'messageId':'44'}) as send:
            a.deliver_one(self.store,config)
        self.assertTrue(send.call_args.args[1]['message'].startswith('Ready: '))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT notification_message_id FROM deliveries').fetchone()[0],'44')
    def test_uncertain_short_notice_reconciles_without_replacing_task_card(self):
        import sqlite3,json
        self.decide('accept');self.store.prepare(self.sid,'Actual result')
        when=(a.dt.datetime.now(a.dt.timezone.utc)-a.dt.timedelta(minutes=5)).isoformat()
        with self.store.connect() as db:db.execute("UPDATE deliveries SET status='uncertain',reminder=1,send_kind='notice',message_id='40',updated_at=?",(when,))
        native=self.root/'native.sqlite'
        with sqlite3.connect(native) as db:
            db.execute('CREATE TABLE plugin_state_entries(plugin_id,namespace,value_json)')
            db.execute('INSERT INTO plugin_state_entries VALUES(?,?,?)',('telegram','telegram.message-cache',json.dumps({'sourceMessage':{
                'message_id':43,'date':a.dt.datetime.fromisoformat(when).timestamp()+10,'from':{'is_bot':True},
                'chat':{'id':1000001},'text':'Ready [View result](https://machine.example/#result='+self.sid+')'}})))
        a.reconcile_uncertain(self.store,{'telegram_owner':'1000001','dashboard_host':'machine.example','openclaw_state':str(native)})
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM deliveries').fetchone())
            self.assertEqual((row['status'],row['message_id'],row['notification_message_id'],row['reminder']),('sent','40','43',0))
    def test_only_linked_assistant_files_are_served(self):
        reports=self.root/'memory/assistant';reports.mkdir(parents=True)
        report=reports/'261011-draft.md';report.write_text('Real draft');(self.root/'secret.txt').write_text('Private')
        with self.store.connect() as db:db.execute('UPDATE suggestions SET body=?',('memory/assistant/261011-draft.md',))
        with patch.object(a,'WORKSPACE',self.root):
            self.assertEqual(self.store.result_document(self.sid,'memory/assistant/261011-draft.md'),report.resolve())
            with self.assertRaises(ValueError):self.store.result_document(self.sid,'secret.txt')
            with self.assertRaises(ValueError):self.store.result_document(self.sid,'memory/assistant/../../secret.txt')
            report.unlink();report.symlink_to(self.root/'secret.txt')
            with self.assertRaises(ValueError):self.store.result_document(self.sid,'memory/assistant/261011-draft.md')
    def test_telegram_has_mobile_result_link_not_path(self):
        row=self.row();row.update(status='prepared',body='Draft. [Full result](memory/assistant/261011-draft.md)')
        with patch.object(a,'telegram_request',side_effect=lambda config,args:args):
            payload=a.telegram({'dashboard_host':'machine.example','dashboard_path':'/assistant/','telegram_owner':'owner'},row,'send')
        self.assertNotIn('memory/assistant/',payload['message']);self.assertIn('https://machine.example/assistant/#chat=',payload['message'])
    def test_telegram_links_open_the_cards_own_page_not_the_old_result_modal(self):
        """A tap on a Telegram link opened the old Result modal (Copy draft) over the list. Every link now opens the card's
        own page (#chat=<id>), as a tap on the card in the list does."""
        config={'dashboard_host':'machine.example','dashboard_path':'/assistant/','telegram_owner':'owner'}
        row=self.row();row.update(status='prepared',body='Draft.',message_id=5)
        with patch.object(a,'telegram_request',side_effect=lambda config,args:args):
            texts=[a.telegram(config,row,'send')['message'],a.telegram(config,{**row,'_result_notice':True},'send')['message'],
                   a.telegram(config,{**row,'status':'instructions','preparation_status':'blocked','_result_notice':True},'send')['message']]
        for text in texts:
            self.assertIn('https://machine.example/assistant/#chat='+self.sid+')',text);self.assertNotIn('#result=',text)
