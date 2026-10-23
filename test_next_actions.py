import json
import subprocess
import threading
import unittest
from unittest.mock import patch
import next_actions as n
import test_ux as ux
a=ux.a


class NextActions(unittest.TestCase):
    setUp=ux.UX.setUp
    row=ux.UX.row
    job=ux.UX.job
    decide=ux.UX.decide

    def app(self):
        app=object.__new__(a.App);app.store=self.store
        app.config={'accounts':[{'id':'work','email':'owner@example.com','client':'existing','service':'gmail','transport':'gog'}]}
        return app

    def draft(self,**extra):
        return {'kind':'email','account_id':'work','to':['recipient@example.com'],'subject':'Verified subject','body':'Exact approved draft.',**extra}

    def ready(self,**extra):
        self.decide('accept');app=self.app()
        app.action('prepare',{'suggestion_id':self.sid,'body':'The reply is ready.','next_action':self.draft(**extra)})
        return self.row()['next_action']

    def confirm(self,revision=None,key='owner-approval'):
        action=self.row()['next_action']
        return self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':revision or action['revision'],'approval_id':key},owner_dashboard=True)

    def action_job(self):
        with self.store.connect() as db:return dict(db.execute('SELECT * FROM action_jobs ORDER BY created_at DESC LIMIT 1').fetchone())

    def transport(self,send=None,preflight=None,reconcile=None):
        class Transport:
            def preflight(self,action):return preflight(action) if preflight else None
            def send(self,action,job):return send(action,job) if send else {'message_id':'accepted'}
            def reconcile(self,action,job):return reconcile(action,job) if reconcile else {}
        return Transport()

    def test_prepare_adds_send_but_never_sends_or_approves(self):
        self.ready();self.assertEqual(a.controls(self.row())['blocks'][0]['buttons'][0]['label'],'Send')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0],0)
        self.app().action_step(self.transport(send=lambda *_:self.fail('Preparation cannot send')))

    def test_send_now_when_owner_asked_goes_through_the_send_route_once(self):
        self.decide('accept');sent=[]
        # A draft without recipients still needs the owner to pick them.
        with self.assertRaises(ValueError):
            self.app().action('prepare',{'suggestion_id':self.sid,'body':'x','next_action':{**self.draft(),'to':[]},'send_now':True})
        with self.assertRaisesRegex(ValueError,'Not sent'):  # a draft they haven't seen yet is shown first
            self.app().action('prepare',{'suggestion_id':self.sid,'body':'Sent the reply.','next_action':self.draft(),'send_now':True})
        self.app().action('prepare',{'suggestion_id':self.sid,'body':'The reply is ready.','next_action':self.draft()})
        self.decide('other','send-it','Send it')
        saved=self.app().action('prepare',{'suggestion_id':self.sid,'body':'Sent the reply.','next_action':self.draft(),'send_now':True})
        self.assertEqual(saved['send'],{'status':'queued'})
        app=self.app();app.action_step(self.transport(send=lambda action,job:(sent.append(action),{'message_id':'sent'})[1]))
        app.action_step(self.transport(send=lambda *_:self.fail('Duplicate send')))
        self.assertEqual([s['body'] for s in sent],['Exact approved draft.'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT source FROM decisions WHERE choice='send'").fetchone()[0],'assistant')

    def test_exact_owner_approval_replay_and_send_once(self):
        self.ready();self.confirm();self.confirm(key='another-click');calls=[]
        app=self.app();app.action_step(self.transport(send=lambda action,job:(calls.append(action),{'message_id':'accepted'})[1]))
        app.action_step(self.transport(send=lambda *_:self.fail('Duplicate send')))
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['body'],'Exact approved draft.')
        self.assertEqual(self.action_job()['status'],'sent');self.assertEqual(self.row()['status'],'completed')
        self.assertNotIn('Send',[b['label'] for b in a.controls(self.row())['blocks'][0]['buttons']])

    def test_owner_edit_sends_exactly_the_edited_draft_and_is_remembered(self):
        old=self.ready();app=self.app()
        with self.assertRaises(ValueError):  # only the owner in the dashboard edits drafts
            app.action('revise_draft',{'suggestion_id':self.sid,'revision':old['revision'],'body':'x'})
        same=app.action('revise_draft',{'suggestion_id':self.sid,'revision':old['revision'],'body':old['body']},owner_dashboard=True)
        self.assertEqual(same,{'status':'unchanged','revision':old['revision']})
        r=app.action('revise_draft',{'suggestion_id':self.sid,'revision':old['revision'],'subject':'Shorter subject','body':'My own words.\n\nCheers,\nAlex'},owner_dashboard=True)
        self.assertNotEqual(r['revision'],old['revision']);row=self.row()['next_action']
        self.assertEqual((row['subject'],row['to'],row['from'],row['revision']),('Shorter subject',['recipient@example.com'],'owner@example.com',r['revision']))
        with self.assertRaises(ValueError):self.confirm(old['revision'])
        self.confirm(r['revision']);sent=[]
        app.action_step(self.transport(send=lambda action,job:(sent.append(action),{'message_id':'ok'})[1]))
        self.assertEqual([s['body'] for s in sent],['My own words.\n\nCheers,\n\nAlex'])
        with self.store.connect() as db:
            edit=dict(db.execute('SELECT * FROM draft_edits').fetchone())
        self.assertEqual((edit['before_body'],edit['after_subject'],edit['source']),('Exact approved draft.','Shorter subject','dashboard'))
        with self.assertRaises(ValueError):  # sent drafts stay as sent
            app.action('revise_draft',{'suggestion_id':self.sid,'revision':r['revision'],'body':'Later change'},owner_dashboard=True)

    def test_answers_to_a_card_send_are_flagged_for_the_review(self):
        self.ready(thread_id='abc123',in_reply_to='<orig@x>');self.confirm();app=self.app()
        app.action_step(self.transport(send=lambda *_:{'message_id':'gm1'}))
        rfc=self.action_job()['rfc_message_id']
        later=(a.dt.datetime.fromisoformat(a.now())+a.dt.timedelta(minutes=5)).isoformat()
        mail=lambda sid,meta:{'account_id':'work','source_id':sid,'kind':'gmail.message','occurred_at':later,'title':'Re: Verified subject','body':'Sounds good, but Tuesday?','metadata':meta,'deleted':False}
        self.store.commit_page('work',{},[mail('r1',{'in-reply-to':rfc,'labels':['INBOX']}),mail('r2',{'thread_id':'abc123','labels':['INBOX']}),
                                          mail('r3',{'thread_id':'abc123','labels':['SENT']}),mail('r4',{'thread_id':'other','labels':['INBOX']})])
        events=self.store.begin_analysis(['work'],limit=40)['events']
        with self.store.connect() as db:ids=dict(db.execute("SELECT source_id,id FROM events").fetchall())
        got={sid for sid in ('r1','r2','r3','r4') if any(e['id']==ids[sid] and e.get('answers_your_card',{}).get('suggestion_id')==self.sid for e in events)}
        self.assertEqual(got,{'r1','r2'})  # the owner's own sent copy and other threads are not answers

    def test_old_draft_confirmation_rejected_and_queue_cancelled(self):
        old=self.ready();self.confirm()
        self.app().action('prepare',{'suggestion_id':self.sid,'body':'Revised reply','next_action':self.draft(body='New exact body')})
        with self.assertRaises(ValueError):self.confirm(old['revision'])
        self.assertEqual(self.action_job()['status'],'cancelled')
        self.app().action_step(self.transport(send=lambda *_:self.fail('Old approval must not send new draft')))

    def test_done_or_snooze_before_send_cancels_and_cannot_revive(self):
        self.ready();self.confirm();self.decide('snooze')
        self.app().action_step(self.transport(send=lambda *_:self.fail('Send after snooze')))
        self.assertEqual(self.action_job()['status'],'cancelled')

    def test_cancellation_during_preflight_wins(self):
        self.ready();self.confirm()
        self.app().action_step(self.transport(preflight=lambda _:self.decide('complete'),send=lambda *_:self.fail('Send after cancellation')))
        self.assertEqual(self.action_job()['status'],'cancelled')

    def test_concurrent_workers_admit_only_once(self):
        self.ready();self.confirm();barrier=threading.Barrier(2);sent=[]
        transport=self.transport(preflight=lambda _:barrier.wait(timeout=3),send=lambda *_:(sent.append(1),{'message_id':'accepted'})[1])
        threads=[threading.Thread(target=self.app().action_step,args=(transport,)) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=4)
        self.assertEqual(sent,[1])

    def test_unknown_send_and_restart_only_reconcile_positive_identity(self):
        self.ready();self.confirm();self.app().action_step(self.transport(send=lambda *_:{}))
        with self.store.connect() as db:db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.app().action_step(self.transport(send=lambda *_:self.fail('Blind resend')))
        self.assertEqual(self.action_job()['status'],'uncertain')
        with self.store.connect() as db:db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.app().action_step(self.transport(reconcile=lambda *_:{'message_id':'exact-sent-id'},send=lambda *_:self.fail('Resend')))
        self.assertEqual(self.action_job()['status'],'sent')

    def test_readonly_permission_failure_never_admits_send(self):
        self.ready();self.confirm()
        def deny(_):raise ValueError('Gmail is connected read-only. Nothing was sent.')
        self.app().action_step(self.transport(preflight=deny,send=lambda *_:self.fail('Unapproved permission')))
        self.assertEqual(self.action_job()['status'],'blocked')

    def test_a_calendar_task_sends_from_its_companys_gmail_but_not_another_companys(self):
        config={'accounts':[{'id':'cal','service':'calendar','email':'owner@acme.test','boundary':'work:Acme'},
                            {'id':'acme-mail','service':'gmail','email':'owner@acme.test','boundary':'work:Acme','transport':'broker'},
                            {'id':'side-mail','service':'gmail','email':'me@side.test','boundary':'work:Side','transport':'gog'}]}
        self.assertEqual(n.validate(self.draft(account_id='acme-mail'),config,['cal'])['from'],'owner@acme.test')
        with self.assertRaises(ValueError):n.validate(self.draft(account_id='side-mail'),config,['cal'])

    def test_account_isolation_header_injection_arbitrary_actions_rejected(self):
        for invalid in [self.draft(account_id='personal'),self.draft(subject='Hi\nBcc: thief@example.com'),
                        self.draft(to=['"Bob" <b@example.com>']),self.draft(attach=['/private']),{'kind':'command','cmd':'do anything'}]:
            with self.assertRaises(ValueError):n.validate(invalid,self.app().config,['work'])
        with self.assertRaises(ValueError):n.validate(self.draft(),self.app().config,['other'])

    def test_confirmation_refused_from_agent_cli_and_unscoped_input(self):
        self.ready();self.assertIn('confirm_action',a.OWNER_ACTIONS)
        with self.assertRaises(ValueError):self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':self.row()['next_action']['revision'],'approval_id':'agent-generated'})

    def test_exact_mime_sender_recipients_reply_and_unique_identity(self):
        self.ready(cc=['cc@example.com'],in_reply_to='<original@example.com>',thread_id='abcdef')
        self.confirm();msg=n.mime_bytes(self.row()['next_action'],self.action_job()).decode()
        self.assertIn('From: owner@example.com',msg);self.assertIn('To: recipient@example.com',msg)
        self.assertIn('In-Reply-To: <original@example.com>',msg);self.assertIn(self.action_job()['rfc_message_id'],msg)
        self.assertIn('Exact approved draft.',msg)

    def attach_setup(self):
        src=self.root/'job-files';src.mkdir();store=self.root/'attachment-store'
        app=self.app();app.config.update(attachment_sources=[str(src)],attachment_dir=str(store))
        self.app=lambda:app
        return src,store

    def test_attachments_from_allowed_folder_are_stored_bound_to_revision_and_sent(self):
        src,store=self.attach_setup();(src/'credit note.pdf').write_bytes(b'%PDF-1 credit')
        action=self.ready(attachments=[str(src/'credit note.pdf')])
        record=action['attachments'][0];self.assertEqual(record['name'],'credit note.pdf');self.assertEqual(record['type'],'application/pdf')
        self.assertEqual((store/record['sha256']).read_bytes(),b'%PDF-1 credit')
        self.assertIn('Attachments: credit note.pdf (13 B)',n.preview(action))
        (src/'credit note.pdf').write_bytes(b'%PDF-1 other')  # the reviewed bytes are kept, not the live file
        other=self.ready(attachments=[str(src/'credit note.pdf')])
        self.assertNotEqual(action['revision'],other['revision'])
        self.confirm();msg=n.mime_bytes(self.row()['next_action'],self.action_job(),config=self.app().config).decode()
        self.assertIn('filename="credit note.pdf"',msg);self.assertIn('Content-Type: application/pdf',msg)
        app=self.app();app.config.setdefault('accounts',[])
        self.assertEqual(a.App.draft_attachment(app,self.sid,other['attachments'][0]['sha256'])[1:],('application/pdf','credit note.pdf'))
        with self.assertRaises(ValueError):a.App.draft_attachment(app,self.sid,'0'*64)  # only this card's own draft files

    def test_attachments_outside_allowed_folders_or_oversized_are_refused(self):
        src,_=self.attach_setup();outside=self.root/'secret.txt';outside.write_text('token')
        (src/'link.txt').symlink_to(outside);(src/'empty.pdf').write_bytes(b'');(src/'a.pdf').write_bytes(b'x')
        for paths in ([str(outside)],[str(src/'link.txt')],[str(src/'missing.pdf')],[str(src/'empty.pdf')],[],[str(src/'a.pdf')]*11,'a.pdf'):
            with self.assertRaises(ValueError,msg=paths):n.store_attachments(paths,self.app().config)
        with patch.object(n,'MAX_ATTACHMENT_BYTES',1),self.assertRaises(ValueError):
            (src/'b.pdf').write_bytes(b'yy');n.store_attachments([str(src/'b.pdf')],self.app().config)
        self.decide('accept')
        with self.assertRaises(ValueError):
            self.app().action('prepare',{'suggestion_id':self.sid,'body':'x','next_action':self.draft(attachments=[str(outside)])})

    def test_missing_or_changed_attachment_blocks_send(self):
        src,store=self.attach_setup();(src/'invoice.pdf').write_bytes(b'invoice')
        record=self.ready(attachments=[str(src/'invoice.pdf')])['attachments'][0]
        (store/record['sha256']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'changed'):n.attachment_bytes(record,self.app().config)
        (store/record['sha256']).unlink()
        with self.assertRaises(OSError):n.attachment_bytes(record,self.app().config)

    def test_unverified_recipient_offers_thread_addresses_and_owner_picks(self):
        self.decide('accept')
        with self.store.connect() as db:
            db.execute("UPDATE events SET kind='gmail.message',metadata=?",(json.dumps({'from':'"\'Will\' via info" <info@example.com>','reply-to':'Will <Will@Vendor.example>',
                       'to':'Owner <owner@example.com>','cc':'noreply@example.com, Ann <ann@example.com>'}),))
        app=self.app()
        with self.assertRaises(ValueError):  # The model cannot widen the choice.
            app.action('prepare',{'suggestion_id':self.sid,'body':'Ready.','next_action':self.draft(to=[],cc=['x@example.com'])|{'to_options':['evil@example.com']}})
        app.action('prepare',{'suggestion_id':self.sid,'body':'Ready.','next_action':self.draft(to=[])})
        action=self.row()['next_action']
        self.assertEqual(action['to_options'],['will@vendor.example','ann@example.com'])
        self.assertIn('you choose: will@vendor.example',n.preview(action))
        for wrong in (None,[],['evil@example.com'],['ann@example.com','ann@example.com']):
            with self.assertRaises(ValueError):
                app.action('confirm_action',{'suggestion_id':self.sid,'revision':action['revision'],'approval_id':'pick','recipients':wrong},owner_dashboard=True)
        app.action('confirm_action',{'suggestion_id':self.sid,'revision':action['revision'],'approval_id':'pick','recipients':['ann@example.com']},owner_dashboard=True)
        payload=json.loads(self.action_job()['payload'])
        self.assertEqual(payload['to'],['ann@example.com']);self.assertNotIn('to_options',payload)

    def test_no_thread_address_still_needs_a_recipient(self):
        self.decide('accept')
        with self.assertRaises(ValueError):
            self.app().action('prepare',{'suggestion_id':self.sid,'body':'Ready.','next_action':self.draft(to=[])})

    def test_verified_recipient_cannot_be_overridden(self):
        action=self.ready()
        with self.assertRaises(ValueError):
            self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':action['revision'],'approval_id':'x','recipients':['other@example.com']},owner_dashboard=True)

    def test_open_link_has_no_execution_approval(self):
        self.decide('accept');self.app().action('prepare',{'suggestion_id':self.sid,'body':'Changes ready','next_action':{'kind':'open','label':'Open changes','url':'https://example.com/editor'}})
        with self.assertRaises(ValueError):self.confirm()

    def test_preflight_does_not_hold_database_transaction(self):
        self.ready();self.confirm()
        def check(_):
            with self.store.connect() as db:db.execute('UPDATE deliveries SET needs_edit=1')
        self.app().action_step(self.transport(preflight=check))
        self.assertEqual(self.action_job()['status'],'sent')

    def test_cancelled_approval_requires_fresh_click_even_after_reopen(self):
        self.ready();self.confirm();self.decide('complete');self.decide('undo')
        self.confirm();self.assertEqual(self.action_job()['status'],'cancelled')
        self.confirm(key='fresh-owner-approval');self.assertEqual(self.action_job()['status'],'queued')

    def test_gmail_refusal_is_failed_not_uncertain_and_send_can_be_tapped_again(self):
        self.ready();self.confirm();self.app().action_step(self.transport(send=lambda *_:{'rejected':400}))
        job=self.action_job();self.assertEqual(job['status'],'failed');self.assertIn('Nothing was sent',job['error'])
        self.assertIn('Gmail refused this email',a.card(self.row()))
        self.assertIn('Send',[b['label'] for b in a.controls(self.row())['blocks'][0]['buttons']])
        self.app().action_step(self.transport(send=lambda *_:self.fail('No automatic retry after a refusal')))
        self.confirm(key='second-tap');self.assertEqual(self.action_job()['status'],'queued')
        self.app().action_step(self.transport());self.assertEqual(self.action_job()['status'],'sent')

    def test_gog_errors_only_count_as_refused_for_clear_client_errors(self):
        self.ready();self.confirm();action=self.row()['next_action'];job=self.action_job()
        transport=n.EmailTransport({**self.app().config,'gog':'gog'},lambda *_:'synthetic-secret',lambda *_:{})
        def failing(stdout):return patch.object(n.subprocess,'run',return_value=subprocess.CompletedProcess([],5,stdout,''))
        google=lambda code:json.dumps({'error':{'class':'api','code':5,'message':'gmail.users.messages.send: {"error": {"code": %d, "message": "x"}}'%code}})
        for stdout,expected in [(google(400),{'rejected':400}),(google(403),{'rejected':403}),(google(429),{}),(google(500),{}),
                                ('',{}),('not json',{}),(json.dumps({'error':{'message':'timeout'}}),{})]:
            with failing(stdout):self.assertEqual(transport.send(action,job),expected,stdout)

    def test_gog_transport_uses_exact_private_request_and_readonly_gate(self):
        self.ready();self.confirm();action=self.row()['next_action'];job=self.action_job()
        config={**self.app().config,'gog':'gog'}
        transport=n.EmailTransport(config,lambda *_:'synthetic-secret',lambda *_:{'emailAddress':'owner@example.com'})
        with patch.object(n.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'accounts':[{'email':'owner@example.com','client':'existing','scopes':['https://www.googleapis.com/auth/gmail.readonly']}]}),'')):
            with self.assertRaises(ValueError):transport.preflight(action)
        def send(command,**kwargs):
            from pathlib import Path
            payload=json.loads(Path(command[command.index('--body')+1][1:]).read_text())
            raw=n.base64.urlsafe_b64decode(payload['raw']+'='*(-len(payload['raw'])%4)).decode()
            self.assertIn(job['rfc_message_id'],raw);self.assertIn('Exact approved draft.',raw)
            self.assertIn('--allow-write',command);self.assertIn('owner@example.com',command)
            self.assertNotIn('Exact approved draft.',str(command))
            return subprocess.CompletedProcess(command,0,'{"id":"exact-receipt"}','')
        with patch.object(n.subprocess,'run',side_effect=send):self.assertEqual(transport.send(action,job),{'message_id':'exact-receipt'})

    def menu(self,with_email=True):
        self.decide('accept')
        self.app().action('prepare',{'suggestion_id':self.sid,'body':'The fix proposal is ready.','next_actions':([self.draft(label='Send reply')] if with_email else [])+[
            {'kind':'prepare','label':'Draft support email','instructions':'Draft an email about the exact field-mapping error; do not send it.'},
            {'kind':'open','label':'Open the Zap','url':'https://example.com/zap'}]})
        return self.row()['next_actions']

    def test_smart_menu_labels_owner_selected_preparation_and_replay(self):
        choices=self.menu(with_email=False);buttons=[b['label'] for block in a.controls(self.row())['blocks'] for b in block['buttons']]
        for label in ('Draft support email','Open the Zap'):self.assertIn(label,buttons)
        self.confirm(choices[0]['revision']);job=self.job()
        self.assertEqual(self.row()['status'],'accepted');self.assertEqual(job['status'],'queued')
        self.assertEqual(self.action_job()['status'],'local_preparation')
        with self.assertRaises(ValueError):self.confirm(choices[0]['revision'])
        requests=[];self.app().preparation_step(lambda request:(requests.append(request),{'status':'admitted'})[1])
        self.assertIn(choices[0]['instructions'],requests[0]['message'])
        self.app().action_step(self.transport(send=lambda *_:self.fail('A preparation cannot send')))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0],1)
            decision=db.execute("SELECT choice,feedback FROM decisions WHERE choice='prepare_next'").fetchone()
            self.assertEqual(tuple(decision),('prepare_next',''))
        # The step finishes; when a later result offers the same step, it works again.
        self.app().action('prepare',{'suggestion_id':self.sid,'job_id':self.job()['job_id'],'body':'Support email drafted.','outcome':'done','next_actions':[
            {'kind':'prepare','label':'Draft support email','instructions':'Draft an email about the exact field-mapping error; do not send it.'}]})
        self.assertEqual(self.action_job()['status'],'done')
        again=self.row()['next_actions'][0];self.assertEqual(again['revision'],choices[0]['revision'])
        self.assertIn('Draft support email',[b['label'] for block in a.controls(self.row())['blocks'] for b in block['buttons']])
        self.confirm(again['revision'],key='second-step');self.assertEqual(self.action_job()['status'],'local_preparation')
        self.assertEqual(self.row()['status'],'accepted')

    def test_menu_email_approval_uses_selected_payload_and_renders_outcome(self):
        choices=self.menu();self.confirm(choices[0]['revision']);sent=[]
        self.app().action_step(self.transport(send=lambda action,job:(sent.append(action),{'message_id':'approved'})[1]))
        self.assertEqual(sent[0]['body'],choices[0]['body']);self.assertEqual(self.row()['next_actions'][0]['state']['status'],'sent')
        self.assertIn('✓ Email sent to recipient@example.com at ',a.card(self.row()))
        self.assertNotIn('Send reply',[b['label'] for block in a.controls(self.row())['blocks'] for b in block['buttons']])

    def test_menu_cannot_start_new_preparation_while_send_is_unknown(self):
        choices=self.menu();self.confirm(choices[0]['revision']);self.app().action_step(self.transport(send=lambda *_:{}))
        hidden=self.row()['next_action']['actions'][1]
        with self.assertRaises(ValueError):self.confirm(hidden['revision'],key='new-owner-click')
        self.assertEqual(self.row()['status'],'prepared')

    def test_smart_labels_and_choices_do_not_fake_execution_or_confuse_owner_status(self):
        config=self.app().config
        for invalid in [[],[self.draft()]*4,[[self.draft()]],
                        [self.draft(),self.draft(body='Different body')],
                        {'kind':'prepare','label':'Pay invoice','instructions':'Pay it'},
                        {'kind':'prepare','label':'Create event','instructions':'Add it'},
                        {'kind':'open','label':'Send email','url':'https://example.com'},
                        {'kind':'prepare','label':'Change','instructions':'Anything'},self.draft(label='Draft reply')]:
            with self.assertRaises(ValueError):n.validate(invalid,config,['work'])

    def test_a_last_step_says_what_it_does_and_what_goes_out(self):
        config=self.app().config
        step={'kind':'finish','label':'Pay invoice 2026-114','instructions':'In the bank app pay 1,210.00 EUR to BE68 5390 0754 7034, reference +++090/9337/55493+++.',
              'preview':'1,210.00 EUR to Globex BV (BE68 5390 0754 7034), reference +++090/9337/55493+++, from the acme business account.'}
        result=n.validate(step,config,['work'])
        self.assertEqual((n.label(result),n.preview(result)),('Pay invoice 2026-114',step['preview']))
        for invalid in [{**step,'label':'Handle invoice'},{**step,'label':None},{**step,'preview':''},{**step,'instructions':'x'*2001},
                        {**step,'url':'http://bank.example.com'},{**step,'url':'https://user@bank.example.com'},{**step,'amount':12}]:
            with self.assertRaises(ValueError):n.validate(invalid,config,['work'])
        menu=n.validate([step,{'kind':'prepare','label':'Check the amount','instructions':'Compare with the order'}],config,['work'])
        self.assertEqual([a['kind'] for a in n.visible_actions(menu)],['finish'])  # the confirm is the next step

    def test_menu_old_revision_cannot_approve_new_result(self):
        choices=self.menu()
        self.app().action('prepare',{'suggestion_id':self.sid,'body':'Updated proposal','next_actions':[self.draft(label='Send follow-up',body='Changed full draft')]})
        with self.assertRaises(ValueError):self.confirm(choices[0]['revision'])

    def test_finished_email_uses_send_and_change_not_more_drafting(self):
        self.menu();row=self.row()
        labels=[b['label'] for block in a.controls(row)['blocks'] for b in block['buttons']]
        self.assertEqual(labels,['Send reply','Open the Zap','Mark done','Snooze','Change','Not needed'])
        hidden=row['next_action']['actions'][1]
        with self.assertRaises(ValueError):self.confirm(hidden['revision'])
        self.assertNotIn('Draft support email',[x['label'] for x in row['next_actions']])
        self.assertEqual(n.validate(self.draft(label='Send'),self.app().config,['work'])['label'],'Send')

    def test_rewrite_shortcuts_are_hidden_even_without_email(self):
        self.decide('accept');self.app().action('prepare',{'suggestion_id':self.sid,'body':'Finished draft','next_actions':[
            {'kind':'prepare','label':'Draft shorter reply','instructions':'Shorten this'},
            {'kind':'prepare','label':'Draft follow-up','instructions':'Rewrite it'}]})
        self.assertEqual(self.row()['next_actions'],[])
        self.assertEqual([b['label'] for block in a.controls(self.row())['blocks'] for b in block['buttons']],['Mark done','Snooze','Change','Not needed'])

    def test_demo_preview_cannot_send_or_approve_and_requires_test_evidence(self):
        self.decide('accept');demo={'kind':'email_demo','label':'Send','subject':'Demo supplier reply','body':'Thanks for the proposal.'}
        with self.assertRaises(ValueError):self.app().action('prepare',{'suggestion_id':self.sid,'body':'Demo reply','next_action':demo})
        with self.store.connect() as db:db.execute("UPDATE events SET kind='assistant.test',metadata=?",(json.dumps({'test':True}),))
        self.app().action('prepare',{'suggestion_id':self.sid,'body':'Demo reply','next_action':demo})
        self.assertEqual(self.row()['next_action']['to'],['alex@example.invalid'])
        with self.assertRaises(ValueError):self.confirm()
        self.app().action_step(self.transport(send=lambda *_:self.fail('Demo cannot send')))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0],0)


