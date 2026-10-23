import tempfile, unittest
from pathlib import Path
import assistant as a
from unittest.mock import patch

SUB = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': 'B' + 'x' * 86, 'auth': 'y' * 22}}


class Daytime(a.dt.datetime):
    @classmethod
    def now(cls, tz=None): return cls(2026, 10, 14, 10, 0, tzinfo=tz or a.dt.timezone.utc)


class SimulatedTermination(BaseException):
    pass


class PhoneNotifications(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.config = self.app.config = {'accounts': [], 'dashboard_host': 'the-machine.example', 'dashboard_path': '/assistant/'}

    def subscriptions(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM push_subscriptions')]

    def test_only_the_dashboard_subscribes_and_unsubscribes(self):
        with self.assertRaises(ValueError):
            self.app.action('push_subscribe', SUB)
        self.assertEqual(self.app.action('push_subscribe', SUB, owner_dashboard=True), {'subscribed': True})
        self.app.action('push_subscribe', SUB, owner_dashboard=True)  # re-sync on every app start is harmless
        self.assertEqual(len(self.subscriptions()), 1)
        for bad in ({'endpoint': 'http://insecure/x', 'keys': SUB['keys']}, {'endpoint': SUB['endpoint'], 'keys': {'p256dh': 'x;', 'auth': 'y'}}):
            with self.assertRaises(ValueError):
                self.app.action('push_subscribe', bad, owner_dashboard=True)
        self.app.action('push_unsubscribe', {'endpoint': SUB['endpoint']}, owner_dashboard=True)
        self.assertEqual(self.subscriptions(), [])

    def test_push_mirrors_the_card_and_result_notice_with_links(self):
        self.app.action('push_subscribe', SUB, owner_dashboard=True)
        sent = []
        sender = lambda request: sent.append(request) or {'results': [{'endpoint': SUB['endpoint'], 'status': 201}]}
        row = {'id': '0123456789abcdef', 'title': 'Top up Wise', 'body': 'Stripe debits **€420** on [Tuesday](https://x).', 'status': 'pending'}
        a.push_notify(self.store, self.config, row, sender)
        self.assertEqual(sent[-1]['payload'], {'title': 'Top up Wise', 'body': 'Stripe debits €420 on Tuesday.',
                                               'url': 'https://the-machine.example/assistant/#card=0123456789abcdef', 'tag': '0123456789abcdef'})
        self.assertEqual(sent[-1]['subscriptions'][0]['endpoint'], SUB['endpoint'])
        a.push_notify(self.store, self.config, dict(row, status='prepared', _result_notice=True, body='Draft ready.'), sender)
        self.assertEqual(sent[-1]['payload']['title'], 'Ready: Top up Wise')
        self.assertTrue(sent[-1]['payload']['url'].endswith('#result=0123456789abcdef'))
        a.push_notify(self.store, self.config, dict(row, status='accepted', _result_notice=True, preparation_status='blocked', preparation_reason='Exact needs a login.'), sender)
        self.assertEqual((sent[-1]['payload']['title'], sent[-1]['payload']['body']), ('Needs your help: Top up Wise', 'Exact needs a login.'))
        # A run the service stopped (it timed out) asks nothing: Stuck, also after browser steps
        a.push_notify(self.store, self.config, dict(row, status='accepted', _result_notice=True, preparation_status='blocked', preparation_error='completion_unconfirmed',
                                                    preparation_browser={'since': 'x'}, preparation_reason='This didn’t finish.'), sender)
        self.assertEqual(sent[-1]['payload']['title'], 'Stuck: Top up Wise')
        self.assertEqual(self.subscriptions()[0]['last_status'], 201)

    def test_subscription_refresh_keeps_success_and_failure_history_and_updates_keys(self):
        self.app.action('push_subscribe', SUB, owner_dashboard=True)
        for status in (201, 503):
            a.push_notify(self.store, self.config, {'id': 'x', 'title': 'T', 'body': 'B', 'status': 'pending'},
                          lambda r: {'results': [{'endpoint': SUB['endpoint'], 'status': status}]})
            before = self.subscriptions()[0]
            updated = {**SUB, 'keys': {'p256dh': 'C' + 'z' * 86, 'auth': 'q' * 22}}
            with patch.object(a, 'now', return_value='2026-10-15T09:00:00+00:00'):
                self.app.action('push_subscribe', updated, owner_dashboard=True)
            after = self.subscriptions()[0]
            self.assertEqual(after['p256dh'], updated['keys']['p256dh'])
            self.assertEqual(after['auth'], updated['keys']['auth'])
            for key in ('created_at', 'last_status', 'last_sent_at'):
                self.assertEqual(after[key], before[key], key)
        reopened = a.Store(self.store.path)
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT last_status FROM push_subscriptions').fetchone()[0], 503)
        self.app.action('push_subscribe', {**SUB, 'endpoint': SUB['endpoint'] + '-new'}, owner_dashboard=True)
        self.assertEqual(len(self.subscriptions()), 2)
        self.assertIsNone(self.subscriptions()[1]['last_status'])
        self.assertIsNone(self.subscriptions()[1]['last_sent_at'])

    def delivery_fixture(self):
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message',
            'occurred_at': a.now(), 'title': 'Evidence', 'body': 'Evidence', 'metadata': {}, 'deleted': False}])
        sid = self.store.suggest('one', 'First', 'First body', [1])['id']
        with self.store.connect() as db:
            db.execute('UPDATE suggestions SET deliver_after=NULL WHERE id=?', (sid,))
        return sid

    def test_card_receipt_and_newer_owner_state_commit_before_push_termination(self):
        sid = self.delivery_fixture()
        def telegram(*args):
            # The owner closes the task while Telegram's send is in flight.
            self.store.decide(sid, 'complete', '', 'disposable-owner', 'telegram')
            return {'messageId': '101'}
        def push(*args):
            with self.store.connect() as db:
                row = db.execute('SELECT status,message_id,needs_edit FROM deliveries WHERE suggestion_id=?', (sid,)).fetchone()
            self.assertEqual(tuple(row), ('sent', '101', 1))
            raise SimulatedTermination()
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram', side_effect=telegram) as send, \
                patch.object(a, 'push_notify', side_effect=push) as notify:
            with self.assertRaises(SimulatedTermination):
                a.deliver_one(self.store, self.config, edit_cards=False)
            # Reopening SQLite and running delivery again must not repeat a known send.
            reopened = a.Store(self.store.path)
            self.assertIsNone(a.deliver_one(reopened, self.config, edit_cards=False))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(notify.call_count, 1)
        with reopened.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT status,message_id,needs_edit FROM deliveries WHERE suggestion_id=?', (sid,)).fetchone()), ('sent', '101', 1))

    def test_result_notice_receipt_commits_before_push_termination(self):
        sid = self.delivery_fixture()
        self.store.decide(sid, 'accept', '', 'disposable-owner', 'telegram')
        self.store.prepare(sid, 'The real result')
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET status='queued',message_id='40',needs_edit=0,reminder=1 WHERE suggestion_id=?", (sid,))
        def push(*args):
            with self.store.connect() as db:
                row = db.execute('SELECT status,message_id,notification_message_id,reminder FROM deliveries WHERE suggestion_id=?', (sid,)).fetchone()
            self.assertEqual(tuple(row), ('sent', '40', '43', 0))
            raise SimulatedTermination()
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram', return_value={'messageId': '43'}) as send, \
                patch.object(a, 'push_notify', side_effect=push) as notify:
            with self.assertRaises(SimulatedTermination):
                a.deliver_one(self.store, self.config, edit_cards=False)
            self.assertIsNone(a.deliver_one(a.Store(self.store.path), self.config, edit_cards=False))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(notify.call_count, 1)

    def test_gone_subscription_is_removed_and_failures_never_raise(self):
        self.app.action('push_subscribe', SUB, owner_dashboard=True)
        row = {'id': '0123456789abcdef', 'title': 'T', 'body': 'B', 'status': 'pending'}
        def broken(request): raise ValueError('push sender failed')
        self.assertEqual(a.push_notify(self.store, self.config, row, broken), [])
        self.assertEqual(len(self.subscriptions()), 1)
        a.push_notify(self.store, self.config, row, lambda r: {'results': [{'endpoint': SUB['endpoint'], 'status': 410}]})
        self.assertEqual(self.subscriptions(), [])

    def test_delivery_pushes_once_per_telegram_buzz_and_survives_push_failure(self):
        from unittest.mock import patch
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'Evidence', 'body': 'Evidence', 'metadata': {}, 'deleted': False}])
        first = self.store.suggest('one', 'First', 'First body', [1])['id']; second = self.store.suggest('two', 'Second', 'Second body', [1])['id']
        with self.store.connect() as db: db.execute('UPDATE suggestions SET deliver_after=NULL')
        class Daytime(a.dt.datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026, 10, 12, 10, 0, tzinfo=tz or a.dt.timezone.utc)
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram', return_value={'messageId': '101'}), \
                patch.object(a, 'push_notify', side_effect=RuntimeError('phone push broke')) as push:
            self.assertTrue(a.deliver_one(self.store, {}, edit_cards=False))  # a push failure never fails the delivery
            self.assertTrue(a.deliver_one(self.store, {}, edit_cards=False))  # second card of the batch arrives silently
        self.assertEqual([c.args[2]['id'] for c in push.call_args_list], [first])
        with self.store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT status FROM deliveries WHERE suggestion_id IN (?,?)', (first, second))}, {'sent'})

    def test_without_telegram_cards_reach_the_app_and_nothing_goes_to_telegram(self):
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'Evidence', 'body': 'Evidence', 'metadata': {}, 'deleted': False}])
        card = self.store.suggest('one', 'First', 'First body', [1])['id']
        with self.store.connect() as db: db.execute('UPDATE suggestions SET deliver_after=NULL')
        config = dict(self.config, telegram_owner='')
        self.assertFalse(a.telegram_on(config))
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a.subprocess, 'run', side_effect=AssertionError('no Telegram helper')), \
                patch.object(a, 'push_notify') as push:
            self.assertTrue(a.deliver_one(self.store, config, edit_cards=False))
            a.telegram(config, {'id': card, 'title': 'First', 'body': 'Edited', 'status': 'accepted'}, 'edit', 'web-1')  # edits are local too
        self.assertEqual([c.args[2]['id'] for c in push.call_args_list], [card])
        with self.store.connect() as db:
            status, message = db.execute('SELECT status,message_id FROM deliveries WHERE suggestion_id=?', (card,)).fetchone()
        self.assertEqual(status, 'sent'); self.assertTrue(message.startswith('web-'))

    def test_without_telegram_the_recap_is_a_push_and_change_prompts_stay_on_the_dashboard(self):
        self.app.action('push_subscribe', SUB, owner_dashboard=True)
        self.app.config = dict(self.config, telegram_owner='', weekly_recap={'enabled': True})
        sent = []
        friday = a.dt.datetime(2026, 10, 16, 17, 0, tzinfo=a.dt.timezone.utc)
        with patch.object(a, 'push_node', side_effect=lambda config, request: sent.append(request) or {'results': []}), \
                patch.object(a, 'telegram_request', side_effect=AssertionError('no Telegram')), \
                patch.object(a, 'local_tz', return_value=a.dt.timezone.utc), patch.object(a, 'assistant_token_usage', return_value=None):
            self.assertEqual(self.app.recap_step(current=friday), 'sent')
        self.assertEqual((sent[0]['payload']['title'], sent[0]['payload']['url']), ('Your week', 'https://the-machine.example/assistant/'))
        with self.store.connect() as db:
            db.execute("INSERT INTO feedback_prompts(request_id,suggestion_id,card_id,title,status,created_at,updated_at,kind) VALUES('r','s','c','T','queued',?,?,'change')", (a.now(), a.now()))
        self.assertIsNone(self.app.feedback_prompt_step(sender=lambda row: self.fail('no Telegram prompt')))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM feedback_prompts WHERE request_id='r'").fetchone()[0], 'cancelled')

    def test_no_subscription_means_no_push_call(self):
        a.push_notify(self.store, self.config, {'id': 'x', 'title': 'T', 'body': 'B', 'status': 'pending'}, lambda r: self.fail('no phone subscribed'))

    def test_send_a_test_reaches_only_this_device_and_says_when_it_fails(self):
        other = {**SUB, 'endpoint': SUB['endpoint'] + '-other'}
        self.app.action('push_subscribe', SUB, owner_dashboard=True)
        self.app.action('push_subscribe', other, owner_dashboard=True)
        with self.assertRaises(ValueError):
            self.app.action('push_test', {'endpoint': SUB['endpoint']})  # only the owner's dashboard
        with self.assertRaises(ValueError):
            self.app.action('push_test', {'endpoint': 'https://push.example/unknown'}, owner_dashboard=True)
        sent = []
        ok = lambda config, request: sent.append(request) or {'results': [{'endpoint': s['endpoint'], 'status': 201} for s in request['subscriptions']]}
        with patch.object(a, 'push_node', ok):
            self.assertEqual(self.app.action('push_test', {'endpoint': SUB['endpoint']}, owner_dashboard=True), {'sent': True})
        self.assertEqual([s['endpoint'] for s in sent[0]['subscriptions']], [SUB['endpoint']])
        self.assertEqual(sent[0]['payload']['tag'], 'push-test')
        self.assertEqual(sent[0]['payload']['title'], 'Notifications are on')
        gone = lambda config, request: {'results': [{'endpoint': SUB['endpoint'], 'status': 410}]}
        with patch.object(a, 'push_node', gone), self.assertRaises(ValueError):
            self.app.action('push_test', {'endpoint': SUB['endpoint']}, owner_dashboard=True)
        self.assertEqual([s['endpoint'] for s in self.subscriptions()], [other['endpoint']])  # a gone subscription is removed


if __name__ == '__main__':
    unittest.main()
