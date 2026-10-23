"""Signing in to a site in the assistant's browser from a small dialog on the dashboard, instead of the browser itself.

When a preparation or a source reader stops at a sign-in page, the owner types the username, password and, when the
page asks, a one-time code in the dashboard. The service puts them in that page over DevTools (CDP, loopback only)
and submits. This is its own code path: the agent and the model never see these values, nothing here logs them, and
errors never quote them.

"Save for next time" keeps a site's username and password in browser-logins.sealed in the data folder, sealed with the
message key (message_seal.py: the same key as the messages, so a hosted Machine keeps it off the disk, and no backup of
the disk holds a key that opens them). Without a message key (message protection turned off) they go in
browser-logins.enc with a key of their own next to it (browser-logins.key, readable by this user only), as before; once
there is a message key they move over and the old file and its key are overwritten and removed. Saved sign-ins are
listed and forgotten on the dashboard; a move (moving.py) only takes them along with a passphrase. One-time codes are
never saved.
"""
import datetime as dt, json, os, re, secrets, subprocess, sys, threading, time, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'integrations'))
import browser_cdp  # noqa: E402
import message_seal  # noqa: E402
import portable  # noqa: E402

LOOPBACK = ('127.0.0.1', 'localhost', '::1')
SEALED = 'browser-logins.sealed'  # sealed with the message key
STORE, KEY = 'browser-logins.enc', 'browser-logins.key'  # only without a message key, and what older versions kept
OPENSSL = 'openssl'
SETTLE = 10  # seconds a submitted page has to move on before the dialog says it is still there

# Finds the sign-in fields on the page: {u, p, c, next} (username, password, one-time code, a Next/Sign in button).
# Read-only, and never returns what is in a field.
_FIELDS = r'''
const fields = () => {
  const shown = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.disabled && !e.readOnly; };
  const words = e => [e.name, e.id, e.autocomplete, e.getAttribute('aria-label'), e.placeholder].join(' ').toLowerCase();
  const inputs = [...document.querySelectorAll('input')].filter(shown);
  const texts = inputs.filter(e => /^(text|email|tel|number|)$/.test(e.type));
  const p = inputs.find(e => e.type === 'password' && e.autocomplete !== 'new-password');
  const c = texts.find(e => e.autocomplete === 'one-time-code' || (/otp|totp|2fa|mfa|one.?time|verif|security.?code|auth.?code|two.?factor|passcode|\bcode\b|\bpin\b/.test(words(e))
    && !/zip|post|promo|coupon|country|area|voucher/.test(words(e))));
  const named = e => e.autocomplete === 'username' || e.type === 'email' || /user|login|e-?mail|identifier|account|phone/.test(words(e));
  let u = texts.find(e => e !== c && named(e) && (!p || !p.form || e.form === p.form));
  if (!u && p) u = texts.filter(e => e !== c && e.form === p.form && (e.compareDocumentPosition(p) & 4)).pop();
  const buttons = [...document.querySelectorAll('button, input[type=submit], [role=button]')].filter(shown);
  const next = buttons.find(b => /^\s*(next|continue|sign ?in|log ?in|submit|verify|weiter|volgende|suivant|inloggen|aanmelden|anmelden|connexion)\b/i
    .test(b.innerText || b.value || b.getAttribute('aria-label') || ''));
  if (!p && !c && !(u && next && texts.length <= 2)) return null;  // a username alone counts on a short "Next" page only
  return {u, p, c, next};
};
'''