class ThreadSnoozes(unittest.TestCase):
    setUp=ux.UX.setUp
    row=ux.UX.row
    decide=ux.UX.decide
    app=NextActions.app
    draft=NextActions.draft
    ready=NextActions.ready
    confirm=NextActions.confirm
    transport=NextActions.transport
    reply={'thread_id':'abc123','in_reply_to':'<orig@example.com>'}

    def snoozes(self):
        with self.store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM thread_snoozes')]

    def test_replies_default_to_three_days_and_new_emails_never_snooze(self):
        config=self.app().config
        self.assertEqual(n.validate(self.draft(**self.reply),config,['work'])['snooze_days'],3)
        self.assertNotIn('snooze_days',n.validate(self.draft(**self.reply,snooze_days=0),config,['work']))
        self.assertNotIn('snooze_days',n.validate(self.draft(),config,['work']))
        for bad in (dict(snooze_days=2),{**self.reply,'snooze_days':15},{**self.reply,'snooze_days':True},{**self.reply,'snooze_days':'3'}):
            with self.assertRaises(ValueError):n.validate(self.draft(**bad),config,['work'])
        self.assertIn('leaves your inbox for 5 days',n.email_preview(n.validate(self.draft(**self.reply,snooze_days=5),config,['work'])))

    def test_return_time_is_nine_on_a_weekday(self):
        with patch.object(a,'now',return_value='2026-10-12T08:00:00+00:00'):  # Monday
            self.assertEqual(a.thread_return_time(3),'2026-10-15T07:00:00+00:00')  # Thursday 09:00 local
            self.assertEqual(a.thread_return_time(5),'2026-10-19T07:00:00+00:00')  # Saturday -> Monday
        with patch.object(a,'now',return_value='2026-10-23T08:00:00+00:00'):  # Friday, across the DST change
            self.assertEqual(a.thread_return_time(2),'2026-10-26T08:00:00+00:00')

    def test_sent_reply_is_archived_then_returned_and_shown_on_the_card(self):
        self.ready(**self.reply);self.confirm();self.app().action_step(self.transport())
        [row]=self.snoozes();self.assertEqual((row['thread_id'],row['status']),('abc123','queued'))
        self.assertIn('Thread out of your inbox until',a.card(self.row()))
        calls=[]
        class Gmail:
            def snooze(self,account,thread,sent):calls.append(('snooze',account,thread));return 'snoozed'
            def wake(self,account,thread):calls.append(('wake',account,thread));return 'returned'
        app=self.app();app.thread_snooze_step(Gmail());app.thread_snooze_step(Gmail())
        self.assertEqual(calls,[('snooze','work','abc123')]);self.assertEqual(self.snoozes()[0]['status'],'snoozed')
        with self.store.connect() as db:db.execute("UPDATE thread_snoozes SET until='2000-01-01T00:00:00+00:00'")
        app.thread_snooze_step(Gmail());self.assertEqual(calls[-1],('wake','work','abc123'))
        self.assertEqual(self.snoozes()[0]['status'],'returned');self.assertNotIn('Thread out',a.card(self.row()))

    def test_missing_permission_and_errors_are_reported_not_hidden(self):
        self.ready(**self.reply);self.confirm();self.app().action_step(self.transport())
        class Gmail:
            def snooze(self,*_):return 'no_permission'
        self.app().thread_snooze_step(Gmail())
        self.assertEqual(self.snoozes()[0]['status'],'failed');self.assertIn('modify permission',a.card(self.row()))
        with self.store.connect() as db:db.execute("UPDATE thread_snoozes SET status='snoozed',until='2000-01-01T00:00:00+00:00',error=NULL")
        class Down:
            def wake(self,*_):raise RuntimeError('down')
        self.app().thread_snooze_step(Down())
        row=self.snoozes()[0];self.assertEqual(row['status'],'snoozed');self.assertIsNotNone(row['retry_at'])

    def test_gmail_wake_respects_a_reply_or_owner_move(self):
        config={'accounts':[{'id':'work','email':'owner@example.com','client':'existing'}],'gog':'gog'}
        gmail=n.ThreadSnooze(config,lambda *_:'secret',None);writes=[]
        gmail.label=lambda account:'Label_9'
        gmail.write=lambda account,method,params,body:writes.append(body)
        for labels,expected in [({'Label_9'},{'addLabelIds':['INBOX','UNREAD'],'removeLabelIds':['Label_9']}),
                                ({'Label_9','INBOX'},{'addLabelIds':[],'removeLabelIds':['Label_9']}),(set(),None)]:
            writes.clear();gmail.thread_labels=lambda account,thread,labels=labels:labels
            self.assertEqual(gmail.wake('work','t1'),'returned');self.assertEqual(writes[-1] if writes else None,expected)
        gmail.allowed=lambda account:True
        gmail.thread=lambda *_:{'messages':[{'id':'sent1','labelIds':['SENT'],'internalDate':'100'}]}
        self.assertEqual(gmail.snooze('work','t1','sent1'),'snoozed');self.assertEqual(writes[-1],{'addLabelIds':['Label_9'],'removeLabelIds':['INBOX']})
        gmail.allowed=lambda account:False;writes.clear()
        self.assertEqual(gmail.snooze('work','t1','sent1'),'no_permission');self.assertEqual(writes,[])


