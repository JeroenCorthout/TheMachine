"""LinkedIn messages, for the account signed in to the OpenClaw browser.

Read-only through integrations/linkedin_inbox.py, which calls LinkedIn's own messaging API from a
tab of the signed-in OpenClaw browser parked on linkedin.com/robots.txt: no API key, no cookies
leaving the browser, and nothing marked as read. Sign in to LinkedIn once in that browser
(`openclaw browser --browser-profile openclaw open https://www.linkedin.com/login`). One account:

    {"id": "me-linkedin", "service": "linkedin", "transport": "linkedin-inbox", "boundary": "work:Acme",
     "poll_seconds": 3600}

A second LinkedIn account needs a browser profile of its own (a browser holds one LinkedIn sign-in):
give that account "browser_profile": "<OpenClaw profile>" (and "browser_cdp": "<its DevTools address>" when
read directly); without them the reader uses LINKEDIN_INBOX_BROWSER_PROFILE / _CDP, else "openclaw".

LinkedIn has no API for this, so reads stay rare and in waking hours. Optional: "active_hours":
[7, 22] (local time, the default) and config "linkedin_inbox" for the path to the reader. Replies
go out only as an owner-approved `linkedin` next action (next_actions.LinkedInTransport).
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

KINDS = {'linkedin.message': 'LinkedIn'}
# For `assistant connect linkedin`: the account it adds (see docs/adding-a-source.md).
SETUP = {'label': 'LinkedIn', 'what': 'Your LinkedIn messages.',
         'account': {'id': 'me-linkedin', 'service': 'linkedin', 'transport': 'linkedin-inbox', 'boundary': 'work', 'poll_seconds': 3600},
         # Read through the shared browser: a hosted install links to that browser on this site to sign in again.
         'browser': 'linkedin',
         # an account's own browser (a second account of the site): its fields, read into the reader's environment
         'browser_env': {'browser_profile': 'LINKEDIN_INBOX_BROWSER_PROFILE', 'browser_cdp': 'LINKEDIN_INBOX_BROWSER_CDP'},
         'hints': {'linkedin_signed_out': 'the browser it reads with is not signed in to LinkedIn; sign in there, then connect again'},
         'note': 'Sign in to LinkedIn once in the OpenClaw browser first: openclaw browser --browser-profile openclaw open https://www.linkedin.com/login'}
READER = Path(__file__).resolve().parent.parent / 'integrations/linkedin_inbox.py'


class Source:
    def __init__(self, config, account, helpers):
        self.config, self.account, self.helpers = config, account, helpers

    def run(self, command, timeout=480):
        SourceError = self.helpers.SourceError
        if command[:1] not in (['sync'], ['verify']):
            raise SourceError('unsupported_linkedin_method')
        try:
            result = subprocess.run([self.config.get('linkedin_inbox', str(READER)), *command], capture_output=True, text=True, timeout=timeout,
                                    env=browser_env(self.account))
        except subprocess.TimeoutExpired:
            raise SourceError('linkedin_read_timeout', retry_after=900) from None
        except OSError as error:
            raise SourceError('linkedin_reader_missing:' + type(error).__name__) from None
        if result.returncode:
            err = result.stderr.lower()
            if 'busy' in err:
                raise SourceError('linkedin_browser_busy', retry_after=600)
            if 'signed out' in err or 'not signed in' in err:
                raise SourceError('linkedin_signed_out', retry_after=3600)
            if 'in time' in err:
                raise SourceError('linkedin_read_timeout', retry_after=900)
            code = self.helpers.browser_read_failure('linkedin', result.stderr)
            if code.endswith('_browser_unreachable'):  # the browser crashed or hung: start it again, retry soon
                env = browser_env(self.account) or os.environ
                self.helpers.recover_browser(env.get('LINKEDIN_INBOX_BROWSER_PROFILE') or 'openclaw', own_cdp=bool(env.get('LINKEDIN_INBOX_BROWSER_CDP')), config=self.config)
                raise SourceError(code, retry_after=300)
            raise SourceError(code, retry_after=900)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise SourceError('linkedin_invalid_json') from None

    def page(self, since_ms, state):
        data = self.run(['sync', '--since', str(int(since_ms)), '--state', json.dumps(state)])
        if not isinstance(data.get('messages'), list) or not isinstance(data.get('state'), dict):
            raise self.helpers.SourceError('linkedin_invalid_page')
        return data


def browser_env(account):
    """The reader runs in this account's own browser, when it has one."""
    own = {var: account[field] for field, var in SETUP['browser_env'].items() if account.get(field)}
    return {**os.environ, **own} if own else None


