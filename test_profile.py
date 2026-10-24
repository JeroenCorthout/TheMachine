"""assistant profile: one read-only agent turn drafts USER.md lines from the owner's own messages. The
draft waits for the owner; USER.md only changes after their yes. No Gateway, no Telegram."""
import concurrent.futures, contextlib, datetime as dt, importlib.util, io, json, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

a = load('profile_assistant', 'assistant.py')
cli = load('profile_cli', 'cli.py')


class Runner:
    def __init__(self, *results): self.results = list(results); self.requests = []
    def __call__(self, request): self.requests.append(request); return {'status': self.results.pop(0)}


def signal(source_id, minutes_ago, kind='gmail.message', body='Thanks Sam, sounds good to me. Talk soon', **metadata):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).isoformat()
    return {'account_id': 'work', 'source_id': source_id, 'kind': kind, 'occurred_at': when, 'title': 'Re: plan',
            'body': body, 'metadata': metadata, 'deleted': False}


class Profile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.owner_user_ids = {'U1'}
        self.store.accounts = [{'id': 'work', 'email': 'alex@acme.test'}]
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.config = {'accounts': self.store.accounts, 'review_worker': {'enabled': False}}
        replies = ['Thanks Sam, sounds good to me. Talk soon', 'Works for me, see you Thursday at the office',
                   'Could you send the signed copy when you have it?', 'Great news, congrats to the whole team',
                   'I will check with finance and come back to you tomorrow', 'No problem at all, happens to everyone']
        sent = [signal(f'sent{i}', i, body=replies[i], labels=['SENT'], to='sam@globex.test',
                       **({'from': 'Alex <alex@acme.test>'})) for i in range(6)]
        sent[0]['body'] += '\n\n[Quoted earlier messages]\nOn Monday Sam wrote: secret'
        self.store.commit_page('work', {}, sent + [
            signal('in1', 1, body='Weekly digest of things', labels=['INBOX'], **{'from': 'News <news@digest.test>'}),
            signal('in2', 2, body='Weekly digest of things', labels=['INBOX'], **{'from': 'News <news@digest.test>'}),
            signal('in3', 3, body='Can you sign?', labels=['INBOX'], to='alex@acme.test', **{'from': 'Sam <sam@globex.test>'}),
            # A second day in touch, so Sam is past closeness 1 at any hour (a day's age counts from its noon UTC).
            signal('in-yesterday', 1500, body='Here is the draft contract', labels=['INBOX'], to='alex@acme.test', **{'from': 'Sam <sam@globex.test>'}),
            signal('wa1', 1, 'whatsapp.message', 'Ok top, tot straks', from_me=True, chat_name='Family'),
            signal('wa2', 2, 'whatsapp.message', 'Not mine at all', from_me=False),
            signal('sl1', 1, 'slack.message', 'Shipping it today', user_id='U1', channel_name='product'),
            signal('sl2', 2, 'slack.message', 'Someone else here', user_id='U2'),
            # Not how anyone writes: media, reactions, an automated confirmation and one outreach template sent many times.
            signal('wa3', 3, 'whatsapp.message', 'Sent image', from_me=True), signal('wa4', 4, 'whatsapp.message', 'Reacted 😂 to Sent image', from_me=True),
            signal('auto', 9, body='![](https://booking.test/logo.png) I look forward to talking with you!', labels=['SENT']),
            *(signal(f'tpl{i}', 10 + i, body=f'Hey {name}, We are looking for a partner for our product, a CRM since 2014. I saw your work with {name} Corp.',
                     labels=['SENT']) for i, name in enumerate(['Dirk', 'Ville', 'Tony', 'Francesca']))])

    def test_reads_only_the_owners_own_messages_without_quoted_history(self):
        items = self.store.own_messages()
        self.assertEqual(sorted(i['source'] for i in items), ['Slack', 'WhatsApp'] + ['email'] * 8)  # 6 replies + 2 of the template
        self.assertEqual(sum('looking for a partner' in i['text'] for i in items), 2)
        self.assertFalse([i for i in items if i['text'].startswith(('Sent image', 'Reacted', '!['))])
        self.assertTrue(all('secret' not in i['text'] and 'Not mine' not in i['text'] and 'Someone else' not in i['text'] for i in items))
        self.assertEqual({i.get('chat') for i in items if i['source'] != 'email'}, {'Family', 'product'})
        # Round-robin with email weighted double: a small limit still shows every source.
        self.assertEqual(sorted(i['source'] for i in self.store.own_messages(4)), ['Slack', 'WhatsApp', 'email', 'email'])
        first = self.store.profile_evidence(1)
        self.assertEqual(first['counts'], {'email': 8, 'WhatsApp': 1, 'Slack': 1})
        self.assertEqual(first['frequent_unanswered_senders'], [{'sender': 'news@digest.test', 'emails': 2}])  # Sam got answers

    def test_the_draft_belongs_to_the_running_profile_turn_only(self):
        run_id = self.app.action('profile_start', {})['run_id']
        self.assertEqual(self.app.action('profile_start', {})['attached'], True)
        with self.assertRaisesRegex(ValueError, 'profile turn'):
            self.app.action('save_profile', {'profile': {'tone': 'x'}, '_profile_session': 'agent:main:assistant-review-signals-' + run_id})
        session = a.PROFILE_SESSION_PREFIX + run_id
        habits = 'Books calls through a link. ' * 12 + 'Shares technical details'
        saved = self.app.action('save_profile', {'_profile_session': session, 'profile': {
            'tone': '  Short and\nfriendly ', 'habits': habits, 'examples': ['a', 'b', 'c', 'd', 'e'], 'languages': ['Dutch: friends.', 'English: work;'],
            'matters': [{'name': 'Sam', 'why': 'weekly call'}], 'evil': 'x', 'based_on': 'my own long story about what I read'}})
        self.assertEqual(saved['fields'], ['based_on', 'examples', 'habits', 'languages', 'matters', 'tone'])
        draft = self.app.action('profile_status', {'run_id': run_id})['draft']
        self.assertTrue(draft['habits'].endswith('link.') and len(draft['habits']) <= 300)  # whole sentences, never "techn…"
        self.assertEqual(draft['based_on'], '8 email, 1 Slack, 1 WhatsApp')  # counted by the service, not the model
        self.assertEqual({k: draft[k] for k in ('tone', 'examples', 'languages', 'matters')},
                         {'tone': 'Short and friendly', 'examples': ['a', 'b', 'c', 'd'], 'languages': ['Dutch: friends', 'English: work'], 'matters': ['Sam: weekly call']})
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.app.action('save_profile', {'_profile_session': session, 'profile': {}})

    def test_onboarding_also_names_what_the_closest_people_are(self):
        self.assertNotIn('role', a.profile_prompt('r', {'email': 6}).split('2. ')[1].split('3. ')[0])  # its own turn, after the draft
        self.app.graph_build()
        started = self.app.action('profile_start', {})
        roles_id = started['roles_run_id']
        session = a.PROFILE_SESSION_PREFIX + roles_id
        # The draft runs first; the roles turn after it.
        self.app.profile_step(Runner('admitted'))
        self.app.action('save_profile', {'_profile_session': a.PROFILE_SESSION_PREFIX + started['run_id'], 'profile': {'tone': 'Warm'}})
        self.app.profile_step(Runner('ok'))
        runner = Runner('admitted', 'ok')
        self.app.profile_step(runner)
        self.assertEqual(runner.requests[0]['job_id'], roles_id)
        self.assertIn('roles run', runner.requests[0]['message'])
        self.assertIn('a wrong role is worse than none', runner.requests[0]['message'])
        page = self.app.action('profile_evidence', {'page': 1, '_profile_session': session})
        sam = page['people'][0]
        self.assertEqual((sam['name'], sam['node_id']), ('Sam', 'email:sam@globex.test'))
        texts = [(m['from'], m['text']) for m in sam['messages']]
        self.assertIn(('Sam <sam@globex.test>', 'Can you sign?'), texts)  # theirs and the owner's, both directions
        self.assertTrue(any(who == 'the owner' and 'secret' not in text for who, text in texts))
        self.assertNotIn('news@digest.test', json.dumps(page))  # only people close enough
        with self.assertRaisesRegex(ValueError, 'no draft'):
            self.app.action('save_profile', {'_profile_session': session, 'profile': {'tone': 'x'}})
        self.assertEqual([o['node_id'] for o in page['organisations']], ['org:globex.test'])  # organizations get a role too
        sign = next(m['event_id'] for m in sam['messages'] if m['text'] == 'Can you sign?')
        with self.store.connect() as db:
            a.graph.set_role(db, 'email:sam@globex.test', 'CFO at Globex', 'agent', a.now())
            a.graph.set_role(db, 'org:globex.test', 'customer', 'agent', a.now(), sign, 'Can you sign?')
        self.app.profile_step(runner)
        run = self.app.action('profile_status', {'run_id': roles_id})
        self.assertEqual((run['status'], sorted(r['role'] for r in run['roles'])), ('ok', ['CFO at Globex', 'customer']))  # done without a draft
        self.app.graph_build()
        self.assertEqual(self.app.action('roles_start', {'again': True}), {'people': 0})  # everyone close has a role

    def test_one_voice_per_channel_most_used_first(self):
        run_id = self.app.action('profile_start', {})['run_id']
        self.app.action('save_profile', {'_profile_session': a.PROFILE_SESSION_PREFIX + run_id, 'profile': {
            'voices': {'whatsapp': 'lowercase bursts, haha', 'Slack': 'terse, no greeting', 'mail': 'Hey [name], short; Regards, Alex', 'Pigeon': 'coo'}}})
        voices = self.app.action('profile_status', {'run_id': run_id})['draft']['voices']
        self.assertEqual(list(voices), ['Email', 'Slack', 'WhatsApp', 'Pigeon'])  # channel names as in the evidence; email (8) first
        self.assertEqual(voices['Email'], 'Hey [name], short; Regards, Alex')
        run_id = self.app.action('profile_start', {'again': True})['run_id']
        self.app.action('save_profile', {'_profile_session': a.PROFILE_SESSION_PREFIX + run_id, 'profile': {
            'voices': [{'channel': 'Email', 'voice': 'warm'}, {'channel': 'LinkedIn', 'voice': 'friendly, no signature'}]}})
        self.assertEqual(self.app.action('profile_status', {'run_id': run_id})['draft']['voices'], {'Email': 'warm', 'LinkedIn': 'friendly, no signature'})

    def test_too_few_own_messages_says_so(self):
        with self.store.connect() as db: db.execute('UPDATE events SET deleted=1')
        with self.assertRaisesRegex(ValueError, 'Only 0 of your own messages'):
            self.app.action('profile_start', {})

    def test_busy_chat_and_template_burst_do_not_crowd_out_email(self):
        with self.store.connect() as db:
            db.execute("UPDATE events SET occurred_at=?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(),))
        self.store.commit_page('work', {}, [signal(f'busy{i}', i / 1000, kind='whatsapp.message',
                                                   body=f'Distinct chat text {i}', from_me=True) for i in range(600)])
        self.assertEqual(sum(i['source'] == 'email' for i in self.store.own_messages()), 8)
        self.store.commit_page('work', {}, [signal(f'template{i}', i / 1000, body='Identical automated template email', labels=['SENT']) for i in range(500)])
        self.assertGreaterEqual(sum(i['source'] == 'email' for i in self.store.own_messages()), 10)

    def test_profile_pages_and_provenance_stay_fixed_during_ingestion(self):
        self.store.commit_page('work', {}, [signal(f'page{i}', 100 + i, kind='whatsapp.message',
                                                   body=f'Initial separate message {i}', from_me=True) for i in range(30)])
        run = self.store.profile_start()
        session = a.PROFILE_SESSION_PREFIX + run['run_id']
        first = self.store.profile_evidence(1, session)
        before = self.store.profile_evidence(2, session)
        self.store.commit_page('work', {}, [signal(f'arrival{i}', i / 1000, kind='whatsapp.message',
                                                   body=f'New separate message {i}', from_me=True) for i in range(25)])
        self.assertEqual(self.store.profile_evidence(1, session), first)
        self.assertEqual(self.store.profile_evidence(2, session), before)
        self.assertFalse({i['id'] for i in first['messages']} & {i['id'] for i in before['messages']})
        self.store.save_profile({'tone': 'Friendly'}, session)
        self.assertEqual(self.store.profile_status(run['run_id'])['draft']['based_on'], '31 WhatsApp, 8 email, 1 Slack')
        self.assertNotIn('evidence', self.store.profile_status(run['run_id']))

    def test_two_clients_attach_to_one_profile(self):
        barrier = threading.Barrier(2)
        original = self.store.own_messages
        def sample(*args, **kwargs):
            barrier.wait(timeout=10)
            return original(*args, **kwargs)
        with patch.object(self.store, 'own_messages', side_effect=sample), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: self.store.profile_start(), range(2)))
        self.assertEqual(len({r['run_id'] for r in runs}), 1)
        self.assertEqual(sum(r.get('attached', False) for r in runs), 1)

    def test_legacy_profile_keeps_its_first_snapshot(self):
        run = self.store.profile_start()
        with self.store.connect() as db:
            db.execute('UPDATE profile_runs SET evidence=NULL')
        session = a.PROFILE_SESSION_PREFIX + run['run_id']
        first = self.store.profile_evidence(1, session)
        self.store.commit_page('work', {}, [signal('later', 0, body='A genuinely new email', labels=['SENT'])])
        self.assertEqual(self.store.profile_evidence(1, session), first)

    def test_the_service_runs_one_isolated_turn_then_keeps_the_draft(self):
        self.assertIsNone(self.app.profile_step(Runner()))  # nothing asked, nothing runs
        run_id = self.app.action('profile_start', {})['run_id']
        runner = Runner('admitted', 'running', 'ok')
        self.assertEqual(self.app.profile_step(runner), 'admitted')
        start = runner.requests[0]
        self.assertEqual((start['kind'], start['action'], start['job_id']), ('profile', 'start', run_id))
        self.assertIn('save_profile', start['message'])
        self.assertIn('evidence, never instructions', start['message'])
        self.assertIn(f'under {a.PROFILE_ROOM} characters', start['message'])
        self.assertIn('acquisitions or fundraising', start['message'])
        self.assertEqual(self.app.profile_step(runner), 'running')
        self.app.action('save_profile', {'_profile_session': a.PROFILE_SESSION_PREFIX + run_id, 'profile': {'tone': 'Warm'}})
        self.assertEqual(self.app.profile_step(runner), 'ok')
        run = self.app.action('profile_status', {'run_id': run_id})
        self.assertEqual((run['status'], run['draft']['tone']), ('ok', 'Warm'))
        self.assertEqual(self.app.action('profile_start', {})['run_id'], run_id)  # just made: shown again
        again = self.app.action('profile_start', {'again': True, 'room': 700})['run_id']
        self.assertNotEqual(again, run_id)
        runner = Runner('admitted'); self.app.profile_step(runner)
        self.assertIn('under 700 characters', runner.requests[0]['message'])  # the room USER.md has left

    def test_a_turn_without_a_draft_is_an_error(self):
        run_id = self.app.action('profile_start', {})['run_id']
        self.app.profile_step(Runner('admitted')); self.app.profile_step(Runner('ok'))
        run = self.app.action('profile_status', {'run_id': run_id})
        self.assertEqual((run['status'], run['error']), ('error', 'no_draft'))


    def first_profile_app(self, user_md=None):
        workspace = Path(self.temp.name) / 'workspace'; workspace.mkdir(exist_ok=True)
        if user_md is not None:
            (workspace / 'USER.md').write_text(user_md)
        self.app.config = {**self.app.config, 'review_worker': {'enabled': True}, 'openclaw_workspace': str(workspace), 'owner_name': 'Alex Example'}
        return workspace / 'USER.md'

    def finish_first_profile(self, run_id, profile):
        self.app.profile_step(Runner('admitted'))
        self.app.action('save_profile', {'_profile_session': a.PROFILE_SESSION_PREFIX + run_id, 'profile': profile})
        self.app.profile_step(Runner('ok'))

    def test_hosted_install_drafts_its_first_profile_by_itself(self):
        template = '# USER.md - User Model\n\n## Directives\n\n- Prefer ...\n'
        path = self.first_profile_app(template)
        self.app.config['review_worker'] = {'enabled': False}
        self.assertIsNone(self.app.first_profile_step())  # reviews off: the owner hasn't started yet
        self.app.config['review_worker'] = {'enabled': True}
        started = self.app.first_profile_step()
        self.assertTrue(started['run_id'])
        self.assertIsNone(self.app.first_profile_step())  # one at a time
        self.finish_first_profile(started['run_id'], {'role': 'Developer working on integrations', 'tone': 'Short and direct',
                                                      'leave_alone': ['payment alerts: a colleague handles them']})
        text = path.read_text()
        self.assertIn('## Observed from my messages', text)
        self.assertIn('- Role: Developer working on integrations', text)
        self.assertIn('# About me', text)  # the starter for the template, its covered blanks gone
        self.assertNotIn('- Tone: (for example', text)
        self.assertIsNone(self.app.first_profile_step())  # says how they write now: never again by itself

    def test_first_profile_leaves_a_written_user_md_and_the_owners_own_run_alone(self):
        self.first_profile_app('# About me\n\n- Tone: warm, first names\n')
        self.assertIsNone(self.app.first_profile_step())
        path = self.first_profile_app('# About me\n\n- Role: \n- Tone: \n')
        own = self.app.action('profile_start', {})['run_id']  # the owner ran assistant profile themselves
        self.assertIsNone(self.app.first_profile_step())
        self.finish_first_profile(own, {'tone': 'Warm'})
        self.assertNotIn('Observed from my messages', path.read_text())  # their own run waits for their yes

    def test_first_profile_never_overwrites_edits_made_while_it_ran(self):
        path = self.first_profile_app('# About me\n\n- Tone: \n')
        run_id = self.app.first_profile_step()['run_id']
        path.write_text('# About me\n\n- Tone: terse, no greeting\n')
        self.finish_first_profile(run_id, {'tone': 'Warm'})
        self.assertEqual(path.read_text(), '# About me\n\n- Tone: terse, no greeting\n')

    def test_first_profile_waits_for_enough_own_messages_and_gives_up_after_a_few_tries(self):
        self.first_profile_app('# About me\n\n- Tone: \n')
        with self.store.connect() as db: db.execute('UPDATE events SET deleted=1')
        self.assertIsNone(self.app.first_profile_step())
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM profile_runs').fetchone()[0], 0)
            db.execute('UPDATE events SET deleted=0')
        self.assertIsNone(self.app.first_profile_step())  # waits before looking again
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=a.FIRST_PROFILE_RETRY_SECONDS + 60)
        self.assertTrue(self.app.first_profile_step(later)['run_id'])
        with self.store.connect() as db:
            db.execute("UPDATE profile_runs SET status='error',finished_at='2000-01-01'")
            for n in range(a.FIRST_PROFILE_TRIES - 1):
                db.execute("INSERT INTO profile_runs(run_id,status,started_at,updated_at,finished_at,auto) VALUES(?,'error','2000','2000','2000',1)", (f'x{n}',))
        self.assertIsNone(self.app.first_profile_step(later))  # tried enough
        self.app.config['first_profile'] = False
        with self.store.connect() as db: db.execute("DELETE FROM profile_runs WHERE run_id LIKE 'x%'")
        self.assertIsNone(self.app.first_profile_step(later))