# What the page itself says around a one-time code field, so the dialog can say which code it wants: `ask`, the site's
# own words before the field (its label, heading and instructions); `error`, a message it flags (role=alert, an
# invalid field's description); `others`, the links and buttons that switch method or send the code again. Visible
# text only, never what is in a field: inputs carry no text, and buttons, links and form controls are left out.
_WORDS = r'''
const clean = t => (t || '').replace(/\s+/g, ' ').trim();
const ALERT = '[role=alert],[aria-live=assertive],[aria-invalid=true],[class*=error i],[id*=error i],[class*=invalid i]';
const CONTROL = 'a,button,[role=button],[role=link],input,textarea,select,option,script,style,noscript,svg,template';
const shownText = e => { const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && e.getClientRects().length > 0; };
const inAlert = el => { const a = el.closest(ALERT); return !!a && !a.querySelector('input') && clean(a.innerText).length <= 300; };
// The visible text in `box`, per block (a paragraph, heading, label...): [{el, text}] in page order.
const blocks = box => {
  const found = new Map(), walk = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) {
    const el = n.parentElement;
    if (!clean(n.data) || !el || el.closest(CONTROL) || inAlert(el) || !shownText(el)) continue;
    const block = el.closest('p,h1,h2,h3,h4,h5,h6,label,legend,li,dt,dd,td,th,div,section,header,form,main,body') || el;
    found.set(block, (found.get(block) || '') + ' ' + n.data);
  }
  return [...found].map(([el, text]) => ({el, text: clean(text)}));
};
const asks = (c, f) => {
  const said = [], add = t => { t = clean(t); if (t && t.length <= 300 && !said.includes(t)) said.push(t); };
  for (const id of clean(c.getAttribute('aria-labelledby') + ' ' + c.getAttribute('aria-describedby')).split(' ')) {
    const e = id && id !== 'null' && document.getElementById(id); if (e && !inAlert(e) && shownText(e)) add(e.innerText);
  }
  let box = c.parentElement, before = [];
  for (let up = 0; box && up < 8; up++, box = box.parentElement) {  // widen until there are a few words before the field
    before = blocks(box).filter(b => b.el.compareDocumentPosition(c) & 4 || b.el.contains(c)).map(b => b.text);
    if (before.join(' ').split(' ').length >= 6 || box === document.body) break;
  }
  before.slice(-3).forEach(add);
  let text = said.join(' \u00b7 ');
  return text.length > 300 ? '\u2026' + text.slice(-299) : text;
};
const flagged = c => {
  const ids = clean(c.getAttribute('aria-describedby') + ' ' + c.getAttribute('aria-errormessage')).split(' ');
  const own = c.getAttribute('aria-invalid') === 'true' ? ids.map(id => id && document.getElementById(id)).filter(Boolean) : [];
  const all = [...own, ...document.querySelectorAll(ALERT)].filter(e => e.tagName !== 'INPUT' && shownText(e) && !e.querySelector('input'));
  const leaves = all.filter(e => clean(e.innerText).length > 2 && clean(e.innerText).length <= 300 && !all.some(o => o !== e && e.contains(o) && clean(o.innerText)));
  return leaves.length ? clean(leaves[0].innerText) : '';
};
// Another way to get the code, or a new one: the page's own links and buttons for it, on this site only. Never a
// button that submits the form with the code or password in it.
const OTHER = /\b(another|other|different|instead|alternative|switch|resend|re-send|new code|send (it |a |the )?(code )?again|send (a |the )?(new )?code|get (a )?(new )?code|didn.?t (get|receive)|did not (get|receive)|can.?t (use|access|get)|try (another|a different|again)|more options|use (a|an|your|my|the)\b|call me|text me|email me|by (phone|text|sms|email|call)|via (phone|text|sms|email))/i;
const others = f => {
  const shown = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && shownText(e) && !e.disabled; };
  const found = [];
  for (const e of document.querySelectorAll('a[href],button,[role=button],[role=link],input[type=button]')) {
    const label = clean(e.innerText || e.value || e.getAttribute('aria-label'));
    if (!label || label.length > 60 || found.some(o => o.label === label) || e === f.next || !shown(e) || !OTHER.test(label)) continue;
    if (/account|sign ?out|log ?out|password|cancel|privacy|terms|help\b/i.test(label)) continue;
    if (e.form && (e.type === 'submit' || e.type === 'image') && [f.c, f.p].some(x => x && x.form === e.form)) continue;
    if (e.tagName === 'A') { try { const u = new URL(e.getAttribute('href'), location.href);
      if (!/^(https?|javascript):$/.test(u.protocol) || (u.protocol !== 'javascript:' && u.origin !== location.origin)) continue; } catch (_) { continue; } }
    found.push({label, e});
    if (found.length >= 4) break;
  }
  return found;
};
'''

DETECT = '() => {' + _FIELDS + _WORDS + '''
  const f = fields();
  if (!f) return null;
  const found = {host: location.host, secure: location.protocol === 'https:', title: document.title.slice(0, 80),
                 username: !!f.u, password: !!f.p, code: !!f.c};
  if (f.c) {
    const meta = document.querySelector('meta[property="og:site_name"]');
    Object.assign(found, {ask: asks(f.c, f), error: flagged(f.c), others: others(f).map(o => o.label), step: location.pathname.slice(0, 200),
      field: [f.c.name, f.c.id, f.c.autocomplete, f.c.getAttribute('aria-label')].join(' ').slice(0, 200),
      name: meta ? clean(meta.content).slice(0, 40) : ''});
  }
  return found;
}'''