class EmailFormat(unittest.TestCase):
    config={'accounts':[{'id':'work','email':'owner@example.com','service':'gmail','transport':'gog','client':'existing'}]}
    def email(self,**extra):
        return n.validate({'kind':'email','account_id':'work','to':['r@example.com'],'subject':'S',
                           'body':'Hi Anna,\n\nBook [a slot](https://example.com/book).\n\nCheers,\nAlex',**extra},self.config,['work'])
    def parts(self,action,signature):
        import email,email.policy
        message=email.message_from_bytes(n.mime_bytes(action,{'rfc_message_id':'<id@x>'},signature),policy=email.policy.default)
        return {p.get_content_type():p.get_content().replace('\r\n','\n') for p in message.walk() if p.get_content_maintype()=='text'}
    def test_blank_line_before_name_and_signature_on_by_default(self):
        action=self.email()
        self.assertTrue(action['body'].endswith('Cheers,\n\nAlex'));self.assertTrue(action['signature'])
        self.assertTrue(n.email_preview(action).endswith('Alex\n\n+ your signature'))
        self.assertEqual(self.email(body='Cheers,\n\nAlex')['body'],'Cheers,\n\nAlex')
        self.assertEqual(self.email(body='Thanks, see you.\nAnyway this is long')['body'],'Thanks, see you.\nAnyway this is long')
        with self.assertRaises(ValueError):self.email(signature='yes')
    def test_html_links_behind_text_then_blank_line_and_real_signature(self):
        parts=self.parts(self.email(),'<div dir="ltr">Alex Owner<div><a href="https://example.com">example.com</a></div></div>')
        html,text=parts['text/html'],parts['text/plain']
        self.assertIn('<a href="https://example.com/book">a slot</a>',html);self.assertNotIn('](',html)
        self.assertIn('Alex<br>\n<br>\n<div class="gmail_signature"><div dir="ltr">Alex Owner',html)
        self.assertIn('a slot (https://example.com/book)',text);self.assertIn('Alex\n\nAlex Owner\nexample.com',text)
        self.assertNotIn('<b>',text)
    def test_no_signature_when_turned_off_or_on_old_approved_drafts(self):
        action=self.email(signature=False)
        self.assertNotIn('gmail_signature',self.parts(action,'<b>sig</b>')['text/html'])
        old={k:v for k,v in self.email().items() if k!='signature'}
        self.assertNotIn('sig',self.parts(old,'<b>sig</b>')['text/plain'])
    def test_preflight_loads_signature_read_only_and_blocks_on_failure(self):
        calls=[]
        def read(config,account,api,version,method,params,scope):
            calls.append((method,scope))
            if method.endswith('getProfile'):return {'emailAddress':'owner@example.com'}
            return {'signature':'<b>Sig</b>'}
        auth=json.dumps({'accounts':[{'email':'owner@example.com','client':'existing','scopes':['https://www.googleapis.com/auth/gmail.send']}]})
        transport=n.EmailTransport({**self.config,'gog':'gog'},lambda *_:'secret',read)
        with patch.object(n.subprocess,'run',return_value=subprocess.CompletedProcess([],0,auth,'')):transport.preflight(self.email())
        self.assertEqual(calls[-1],('gmail.users.settings.sendAs.get','https://www.googleapis.com/auth/gmail.readonly'))
        self.assertEqual(transport.signatures,{'owner@example.com':'<b>Sig</b>'})
        def broken(*args):
            if args[4].endswith('getProfile'):return {'emailAddress':'owner@example.com'}
            raise RuntimeError('down')
        transport=n.EmailTransport({**self.config,'gog':'gog'},lambda *_:'secret',broken)
        with patch.object(n.subprocess,'run',return_value=subprocess.CompletedProcess([],0,auth,'')):
            with self.assertRaisesRegex(ValueError,'Nothing was sent'):transport.preflight(self.email())