def thread_url(conversation):
    match = re.search(r',([^,()]+)\)$', conversation or '')
    return 'https://www.linkedin.com/messaging/thread/' + urllib.parse.quote(match[1]) + '/' if match else 'https://www.linkedin.com/messaging/'


def event(account, raw, helpers):
    urn, conversation, at = raw.get('urn'), raw.get('conversation'), raw.get('at')
    if (not isinstance(urn, str) or not urn.startswith('urn:li:msg_message:') or not isinstance(conversation, str)
            or not conversation.startswith('urn:li:msg_conversation:') or isinstance(at, bool) or not isinstance(at, int)
            or type(raw.get('fromMe')) is not bool):
        raise helpers.SourceError('linkedin_invalid_message')
    body = raw.get('text') if isinstance(raw.get('text'), str) else ''
    if raw.get('subject'):
        body = 'Subject: ' + str(raw['subject']) + '\n\n' + body
    name = str(raw.get('with') or 'LinkedIn conversation')
    sender = 'You' if raw['fromMe'] else str(raw.get('from') or 'Unknown sender')
    metadata = {'chat_name': name, 'conversation_id': conversation, 'message_id': urn, 'from_me': raw['fromMe'],
                'from': sender, 'sender_display': sender, 'headline': raw.get('headline') or None,
                'group': raw.get('group') is True, 'categories': [str(c) for c in raw.get('categories') or []][:6],
                'source_url': thread_url(conversation), 'truncated': len(body) > 40000,
                'transport': 'linkedin_inbox_browser',
                'coverage': 'Paged LinkedIn inbox conversations and messages; read without marking them read.'}
    return {'account_id': account['id'], 'source_id': hashlib.sha256(urn.encode()).hexdigest(), 'kind': 'linkedin.message',
            'occurred_at': helpers.iso(at / 1000), 'title': name[:1000], 'body': body[:40000], 'metadata': metadata, 'deleted': False}


def events(account, data, helpers):
    return list({e['source_id']: e for e in (event(account, raw, helpers) for raw in data['messages'])}.values())


PHOTO = re.compile(r'https://media\.licdn\.com/dms/image/[^\s"\'<>\\]{10,600}')


def profiles(data):
    """What LinkedIn shows about the person in each one-to-one conversation of this page, from the participant it sent
    anyway: their headline, profile link and photo, in their own words."""
    out = []
    for urn, p in (data.get('profiles') or {}).items() if isinstance(data.get('profiles'), dict) else ():
        if not isinstance(p, dict) or not str(urn).startswith('urn:li:msg_conversation:'):
            continue
        photo = str(p.get('photo') or '')
        out.append({'ids': ['li:' + str(urn)], 'rank': 2, 'facts': {'headline': p.get('headline') or '', 'linkedin_url': p.get('url') or ''},
                    'photo': photo if PHOTO.fullmatch(photo) else None})
    return out


def sync(store, config, account, helpers, source=None):
    # Browser reads with no official API: keep them rare and in waking hours.
    current = dt.datetime.fromisoformat(helpers.now())
    start, end = account.get('active_hours', [7, 22])
    if not start <= current.astimezone(helpers.local_tz()).hour < end:
        return {'mode': 'incremental', 'events': 0}
    source = source or Source(config, account, helpers)
    state = store.checkpoint(account['id']) or {}
    # A scan freezes its lower bound until every conversation and message page has committed,
    # however long an outage lasts; only a completed scan moves it (with an hour of overlap).
    first = int((current - dt.timedelta(days=int(config.get('bootstrap_days', 7)))).timestamp() * 1000)
    since = state.get('scan_since_ms') or (max(0, state['completed_ms'] - 3600000) if state.get('completed_ms') else min(state.get('since_ms') or first, first))
    page = source.page(since, state.get('page') or {})
    found = events(account, page, helpers)
    latest = max([state.get('latest_ms', 0)] + [int(dt.datetime.fromisoformat(e['occurred_at']).timestamp() * 1000) for e in found])
    scan_until = state.get('scan_until_ms') or int(current.timestamp() * 1000)
    more = page.get('more') is True
    state.update(mode='incremental', latest_ms=latest, since_ms=since, read_messages=len(found), page=page['state'],
                 scan_since_ms=since if more else None, scan_until_ms=scan_until if more else None,
                 completed_ms=state.get('completed_ms', 0) if more else scan_until, coverage_complete=not more,
                 coverage='Paged inbox catch-up in progress.' if more else 'Inbox conversations and message pages caught up; spam excluded.')
    store.commit_page(account['id'], state, found)
    if getattr(helpers, 'remember_profiles', None):
        helpers.remember_profiles(store, 'linkedin', profiles(page))
    return {'mode': 'incremental', 'events': len(found), 'more': more}