# Clicks one of `others` by its label, after checking the page is still on `host`. Never a field or a submit.
CHOOSE = 'function(host, label) {' + _FIELDS + _WORDS + '''
  if (location.host !== host) return 'moved';
  const f = fields(), pick = f && f.c && others(f).find(o => o.label === label);
  if (!pick) return 'missing';
  pick.e.scrollIntoView({block: 'center'}); pick.e.click();
  return 'clicked';
}'''

# Focuses one field after checking the page is still on `host`, and selects what is in it, so typing replaces it.
FOCUS = 'function(host, which) {' + _FIELDS + '''
  if (location.host !== host) return 'moved';
  const f = fields(), e = f && f[which];
  if (!e) return 'missing';
  e.scrollIntoView({block: 'center'}); e.focus();
  try { e.select(); } catch (_) { e.value = ''; }
  return 'ok';
}'''

SUBMIT = 'function(host, mark) {' + _FIELDS + '''
  if (location.host !== host) return 'moved';
  window.__machineLogin = mark;
  const f = fields() || {}, field = f.p || f.c || f.u, form = field && field.form;
  const button = (form && [...form.querySelectorAll('button:not([type=button]):not([type=reset]), input[type=submit]')].find(b => b.offsetParent)) || f.next;
  if (button) { button.click(); return 'clicked'; }
  if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return 'submitted'; }
  return 'enter';
}'''

# Still the same document, with the field filled last still filled, after the submit? Then empty the password field (without events: the site already has it)
# so it does not stay readable in the page.
SETTLED = 'function(mark, which, clear) {' + _FIELDS + '''
  if (window.__machineLogin !== mark) return 'moved';
  const f = fields(), e = f && f[which];
  if (!e || !e.value) return 'changed';
  if (clear && which === 'p') Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(e, '');
  return 'same';
}'''


def _loopback(base):
    parsed = urllib.parse.urlsplit(base)
    return parsed.scheme == 'http' and parsed.hostname in LOOPBACK


def endpoints(config):
    """The DevTools addresses of the assistant's browsers: the shared one and each source account's own. Loopback only."""
    found = []
    for base in [config.get('browser_cdp_url') or 'http://127.0.0.1:9222'] + [a.get('browser_cdp') for a in config.get('accounts', [])]:
        if isinstance(base, str) and _loopback(base) and base.rstrip('/') not in found:
            found.append(base.rstrip('/'))
    return found


def site(host):
    host = str(host or '').lower().split(':')[0]
    return host[4:] if host.startswith('www.') else host


def brand(host, name=''):
    """The site's name for the dialog: what the page calls itself (og:site_name), else its domain's own label
    (dashboard.stripe.com: Stripe; shop.example.co.uk: Example), else the host."""
    if name and len(name) <= 40:
        return name
    labels = site(host).split('.')
    if len(labels) < 2 or labels[-1].isdigit() or ':' in site(host):
        return site(host)
    label = labels[-3] if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3 else labels[-2]
    return label[:1].upper() + label[1:]


# Which code a page wants, by what it says. The page's own words decide, before what its field is called and its
# address: an authenticator app is named outright; a text message or an email is where it was sent (a masked number
# or address, "your phone", "your inbox"). General phrasing, never per site.
_MASK = '•*●·∙xX'
_PHONE_SENT = re.compile(r'(?i)(?:ending (?:in|with)|last (?:\w+ )?digits?(?: are| of)?)\s*:?\s*([\d]{2,4})\b|'
                         r'((?:\+\d{1,3}[\s.-]?)?(?:[(\[]?[' + _MASK + r'][' + _MASK + r'\s().\-]*)\d{2,4})\b')
