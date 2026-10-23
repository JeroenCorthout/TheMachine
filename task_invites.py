"""Coordinating with someone who is not on The Machine Network yet, by email (docs/network.md).

A handoff (and later follow-ups and planning) to a person outside the Network becomes an ordinary email draft on the
owner's card: the task in plain words. Nothing goes out until the owner reads it and presses Send. Only if the owner
also approves a task page on the card (task_pages.py) does the email get one plain line, "Status and replies: <link>";
never a promotional signature.

The page behind the link is sealed here with a fresh key that only travels in the link's #fragment: the Network keeps
an opaque blob for 7, 14 or 30 days and cannot read it. Their browser opens it with that key (and their new Machine
adds it). Answers from the page come back sealed with a key derived from it (`derived(key, 'reply')`); the Network gets
`derived(key, 'check')` only, to tell an answer from the page from one that is not.

Sealing uses only the standard library, so it opens in a browser with WebCrypto too: keys enc = HMAC(k, "enc") and
mac = HMAC(k, "mac"); the body is XORed with HMAC(enc, nonce || block counter) (a PRF in counter mode) and the whole is
authenticated with HMAC(mac, nonce || ciphertext): byte 1 (version), 16-byte nonce, ciphertext, 32-byte tag.

What the other Machine adds is their words, kept to a fixed form and shown quoted, never read as instructions."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

KINDS = ('handoff', 'followup', 'schedule', 'intro')
LINK = re.compile(r'https://[A-Za-z0-9.-]+(?::\d+)?/t/([A-Za-z0-9_-]{22})#k=([A-Za-z0-9_-]{43})')
REF = re.compile(r'([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})')
EMAIL = re.compile(r'[^@\s<>"]+@[^@\s<>"]+\.[A-Za-z]{2,}')
FIELDS = {'title': 120, 'summary': 1000, 'due': 10, 'from_name': 80, 'contact': 120}


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _keys(key):
    return hmac.new(key, b'enc', hashlib.sha256).digest(), hmac.new(key, b'mac', hashlib.sha256).digest()


def _stream(enc, nonce, size):
    return b''.join(hmac.new(enc, nonce + i.to_bytes(4, 'big'), hashlib.sha256).digest() for i in range((size + 31) // 32))[:size]


def seal(task, key=None):
    """(sealed, key) for a task dict: both base64url. The key goes only into the link's #fragment."""
    key = key or os.urandom(32)
    enc, mac = _keys(key)
    nonce, plain = os.urandom(16), json.dumps(task, ensure_ascii=False).encode()
    body = bytes(a ^ b for a, b in zip(plain, _stream(enc, nonce, len(plain))))
    tag = hmac.new(mac, nonce + body, hashlib.sha256).digest()
    return _b64(b'\x01' + nonce + body + tag), _b64(key)


def _open(sealed, key):
    raw, key = _unb64(sealed), _unb64(key)
    if len(raw) < 49 or raw[0] != 1 or len(key) != 32:
        raise ValueError('That task link is not one this Machine can open')
    enc, mac = _keys(key)
    nonce, body, tag = raw[1:17], raw[17:-32], raw[-32:]
    if not hmac.compare_digest(hmac.new(mac, nonce + body, hashlib.sha256).digest(), tag):
        raise ValueError('That task link is not one this Machine can open')
    return json.loads(bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body)))))


def unseal(sealed, key):
    return clean(_open(sealed, key))


def derived(key, name):
    """A key made from the link's key (base64url in, base64url out): 'reply' seals answers, 'check' goes to the Network."""
    return _b64(hmac.new(_unb64(key), name.encode(), hashlib.sha256).digest())


ANSWERS = {'on': "is on it", 'done': 'marked it done', 'cant': "can't do this"}
NOTE_CHARS = 280


def open_answer(sealed, key):
    """An answer from the task page, opened with the link's key and kept to its fixed form: a choice and a short note.
    Their words, shown quoted on the card, never read as instructions."""
    found = _open(sealed, derived(key, 'reply'))
    if not isinstance(found, dict) or found.get('answer') not in ANSWERS:
        raise ValueError('That answer is not one this Machine can open')
    note = ' '.join(str(found.get('note') or '').split())[:NOTE_CHARS] if isinstance(found.get('note'), str) else ''
    return {'answer': found['answer'], 'note': note}


PAGE_PARTS = ('summary', 'due', 'steps')  # what the owner may leave out; the title and their first name always go
STATUSES = ('waiting', 'in_progress', 'back', 'done')


