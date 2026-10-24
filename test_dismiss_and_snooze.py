import datetime as dt
import json
import unittest
import threading
from unittest.mock import patch
import test_ux as ux
a=ux.a
BXL=a.ZoneInfo('Europe/Amsterdam')
def at(y,m,d,h,mi=0):return dt.datetime(y,m,d,h,mi,tzinfo=BXL).astimezone(dt.timezone.utc).isoformat()

class WorkSnooze(unittest.TestCase):
    def test_work_skips_the_weekend_personal_does_not(self):
        with patch.object(a,'now',return_value=at(2026,10,9,15)):  # Friday
            self.assertEqual(a.snooze_time('tomorrow',work=True),at(2026,10,12,9))
            self.assertEqual(a.snooze_time('tomorrow'),at(2026,10,10,9))
            self.assertEqual(a.snooze_time('2h',work=True),at(2026,10,9,17))
            self.assertEqual(a.snooze_choices(True),[['2h','2 hours'],['tomorrow','Monday · 9:00'],['1w','1 week · 9:00']])
            self.assertEqual(a.snooze_choices(False)[1],['tomorrow','Tomorrow · 9:00'])
        with patch.object(a,'now',return_value=at(2026,10,10,15)):  # Saturday: a week lands on Saturday
            self.assertEqual(a.snooze_choices(True)[-1],['1w','Mon, Oct 19 · 9:00'])

class WorkSnoozeDecisions(unittest.TestCase):
    setUp=ux.UX.setUp
    def test_status_labels_and_decide_use_the_source_boundary(self):
        app=object.__new__(a.App);app.store=self.store
        app.config={'accounts':[{'id':'work','email':'owner@example.com','boundary':'work:Acme'}],'telegram_owner':'1000001'}
        with patch.object(a,'now',return_value=at(2026,10,9,15)):
            self.assertEqual(app.action('status',{'full':True})['suggestions'][0]['snooze_choices'][1][1],'Monday · 9:00')
            app.action('decide',{'suggestion_id':self.sid,'choice':'snooze','snooze_option':'tomorrow','decision_id':'telegram:cb','feedback':''})
        self.assertEqual(self.store.snapshot([])['suggestions'][0]['snoozed_until'],at(2026,10,12,9))

