#!/usr/bin/env python3
"""Check the help articles in docs/help/ against the code, so they stay true as the app changes.

Each article is Markdown with a small front-matter block:

    ---
    title: Gmail
    area: connections
    summary: One line for the article list and search.
    covers: connector:gmail
    code: onboarding.py, google_api.py
    site: Connect Google
    ---

It fails when:
- a feature exists without an article: every connector (onboarding.SERVICES and connectors/*.py), every dashboard tab
  (the sidebar's data-tab buttons), every Settings tab (SETTINGS_TABS) and every entry of FEATURES below must be in
  some article's `covers`, unless PENDING says why not yet;
- an article covers a feature that no longer exists, or names a `code` path that is gone, or the dashboard's Help link
  (HELP_FOR in dashboard.html) points at an article that doesn't exist;
- an article names app text that no longer exists: every **bold** phrase is button or label text and must appear in
  the app's code; a `/route` in backticks must appear in the code; `assistant <command>` must be a CLI command.
  Text the hosted control plane shows (its setup and account pages) goes in `site:`, and its own test checks it;
- the copy breaks the house style: an em dash, a line that starts with "You", a paragraph over 420 characters,
  a summary over 160, a placeholder other than TOKENS (like {price.standard}, which the site fills in), or Markdown
  the site does not render (only ## and ### headings, paragraphs, - and 1. lists,
  **bold**, `code` and links; a link to another article is its file name, like gmail.md).

    python3 scripts/help_check.py          # prints what is wrong, exit code 1 if anything is
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELP = ROOT / 'docs' / 'help'
# The areas of the help, in the order the site shows them.
AREAS = {'getting-started': 'Getting started', 'cards': 'Cards and chat', 'calendar': 'Calendar', 'graph': 'Graph',
         'connections': 'Connections', 'fleet': 'Fleet and The Machine Network', 'weekly': 'Weekly recap',
         'settings': 'Settings', 'account': 'Account and billing', 'security': 'Security and privacy',
         'troubleshooting': 'Troubleshooting'}
# Features that are not a connector, tab or Settings tab, each with a piece of code that shows it still exists.
# Add one when you add such a feature; when its code is gone, the check asks you to update or delete its article.
FEATURES = {
    'do-it': ('dashboard.html', 'Do it'),
    'snooze': ('dashboard.html', 'Remind me later'),
    'card-chat': ('dashboard.html', 'id="chat'),
    'hand-to-agent': ('dashboard.html', 'Hand to Codex'),
    'promises': ('dashboard.html', 'You promised'),
    'telegram': ('assistant.py', 'telegram_owner'),
    'chatgpt': ('docs/models.md', 'ChatGPT'),
    'phone-app': ('manifest.webmanifest', '"name"'),
    'weekly-recap': ('assistant.py', 'def weekly_recap'),
    'enrichment': ('enrichment.py', 'def '),
    'network': ('network.py', 'def '),
    'friends': ('friends.py', 'def '),
    'family': ('family.py', 'def '),
    'handoffs': ('fleet_requests.py', 'def '),
    'task-pages': ('task_pages.py', 'def '),
    'saved-signins': ('dashboard.html', 'Saved sign-ins'),
    'self-hosted': ('install.sh', '#!'),
    'moving': ('moving.py', 'def '),
    'support-bundle': ('cli.py', "'support-bundle'"),
    'keyboard': ('dashboard.html', 'Search and commands'),
}
# Features that exist in the code but have no article yet, each with the reason. Keep this short.
PENDING = {
    'connector:zoom': 'Connect Zoom needs the hosted sign-in (control plane pull request 37), not merged yet',
}
# Where app text may live: the dashboard and the code that writes what it shows.
TEXT_FILES = ('dashboard.html', '*.py', '*.mjs', 'connectors/*.py', 'integrations/*.py', 'openclaw-plugin/*.ts', 'install.sh')
# Pages of the public site an article may link to (the control plane's own test checks they exist).
SITE_PAGES = ('/', '/how-it-works', '/demo', '/network', '/pricing', '/open-source', '/security', '/privacy', '/terms',
              '/dpa', '/licenses', '/support', '/help', '/compare')
FIELDS = {'title', 'area', 'summary', 'covers', 'code', 'site', 'order'}
# Values the site fills in from the hosted service's own settings, so a price or a number of days is never out of date
TOKENS = {'{price.standard}', '{price.big}', '{price.seat}', '{price.seat_big}', '{price.network}', '{trial_days}', '{grace_days}'}


def front(text):
    """(fields, body) of an article; fields with a comma list (covers, code, site) as lists."""
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not m:
        return None, text
    fields = {}
    for line in m[1].splitlines():
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()
    for key in ('covers', 'code', 'site'):
        fields[key] = [v.strip() for v in fields.get(key, '').split(',') if v.strip()]
    return fields, text[m.end():]


def articles(folder=HELP):
    return {p.stem: front(p.read_text(encoding='utf-8')) for p in sorted(folder.glob('*.md'))}


def literal(path, name):
    for node in ast.parse((ROOT / path).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f'{path} has no {name}')


def features():
    """Every feature id the code has today."""
    html = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
    nav = re.search(r'<nav aria-label="Sections">(.*?)</nav>', html, re.S)[1]
    settings = re.search(r'const SETTINGS_TABS=(\[.*?\]);', html)[1]
    found = {f'connector:{s}' for s in literal('onboarding.py', 'SERVICES').values()}
    found |= {f'connector:{p.stem}' for p in (ROOT / 'connectors').glob('*.py') if 'SETUP' in p.read_text(encoding='utf-8')}
    found |= {f'tab:{t}' for t in re.findall(r'data-tab="([a-z]+)"', nav)}
    found |= {f'settings:{t}' for t in re.findall(r"\['([a-z]+)','", settings)}
    gone = [f for f, (path, needle) in FEATURES.items() if not (ROOT / path).is_file() or needle not in (ROOT / path).read_text(encoding='utf-8')]
    return found | (set(FEATURES) - set(gone)), gone


def app_text():
    return '\n'.join(p.read_text(encoding='utf-8', errors='replace') for pattern in TEXT_FILES for p in ROOT.glob(pattern)
                     if not p.name.startswith('test_'))


def commands():
    return set(re.findall(r"\badd\('([a-z-]+)'", (ROOT / 'cli.py').read_text(encoding='utf-8')))


def check_copy(name, fields, body, known):
    problems = []
    if '—' in body or '—' in ''.join(map(str, fields.values())):
        problems.append('uses an em dash')
    problems += [f'unknown placeholder {t} (one of {", ".join(sorted(TOKENS))})' for t in
                 set(re.findall(r'\{[^}\s]*\}', body + fields.get('summary', ''))) - TOKENS]
    if len(fields.get('summary', '')) > 160:
        problems.append('summary is over 160 characters')
    for paragraph in re.split(r'\n\s*\n', body):
        if len(paragraph) > 420 and not paragraph.lstrip().startswith(('- ', '1. ')):
            problems.append(f'paragraph over 420 characters: {paragraph[:50]}…')
    for line in body.splitlines():
        text = re.sub(r'^(#{2,3} |- |\d+\. )', '', line.strip())
        if re.match(r'(\*\*)?You\b', text):
            problems.append(f'line starts with "You": {line.strip()[:60]}')
        if re.match(r'#( |#{3,})', line) or line.startswith(('|', '<', '![', '>', '```', '* ')):
            problems.append(f'Markdown the site does not render: {line[:40]}')
    for target in re.findall(r'\]\(([^)]+)\)', body):
        if target.startswith(('https://', 'mailto:')):
            continue
        page = target.split('#')[0]
        if target.startswith('/'):
            if page not in SITE_PAGES:
                problems.append(f'links to {target}, not a page of the site')
        elif not page.endswith('.md') or page[:-3] not in known:
            problems.append(f'links to {target}, not an article')
    return problems


def check(folder=HELP):
    """The problems found, as 'article: what' lines."""
    found = articles(folder)
    exist, gone = features()
    text, cli = app_text(), commands()
    problems = [f'FEATURES: {f} is gone from {FEATURES[f][0]}: update or delete its article, and this entry' for f in gone]
    covered = {}
    for name, (fields, body) in found.items():
        where = f'docs/help/{name}.md'
        if fields is None:
            problems.append(f'{where}: no front matter')
            continue
        problems += [f'{where}: unknown field {k}' for k in set(fields) - FIELDS]
        problems += [f'{where}: needs {k}' for k in ('title', 'area', 'summary', 'code') if not fields.get(k)]
        if fields.get('area') not in AREAS:
            problems.append(f'{where}: area {fields.get("area")!r} is not one of {", ".join(AREAS)}')
        problems += [f'{where}: code path {p} does not exist' for p in fields['code'] if not (ROOT / p).exists()]
        for f in fields['covers']:
            if f not in exist:
                problems.append(f'{where}: covers {f}, which the code no longer has')
            covered.setdefault(f, name)
        for phrase in set(re.findall(r'\*\*(.+?)\*\*', body)):
            if phrase not in text and phrase not in fields['site']:
                problems.append(f'{where}: names "{phrase}", which the app no longer shows (or add it to site: if the hosted pages show it)')
        for code in set(re.findall(r'`([^`]+)`', body)):
            if code.startswith('/') and not code.startswith('//') and code.split('?')[0].rstrip('/') not in text and code not in fields['site']:
                problems.append(f'{where}: names the route {code}, which the code no longer has')
            m = re.match(r'assistant ([a-z-]+)', code)
            if m and m[1] not in cli:
                problems.append(f'{where}: names `assistant {m[1]}`, not a command of cli.py')
        problems += [f'{where}: {p}' for p in check_copy(name, fields, body, found)]
    html = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
    for tab, slug in re.findall(r"(\w+):'([a-z0-9-]+)'", (re.search(r'HELP_FOR=\{(.*?)\}', html) or [None, ''])[1]):
        if slug not in found:
            problems.append(f'dashboard.html: HELP_FOR sends {tab} to the help article {slug}, which docs/help/ does not have')
    for f in sorted(exist - set(covered) - set(PENDING)):
        problems.append(f'{f} has no help article: add one in docs/help/ with "covers: {f}" (or a reason in PENDING)')
    problems += [f'PENDING: {f} has an article now, so take it out of PENDING' for f in PENDING if f in covered]
    problems += [f'PENDING: {f} no longer exists' for f in PENDING if f not in exist]
    return problems


def main():
    problems = check()
    for p in problems:
        print(p)
    if problems:
        print(f'\n{len(problems)} problem(s) in the help articles (docs/help/). See scripts/help_check.py.')
        return 1
    print(f'{len(articles())} help articles, all in line with the code.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
