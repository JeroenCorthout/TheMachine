import importlib.util
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('assistant_wa',Path(__file__).with_name('assistant.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
account={'id':'personal-whatsapp','identity_sha256':a.hashlib.sha256(b'123@s.whatsapp.net').hexdigest()}
def message():return {'ChatJID':'12345@g.us','MsgID':'FULL-NATIVE-ID','Timestamp':'2026-10-09T12:00:00Z','FromMe':False,'SenderJID':'12345@g.us','Text':'Hello','ChatName':'Family'}
class WhatsAppTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=a.Store(Path(self.tmp.name)/'a.sqlite')
 def tearDown(self):self.tmp.cleanup()
 def test_account_mismatch_and_mutation_blocked(self):
  source=a.WhatsApp({},account)
  with patch.object(source,'read',return_value={'authenticated':True,'linked_jid':'wrong'}),self.assertRaises(a.SourceError):source.profile()
  with self.assertRaises(a.SourceError):source.read(['send','text'])
 def test_group_sender_not_invented_and_native_identity_preserved(self):
  raw=message();raw['ChatJID']='123-456@g.us';event=a.whatsapp_event(account,raw);self.assertEqual(event['metadata']['sender_identity'],'unknown');self.assertEqual(event['metadata']['message_id'],'FULL-NATIVE-ID');self.assertEqual(event['title'],'Family')
 def test_outgoing_sender_is_owner_even_when_provider_labels_recipient(self):
  raw=message();raw.update(ChatJID='234@s.whatsapp.net',ChatName='Xavier',SenderName='Xavier',SenderJID='234@s.whatsapp.net',FromMe=True)
  event=a.whatsapp_event(account,raw)
  self.assertEqual(event['title'],'Xavier');self.assertEqual(event['metadata']['from'],'You')
  self.assertEqual(event['metadata']['sender_display'],'You');self.assertIsNone(event['metadata']['sender_id'])
  self.assertEqual(event['metadata']['sender_identity'],'authenticated_account_owner')
  raw['FromMe']=False;event=a.whatsapp_event(account,raw);self.assertEqual(event['metadata']['from'],'Xavier')

 def test_full_window_replay_edit_and_revocation(self):
  src=a.WhatsApp({},account);raw=message()
  with patch.object(src,'profile',return_value={}),patch.object(src,'read',side_effect=lambda _: {'messages':[raw]}):
   a.sync_whatsapp(self.store,{},account,src);a.sync_whatsapp(self.store,{},account,src)
   raw['Text']='Updated';raw['Edited']=True;a.sync_whatsapp(self.store,{},account,src)
   raw['Revoked']=True;a.sync_whatsapp(self.store,{},account,src)
  with self.store.connect() as db:self.assertEqual(tuple(db.execute('SELECT count(*),version,deleted FROM events').fetchone()),(1,3,1))
 def test_saturated_or_invalid_window_cannot_advance_checkpoint(self):
  src=a.WhatsApp({},account)
  with patch.object(src,'profile',return_value={}),patch.object(src,'read',return_value={'messages':[message()]*1000}),self.assertRaises(a.SourceError):a.sync_whatsapp(self.store,{},account,src)
  self.assertIsNone(self.store.checkpoint(account['id']))
  raw=message();raw['Timestamp']='2026-10-09T12:00:00'
  with self.assertRaises(a.SourceError):a.whatsapp_event(account,raw)
  raw=message();raw['MsgID']='imported_synthetic'
  with self.assertRaises(a.SourceError):a.whatsapp_event(account,raw)


# Sending: only an owner-approved card sends, once, through the one writable wacli call. No live sends: wacli is always faked.
import json,subprocess
import next_actions as n
import test_ux as ux
WA={**account,'service':'whatsapp','transport':'wacli-local','store':'/tmp/wa-store','boundary':'personal:whatsapp'}
CHAT='32470000000@s.whatsapp.net'
def ok(data):return subprocess.CompletedProcess([],0,json.dumps({'success':True,'data':data,'error':None}),'')
class WhatsAppSend(unittest.TestCase):
 row=ux.UX.row;decide=ux.UX.decide
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=a.Store(Path(self.tmp.name)/'events.sqlite')
  raw=message();raw.update(ChatJID=CHAT,ChatName='Sam',SenderJID=CHAT,Text='Are we still on for Friday?')
  self.store.commit_page(WA['id'],{},[a.whatsapp_event(WA,raw)])
  with self.store.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM events')]
  self.sid=self.store.suggest('wa-task','Answer Sam','Sam asks about Friday.',ids)['id']
  self.accounts=[WA,{**WA,'id':'other-whatsapp'},{'id':'work','email':'owner@example.com','service':'gmail','transport':'gog'}]
 def app(self):
  app=object.__new__(a.App);app.store=self.store;app.config={'accounts':self.accounts};return app
 def save(self,**draft):
  action={k:v for k,v in {'kind':'whatsapp','label':'Send reply','account_id':WA['id'],'chat_id':CHAT,'body':'Yes, Friday works.',**draft}.items() if v is not None}
  return self.app().action('prepare',{'suggestion_id':self.sid,'body':'Reply ready.','next_action':action})
 def ready(self,**draft):
  self.decide('accept');self.save(**draft);return self.row()['next_action']
 def confirm(self,key='owner-approval'):
  return self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':self.row()['next_action']['revision'],'approval_id':key},owner_dashboard=True)
 def job(self):
  with self.store.connect() as db:return dict(db.execute('SELECT * FROM action_jobs ORDER BY created_at DESC LIMIT 1').fetchone())
 def transport(self,send=None,preflight=None,reconcile=None):
  class T:
   def preflight(s,action):return preflight(action) if preflight else None
   def send(s,action,job):return send(action,job) if send else {'message_id':'whatsapp:3EB0AA'}
   def reconcile(s,action,job):return reconcile(action,job) if reconcile else {}
  return T()
 def test_reply_only_in_a_task_chat_plain_text(self):
  action=self.ready()
  self.assertEqual((action['chat_id'],action['to_name'],action['max_length']),(CHAT,'Sam',4000))
  self.assertEqual(n.preview(action),'WhatsApp message to Sam\n\nYes, Friday works.')
  self.assertEqual(a.controls(self.row())['blocks'][0]['buttons'][0]['label'],'Send reply')
  for bad in ({'chat_id':'999@s.whatsapp.net'},{'account_id':'other-whatsapp'},{'account_id':'work'},{'label':'Post it'},
              {'body':'a'*4001},{'body':' '},{'media':['a.png']},{'to':'+3247000'}):
   with self.assertRaises(ValueError,msg=bad):self.save(**bad)
  with self.assertRaises(ValueError):n.revise(action,body='a'*4001)
  self.assertEqual(n.revise(action,body='Friday!')['body'],'Friday!')
 def test_send_now_never_sends_whatsapp(self):
  self.decide('accept')
  with self.assertRaises(ValueError):
   self.app().action('prepare',{'suggestion_id':self.sid,'body':'Reply ready.','send_now':True,
                                'next_action':{'kind':'whatsapp','account_id':WA['id'],'chat_id':CHAT,'body':'Yes'}})
  with self.store.connect() as db:self.assertIsNone(db.execute('SELECT 1 FROM action_jobs').fetchone())
 def test_approved_reply_sends_once_and_the_answer_returns_to_the_card(self):
  self.ready();self.confirm();self.confirm('second-click');calls=[]
  self.app().action_step(self.transport(send=lambda action,job:(calls.append(action),{'message_id':'whatsapp:3EB0AA'})[1]))
  self.app().action_step(self.transport(send=lambda *_:self.fail('Duplicate send')))
  self.assertEqual([c['body'] for c in calls],['Yes, Friday works.'])
  self.assertEqual(self.job()['status'],'sent');self.assertIn('✓ WhatsApp message sent to Sam at ',str(a.card(self.row())))
  raw=message();raw.update(ChatJID=CHAT,ChatName='Sam',SenderJID=CHAT,MsgID='ANSWER',Text='Great!',Timestamp='2099-01-01T00:00:00Z')
  self.store.commit_page(WA['id'],{},[a.whatsapp_event(WA,raw)])
  with self.store.connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM events WHERE kind='whatsapp.message' ORDER BY occurred_at DESC LIMIT 1")]
  self.assertEqual(list(self.store.card_replies(rows).values())[0]['suggestion_id'],self.sid)
 def test_unknown_outcome_never_resends_and_signed_out_blocks(self):
  self.ready();self.confirm()
  self.app().action_step(self.transport(send=lambda *_:{}))
  self.assertEqual(self.job()['status'],'uncertain')
  with self.store.connect() as db:db.execute('UPDATE action_jobs SET retry_at=NULL')
  self.app().action_step(self.transport(send=lambda *_:self.fail('resent')))
  self.assertEqual(self.job()['error'],'I couldn’t confirm whether this WhatsApp message was sent. I’m checking the chat; I won’t send it again.')
  with self.store.connect() as db:db.execute('UPDATE action_jobs SET retry_at=NULL')
  self.app().action_step(self.transport(reconcile=lambda *_:{'message_id':'whatsapp:3EB0AA'}))
  self.assertEqual(self.job()['status'],'sent')
 def test_signed_out_blocks_cleanly(self):
  self.ready();self.confirm()
  def out(_):raise ValueError('WhatsApp is signed out, or linked to another number. Nothing was sent.')
  self.assertEqual(self.app().action_step(self.transport(preflight=out,send=lambda *_:self.fail('sent'))),'blocked')

 # A card that reopens with a new draft after an earlier reply went out (2026-10-22): the old send
 # never locks the new draft, the edit is what goes out, and every lock says what is going on.
 def revise(self,body):
  return self.app().action('revise_draft',{'suggestion_id':self.sid,'revision':self.row()['next_action']['revision'],'body':body},owner_dashboard=True)
 def sent_then_reopened(self):
  self.ready();self.confirm();self.app().action_step(self.transport())
  self.assertEqual(self.job()['status'],'sent')
  with self.store.connect() as db:db.execute("UPDATE suggestions SET status='accepted' WHERE id=?",(self.sid,))
  self.save(body='Second draft.')
 def test_new_draft_after_an_earlier_reply_sends_the_edited_text(self):
  self.sent_then_reopened()
  self.assertEqual(self.revise('My own words')['status'],'revised')
  self.confirm('second-send');calls=[]
  self.app().action_step(self.transport(send=lambda action,job:(calls.append(action['body']),{'message_id':'whatsapp:3EB0BB'})[1]))
  self.assertEqual(calls,['My own words']);self.assertEqual(self.job()['status'],'sent')
 def test_edit_waits_while_another_draft_of_the_card_is_sending(self):
  self.sent_then_reopened()
  with self.store.connect() as db:db.execute("UPDATE action_jobs SET status='sending',retry_at=?",('2999-01-01T00:00:00+00:00',))
  with self.assertRaisesRegex(ValueError,'Another draft on this card is sending right now'):self.revise('Edit')
  with self.store.connect() as db:db.execute("UPDATE action_jobs SET retry_at=?",('2000-01-01T00:00:00+00:00',))
  self.assertEqual(self.revise('Edit')['status'],'revised')  # a stale lock expires
 def test_this_drafts_own_attempt_says_what_happened(self):
  self.ready();self.confirm()
  with self.store.connect() as db:db.execute("UPDATE action_jobs SET status='sending'")
  with self.assertRaisesRegex(ValueError,'^Sending… '):self.revise('Edit')
  with self.store.connect() as db:db.execute("UPDATE action_jobs SET status='sent'")
  with self.assertRaisesRegex(ValueError,r'^Sent at \d\d:\d\d\. '):self.revise('Edit')
 def test_failed_send_says_so_and_can_be_retried(self):
  self.ready();self.confirm()
  self.assertEqual(self.app().action_step(self.transport(send=lambda *_:{'rejected':'refused'})),'failed')
  self.assertIn('Last attempt failed: WhatsApp refused this WhatsApp message',a.card(self.row()))
  self.confirm('retry');self.assertEqual(self.job()['status'],'queued')
  self.assertEqual(self.app().action_step(self.transport()),'sent')
 def test_unconfirmed_send_is_retried_only_by_the_owner_after_checking(self):
  self.ready();self.confirm()
  self.app().action_step(self.transport(send=lambda *_:{}))
  self.assertEqual(self.job()['status'],'uncertain')
  self.confirm('impatient');self.assertEqual(self.job()['status'],'uncertain')  # no second send yet
  with self.assertRaisesRegex(ValueError,'still checking whether the last attempt went out'):self.revise('Edit')
  self.assertFalse(any(b['label']=='Send reply' for block in a.controls(self.row())['blocks'] for b in block['buttons']))
  with self.store.connect() as db:db.execute("UPDATE action_jobs SET updated_at=?,retry_at=?",('2000-01-01T00:00:00+00:00','2999-01-01T00:00:00+00:00'))
  self.assertIn('Last attempt unconfirmed: I found no sign of it going out',a.card(self.row()))
  self.assertTrue(any(b['label']=='Send reply' for block in a.controls(self.row())['blocks'] for b in block['buttons']))
  self.assertEqual(self.revise('Edited after checking')['status'],'revised')
  self.confirm('after-checking');calls=[]
  self.app().action_step(self.transport(send=lambda action,job:(calls.append(action['body']),{'message_id':'whatsapp:3EB0CC'})[1]))
  self.assertEqual(calls,['Edited after checking'])

class WhatsAppTransportTest(unittest.TestCase):
 config={'wacli':'/opt/wacli','accounts':[WA]}
 action={'kind':'whatsapp','account_id':WA['id'],'chat_id':CHAT,'to_name':'Sam','body':'Yes, Friday works.'}
 def test_checks_read_only_then_one_writable_send(self):
  seen=[]
  def run(args,**kw):
   seen.append((args,kw['env'].get('WACLI_READONLY')))
   if 'auth' in args:return ok({'authenticated':True,'linked_jid':'123@s.whatsapp.net'})
   if 'messages' in args:return ok({'messages':[{'MsgID':'1'}]})
   return ok({'sent':True,'to':CHAT,'id':'3EB0AA'})
  t=n.WhatsAppTransport(self.config)
  with patch.dict(a.os.environ,{'WACLI_READONLY':'1'}),patch.object(n.subprocess,'run',side_effect=run):
   t.preflight(self.action);self.assertEqual(t.send(self.action,{'id':'J1'}),{'message_id':'whatsapp:3EB0AA'})
  reads,(send,send_env)=seen[:-1],seen[-1]
  self.assertTrue(all('--read-only' in args and env=='1' for args,env in reads))
  self.assertEqual(send[:3],['/opt/wacli','--store','/tmp/wa-store']);self.assertNotIn('--read-only',send);self.assertIsNone(send_env)
  self.assertEqual(send[send.index('send'):],['send','text','--to',CHAT,'--message','Yes, Friday works.'])
 def test_preflight_refuses_another_number_or_unknown_chat(self):
  t=n.WhatsAppTransport(self.config)
  for status,rows in (({'authenticated':True,'linked_jid':'999@s.whatsapp.net'},[{}]),({'authenticated':False},[{}]),({'authenticated':True,'linked_jid':'123@s.whatsapp.net'},[])):
   with patch.object(n.subprocess,'run',side_effect=lambda args,**kw:ok(status) if 'auth' in args else ok({'messages':rows})),self.assertRaisesRegex(ValueError,'Nothing was sent'):
    t.preflight(self.action)
 def test_send_outcomes_never_resend_on_doubt(self):
  t=n.WhatsAppTransport(self.config)
  for result,expected in ((subprocess.CompletedProcess([],1,'','no reply from the running sync process before the timeout; the send may still have gone through'),{}),
                          (subprocess.CompletedProcess([],1,'','store is locked by another wacli process'),{'not_sent':True}),
                          (subprocess.CompletedProcess([],1,'','not authenticated; run wacli auth'),{'rejected':'refused'}),
                          (subprocess.CompletedProcess([],1,'','connection reset'),{}),
                          (subprocess.CompletedProcess([],0,'garbage',''),{})):
   with patch.object(n.subprocess,'run',return_value=result):self.assertEqual(t.send(self.action,{'id':'J'}),expected,result.stderr)
  with patch.object(n.subprocess,'run',side_effect=subprocess.TimeoutExpired('wacli',150)):self.assertEqual(t.send(self.action,{'id':'J'}),{})
 def test_reconcile_finds_exactly_one_matching_own_message(self):
  t=n.WhatsAppTransport(self.config);job={'id':'J','created_at':'2026-10-19T10:00:00+00:00'}
  self.action['proof']={'whatsapp_dispatched_at':'2026-10-19T10:00:00+00:00','whatsapp_before_ids':['OLD']}
  mine={'MsgID':'3EB0AA','FromMe':True,'Text':'Yes, Friday works.','Timestamp':'2026-10-19T10:00:02Z'}
  for rows,expected in (([mine],{'message_id':'whatsapp:3EB0AA'}),([mine,{**mine,'MsgID':'B'}],{}),([{**mine,'Text':'Other'}],{}),([{**mine,'FromMe':False}],{})):
   with patch.object(n.subprocess,'run',return_value=ok({'messages':rows})) as run:self.assertEqual(t.reconcile(self.action,job),expected)
   self.assertIn('--read-only',run.call_args[0][0]);self.assertIn('--from-me',run.call_args[0][0])
if __name__=='__main__':unittest.main()


# A card from email whose owner says they talk to the person on WhatsApp (2026-10-23): the card switches to their existing
# chat in the owner's WhatsApp account, with the normal review-and-send. The chat is looked up read-only; never a new
# number, never an excluded chat, never sent without the owner's Send. wacli is always faked.
from test_assistant import event
class WhatsAppSwitch(unittest.TestCase):
 row=ux.UX.row;decide=ux.UX.decide
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=a.Store(Path(self.tmp.name)/'events.sqlite')
  self.store.commit_page('work',{},[event()])
  self.sid=self.store.suggest('task','Arrange the installer','Ask them about the speakers.',[1])['id']
  self.accounts=[{**WA,'exclude':[{'chats':['32471111111@s.whatsapp.net']}]},{'id':'work','email':'owner@example.com','service':'gmail','transport':'gog','boundary':'work:Example'}]
  self.reads=[]
 def app(self):
  app=object.__new__(a.App);app.store=self.store;app.config={'accounts':self.accounts};return app
 def wacli(self,chats):
  def read(source,command):
   self.reads.append(command)
   if command[:2]==['chats','show']:
    found=[c for c in chats if c['jid']==command[3]]
    if not found:raise a.SourceError('whatsapp_source_unavailable')
    return found[0]
   if command[:2]==['chats','list']:return [c for c in chats if command[3].lower() in c['name'].lower()]
   self.fail('only read-only chat lookups: '+str(command))
  return patch.object(a.WhatsApp,'read',read)
 def save(self,**draft):
  action={k:v for k,v in {'kind':'whatsapp','label':'Send','account_id':WA['id'],'body':'Hi Sam! We’d like ceiling speakers put in. Could you help?',**draft}.items() if v is not None}
  return self.app().action('prepare',{'suggestion_id':self.sid,'body':'Switched to WhatsApp.','next_action':action})
 def test_switches_to_the_persons_existing_chat_by_name(self):
  self.decide('accept')
  with self.wacli([{'jid':CHAT,'kind':'dm','name':'Sam Lee'},{'jid':'120363000000000000@g.us','kind':'group','name':'Sam’s party'}]):self.save(to_name='Sam')
  action=self.row()['next_action']
  self.assertEqual((action['kind'],action['chat_id'],action['to_name'],action['account_id']),('whatsapp',CHAT,'Sam Lee',WA['id']))
  self.assertEqual(n.preview(action),'WhatsApp message to Sam Lee\n\nHi Sam! We’d like ceiling speakers put in. Could you help?')
  self.assertEqual(self.row()['status'],'prepared')
  self.assertTrue(all(c[:2] in (['chats','show'],['chats','list']) for c in self.reads))
  with self.store.connect() as db:self.assertIsNone(db.execute('SELECT 1 FROM action_jobs').fetchone())  # nothing sent: it waits for Send
  # The owner's Send sends it once, into that chat (a fake transport)
  self.app().action('confirm_action',{'suggestion_id':self.sid,'revision':action['revision'],'approval_id':'owner-approval'},owner_dashboard=True)
  sent=[];T=type('T',(),{'preflight':lambda s,x:None,'send':lambda s,x,j:(sent.append(x),{'message_id':'whatsapp:3EB0AA'})[1],'reconcile':lambda s,x,j:{}})
  self.app().action_step(T());self.app().action_step(T())
  self.assertEqual([(x['chat_id'],x['body']) for x in sent],[(CHAT,'Hi Sam! We’d like ceiling speakers put in. Could you help?')])
 def test_switches_by_chat_id_from_the_account_or_its_messages(self):
  self.decide('accept')
  with self.wacli([{'jid':CHAT,'kind':'dm','name':'Sam Lee'}]):self.save(chat_id=CHAT)
  self.assertEqual(self.row()['next_action']['to_name'],'Sam Lee')
  # A chat the account already synced needs no lookup
  raw=message();raw.update(ChatJID='32472222222@s.whatsapp.net',ChatName='Robin',SenderJID='32472222222@s.whatsapp.net')
  self.store.commit_page(WA['id'],{},[a.whatsapp_event(WA,raw)]);self.reads.clear()
  with self.wacli([]):self.save(chat_id='32472222222@s.whatsapp.net')
  self.assertEqual((self.row()['next_action']['to_name'],self.reads),('Robin',[]))
 def test_never_a_new_number_an_excluded_chat_or_a_guess(self):
  self.decide('accept')
  chats=[{'jid':CHAT,'kind':'dm','name':'Sam Lee'},{'jid':'32473333333@s.whatsapp.net','kind':'dm','name':'Sam Peters'},
         {'jid':'32471111111@s.whatsapp.net','kind':'dm','name':'Quinn'}]
  with self.wacli(chats):
   for bad,why in (({'chat_id':'32479999999@s.whatsapp.net'},'existing WhatsApp chat'),({'chat_id':'32471111111@s.whatsapp.net'},'existing WhatsApp chat'),
                   ({'to_name':'Quinn'},'No WhatsApp chat with Quinn'),({'to_name':'Alex'},'No WhatsApp chat with Alex'),
                   ({'to_name':'Sam'},'More than one WhatsApp chat matches'),({'chat_id':'+32470000000'},'existing WhatsApp chat'),
                   ({'account_id':'work','chat_id':CHAT},'WhatsApp accounts'),({},'existing WhatsApp chat')):
    with self.assertRaisesRegex(ValueError,why,msg=bad):self.save(**bad)
  self.assertNotEqual(self.row()['status'],'prepared')
 def test_send_now_still_never_sends_a_switched_chat(self):
  self.decide('accept')
  with self.wacli([{'jid':CHAT,'kind':'dm','name':'Sam Lee'}]),self.assertRaises(ValueError):
   self.app().action('prepare',{'suggestion_id':self.sid,'body':'Sent.','send_now':True,
                                'next_action':{'kind':'whatsapp','account_id':WA['id'],'to_name':'Sam','body':'Hi'}})
  with self.store.connect() as db:self.assertIsNone(db.execute('SELECT 1 FROM action_jobs').fetchone())
 def test_without_a_readable_whatsapp_it_says_what_to_do(self):
  self.decide('accept')
  def down(source,command):raise a.SourceError('whatsapp_source_unavailable')
  with patch.object(a.WhatsApp,'read',down),self.assertRaisesRegex(ValueError,'send it on WhatsApp themselves'):self.save(to_name='Sam')