if __name__=='__main__':unittest.main()


class CardDraft(unittest.TestCase):
    """Telegram shows the draft on the card, so Send there sends exactly what the owner read."""
    setUp=ux.UX.setUp
    row=ux.UX.row
    decide=ux.UX.decide
    app=NextActions.app
    draft=NextActions.draft
    ready=NextActions.ready
    CONFIG={'dashboard_host':'machine.example','dashboard_path':'/','telegram_owner':'owner'}

    def send(self):
        with patch.object(a,'telegram_request',side_effect=lambda config,args:args):
            return a.telegram(self.CONFIG,self.row(),'send')

    def buttons(self,payload):
        return [(b['label'],b['action'].get('value') or b['action'].get('url')) for block in payload['presentation']['blocks'] for b in block['buttons']]

    def test_card_shows_the_draft_and_send_confirms_that_revision(self):
        revision=self.ready(cc=['cc@example.com'],body='Hi Ada,\n\nThanks! See you on the 3rd.')['revision']
        payload=self.send()
        for text in ('To: recipient@example\\.com','Cc: cc@example\\.com','Subject: Verified subject','Thanks\\! See you on the 3rd\\.'):
            self.assertIn(text,payload['message'])
        self.assertEqual(self.buttons(payload)[:2],[('Send',f'assistant:{self.sid}:confirm_{revision}'),
                         ('Edit',f'https://machine.example/#send={self.sid}&revision={revision}&edit=1')])
        self.assertEqual(self.row()['card_send'],revision)  # the plugin's redraws offer the same Send
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0],0)

    def test_a_trimmed_draft_opens_the_review_first(self):
        revision=self.ready(body='Long line. '*600)['revision']
        payload=self.send()
        self.assertIn('Full draft in the dashboard\\.',payload['message']);self.assertLess(len(payload['message']),4096)
        self.assertEqual(self.buttons(payload)[0],('Send',f'assistant:{self.sid}:send_{revision}'))
        self.assertNotIn('card_send',self.row())

    def test_multibyte_and_markdown_heavy_drafts_do_not_overflow_the_card(self):
        for text in ('😀' * 2300, '*_' * 1150):
            revision = self.ready(body=text)['revision']
            payload = self.send()
            self.assertLess(len(payload['message'].encode('utf-16-le')) // 2, 4096)
            self.assertEqual(self.buttons(payload)[0], ('Send', f'assistant:{self.sid}:send_{revision}'))
            self.assertNotIn('card_send', self.row())

    def test_long_feedback_shares_the_draft_budget(self):
        self.ready(body='*_' * 1150)
        row = {**self.row(), 'requested_feedback': '*_' * 250}
        with patch.object(a, 'telegram_request', side_effect=lambda config, args: args):
            payload = a.telegram(self.CONFIG, row, 'send')
        self.assertLess(len(payload['message'].encode('utf-16-le')) // 2, 4096)

    def test_a_recipient_still_to_choose_keeps_the_review(self):
        revision=self.ready()['revision'];row=self.row()
        row['next_actions']=[{**row['next_actions'][0],'to':[],'to_options':['a@example.com','b@example.com']}]  # set by the service
        with patch.object(a,'telegram_request',side_effect=lambda config,args:args):
            payload=a.telegram(self.CONFIG,row,'send')
        self.assertNotIn('**Draft**',payload['message'])
        self.assertEqual(self.buttons(payload)[0],('Send',f'assistant:{self.sid}:send_{revision}'))

    def test_an_older_card_send_after_the_draft_changed_sends_nothing(self):
        old=self.ready()['revision'];self.ready(body='A newer draft.')
        with self.assertRaises(ValueError):
            self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':old,'approval_id':'telegram:1'},owner_dashboard=True)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0],0)

    def test_sent_card_says_to_whom_and_when(self):
        self.ready();action=self.row()['next_action']
        self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':action['revision'],'approval_id':'telegram:1'},owner_dashboard=True)
        self.app().action_step(NextActions.transport(self,send=lambda *_:{'message_id':'sent'}))
        self.assertRegex(a.card(self.row()),r'✓ Email sent to recipient@example\.com at \d\d:\d\d\.')
        self.assertNotIn('**Draft**',self.send()['message'])