class DismissReasons(unittest.TestCase):
    setUp=ux.UX.setUp
    row=ux.UX.row
    def app(self):
        app=object.__new__(a.App);app.store=self.store
        app.config={'telegram_owner':'1000001','accounts':[],'prompt_receipts':str(self.root/'r'),'openclaw_state':str(self.root/'n.sqlite')}
        return app
    def test_typed_reason_dismisses_with_that_reason(self):
        app=self.app()
        app.action('request_feedback',{'suggestion_id':self.sid,'request_id':'cb1','card_message_id':'40','managed_prompt':True,'kind':'dismiss'})
        sent=[]
        self.assertEqual(app.feedback_prompt_step(sender=lambda row:(sent.append(row),{'messageId':'41'})[1]),'sent')
        self.assertEqual(sent[0]['kind'],'dismiss')
        self.assertTrue(a.prompt_text(sent[0]['title'],kind='dismiss').startswith('Not needed: Prepare a draft'))
        result=self.store.feedback_reply('We already chose another vendor','42',reply_to_id='41')
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.row()['status'],'rejected')
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT choice,feedback,source FROM decisions").fetchone()),('reject','We already chose another vendor','telegram'))
        self.assertIn('We already chose another vendor',self.store.preference_summary())
        # The reason also reaches the next review as a note to learn from, with its decision, until it is kept as a fact.
        notes=self.store.notes_to_learn()
        with self.store.connect() as db:decision=db.execute("SELECT id FROM decisions").fetchone()[0]
        self.assertIn('We already chose another vendor',notes);self.assertIn(decision,notes)
        self.assertIn('Notes the owner wrote when dismissing',a.review_prompt('r',['work'],notes=notes))
        self.assertNotIn('Notes the owner wrote when dismissing',a.review_prompt('r',['work']))
        self.store.remember_fact('The owner already chose another vendor for this.',decision)
        self.assertEqual(self.store.notes_to_learn(),'')
        self.assertTrue(self.store.feedback_reply('again','42',reply_to_id='41')['duplicate'])

    def test_already_handled_reason_is_recorded_as_done_not_as_a_bad_suggestion(self):
        for reason in ('Already handled','I already replied to her','Did it myself','took care of it',
                       'I handled it','I handled it, thanks','I fixed this','I have sorted that','I’ve done it',
                       'Not needed — I already did it','Not needed, already handled',"took care of it, don't worry",'Already handled. Was it ok?',
                       'Not sure why, but I already handled it',"I didn't realise I already handled it",'No longer needed, I already handled it'):
            with self.subTest(reason=reason):
                self.store.decide(self.sid,'reopen','','r-'+reason,'dashboard') if self.row()['status']=='completed' else None
                self.assertEqual(self.store.decide(self.sid,'reject',reason,'d-'+reason,'telegram')['status'],'completed')
        self.store.decide(self.sid,'reopen','','r-last','dashboard')
        self.assertEqual(self.store.decide(self.sid,'reject','Not relevant to me','d-last','telegram')['status'],'rejected')
        self.assertIn('Not needed',[b['label'] for block in a.controls({**self.row(),'status':'pending'})['blocks'] for b in block['buttons']])

    def test_negative_or_future_feedback_stays_rejected(self):
        for reason in ("I haven't handled it",'I have not already handled it',"I didn't already do it",
                       'I never took care of it','I will handle it','I handled it? No.',
                       'Not relevant to me','I handled it badly',"She said I handled it, but I haven't",'Not done yet','Did you handle it?',
                       'I do not think this is already handled',"I don't believe that was already done",'I no longer consider it already handled'):
            with self.subTest(reason=reason):
                # Fresh fixture: genuine owner rejection must not become completion.
                sid=self.store.suggest('negative-'+reason,'Other task','Body',[1])['id']
                self.assertEqual(self.store.decide(sid,'reject',reason,'reject-'+reason,'dashboard')['status'],'rejected')

    def test_handled_reply_on_snoozed_task_completes_once_and_keeps_feedback(self):
        self.store.decide(self.sid,'snooze','','later','dashboard',snooze_option='tomorrow')
        self.store.request_feedback(self.sid,'why',card_message_id='40',managed=True,kind='dismiss')
        self.store.bind_feedback_prompt(self.sid,'why','41')
        result=self.store.feedback_reply('I handled it','42',reply_to_id='41')
        self.assertEqual(result['status'],'completed')
        self.assertTrue(self.store.feedback_reply('I handled it','42',reply_to_id='41')['duplicate'])
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT choice,feedback FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()),('complete','I handled it'))

    def test_cancel_keeps_the_item_and_snoozed_items_can_be_dismissed(self):
        self.store.decide(self.sid,'snooze','','s1','dashboard',snooze_option='tomorrow')
        with self.assertRaises(ValueError):self.store.request_feedback(self.sid,'change',card_message_id='40',managed=True)
        app=self.app()
        app.action('request_feedback',{'suggestion_id':self.sid,'request_id':'cb2','card_message_id':'40','managed_prompt':True,'kind':'dismiss'})
        app.feedback_prompt_step(sender=lambda row:{'messageId':'41'})
        self.assertEqual(self.store.feedback_reply('cancel','42',reply_to_id='41'),{'handled':True,'cancelled':True})
        self.assertEqual(self.row()['status'],'snoozed')

    def test_a_change_prompt_is_not_reused_for_a_dismiss_reason(self):
        self.store.request_feedback(self.sid,'change',card_message_id='40',managed=True)
        self.store.bind_feedback_prompt(self.sid,'change','41')
        self.assertFalse(self.store.request_feedback(self.sid,'why',card_message_id='40',managed=True,kind='dismiss').get('duplicate',False))

    def dismiss_prompt(self):
        self.store.request_feedback(self.sid,'old-dismiss',card_message_id='40',managed=True,kind='dismiss')
        self.store.bind_feedback_prompt(self.sid,'old-dismiss','41')

    def test_dismiss_and_cancel_work_rollback_together(self):
        self.store.decide(self.sid,'accept','','prepare','dashboard')
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running'")
            db.execute("UPDATE deliveries SET status='sent',message_id='40'")
        self.dismiss_prompt()
        original=self.store._decide
        def fail_after_decision(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('Commit failure')
        with patch.object(self.store,'_decide',side_effect=fail_after_decision):
            with self.assertRaises(RuntimeError):self.store.feedback_reply('No longer needed','42',reply_to_id='41')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM suggestions').fetchone()[0],'accepted')
            self.assertIsNotNone(db.execute('SELECT feedback_until FROM suggestions').fetchone()[0])
            self.assertEqual(db.execute('SELECT status FROM preparations').fetchone()[0],'running')
            self.assertEqual(db.execute('SELECT needs_edit FROM deliveries').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM decisions').fetchone()[0],1)
        self.assertEqual(self.store.feedback_reply('No longer needed','42',reply_to_id='41')['status'],'rejected')

    def test_old_dismiss_reply_cannot_clear_newer_prompt_or_completed_task(self):
        self.dismiss_prompt()
        self.store.decide(self.sid,'accept','','new-prepare','dashboard')
        self.store.request_feedback(self.sid,'new-change',card_message_id='40',managed=True)
        self.store.bind_feedback_prompt(self.sid,'new-change','50')
        self.assertFalse(self.store.feedback_reply('Old reason','42',reply_to_id='41')['handled'])
        with self.store.connect() as db:
            row=db.execute('SELECT status,feedback_request_id,feedback_until FROM suggestions').fetchone()
            self.assertEqual(row['status'],'accepted');self.assertEqual(row['feedback_request_id'],'new-change')
            self.assertIsNotNone(row['feedback_until'])
        self.store.decide(self.sid,'complete','','done','dashboard')
        self.assertFalse(self.store.feedback_reply('Old reason','51',reply_to_id='41')['handled'])
        self.assertEqual(self.row()['status'],'completed')

    def test_newer_owner_choice_waits_for_atomic_dismiss_then_wins(self):
        self.dismiss_prompt()
        started,finished=threading.Event(),threading.Event();errors=[];threads=[]
        original=self.store._decide
        def owner_choice():
            try:
                started.set()
                self.store.decide(self.sid,'accept','','newer-owner-choice','dashboard')
                self.store.request_feedback(self.sid,'new-change',card_message_id='40',managed=True)
                self.store.bind_feedback_prompt(self.sid,'new-change','50')
            except Exception as error:errors.append(error)
            finally:finished.set()
        def interleave(db,*args,**kwargs):
            if args[1]=='reject':
                thread=threading.Thread(target=owner_choice);threads.append(thread);thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(.1))  # Owner's second writer must wait for this transaction.
            return original(db,*args,**kwargs)
        with patch.object(self.store,'_decide',side_effect=interleave):
            result=self.store.feedback_reply('Old reason','42',reply_to_id='41')
            for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertTrue(result['handled'])
        with self.store.connect() as db:
            row=db.execute('SELECT status,feedback_request_id,feedback_until FROM suggestions').fetchone()
            self.assertEqual(row['status'],'accepted');self.assertEqual(row['feedback_request_id'],'new-change')
            self.assertIsNotNone(row['feedback_until'])
            self.assertEqual([r[0] for r in db.execute('SELECT choice FROM decisions ORDER BY rowid')],['reject','accept'])
            self.assertEqual(db.execute('SELECT status FROM preparations').fetchone()[0],'queued')

class SilentBatch(unittest.TestCase):
    setUp=ux.UX.setUp
    def test_only_the_first_card_in_a_batch_notifies(self):
        self.store.suggest('two','Second task','Body',[1])
        class Day(dt.datetime):
            @classmethod
            def now(cls,tz=None):return dt.datetime(2026,10,12,9,0,tzinfo=BXL).astimezone(tz or dt.timezone.utc)
        sent=[]
        with patch.object(a.dt,'datetime',Day),patch.object(a,'now',return_value=at(2026,10,12,9)),\
             patch.object(a,'telegram_request',side_effect=lambda c,args:(sent.append(args),{'messageId':str(40+len(sent))})[1]):
            a.deliver(self.store,{'telegram_owner':'1000001'})
        self.assertEqual([args.get('silent') for args in sent],[None,True])

class AnswerQuestion(unittest.TestCase):
    """A result waiting on the owner's answer (needs_you, written as a question) leads with Answer on Telegram too; the
    question keeps its own "?" in the status line."""
    setUp=ux.UX.setUp
    def labels(self,row):return [b['label'] for block in a.controls(row)['blocks'] for b in block['buttons']]
    def test_a_question_leads_with_answer_and_keeps_its_mark(self):
        row={**ux.UX.row(self),'status':'prepared','outcome':json.dumps({'state':'needs_you','needs':'Which date goes in the cash-out field?'})}
        self.assertEqual(self.labels(row)[:2],['Answer','Mark done'])
        self.assertNotIn('Change',self.labels(row))
        answer=next(b for block in a.controls(row)['blocks'] for b in block['buttons'] if b['label']=='Answer')
        self.assertTrue(answer['action']['value'].endswith(':other'))  # the Change path
        self.assertTrue(a.card(row).endswith('Needs you: Which date goes in the cash-out field?'))
        # Not a question, a draft to send, or a sign it's handled: the usual buttons
        plain={**row,'outcome':json.dumps({'state':'needs_you','needs':'Pay in the bank app'})}
        self.assertEqual(self.labels(plain)[0],'Mark done');self.assertTrue(a.card(plain).endswith('Needs you: Pay in the bank app.'))
        self.assertNotIn('Answer',self.labels({**row,'next_actions':[{'kind':'email','revision':'v1','label':'Send','to':['x@example.com'],'subject':'s','body':'b'}]}))
        self.assertNotIn('Answer',self.labels({**row,'handled_hint':json.dumps({'reason':'You replied'})}))
        self.assertNotIn('Answer',self.labels({**row,'preparation_status':'blocked'}))

    def test_a_run_that_stopped_to_ask_leads_with_answer(self):
        ask='I couldn’t find the amount; please share it or point me to the message.'
        row={**ux.UX.row(self),'status':'instructions','preparation_status':'blocked','preparation_reason':ask,'preparation_error':None}
        self.assertEqual(self.labels(row)[:3],['Answer','Try again','Mark done'])
        self.assertTrue(a.card(row).endswith('Needs you: '+ask))
        # The service's own failure, or a sign-in: the usual buttons and line
        failed={**row,'preparation_error':'ai_run_failed','preparation_reason':'I tried twice but couldn’t finish this. Tap Try again, or change the request.'}
        self.assertEqual(self.labels(failed)[0],'Try again');self.assertIn('I’m stuck:',a.card(failed))
        self.assertEqual(self.labels({**row,'preparation_reason':'Stripe needs your code: complete sign-in in the tab, then tap Continue.'})[0],'Continue')


if __name__=='__main__':unittest.main()


class AskConcretely(unittest.TestCase):
    """A run that stops for the owner's input names it as short questions (quick picks for a clear choice); a Try again
    that came back asking again isn't offered once more."""
    setUp=ux.UX.setUp
    def labels(self,row):return [b['label'] for block in a.controls(row)['blocks'] for b in block['buttons']]
    def block(self,questions=None):
        with self.store.connect() as db:db.execute("UPDATE preparations SET status='running'")
        return self.store.preparation_blocked(self.sid,ux.UX.job(self)['job_id'],'Finishing this needs your notes and one choice.','long technical dump',None,questions)
    def test_questions_are_stored_shown_and_cleared(self):
        ux.UX.decide(self,'accept')
        asks=[{'question':'What did you notice when you tried it?'},{'question':'Which score fills the gap?','options':['3.9','4.0']}]
        self.block(asks)
        row=ux.UX.row(self)
        self.assertEqual(row['preparation_questions'],asks);self.assertEqual(row['preparation_cause'],'accept')
        self.assertEqual(self.labels(row)[:3],['Answer','Try again','Mark done'])
        self.assertTrue(a.card(row).endswith('Needs you: What did you notice when you tried it? Which score fills the gap?'))
        # Asked even right after a browser step, because it named its questions
        self.assertEqual(a.ask_question({**row,'preparation_browser':{'since':'x'}},[]),'What did you notice when you tried it? Which score fills the gap?')
        # Try again without anything new, and it stops asking again: no Try again any more
        ux.UX.decide(self,'retry','r1');self.block(asks)
        row=ux.UX.row(self)
        self.assertEqual(row['preparation_cause'],'retry');self.assertNotIn('Try again',self.labels(row));self.assertEqual(self.labels(row)[0],'Answer')
        # An answer starts a new run: the old questions are gone
        ux.UX.decide(self,'other','o1','Notes: it was quick to set up.')
        self.assertEqual(ux.UX.row(self).get('preparation_questions'),[])
    def test_malformed_questions_are_refused(self):
        for bad in ([{'question':''}],[{'question':'x'*201}],[{'question':'Q?','options':['a']*5}],[{'question':'Q?'}]*4,'Q?'):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError,a.blocker_questions,bad,True)
        self.assertEqual(a.blocker_questions('not json'),[])
    def test_the_prompt_asks_for_plain_questions_and_defaults_for_small_calls(self):
        text=open(a.ROOT/'PREPARATION.md').read()
        for words in ('questions=[{question, options}]','make sense on its own','sensible default'):
            self.assertIn(words,text)
        job=a.preparation_prompt({'suggestion_id':self.sid,'job_id':'j'})
        self.assertIn("questions=[{question:<one short plain question>",job);self.assertIn('sensible default you make yourself',job)