class ProfileCommand(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def run_cli(self, *argv, answer='y', existing=None):
        if existing is not None:
            (self.workspace / 'USER.md').write_text(existing)
        statuses = [{'status': 'running', 'started_at': a.now()},
                    {'status': 'ok', 'started_at': a.now(), 'draft': {'tone': 'Short and friendly', 'based_on': '6 sent emails'}}]
        def fake(config, action, args=None):
            if action == 'profile_start':
                return {'run_id': 'r' * 32, 'started_at': a.now(), 'counts': {'email': 6}, 'roles_run_id': 'q' * 32}
            if args.get('run_id') == 'q' * 32:  # the roles turn that follows the draft
                return {'status': 'ok', 'started_at': a.now(), 'roles': [{'name': 'Sam', 'role': 'CFO at Globex'}]}
            return statuses.pop(0)
        out = io.StringIO()
        with patch.object(cli, 'call', side_effect=fake), patch.object(cli, 'load_config', return_value={'owner_name': 'Alex', 'accounts': []}), \
             patch.object(cli.time, 'sleep'), patch('builtins.input', return_value=answer), patch.object(cli.sys.stdin, 'isatty', return_value=True), \
             contextlib.redirect_stdout(out):
            cli.main(['profile', '--workspace', str(self.workspace), *argv])
        return out.getvalue()

    def test_roles_alone_without_redoing_the_profile(self):
        calls = []
        def fake(config, action, args=None):
            calls.append(action)
            return {'run_id': 'q' * 32} if action == 'roles_start' else {'status': 'ok', 'started_at': a.now(), 'roles': [{'name': 'Kim', 'role': 'accountant'}]}
        out = io.StringIO()
        with patch.object(cli, 'call', side_effect=fake), patch.object(cli, 'load_config', return_value={'accounts': []}), contextlib.redirect_stdout(out):
            cli.main(['roles'])
        self.assertEqual(calls, ['roles_start', 'profile_status'])  # no USER.md draft
        self.assertIn('  - Kim: accountant', out.getvalue())

    def test_shows_the_draft_and_saves_only_after_yes(self):
        out = self.run_cli(answer='n')
        self.assertIn('- Tone: Short and friendly', out)
        self.assertIn('  - Sam: CFO at Globex', out)
        self.assertIn('Not saved', out)
        self.assertFalse((self.workspace / 'USER.md').exists())
        self.run_cli()
        text = (self.workspace / 'USER.md').read_text()
        self.assertIn('# About me', text)  # no USER.md yet: the starter, with the draft filling its tone line
        self.assertNotIn('(for example: short and friendly', text)
        self.assertIn('- Tone: Short and friendly', text)

    def test_a_full_user_md_keeps_what_fits_and_says_what_it_left_out(self):
        mine = '# About me\n\n' + '- Something I wrote myself.\n' * 130
        out = self.run_cli(existing=mine)
        saved = (self.workspace / 'USER.md').read_text()
        self.assertLessEqual(len(saved), 4000)
        self.assertTrue(saved.startswith(mine.rstrip()))
        self.assertIn('only the most useful lines', out)
        out = self.run_cli('--all', existing=mine)
        self.assertIn('- Tone: Short and friendly', (self.workspace / 'USER.md').read_text())

    def test_leaves_a_section_the_owner_made_their_own(self):
        mine = '# About me\n\n## Observed from my messages\n- Tone: my own words\n'
        out = self.run_cli(existing=mine)
        self.assertIn('made your own', out)
        self.assertEqual((self.workspace / 'USER.md').read_text(), mine)


    def test_keeps_owner_edits_during_inference_and_the_save_prompt(self):
        from types import SimpleNamespace
        path = self.workspace / 'USER.md'
        path.write_text('# Owner\n- Original preference.\n')
        def call(config, action, args):
            if action == 'profile_start':
                path.write_text(path.read_text() + '- Edit while drafting.\n')
                return {'run_id': 'r', 'counts': {'email': 6}}
            return {'status': 'ok', 'draft': {'tone': 'Friendly'}}
        prompts = []
        def answer(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                path.write_text(path.read_text() + '- Edit at save prompt.\n')
            return 'y'
        args = SimpleNamespace(workspace=str(self.workspace), again=False, json=False, all=False, yes=False)
        with patch.object(cli, 'call', side_effect=call), patch.object(cli.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_profile({'accounts': []}, args)
        self.assertIn('Original preference', path.read_text())
        self.assertIn('Edit while drafting', path.read_text())
        self.assertIn('Edit at save prompt', path.read_text())
        self.assertIn('Friendly', path.read_text())
        self.assertEqual(len(prompts), 2)


if __name__ == '__main__':
    unittest.main()
