"""Importing skills from Claude and ChatGPT (skills.py Importer): what is found on this computer and how it is grouped,
what comes in (only instructions, with the reference files they point to; never scripts), license limits, dedupe,
uploads read in memory, installing a skill with its scripts as OpenClaw's own only exactly as read, and the Find my
skills turn's report. A fake home and a fake OpenClaw stand in; nothing runs and nothing is fetched."""
import base64
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import skills

SKILL = '---\nname: {name}\ndescription: {description}\n{extra}---\n\n# {name}\n\n{body}\n'


def write(folder, name, description='When a customer asks something, research and draft an answer.', body='1. Read the thread.', extra='', files=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'SKILL.md').write_text(SKILL.format(name=name, description=description, extra=extra, body=body))
    for path, text in (files or {}).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text)
    return folder


class FakeOpenClaw:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(argv[1:])
        out = {'skills': []} if argv[1:3] == ['skills', 'list'] else {}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), '')


class Import(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home, self.workspace, self.db = root / 'home', root / 'workspace', root / 'db.sqlite'
        claude, codex = self.home / '.claude' / 'skills', self.home / '.codex' / 'skills'
        write(claude / 'customer-answers', 'customer-answers', body='Use the `Bash` tool to search, then the Read tool. Details in FAQ.md.',
              extra='allowed-tools: Bash, Read\n', files={'FAQ.md': '# FAQ\nPrices are on the website.', 'notes/draft.txt': 'x'})
        bucket = claude / 'synced' / 'org_user'
        write(bucket / 'pdf', 'pdf', extra='license: Proprietary. LICENSE.txt has complete terms\n', body='Run scripts/fill.py.',
              files={'LICENSE.txt': '© Vendor. All rights reserved.', 'scripts/fill.py': 'print(1)'})
        write(bucket / 'morning', 'morning', description='x' * 400 + '. More.', body='Make an artifact with mcp__calendar__list.')
        (bucket / 'manifest.json').write_text(json.dumps({'skills': [{'skillId': 'pdf', 'source': 'anthropic-example'},
                                                                     {'skillId': 'morning', 'source': 'anthropic-example'}]}))
        write(codex / 'weekly-report', 'weekly-report', body='Run `python scripts/build.py` and send the result.', files={'scripts/build.py': 'print(2)'})
        write(codex / '.system' / 'skill-installer', 'skill-installer')
        write(self.home / 'work' / 'site' / '.claude' / 'skills' / 'deploy', 'deploy')
        outside = write(root / 'vendor' / 'omarchy', 'omarchy')
        (claude / 'omarchy').symlink_to(outside)
        (codex / 'omarchy').symlink_to(outside)  # the same folder from Codex: listed once
        (claude / '.trash').mkdir()
        write(claude / '.trash' / 'old', 'old-one')
        (self.workspace / 'skills' / 'deploy').mkdir(parents=True)
        (self.workspace / 'skills' / 'deploy' / 'SKILL.md').write_text('---\nname: deploy\ndescription: theirs\n---\nTheirs.\n')
        self.oc = FakeOpenClaw()
        self.keeper = skills.Skills(self.connect, self.workspace, home=self.home,
                                    catalog=skills.Catalog({'openclaw': '/bin/openclaw'}, run=self.oc.run, fetch=lambda url: b''))

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        return db

    def found(self, view=None):
        view = view or self.keeper.import_find()
        return view, {i['name']: i for i in view['items']}

    def test_find_groups_and_explains_each_skill(self):
        view, items = self.found()
        self.assertEqual(sorted(items), ['customer-answers', 'deploy', 'morning', 'omarchy', 'pdf', 'skill-installer', 'weekly-report'])
        groups = {n: i['group'] for n, i in items.items()}
        self.assertEqual(groups, {'customer-answers': 'yours', 'weekly-report': 'yours', 'deploy': 'project', 'omarchy': 'vendor',
                                  'pdf': 'builtin', 'morning': 'builtin', 'skill-installer': 'builtin'})
        self.assertEqual([i['group'] for i in view['items']], sorted((i['group'] for i in view['items']), key=['yours', 'project', 'vendor', 'builtin'].index))
        pdf = items['pdf']
        self.assertTrue(pdf['blocked'])
        self.assertEqual(pdf['licence'], 'Proprietary. LICENSE.txt has complete terms')
        self.assertEqual((pdf['source'], pdf['url']), ('claude.ai, built in (pdf)', 'https://claude.ai/customize/skills'))
        report = items['weekly-report']
        self.assertEqual((report['from'], report['script_count'], report['depends']), ('chatgpt', 1, True))
        self.assertEqual(items['deploy']['duplicate'], 'OpenClaw already has a skill with this name')
        self.assertEqual(items['customer-answers']['references'], ['FAQ.md'])
        self.assertIn('notes/draft.txt', items['customer-answers']['left_out'])
        self.assertIn('Header settings left out: allowed-tools', items['customer-answers']['flags'])
        morning = items['morning']
        self.assertLessEqual(len(morning['description']), skills.DESCRIPTION_CHARS)
        self.assertTrue(any('MCP' in f for f in morning['flags']) and any('artifacts' in f for f in morning['flags']))
        self.assertTrue(view['local'])

    def test_read_shows_what_comes_in(self):
        view, items = self.found()
        shown = self.keeper.import_read(view['token'], items['customer-answers']['key'])
        self.assertIn('Use the `exec` tool to search, then the read tool.', shown['body'])
        self.assertIn('## Reference: FAQ.md\n\n# FAQ\nPrices are on the website.', shown['body'])
        self.assertIn("Renamed tools to OpenClaw's: Bash → exec, Read → read", shown['flags'])
        self.assertFalse(shown['installable'])
        self.assertTrue(self.keeper.import_read(view['token'], items['weekly-report']['key'])['installable'])
        self.assertFalse(self.keeper.import_read(view['token'], items['pdf']['key'])['installable'])
        with self.assertRaisesRegex(ValueError, 'out of date'):
            self.keeper.import_read('nope', items['pdf']['key'])

    def test_import_brings_only_instructions_and_dedupes(self):
        view, items = self.found()
        keys = [items[n]['key'] for n in ('customer-answers', 'weekly-report', 'pdf')]
        out = self.keeper.import_run(view['token'], keys)
        status = {r['name']: (r['status'], r.get('reason', '')) for r in out['results']}
        self.assertEqual(status['customer-answers'], ('imported', ''))
        self.assertEqual(status['weekly-report'], ('imported', ''))
        self.assertEqual(status['pdf'], ('skipped', "Can't import: its license doesn't allow it"))
        mine = {s['name']: s for s in out['skills']}
        self.assertEqual(mine['weekly-report']['origin'], 'mine')
        self.assertEqual(mine['weekly-report']['imported']['from'], 'chatgpt')
        self.assertEqual(mine['weekly-report']['imported']['source'], '~/.codex/skills/weekly-report')
        root = self.workspace / 'skills' / skills.FOLDER
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()),
                         ['.managed-by-the-machine', 'customer-answers/SKILL.md', 'weekly-report/SKILL.md'])
        written = (root / 'weekly-report' / 'SKILL.md').read_text()
        self.assertIn('> Imported from ChatGPT.', written)
        self.assertIn("## Not included\nThis skill came with scripts that were not brought in (scripts/build.py).", written)
        self.assertNotIn('allowed-tools', (root / 'customer-answers' / 'SKILL.md').read_text())
        again = self.keeper.import_run(view['token'], [keys[0]])
        self.assertEqual(again['results'][0]['reason'], 'Already in your skills')
        _, fresh = self.found()
        self.assertEqual(fresh['customer-answers']['duplicate'], 'Already in your skills')

    def test_install_with_scripts_only_as_read(self):
        view, items = self.found()
        key = items['weekly-report']['key']
        with self.assertRaisesRegex(ValueError, 'Read it first'):
            self.keeper.import_install(view['token'], key, 'x' * 64)
        sha = self.keeper.import_read(view['token'], key)['sha']
        (self.home / '.codex' / 'skills' / 'weekly-report' / 'scripts' / 'build.py').write_text('print("changed")')
        with self.assertRaisesRegex(ValueError, 'changed since you read it'):
            self.keeper.import_install(view['token'], key, sha)
        view, items = self.found()
        sha = self.keeper.import_read(view['token'], key)['sha']
        out = self.keeper.import_install(view['token'], key, sha)
        self.assertEqual(out['installed_skill'], 'weekly-report')
        install = [c for c in self.oc.calls if c[:2] == ['skills', 'install']]
        self.assertEqual(install, [['skills', 'install', str(self.home / '.codex' / 'skills' / 'weekly-report'), '--as', 'weekly-report']])
        with self.assertRaisesRegex(ValueError, 'license'):
            self.keeper.import_install(view['token'], items['pdf']['key'], self.keeper.import_read(view['token'], items['pdf']['key'])['sha'])

    def test_upload_is_read_in_memory_with_limits(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('export/notes/SKILL.md', SKILL.format(name='notes', description='When the owner asks for meeting notes.', extra='', body='Steps: read the thread first, then draft a reply.'))
            archive.writestr('export/notes/scripts/run.sh', 'echo hi')
            archive.writestr('export/brief/SKILL.md', SKILL.format(name='brief', description='When a briefing is needed for a call.', extra='', body='Steps: read the thread first, then draft a reply.'))
            archive.writestr('../evil/SKILL.md', SKILL.format(name='evil', description='Should never be read at all.', extra='', body='x'))
            archive.writestr('__MACOSX/x/SKILL.md', 'x')
        view = self.keeper.import_upload('claude', {'name': 'export.zip', 'zip': base64.b64encode(raw.getvalue()).decode()})
        self.assertEqual(sorted(i['name'] for i in view['items']), ['brief', 'notes'])
        notes = next(i for i in view['items'] if i['name'] == 'notes')
        self.assertEqual((notes['script_count'], notes['source'], notes['group']), (1, 'export.zip › export/notes', 'yours'))
        self.assertTrue(self.keeper.import_read(view['token'], notes['key'])['installable'])  # all its files came with the upload
        folder = self.keeper.import_upload('chatgpt', {'name': 'mine', 'files': [{'path': 'mine/SKILL.md', 'data': base64.b64encode(
            SKILL.format(name='mine', description='When something needs doing my way.', extra='', body='Steps: read the thread first, then draft a reply.').encode()).decode()}]})
        self.assertEqual([(i['name'], i['from']) for i in folder['items']], [('mine', 'chatgpt')])
        with self.assertRaisesRegex(ValueError, "isn't a .skill or .zip"):
            self.keeper.import_upload('claude', {'name': 'x.zip', 'zip': base64.b64encode(b'not a zip').decode()})
        with self.assertRaisesRegex(ValueError, 'Choose Claude or ChatGPT'):
            self.keeper.import_upload('gemini', {'name': 'x.zip', 'zip': ''})
        pasted = self.keeper.import_paste('claude', SKILL.format(name='pasted', description='When it is pasted by the owner.', extra='', body='Steps: read the thread first, then draft a reply.'))
        self.assertEqual(pasted['items'][0]['source'], 'Pasted SKILL.md')
        with self.assertRaisesRegex(ValueError, 'whole SKILL.md'):
            self.keeper.import_paste('claude', 'just text')

    def upload_one(self, body, extra_files=None):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('writer/SKILL.md', SKILL.format(name='writer', description='When a listing page needs writing.', extra='', body=body))
            for path, text in (extra_files or {}).items():
                archive.writestr('writer/' + path, text)
        view = self.keeper.import_upload('claude', {'name': 'writer.zip', 'zip': base64.b64encode(raw.getvalue()).decode()})
        return view, view['items'][0]

    def test_imported_upload_shows_as_done_not_as_a_duplicate(self):
        # The reported case: one uploaded skill, imported once. The list it comes back with says Imported, with the new
        # skill's id, and not a duplicate of itself; its two non-script files are listed as left out, no scripts.
        view, item = self.upload_one('Steps: read the brief, then write.', {'agents/openai.yaml': 'x: 1', 'assets/icon.svg': '<svg/>'})
        self.assertEqual((item['done'], item['existing'], item['script_count']), ('', None, 0))
        self.assertEqual(item['left_out'], ['agents/openai.yaml', 'assets/icon.svg'])
        out = self.keeper.import_run(view['token'], [item['key']])
        self.assertEqual([r['status'] for r in out['results']], ['imported'])
        shown = out['list']['items'][0]
        mine = {s['name']: s for s in out['skills']}
        self.assertEqual((shown['done'], shown['skill_id']), ('Imported', mine['writer']['id']))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM skills WHERE name='writer'").fetchone()[0], 1)

    def test_upload_of_a_skill_already_here_offers_replace(self):
        view, item = self.upload_one('Steps: read the brief, then write.')
        first = self.keeper.import_run(view['token'], [item['key']])['results'][0]['id']
        again, same = self.upload_one('Steps: read the brief, then write.')
        self.assertEqual((same['duplicate'], same['done']), ('Already in your skills', ''))
        self.assertEqual(same['existing'], {'id': first, 'replaceable': True, 'same': True})
        newer, changed = self.upload_one('Steps: read the brief, check the facts, then write.')
        self.assertEqual(changed['existing'], {'id': first, 'replaceable': True, 'same': False})
        skipped = self.keeper.import_run(newer['token'], [changed['key']])
        self.assertEqual(skipped['results'][0]['reason'], 'Already in your skills')
        out = self.keeper.import_run(newer['token'], [changed['key']], replace=True)
        self.assertEqual((out['results'][0]['status'], out['results'][0]['id']), ('replaced', first))
        self.assertEqual((out['list']['items'][0]['done'], out['list']['items'][0]['skill_id']), ('Replaced', first))
        with self.connect() as db:
            rows = db.execute("SELECT id,version,body FROM skills WHERE name='writer'").fetchall()
        self.assertEqual([(r['id'], r['version']) for r in rows], [(first, 2)])
        self.assertIn('check the facts', rows[0]['body'])
        # A teammate's skill of the same name is never replaced from an upload.
        with self.connect() as db:
            db.execute("UPDATE skills SET origin='shared' WHERE id=?", (first,))
        last, theirs = self.upload_one('Steps: something else entirely, then write.')
        self.assertFalse(theirs['existing']['replaceable'])
        refused = self.keeper.import_run(last['token'], [theirs['key']], replace=True)
        self.assertEqual(refused['results'][0]['status'], 'skipped')

    def test_hosted_machine_does_not_look_on_its_computer(self):
        keeper = skills.Skills(self.connect, self.workspace, home=self.home, config={'manage_url': 'https://example.invalid/account'})
        view = keeper.import_find()
        self.assertEqual((view['items'], view['local']), ([], False))
        self.assertFalse(keeper.view()['import_local'])

    def test_progress_per_source_and_never_stale(self):
        """Each place it looks has its own state; one report finishes every account it read, before the turn ends."""
        self.keeper.finder = lambda token, items, sites=None, more=False: 'run1'
        view, _ = self.found()
        token, local = view['token'], len(view['items'])
        state = lambda: {s['key']: (s['state'], s['found']) for s in self.keeper.import_status(token)['sources']}
        self.assertEqual(state(), {'local': ('done', local), 'claude': ('starting', 0), 'chatgpt': ('starting', 0)})
        self.keeper.importer.accounts(token, {'claude': 'ok', 'chatgpt': 'signed_out'})  # the turn has the browser
        self.assertEqual(state(), {'local': ('done', local), 'claude': ('looking', 0), 'chatgpt': ('signed_out', 0)})
        self.keeper.importer.report(token, [{'from': 'claude', 'name': 'late-replies', 'description': 'Chase replies that are late.',
                                      'body': 'Check sent mail.\n\n1. Find unanswered.'}],
                                    [], {'claude': 'ok'})
        self.assertEqual(self.keeper.import_status(token)['finding']['status'], 'running')  # the turn is still ending
        self.assertEqual(state(), {'local': ('done', local), 'claude': ('done', 1), 'chatgpt': ('signed_out', 0)})
        self.keeper.importer.finished(token, 'ok')
        self.assertEqual(state()['claude'], ('done', 1))
        # Signed in to ChatGPT, and that turn fails: claude.ai keeps its result, ChatGPT says it couldn't check.
        self.keeper.import_find(token, ['chatgpt'])
        self.assertEqual(state()['chatgpt'], ('starting', 0))
        self.keeper.importer.finished(token, 'error')
        self.assertEqual(state(), {'local': ('done', local), 'claude': ('done', 1), 'chatgpt': ('error', 0)})
        # A turn that ends well without a report: done, never left looking.
        self.keeper.import_find(token, ['chatgpt'])
        self.keeper.importer.finished(token, 'ok')
        self.assertEqual(state()['chatgpt'], ('done', 0))
        # An upload has no sources to show.
        self.assertEqual(self.keeper.import_paste('claude', '---\nname: x\ndescription: y\n---\nCheck sent mail for unanswered threads.')['sources'], [])

    def test_find_turn_reports_account_skills_and_recommendations(self):
        started = []
        self.keeper.finder = lambda token, items, sites=None, more=False: started.append((token, items, sites)) or 'run1'
        view, items = self.found()
        self.assertEqual(view['finding'], {'run_id': 'run1', 'status': 'running'})
        listed = {i['name']: i for i in started[0][1]}
        self.assertTrue(listed['pdf']['blocked'] and listed['deploy']['here'])
        token = view['token']
        out = self.keeper.importer.report(token, [
            {'from': 'claude', 'name': 'follow-ups', 'description': 'Chase replies that are late.', 'body': 'Check sent mail.\n\n1. Find unanswered.',
             'url': 'https://claude.ai/customize/skills/abc', 'recommended': True, 'why': 'Many follow-up cards'},
            {'from': 'claude', 'name': 'bad', 'body': 'x', 'url': 'https://evil.example/'}],
            [{'key': items['customer-answers']['key'], 'why': 'Customer questions come in daily'}, {'key': items['pdf']['key'], 'why': 'no'}],
            {'claude': 'ok', 'chatgpt': 'signed_out'})
        self.assertEqual(out, {'added': 1})
        self.keeper.importer.finished(token, 'ok')
        after = {i['name']: i for i in self.keeper.import_status(token)['items']}
        self.assertEqual((after['follow-ups']['source'], after['follow-ups']['url'], after['follow-ups']['why']),
                         ('claude.ai, your account', 'https://claude.ai/customize/skills/abc', 'Many follow-up cards'))
        self.assertTrue(after['customer-answers']['recommended'])
        self.assertFalse(after['pdf']['recommended'])  # never one its license keeps out
        self.assertEqual(self.keeper.import_status(token)['browser'], {'claude': 'ok', 'chatgpt': 'signed_out'})
        self.assertEqual(self.keeper.import_status(token)['finding']['status'], 'ok')
        # Signed in to ChatGPT since: the same list, a turn for just that account, which checks it again.
        again = self.keeper.import_find(token, ['chatgpt', 'other'])
        self.assertEqual((again['token'], started[-1][0], started[-1][2]), (token, token, ['chatgpt']))
        self.assertEqual((again['finding']['status'], again['browser']), ('running', {'claude': 'ok'}))
        with self.assertRaisesRegex(ValueError, 'Still looking'):
            self.keeper.import_find(token, ['chatgpt'])
        self.keeper.importer.accounts(token, {'chatgpt': 'signed_out', 'evil': 'ok', 'claude': 'whatever'})
        self.assertEqual(self.keeper.import_status(token)['browser'], {'claude': 'ok', 'chatgpt': 'signed_out'})


def skill_zip(name, text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr(f'{name}/SKILL.md', f'---\nname: {name}\ndescription: {text}\n---\nSteps: {text}\n')
        z.writestr(f'{name}/agents/openai.yaml', 'x: 1\n')
    return base64.b64encode(out.getvalue()).decode()


class FindTurn(unittest.TestCase):
    """Find my skills on the service: the accounts read by the service itself, straight on their skills pages, then one
    turn that only judges; the account the owner picked is the only one read."""

    def app(self):
        import test_profile as tp
        a = tp.a
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = a.Store(Path(temp.name) / 'events.sqlite')
        app = object.__new__(a.App)
        app.store, app.config = store, {'accounts': [{'id': 'work', 'service': 'gmail'}], 'browser_lease_script': '/bin/true'}
        app.skills = a.skills.Skills(store.connect, Path(temp.name) / 'ws', home=Path(temp.name) / 'home')
        app.skills.finder = app.skills_find_start
        return tp, a, app

    def test_only_the_picked_account_is_read_and_its_skills_listed(self):
        tp, a, app = self.app()
        read = []
        app.skills_read = lambda sites: read.append(sites) or {'chatgpt': {'state': 'ok', 'where': a.SKILLS_PAGES['chatgpt'], 'skills': [
            {'name': 'follow-ups', 'description': 'Chase late replies.', 'zip': skill_zip('follow-ups', 'Chase late replies.'), 'url': a.SKILLS_PAGES['chatgpt']}]}}
        view = app.action('skill_import_find', {'sites': ['chatgpt']})
        self.assertEqual([s['key'] for s in view['sources'] if s['key'] != 'local'], ['chatgpt'])  # not claude.ai
        run = view['finding']['run_id']
        self.assertIsNone(app.skills_find_start('t', []))  # one at a time
        runner = tp.Runner('admitted', 'ok')
        app.profile_step(runner)
        self.assertEqual(read, [['chatgpt']])
        status = app.action('skill_import_status', {'token': view['token']})
        self.assertEqual([i['name'] for i in status['items']], ['follow-ups'])
        self.assertEqual({x['key']: (x['state'], x['found'], x.get('where')) for x in status['sources'] if x['key'] != 'local'},
                         {'chatgpt': ('done', 1, 'https://chatgpt.com/skills?collection=installed')})
        # The turn only judges what the service read: no browser, no site's API in its message.
        message = runner.requests[0]['message']
        self.assertEqual(runner.requests[0]['kind'], 'skills')
        self.assertIn(f'skills run {run}', message)
        self.assertIn('This Machine\'s sources: gmail', message)
        self.assertIn('chatgpt:account:follow-ups: follow-ups', message)
        self.assertIn('do not open a browser', message)
        self.assertNotIn('/backend-api/', message)
        out = app.action('skills_found', {'_skills_session': a.SKILLS_SESSION_PREFIX + run,
                                          'recommended': [{'key': 'chatgpt:account:follow-ups', 'why': 'Many follow-up cards'}]})
        self.assertEqual(out, {'added': 0})
        with self.assertRaisesRegex(ValueError, 'Only the Find my skills turn'):
            app.action('skills_found', {'_skills_session': a.PROFILE_SESSION_PREFIX + run})
        app.profile_step(runner)
        status = app.action('skill_import_status', {'token': view['token']})
        self.assertEqual(status['finding']['status'], 'ok')
        self.assertEqual(status['items'][0]['why'], 'Many follow-up cards')

    def test_nothing_found_says_where_it_looked_and_starts_no_turn(self):
        tp, a, app = self.app()
        app.skills_read = lambda sites: {'claude': {'state': 'ok', 'where': a.SKILLS_PAGES['claude'], 'skills': [],
                                                    'detail': 'none you made or uploaded yourself'},
                                         'chatgpt': {'state': 'signed_out', 'where': a.SKILLS_PAGES['chatgpt']}}
        view = app.action('skill_import_find', {})  # no source picked: both, as before
        runner = tp.Runner('admitted')
        self.assertEqual(app.profile_step(runner), 'ok')
        self.assertEqual(runner.requests, [])
        status = app.action('skill_import_status', {'token': view['token']})
        shown = {x['key']: x for x in status['sources']}
        self.assertEqual((shown['claude']['state'], shown['claude']['where'], shown['claude']['detail']),
                         ('done', 'https://claude.ai/customize/skills', 'none you made or uploaded yourself'))
        self.assertEqual(shown['chatgpt']['state'], 'signed_out')
        self.assertEqual((status['finding']['status'], status['browser']), ('ok', {'claude': 'ok', 'chatgpt': 'signed_out'}))
        # Signed in since: a look in just ChatGPT, on the same list.
        app.skills_read = lambda sites: {'chatgpt': {'state': 'ok', 'where': a.SKILLS_PAGES['chatgpt'], 'skills': [
            {'name': 'notes', 'description': 'Keep notes.', 'zip': skill_zip('notes', 'Keep short notes of every customer call, with the next step.'), 'url': a.SKILLS_PAGES['chatgpt']}]}}
        app.action('skill_import_find', {'token': view['token'], 'sites': ['chatgpt']})
        runner = tp.Runner('admitted')
        app.profile_step(runner)
        self.assertIn('chatgpt:account:notes', runner.requests[0]['message'])

    def test_read_opens_the_skills_page_never_the_home_page_and_closes_it(self):
        tp, a, app = self.app()
        opened, closed = [], []

        class Cdp:
            CdpError = RuntimeError

            @staticmethod
            def open_tab(base, url):
                opened.append(url)
                return 'tab' + str(len(opened))

            @staticmethod
            def evaluate(base, tab, fn, timeout_ms=0):
                if 'readyState' in fn:
                    return ('chatgpt.com' if tab == 'tab1' else 'claude.ai') + ' complete'
                return {'state': 'ok', 'skills': [], 'total': 3, 'failed': 0}

            @staticmethod
            def close_tab(base, tab):
                closed.append(tab)
        old = a.browser_login.browser_cdp, a.time.sleep
        a.browser_login.browser_cdp, a.time.sleep = Cdp, lambda s: None
        self.addCleanup(lambda: (setattr(a.browser_login, 'browser_cdp', old[0]), setattr(a.time, 'sleep', old[1])))
        app.config['browser_cdp_url'] = 'http://127.0.0.1:18800'
        got = app.skills_read(['chatgpt'])
        self.assertEqual(opened, ['https://chatgpt.com/skills?collection=installed'])
        self.assertEqual(closed, ['tab1'])
        self.assertEqual(got['chatgpt'], {'where': 'https://chatgpt.com/skills?collection=installed', 'state': 'ok', 'skills': [],
                                          'detail': 'none you made or uploaded yourself'})


class FindPrompt(unittest.TestCase):
    def test_prompt_stays_under_the_gateway_limit(self):
        import assistant
        items = [{'key': f'claude:~/.claude/skills/s{n}', 'name': f's{n}', 'description': 'd' * 200, 'group': 'yours'} for n in range(150)]
        text = assistant.skills_find_prompt('r' * 32, {'token': 't', 'items': items, 'cards': ['c' * 200] * 40, 'sources': ['gmail'], 'skills': []})
        self.assertLess(len(text), 20000)
        self.assertIn('assistant_signals action skills_found', text)
        self.assertIn('more', text)

    def test_readers_are_read_only_and_keep_the_token_in_the_page(self):
        import assistant
        for site, js in assistant.SKILLS_READ.items():
            self.assertNotIn("method: 'POST'", js)
            self.assertNotIn('delete', js.lower())
        self.assertIn('/export', assistant.SKILLS_READ['chatgpt'])
        self.assertIn("source === 'personal'", assistant.SKILLS_READ['claude'])
        self.assertNotIn('accessToken', assistant.SKILLS_READ['chatgpt'].split('return {state')[-1])


if __name__ == '__main__':
    unittest.main()