class StuckIsNotAQuestion(unittest.TestCase):
    """A run that stops without naming questions asks nothing (2026-10-23: "the card is linked to email and can't send on
    WhatsApp yet" showed as Needs your help with an Answer button). It says it's stuck and offers Try again. A blocker
    from before runs named questions still asks with its reason."""
    setUp=ux.UX.setUp
    labels=AskConcretely.labels;block=AskConcretely.block
    def test_a_blocker_without_questions_is_stuck_not_a_question(self):
        ux.UX.decide(self,'accept');self.block()
        row=ux.UX.row(self)
        self.assertTrue(row['preparation_no_questions']);self.assertEqual(row['preparation_questions'],[])
        self.assertFalse(a.ask_blocked(row));self.assertEqual(a.ask_question(row,[]),'')
        self.assertEqual(self.labels(row)[0],'Try again');self.assertNotIn('Answer',self.labels(row))
        self.assertIn('I’m stuck: Finishing this needs your notes and one choice.',a.card(row))
        self.assertNotIn('Help me answer this',a.card_task(row))
        # The Telegram rows read the stored column itself: the same answer
        with self.store.connect() as db:stored=db.execute('SELECT questions FROM preparations WHERE suggestion_id=?',(self.sid,)).fetchone()[0]
        self.assertEqual(stored,'[]')
        self.assertFalse(a.ask_blocked({**row,'preparation_no_questions':None,'preparation_questions':stored}))
        # One saved before runs named questions (no list at all) keeps asking with its reason
        legacy={**row,'preparation_no_questions':False,'preparation_questions':None}
        self.assertTrue(a.ask_blocked(legacy));self.assertEqual(self.labels(legacy)[0],'Answer')
        self.assertTrue(a.ask_blocked({**row,'preparation_questions':[{'question':'Which date?'}]}))