_EMAIL_SENT = re.compile(r'[\w.+\-' + _MASK + r']*[\w' + _MASK + r']@[\w' + _MASK + r'.\-]+\.[a-z' + _MASK + r']{2,}', re.I)
_KINDS = (
    ('app', re.compile(r'(?i)\bauthenticat(?:or|ion) app|\bauthenticator\b|\btotp\b|\bauthy\b|code generator|'
                       r'\b(?:from|in|on|by|with) (?:the |your |an? )?(?:\w+ )?app\b|\bapp(?:lication)? (?:code|generates?)|generated by')),
    ('sms', re.compile(r'(?i)\bsms\b|\btext(?:ed| message)\b|\bby text\b|\bvia text\b|\btext(?:ed)? (?:you|it|a code)|'
                       r'\b(?:to|on) (?:your|the) (?:mobile|cell|phone)(?: number)?\b(?! app)|\bphone (?:number )?ending\b|\bmobile number\b')),
    ('email', re.compile(r'(?i)\be-?mail(?:ed)?\b|\binbox\b')),
    ('key', re.compile(r'(?i)\bsecurity key\b|\bpasskey\b|\byubikey\b|\bhardware key\b')),
)
_HINTS = (('app', re.compile(r'(?i)totp|authenticator|\bapp\b')), ('sms', re.compile(r'(?i)sms|phone|mobile')),
          ('email', re.compile(r'(?i)e-?mail')), ('key', re.compile(r'(?i)webauthn|security.?key|u2f')))


def _phone(ask):
    """(position, last digits) of a phone number the words say the code went to, masked as the site masks it."""
    for found in _PHONE_SENT.finditer(ask or ''):
        if found.group(1):
            return found.start(), found.group(1)
        if not any(c.isalpha() and c not in 'xX' for c in found.group(2)):
            return found.start(), re.findall(r'\d+', found.group(2))[-1]
    return None


def _email(ask):
    """(position, address) of an email address the words say the code went to: masked, or right after "to"."""
    for found in _EMAIL_SENT.finditer(ask or ''):
        if any(c in found.group(0) for c in '•*●·∙') or re.search(r'(?i)\bto\s*$', ask[:found.start()]):
            return found.start(), found.group(0)
    return None


def code_kind(ask, field='', step=''):
    """'sms', 'app', 'email', 'key' or 'unknown' for a one-time code page, from the site's own words (`ask`), then
    the code field's attributes and the address's path. An app named outright wins ("the authenticator app on your
    phone"); else the first channel the words name, where a destination shown masked (•••• 12, j•••@example.com)
    counts as naming it."""
    ask, found = ask or '', {}
    for kind, where in (('email', _email(ask)), ('sms', _phone(ask))):
        if where:
            found[kind] = where[0]
    for kind, pattern in _KINDS:
        hit = pattern.search(ask)
        if hit:
            found[kind] = min(found.get(kind, hit.start()), hit.start())
    if 'app' in found:
        return 'app'
    if found:
        return min(found, key=found.get)
    hints = (field or '') + ' ' + re.sub(r'[/_-]', ' ', step or '')
    return next((kind for kind, pattern in _HINTS if pattern.search(hints)), 'unknown')


def sent_to(ask, kind):
    """Where the site says it sent the code, as the site shows it (masked as it masks it), or ''."""
    where = _email(ask) if kind == 'email' else _phone(ask) if kind == 'sms' else None
    if not where:
        return ''
    return where[1] if kind == 'email' else 'ending in ' + where[1]


def describe(form):
    """A page from DETECT as the dialog shows it: for a code, which one (code_kind), the site's name and where it
    was sent. Drops what only served to tell."""
    if not isinstance(form, dict):
        return form
    form = dict(form)
    field, step = form.pop('field', ''), form.pop('step', '')
    form['brand'] = brand(form.get('host'), form.pop('name', ''))
    if form.get('code'):
        form['code_kind'] = code_kind(form.get('ask'), field, step)
        form['sent_to'] = sent_to(form.get('ask'), form['code_kind'])
    return form