def page(task, leave_out=(), status='waiting', updates=(), steps=()):
    """What the task page shows, and nothing else: built only from the approved task's fixed fields (clean), never
    from the card, its email, its evidence or the owner's other data. The privacy guard is this function's shape."""
    task = clean(task)
    out = {'kind': task['kind'], 'title': task['title'], 'from_name': first_name(task.get('from_name')) or 'Someone',
           'status': status if status in STATUSES else 'waiting', 'updates': [dict(u) for u in updates][-30:]}
    for part in ('summary', 'due'):
        if task.get(part) and part not in leave_out:
            out[part] = task[part]
    if steps and 'steps' not in leave_out:
        out['steps'] = [{'t': ' '.join(str(x.get('t', '')).split())[:120], 'done': bool(x.get('done'))} for x in steps if str(x.get('t', '')).strip()][:8]
    return out


def quotes(text, sources, run=12):
    """Whether `text` repeats `run` words in a row of any source text (the card's original email): such a summary is
    the email itself, which never goes on a task page."""
    words = lambda value: re.findall(r'[^\W_]+', str(value or '').casefold())
    mine = words(text)
    runs = {tuple(mine[i:i + run]) for i in range(len(mine) - run + 1)}
    for source in sources:
        theirs = words(source)
        if any(tuple(theirs[i:i + run]) in runs for i in range(len(theirs) - run + 1)):
            return True
    return False


def with_link(body, link):
    """The email with the task page's one plain line, just before the sign-off (or at the end)."""
    line = f'Status and replies: {link}'
    if line in body:
        return body
    i = body.rfind('\n\nBest,')
    return body[:i] + f'\n\n{line}' + body[i:] if i >= 0 else body.rstrip() + f'\n\n{line}'


def without_link(body):
    return re.sub(r'\n\nStatus and replies: https://\S+', '', body)


def clean(task):
    """A task kept to its fixed form: short plain fields, nothing else."""
    if not isinstance(task, dict):
        raise ValueError('That task link holds no task')
    out = {k: ' '.join(str(task[k]).split())[:n] for k, n in FIELDS.items() if isinstance(task.get(k), str) and task[k].strip()}
    out['kind'] = task.get('kind') if task.get('kind') in KINDS else 'handoff'
    if 'due' in out and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', out['due']):
        out.pop('due')
    if not out.get('title'):
        raise ValueError('That task link holds no task')
    return out


def parse_link(text):
    """(invite id, key) from a task link (…/t/<id>#k=<key>) or its short form <id>.<key>."""
    text = str(text or '').strip()
    found = LINK.search(text) or REF.fullmatch(text.removeprefix('#task='))
    if not found:
        raise ValueError('Paste the whole task link, with the part after the #')
    return found.group(1), found.group(2)


def first_name(name):
    return (str(name or '').split() or [''])[0]


DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')


def spoken_day(day, today=None):
    """A date as a person writes it in an email: "Saturday 31 October", with the year only when it is not this year."""
    found = dt.date.fromisoformat(day)
    year = '' if found.year == (today or dt.date.today()).year else f' {found.year}'
    return f'{DAYS[found.weekday()]} {found.day} {MONTHS[found.month - 1]}{year}'


def draft(task, to_name, me, link=None):
    """The email: the task in plain words. With a task page the owner approved, one plain line with its link. Never a
    promotional line or signature. Plain code, no model."""
    hello = f"Hi {first_name(to_name) or 'there'},"
    due = f"\n\nIt would be great to have it by {spoken_day(task['due'])}." if task.get('due') else ''
    subject = f"Could you take this on: {task['title']}"[:120]
    body = (f"{hello}\n\nCould you take this on?\n\n{task['title']}\n{task.get('summary', '')}{due}\n\n"
            f"Just reply to this email to let me know.\n\nBest,\n{first_name(me) or me}")
    return subject, with_link(body, link) if link else body


def network_url(config, fleet_module):
    found = fleet_module.settings(config)
    return (found or {}).get('url') or getattr(fleet_module, 'NETWORK', fleet_module.RELAY) + '/api/fleet'


def claim(base_url, invite_id, token=None, timeout=20):
    """The sealed task and who sent it, from the Network. No account needed: without the key it is unreadable."""
    headers = {'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})}
    request = urllib.request.Request(base_url.rstrip('/') + '/task-invite', method='POST', headers=headers,
                                     data=json.dumps({'action': 'claim', 'id': invite_id}).encode())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        with error:
            try:
                message = json.loads(error.read() or b'{}').get('error') or ''
            except ValueError:
                message = ''
        raise ValueError(message or f'The Network said {error.code}') from None
    except urllib.error.URLError:
        raise ValueError('The Network is not reachable right now; try again in a minute') from None