def pages(config, store=None, account=None):
    """The open tabs that show a sign-in form: [{id, host, title, username, password, code, saved}]. Looks in the
    browser of `account` (an account id) when it has one of its own, else in the shared one: asking a browser that is
    not running can start it."""
    found, own = [], next((a.get('browser_cdp') for a in config.get('accounts', []) if a.get('id') == account), None)
    for i, base in enumerate(endpoints(config)):
        if base != (own.rstrip('/') if isinstance(own, str) and _loopback(own) else endpoints(config)[0]):
            continue
        try:
            tabs = [t for t in browser_cdp._http(base, '/json/list', timeout=3) if t.get('type') == 'page']
        except (browser_cdp.CdpError, TypeError, AttributeError):
            continue
        for tab in tabs:
            if urllib.parse.urlsplit(tab.get('url') or '').scheme not in ('http', 'https'):
                continue
            try:
                form = browser_cdp.evaluate(base, tab['id'], DETECT, timeout_ms=3000)
            except (browser_cdp.CdpError, OSError):
                continue
            if isinstance(form, dict) and form.get('host'):
                saved = store.get(form['host']) if store else None
                found.append({**describe(form), 'id': f'{i}:{tab["id"]}', 'site': site(form['host']),
                              'saved': {'username': saved['username']} if saved else None})
    return found


_filling = threading.Lock()
_store_locks = {}
_store_locks_guard = threading.Lock()


def fill(config, page, host, username=None, password=None, code=None, settle=SETTLE):
    """Types what the owner gave into the sign-in page `page` (an id from pages()) on `host`, and submits it.

    Returns {'result': 'next'|'done'|'approve'|'still_here', 'page': the sign-in form now shown, if any}. 'approve':
    no form left, but the tab is still at a sign-in step (approve it in the site's app); watch() says when it is past.
    Errors are plain ValueErrors that never contain a value given here."""
    index, _, target = str(page or '').partition(':')
    bases = endpoints(config)
    if not index.isdigit() or int(index) >= len(bases) or not target:
        raise ValueError('That page is not open in the browser any more')
    base, given = bases[int(index)], {'u': username, 'p': password, 'c': code}
    given = {k: v for k, v in given.items() if isinstance(v, str) and v}
    if not given:
        raise ValueError('Nothing to fill in')
    if any(len(v) > 1000 or '\n' in v or '\r' in v for v in given.values()):
        raise ValueError('That is too long, or has a line break')
    if not _filling.acquire(timeout=30):
        raise ValueError('Another sign-in is still going on')
    try:
        try:
            with browser_cdp.Session(base, target, timeout=20) as tab:
                form = tab.run('(' + DETECT + ')')
                if not form or form.get('host') != host:
                    raise ValueError(f'The page in the browser is no longer the sign-in to {site(host)}')
                if not form['secure'] and urllib.parse.urlsplit('//' + host).hostname not in LOOPBACK:
                    raise ValueError('That page is not on a secure (https) address; open the browser to sign in there')
                wanted = {k: v for k, v in given.items() if form[{'u': 'username', 'p': 'password', 'c': 'code'}[k]]}
                if not wanted:
                    raise ValueError('The page does not ask for that right now')
                tab.call('Page.bringToFront')
                for which, value in wanted.items():
                    if tab.run(FOCUS, host, which) != 'ok':
                        raise ValueError(f'The page in the browser is no longer the sign-in to {site(host)}')
                    tab.call('Input.insertText', {'text': value})
                mark, last = secrets.token_hex(8), 'p' if 'p' in wanted else 'c' if 'c' in wanted else 'u'
                how = tab.run(SUBMIT, host, mark)
                if how == 'moved':
                    raise ValueError(f'The page in the browser is no longer the sign-in to {site(host)}')
                if how == 'enter':
                    for kind in ('keyDown', 'keyUp'):
                        tab.call('Input.dispatchKeyEvent', {'type': kind, 'key': 'Enter', 'code': 'Enter',
                                                            'windowsVirtualKeyCode': 13, 'text': '\r' if kind == 'keyDown' else ''})
        except browser_cdp.CdpError:
            raise ValueError('The browser did not take that; open the browser to sign in there') from None
        state, until = 'same', time.monotonic() + settle
        while time.monotonic() < until:
            time.sleep(0.5)
            try:
                with browser_cdp.Session(base, target, timeout=5) as tab:
                    state = tab.run(SETTLED, mark, last, False)
            except browser_cdp.CdpError:
                state = 'moved'  # mid-navigation; look again
                continue
            if state != 'same':
                break
        if state == 'same':
            try:
                with browser_cdp.Session(base, target, timeout=5) as tab:
                    state = tab.run(SETTLED, mark, last, True)
            except browser_cdp.CdpError:
                pass
        time.sleep(1.5 if state != 'same' else 0)  # the next step's page settles
        shown = None
        try:
            shown = describe(browser_cdp.evaluate(base, target, DETECT, timeout_ms=3000))
        except browser_cdp.CdpError:
            raise ValueError('The browser did not answer after submitting; could not confirm the sign-in') from None
        if state == 'same':
            return {'result': 'still_here', 'page': shown and {**shown, 'id': page, 'site': site(shown['host'])}}
        if shown:
            return {'result': 'next', 'page': {**shown, 'id': page, 'site': site(shown['host'])}}
        if _checkpoint(base, target):  # nothing to type, but not in yet: approve it in the site's app, say
            return {'result': 'approve', 'page': {'id': page, 'host': host, 'site': site(host)}}
        return {'result': 'done', 'page': None}
    finally:
        _filling.release()


def choose(config, page, host, label, settle=2.0):
    """Clicks one of the page's own "another way" links or buttons (`others`: use the authenticator app instead,
    resend the code), by its label, and returns what it shows then, like fill(): {'result': 'next', 'page'} with a
    sign-in form, else {'result': 'elsewhere', 'page': None} (finish in the browser). Types nothing and submits no
    field."""
    index, _, target = str(page or '').partition(':')
    bases = endpoints(config)
    if not index.isdigit() or int(index) >= len(bases) or not target:
        raise ValueError('That page is not open in the browser any more')
    if not isinstance(label, str) or not label or len(label) > 60:
        raise ValueError('Which one?')
    base = bases[int(index)]
    if not _filling.acquire(timeout=30):
        raise ValueError('Another sign-in is still going on')
    try:
        try:
            with browser_cdp.Session(base, target, timeout=20) as tab:
                form = tab.run('(' + DETECT + ')')
                if not form or form.get('host') != host:
                    raise ValueError(f'The page in the browser is no longer the sign-in to {site(host)}')
                if tab.run(CHOOSE, host, label) != 'clicked':
                    raise ValueError(f'{brand(host)} doesn’t show “{label}” any more; open the browser to see what it shows')
        except browser_cdp.CdpError:
            raise ValueError('The browser did not take that; open the browser to sign in there') from None
        shown, until = None, time.monotonic() + settle + 4
        time.sleep(settle)
        while True:
            try:
                shown = describe(browser_cdp.evaluate(base, target, DETECT, timeout_ms=3000))
                break
            except browser_cdp.CdpError:  # mid-navigation
                if time.monotonic() > until:
                    break
                time.sleep(0.5)
        if shown:
            return {'result': 'next', 'page': {**shown, 'id': page, 'site': site(shown['host'])}}
        return {'result': 'elsewhere', 'page': None}
    finally:
        _filling.release()


# A tab address that is still part of signing in, with nothing to type: approving it in the site's own app (LinkedIn's
# checkpoint), a two-step or verification page, a challenge.
CHECKPOINT = re.compile(r'(?i)checkpoint|challenge|/2fa|/mfa|two.?(step|factor)|verif|approv|log-?in|sign-?in|/uas/|/auth\b')
ADDRESS = '() => location.pathname + location.search'


def _checkpoint(base, target):
    try:
        return bool(CHECKPOINT.search(str(browser_cdp.evaluate(base, target, ADDRESS, timeout_ms=3000) or '')))
    except (browser_cdp.CdpError, OSError):
        return False


def watch(config, page):
    """How the sign-in in tab `page` (an id from pages()) is going, after the dialog sent it: {'state': 'form', 'page'}
    when the page asks for something to type, 'waiting' while it waits on the owner elsewhere (their phone's app),
    'done' once past it. Read-only: which fields show and the tab's address, never what is in them."""
    index, _, target = str(page or '').partition(':')
    bases = endpoints(config)
    if not index.isdigit() or int(index) >= len(bases) or not target:
        raise ValueError('That page is not open in the browser any more')
    base = bases[int(index)]
    try:
        if not any(t.get('id') == target for t in browser_cdp._http(base, '/json/list', timeout=3)):
            raise ValueError('That page is not open in the browser any more')
        shown = describe(browser_cdp.evaluate(base, target, DETECT, timeout_ms=3000))
    except (browser_cdp.CdpError, OSError, TypeError, AttributeError):
        return {'state': 'waiting'}  # mid-navigation, or the browser is slow: look again
    if isinstance(shown, dict) and shown.get('host'):
        return {'state': 'form', 'page': {**shown, 'id': page, 'site': site(shown['host'])}}
    return {'state': 'waiting' if _checkpoint(base, target) else 'done'}


class LoginStore:
    """Saved sign-ins, {site: {username, password, saved_at}}, encrypted at rest: sealed with the message key (SEALED),
    or, without one, AES-256 with openssl and a key in a file of its own (STORE, KEY). Only the service reads them;
    list() never returns a password, and no error quotes one."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.sealed_path = self.folder / SEALED
        self.path, self.key_path = self.folder / STORE, self.folder / KEY
        # API requests construct separate stores, but their read/change/write must share one lock.
        with _store_locks_guard:
            self.lock = _store_locks.setdefault(str(self.sealed_path.resolve()), threading.Lock())

    def _message_key(self):
        """The message key next to the database in this folder (a link to memory on a hosted Machine), or None."""
        try:
            return message_seal.load_key(self.sealed_path)  # the key of anything in this folder: its key_path
        except (OSError, ValueError):
            return None

    def _key(self):
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(secrets.token_hex(32))
        return self.key_path.read_text().strip()

    def _openssl(self, data, decrypt):
        run = subprocess.run([OPENSSL, 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '100000', '-md', 'sha256', '-salt',
                              *(['-d'] if decrypt else []), '-pass', 'env:MACHINE_LOGIN_KEY'],
                             input=data, capture_output=True, env={**os.environ, 'MACHINE_LOGIN_KEY': self._key()})
        if run.returncode:
            raise ValueError('Saved sign-ins could not be ' + ('read' if decrypt else 'saved'))
        return run.stdout

    def _read(self):
        key = self._message_key()
        if self.sealed_path.exists():
            data = self.sealed_path.read_bytes()
            if message_seal.is_sealed_bytes(data):
                if not key:
                    raise ValueError('Saved sign-ins are encrypted with the message key, which is not here '
                                     + ('yet' if message_seal.held_elsewhere(self.sealed_path) else 'any more'))
                try:
                    return json.loads(message_seal.Sealer(key).open_bytes(data))
                except message_seal.SealError:
                    raise ValueError('Saved sign-ins could not be read') from None
            logins = json.loads(data)  # opened by an export and put here by an import: encrypted again at once
            self._write(logins)
            return logins
        if self.path.exists():
            logins = json.loads(self._openssl(self.path.read_bytes(), True))
            if key:  # a message key now: over to it, and the old file and its own key go
                self._write(logins)
            return logins
        return {}

    def _write(self, logins):
        key = self._message_key()
        if key:
            target, data = self.sealed_path, message_seal.Sealer(key).seal_bytes(json.dumps(logins).encode())
        elif message_seal.held_elsewhere(self.sealed_path):
            raise ValueError('Saved sign-ins wait for the message key ' + ('from your keyring' if message_seal.in_keyring(self.sealed_path) else 'your hosting keeps')
                             + '; try again in a minute')
        else:
            target, data = self.path, self._openssl(json.dumps(logins).encode(), False)
        temp = target.with_name(target.name + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        for older in ((self.path, self.key_path) if key else (self.sealed_path,)):
            portable.shred(older)

    def migrate(self):
        """At the service's start: sign-ins in the older file (or opened by an import) go over to the message key.
        Returns whether anything moved."""
        with self.lock:
            if not self._message_key():
                return False
            plain = self.sealed_path.exists() and not message_seal.is_sealed_file(self.sealed_path)
            if not (plain or self.path.exists()):
                return False
            self._read()
            return True

    def get(self, host):
        with self.lock:
            return self._read().get(site(host))

    def save(self, host, username=None, password=None):
        with self.lock:
            logins = self._read()
            entry = logins.get(site(host)) or {}
            if username:
                entry['username'] = username
            if password:
                entry['password'] = password
            entry['saved_at'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            logins[site(host)] = entry
            self._write(logins)

    def list(self):
        with self.lock:
            # whether a password is kept, never the password or anything from it (not even its length)
            return sorted(({'site': k, 'username': v.get('username') or '', 'saved_at': v.get('saved_at'),
                            'password_saved': bool(v.get('password'))}
                           for k, v in self._read().items()), key=lambda x: x['site'])

    def forget(self, host):
        with self.lock:
            logins = self._read()
            gone = logins.pop(site(host), None) is not None
            if gone:
                self._write(logins)
            return gone
