#!/usr/bin/env python3
"""linkedin-inbox: LinkedIn messages through the signed-in OpenClaw browser.

Calls LinkedIn's own internal messaging API (Voyager) from inside a tab of the
openclaw Chrome profile, so no cookies leave the browser. The tab sits on
linkedin.com/robots.txt, never on /messaging/, so nothing is opened in the UI and no
conversation is marked as read. Reading never writes anything to LinkedIn.

  linkedin-inbox list [--unread] [--limit N] [--json]
  linkedin-inbox show N|NAME [--limit N] [--json]   (N = number from `list`)
  linkedin-inbox send (--to N|NAME | --profile SLUG) --text TEXT [--send-id ID] [--yes] [--json]
  linkedin-inbox sync --since MS                    (JSON for the personal assistant)
  linkedin-inbox verify --send-id ID                 (JSON: did that send land?)
  linkedin-inbox discover                           (refresh LinkedIn's query ids)

`send` only previews the resolved recipient unless --yes is given. Only run it with
--yes for a message the owner explicitly approved. Every send is logged before and after
in SENDS_LOG; a --send-id that already has a sent or uncertain entry is never sent again.

Holds the shared browser lease (integrations/browser-lease.py) while it runs and always
closes its own tab. Used by connectors/linkedin.py and next_actions.py.
"""
import argparse, json, os, subprocess, sys, time, uuid, re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_cdp  # noqa: E402
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import portable  # noqa: E402

LEASE = Path(__file__).resolve().parent / 'browser-lease.py'
OWNER = 'linkedin-inbox-' + str(os.getpid()) + '-' + uuid.uuid4().hex[:6]
PROFILE = os.environ.get('LINKEDIN_INBOX_BROWSER_PROFILE', 'openclaw')
# A DevTools address (e.g. http://127.0.0.1:9222) to talk to the browser directly instead of through OpenClaw.
CDP = os.environ.get('LINKEDIN_INBOX_BROWSER_CDP', '')
NEUTRAL_URL = 'https://www.linkedin.com/robots.txt'
# LinkedIn rotates these hashes now and then; `discover` refreshes them into IDS_FILE.
DEFAULT_IDS = {
    'conversations': 'messengerConversations.0d5e6781bbee71c3e51c8843c6519f48',
    'messages': 'messengerMessages.5846eeb71c981f11e0134cb6626cc314',
    'conversation_pages': 'messengerConversations.db23ac94a546670956b37f89cf64a070',
    'message_pages': 'messengerMessages.1561582483b8a511147478d8c099b03d',
}
IDS_FILE = Path.home() / '.config/linkedin-inbox/query-ids.json'
SENDS_LOG = Path.home() / '.local/share/linkedin-inbox/sends.jsonl'

JS = r"""
async () => {
  const P = __PARAMS__;
  if (location.host !== 'www.linkedin.com' || document.readyState === 'loading') return {error: 'not-ready'};
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1];
  if (!csrf) return {error: 'signed-out'};
  const h = {'csrf-token': csrf, 'accept': 'application/vnd.linkedin.normalized+json+2.1',
             'x-restli-protocol-version': '2.0.0'};
  const get = async (u) => { const r = await fetch(u, {headers: h, credentials: 'include', signal: AbortSignal.timeout(12000)});
    // Only 401 means signed out; a 403 can be a checkpoint or a refused query on a signed-in browser.
    if (r.status === 401) throw Object.assign(Error('signed-out'), {code: 'signed-out'});
    if (!r.ok) throw Object.assign(Error('http ' + r.status), {code: 'query-failed', status: r.status});
    return r.json(); };
  const gql = (id, vars) => get('/voyager/api/voyagerMessagingGraphQL/graphql?queryId=' + id + '&variables=' + vars);
  // Rest.li variables: URNs must have ( ) , percent-encoded too, or LinkedIn answers 400.
  const enc = (v) => encodeURIComponent(v).replace(/\(/g, '%28').replace(/\)/g, '%29');
  const txt = (t) => (t && typeof t === 'object') ? (t.text || '') : (t || '');
  try {
    const me = await get('/voyager/api/me');
    const meUrn = String(me.data && me.data['*miniProfile'] || '').replace('fs_miniProfile', 'fsd_profile');
    if (!meUrn.startsWith('urn:li:fsd_profile:')) return {error: 'signed-out'};
    const byUrn = {};
    const absorb = (j) => (j.included || []).forEach(x => { byUrn[x.entityUrn] = x; });
    const person = (pUrn) => { const p = byUrn[pUrn]; const m = p && p.participantType && (p.participantType.member || p.participantType.organization || p.participantType.custom);
      if (!m) return {name: 'Unknown', me: false};
      const name = [txt(m.firstName), txt(m.lastName)].filter(Boolean).join(' ') || txt(m.name) || 'Unknown';
      // Their profile link and photo come with the participant (no extra request): the artifact nearest 200 px.
      const pic = m.profilePicture || {}, arts = (pic.artifacts || []).filter(x => x && x.fileIdentifyingUrlPathSegment)
        .sort((x, y) => Math.abs((x.width || 0) - 200) - Math.abs((y.width || 0) - 200));
      return {name, headline: txt(m.headline), url: typeof m.profileUrl === 'string' ? m.profileUrl : '',
              photo: pic.rootUrl && arts[0] ? pic.rootUrl + arts[0].fileIdentifyingUrlPathSegment : '', me: pUrn.endsWith(meUrn)}; };
    const msg = (m) => { const s = person(m['*sender']);
      let body = txt(m.body);
      if (!body && m.renderContentFallbackText) body = m.renderContentFallbackText;
      if (!body && (m.renderContent || []).length) body = '[attachment]';
      return {urn: m.entityUrn, originToken: m.originToken || null, from: s.name, fromMe: s.me, at: m.deliveredAt, subject: txt(m.subject) || null, text: body}; };
    const root = j => j.data?.data || j.data || {};
    let conversationPage;
    if (P.mode === 'sync') {
      conversationPage=await gql(P.ids.conversation_pages,'(mailboxUrn:'+enc(meUrn)+',category:INBOX,count:20'+(P.state?.cursor?',nextCursor:'+enc(P.state.cursor):'')+')');
      if (!root(conversationPage).messengerConversationsByCategory?.metadata) throw Error('missing conversation paging metadata');
      absorb(conversationPage);
    } else absorb(await gql(P.ids.conversations, '(mailboxUrn:' + enc(meUrn) + ')'));
    let convs = Object.values(byUrn).filter(x => /messenger\.Conversation$/.test(x.$type))
      .sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))
      .map(c => { const others = (c['*conversationParticipants'] || []).map(person).filter(p => !p.me);
        const lastUrn = ((c.messages || {})['*elements'] || [])[0];
        return {urn: c.entityUrn, with: c.title || others.map(p => p.name).join(', ') || '(only you)',
          headline: others.length === 1 ? (others[0].headline || null) : null,
          profile: others.length === 1 && !c.groupChat ? {headline: others[0].headline || '', url: others[0].url, photo: others[0].photo} : null,
          unread: c.unreadCount || 0, read: !!c.read, lastActivityAt: c.lastActivityAt,
          categories: c.categories || [], group: !!c.groupChat,
          last: lastUrn && byUrn[lastUrn] ? msg(byUrn[lastUrn]) : null}; });
    if (P.mode === 'list') return {conversations: convs};
    if (P.mode === 'sync') {
      // One bounded page per invocation, with durable cursors supplied by SQLite.
      // Do not advance the conversation page until all its message pages commit.
      const out = [], progress={...(P.state?.progress||{})};
      // Bounded in time as well as pages: slow LinkedIn answers end the page early, never the whole read.
      const started=Date.now(), late=()=>Date.now()-started>30000;
      let budget=0, pending=false;
      for (const c of convs.filter(c => (c.lastActivityAt || 0) >= P.since && !c.categories.includes('SPAM'))) {
        if (progress[c.urn]?.done) continue;
        while (budget<5 && !(budget && late())) {
          const previous=progress[c.urn]?.cursor;
          const mj = await gql(P.ids.message_pages,'(conversationUrn:'+enc(c.urn)+',count:20'+(previous?',prevCursor:'+enc(previous):'')+')'); absorb(mj);budget++;
          const page=root(mj).messengerMessagesByConversation;
          if (!page?.metadata) throw Error('missing message paging metadata');
          const messages=(mj.included || []).filter(x => /messenger\.Message$/.test(x.$type)).map(msg);
          messages.filter(m=>(m.at||0)>=P.since).forEach(m=>out.push({...m,conversation:c.urn,with:c.with,headline:c.headline,group:c.group,categories:c.categories,unread:!c.read}));
          const cursor=page.metadata.prevCursor;
          const done=!cursor || messages.some(m=>(m.at||0)<P.since);
          if (cursor && cursor===previous && !done) throw Error('message cursor did not advance');
          progress[c.urn]={cursor:done?null:cursor,done};
          if(done)break;
        }
        if (!progress[c.urn]?.done) {pending=true;break;}
        if(budget>=5 || late()){pending=convs.some(c=>(c.lastActivityAt||0)>=P.since&&!c.categories.includes('SPAM')&&!progress[c.urn]?.done);break;}
      }
      const cursor=root(conversationPage).messengerConversationsByCategory.metadata.nextCursor;
      const reachedFloor=convs.some(c=>(c.lastActivityAt||0)<P.since);
      const more=pending || (!!cursor && !reachedFloor);
      if (!pending && cursor===P.state?.cursor && more) throw Error('conversation cursor did not advance');
      const profiles=Object.fromEntries(convs.filter(c=>c.profile&&out.some(m=>m.conversation===c.urn)).map(c=>[c.urn,c.profile]));
      return {messages:out.sort((a,b)=>(a.at||0)-(b.at||0)),more,profiles,
        state:{cursor:pending?(P.state?.cursor||null):(more?cursor:null),progress:pending?progress:{},coverage_complete:!more}};
    }
    if (P.mode === 'verify') {
      const mj = await gql(P.ids.messages, '(conversationUrn:' + enc(P.which) + ')'); absorb(mj);
      const hit = (mj.included || []).filter(x => /messenger\.Message$/.test(x.$type)).map(msg)
        .filter(m => m.fromMe && m.originToken === P.token && (m.text || '').trim() === P.text.trim());
      return {found: hit.map(m => m.urn)};
    }
    let pick = null;
    if (/^\d+$/.test(P.which)) pick = convs[Number(P.which) - 1];
    else { const q = P.which.toLowerCase(); pick = convs.find(c => c.with.toLowerCase().includes(q)); }
    if (!pick) return {error: 'no-match'};
    const mj = await gql(P.ids.messages, '(conversationUrn:' + enc(pick.urn) + ')'); absorb(mj);
    const msgs = (mj.included || []).filter(x => /messenger\.Message$/.test(x.$type)).map(msg)
      .sort((a, b) => (a.at || 0) - (b.at || 0));
    return {conversation: pick, messages: msgs};
  } catch (e) { return {error: e.code || 'failed', status: e.status || null, detail: String(e).slice(0, 200)}; }
}
"""

SEND_JS = r"""
async () => {
  const P = __PARAMS__;
  if (location.host !== 'www.linkedin.com' || document.readyState === 'loading') return {error: 'not-ready'};
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1];
  if (!csrf) return {error: 'signed-out'};
  const h = {'csrf-token': csrf, 'accept': 'application/vnd.linkedin.normalized+json+2.1',
             'x-restli-protocol-version': '2.0.0'};
  const get = async (u) => { const r = await fetch(u, {headers: h, credentials: 'include'});
    // Only 401 means signed out; a 403 can be a checkpoint or a refused query on a signed-in browser.
    if (r.status === 401) throw Object.assign(Error('signed-out'), {code: 'signed-out'});
    if (!r.ok) throw Object.assign(Error('http ' + r.status), {code: 'query-failed', status: r.status});
    return r.json(); };
  const enc = (v) => encodeURIComponent(v).replace(/\(/g, '%28').replace(/\)/g, '%29');
  const txt = (t) => (t && typeof t === 'object') ? (t.text || '') : (t || '');
  let mailboxUrn, target;
  try {
    const me = await get('/voyager/api/me');
    mailboxUrn = String(me.data && me.data['*miniProfile'] || '').replace('fs_miniProfile', 'fsd_profile');
    if (!mailboxUrn.startsWith('urn:li:fsd_profile:')) return {error: 'signed-out'};
    if (P.conversation) {
      const j = await get('/voyager/api/voyagerMessagingGraphQL/graphql?queryId=' + P.ids.messages +
        '&variables=(conversationUrn:' + enc(P.conversation) + ')');
      // Name the recipients from the conversation itself (recent list), else from who wrote in it.
      // Participant records in the message payload also include people merely mentioned or shared.
      const byUrn = {};
      [j, await get('/voyager/api/voyagerMessagingGraphQL/graphql?queryId=' + P.ids.conversations +
        '&variables=(mailboxUrn:' + enc(mailboxUrn) + ')')].forEach(r => (r.included || []).forEach(x => { byUrn[x.entityUrn] = x; }));
      const name = (pUrn) => { const p = byUrn[pUrn]; const m = p && p.participantType && (p.participantType.member || p.participantType.organization || p.participantType.custom);
        return m ? ([txt(m.firstName), txt(m.lastName)].filter(Boolean).join(' ') || txt(m.name)) : ''; };
      const conv = byUrn[P.conversation];
      const people = conv ? (conv.title ? [conv.title] : (conv['*conversationParticipants'] || []).filter(u => !u.endsWith(mailboxUrn)).map(name))
        : (j.included || []).filter(x => /messenger\.Message$/.test(x.$type) && x['*sender'] && !x['*sender'].endsWith(mailboxUrn)).map(m => name(m['*sender']));
      target = {conversationUrn: P.conversation, name: [...new Set(people.filter(Boolean))].join(', ') || 'this conversation'};
    } else if (P.profile) {
      const j = await get('/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=' + enc(P.profile));
      const p = (j.included || []).find(x => /fsd_profile:/.test(x.entityUrn || '') && x.publicIdentifier === P.profile);
      if (!p) return {error: 'no-match'};
      target = {recipientUrn: p.entityUrn, name: [txt(p.firstName), txt(p.lastName)].filter(Boolean).join(' '),
                headline: txt(p.headline) || null};
    } else {
      const byUrn = {};
      (await get('/voyager/api/voyagerMessagingGraphQL/graphql?queryId=' + P.ids.conversations +
        '&variables=(mailboxUrn:' + enc(mailboxUrn) + ')')).included.forEach(x => { byUrn[x.entityUrn] = x; });
      const name = (pUrn) => { const p = byUrn[pUrn]; const m = p && p.participantType && (p.participantType.member || p.participantType.organization || p.participantType.custom);
        return m ? ([txt(m.firstName), txt(m.lastName)].filter(Boolean).join(' ') || txt(m.name)) : ''; };
      const convs = Object.values(byUrn).filter(x => /messenger\.Conversation$/.test(x.$type))
        .sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))
        .map(c => ({urn: c.entityUrn, with: c.title || (c['*conversationParticipants'] || [])
          .filter(u => !u.endsWith(mailboxUrn)).map(name).join(', ')}));
      let hits;
      if (/^\d+$/.test(P.which)) hits = convs[Number(P.which) - 1] ? [convs[Number(P.which) - 1]] : [];
      else { const q = P.which.toLowerCase(); hits = convs.filter(c => c.with.toLowerCase().includes(q)); }
      if (!hits.length) return {error: 'no-match'};
      if (hits.length > 1) return {error: 'ambiguous', options: hits.map(c => c.with)};
      target = {conversationUrn: hits[0].urn, name: hits[0].with};
    }
  } catch (e) { return {error: e.code || 'failed', status: e.status || null, detail: String(e).slice(0, 200)}; }
  if (!P.send) return {preview: target};
  const tracking = String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16)));
  const message = {body: {attributes: [], text: P.text}, renderContentUnions: [], originToken: P.token};
  if (target.conversationUrn) message.conversationUrn = target.conversationUrn;
  const body = {message, mailboxUrn, trackingId: tracking, dedupeByClientGeneratedToken: false};
  if (target.recipientUrn) body.hostRecipientUrns = [target.recipientUrn];
  let r;
  try {
    r = await fetch('/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage',
      {method: 'POST', headers: {...h, 'content-type': 'text/plain;charset=UTF-8'}, credentials: 'include',
       body: JSON.stringify(body)});
  } catch (e) { return {sent: 'uncertain', target, detail: String(e).slice(0, 200)}; }
  const t = await r.text();
  if (!r.ok) return {sent: r.status >= 500 ? 'uncertain' : 'failed', status: r.status, target, detail: t.slice(0, 300)};
  const urn = (t.match(/urn:li:msg_message:[^"]+/) || [])[0] || null;
  const conv = (t.match(/urn:li:msg_conversation:[^"]+/) || [])[0] || target.conversationUrn || null;
  return {sent: 'sent', status: r.status, target, messageUrn: urn, conversationUrn: conv};
}
"""

DISCOVER_JS = r"""
() => { const out = {};
  performance.getEntriesByType('resource').forEach(e => { try { const id = new URL(e.name).searchParams.get('queryId') || '';
    if (id.startsWith('messengerConversations.')) out.conversations = id;
    if (id.startsWith('messengerMessages.')) out.messages = id; } catch (_) {} });
  return out; }
"""


class Fail(Exception):
    pass


def oc(*args, timeout=120):
    r = subprocess.run(['openclaw', 'browser', '--browser-profile', PROFILE, *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise Fail('browser command failed: ' + (r.stderr or r.stdout).strip()[:300])
    return r.stdout


def evaluate(label, js, timeout_ms=90000):
    if CDP:
        try:
            res = browser_cdp.evaluate(CDP, label, js, timeout_ms)
        except browser_cdp.CdpError as e:
            if 'context' in str(e):  # the tab was still navigating: same as a page that is not ready yet
                return {'error': 'not-ready'}
            raise Fail('browser command failed: ' + str(e)) from None
        if not isinstance(res, dict):
            raise Fail('unexpected browser output: ' + str(res)[:200])
        return res
    out = oc('evaluate', '--target-id', label, '--timeout-ms', str(timeout_ms), '--fn', js,
             timeout=timeout_ms // 1000 + 30)
    start = out.find('{') if out.lstrip().startswith('{') else out.find('\n{') + 1
    try:
        return json.loads(out[start:])
    except ValueError:
        raise Fail('unexpected browser output: ' + out.strip()[:200])


def lease(action):
    args = ['python3', str(LEASE), action, '--owner', OWNER] + ([] if action == 'release' else ['--minutes', '10'])
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


# per browser profile: each holds its own tabs (a second account reads in a browser of its own)
OPEN_TABS = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'the-machine' / ('linkedin-inbox-tabs' + ('' if PROFILE == 'openclaw' else '-' + PROFILE))


def tab_ids():
    try:
        if CDP:
            return browser_cdp.tab_ids(CDP)
        return set(re.findall(r'^\s*id:\s*(\S+)', oc('tabs', timeout=90), re.M))
    except Exception:
        return None


def remember_tab(target, opened):
    ids = set(OPEN_TABS.read_text().split()) if OPEN_TABS.exists() else set()
    ids = ids | {target} if opened else ids - {target}
    OPEN_TABS.parent.mkdir(parents=True, exist_ok=True)
    OPEN_TABS.write_text('\n'.join(sorted(ids)))


def close_tab(target):
    try:
        browser_cdp.close_tab(CDP, target) if CDP else oc('close', target, timeout=90)
    except Exception as e:  # never leave our tab behind silently
        print('warning: could not close tab ' + target + ': ' + str(e), file=sys.stderr)
        return False
    remember_tab(target, False)
    return True


def tab_inventory():
    if CDP:
        return browser_cdp._http(CDP, '/json/list')
    result = json.loads(oc('--json', 'tabs', timeout=90))
    return result.get('tabs', []) if isinstance(result, dict) else result


def recover_tab(marker):
    # Inventory differences also include tabs opened by unrelated concurrent jobs.
    # Only our persisted unique marker establishes ownership after a lost receipt.
    tag = marker[marker.index('#'):]
    for row in tab_inventory():
        if (row.get('url') or '').endswith(tag):
            return row.get('targetId') or row.get('id')
    return None


def open_tab(label, url):
    # The unique fragment marks the tab as ours if its receipt is lost. OpenClaw's gateway only
    # opens http(s) pages (about:blank#... is refused), so it rides on the real page's address there.
    tag = '#the-machine-' + uuid.uuid4().hex
    marker = 'about:blank' + tag if CDP else url.split('#')[0] + tag
    remember_tab('pending:' + marker, True)
    target = None
    try:
        if CDP:
            target = browser_cdp.open_tab(CDP, marker)
        else:
            receipt = oc('open', '--label', label, marker)
            match = re.search(r'^(?:id|tab):\s*(\S+)', receipt, re.M)
            target = match[1] if match else recover_tab(marker)
        if not target:
            raise Fail('The new browser tab has no verified receipt')
        remember_tab(target, True)
        remember_tab('pending:' + marker, False)
        if CDP:
            browser_cdp.navigate(CDP, target, url)
        return target
    except Exception:
        if target:
            close_tab(target)
        else:
            try:
                target = recover_tab(marker)
                if target:
                    close_tab(target)
                    remember_tab('pending:' + marker, False)
            except Exception:
                pass  # retain the exact marker for the next run; never guess ownership
        raise


def tab_guard():
    OPEN_TABS.parent.mkdir(parents=True, exist_ok=True)
    handle = OPEN_TABS.with_suffix('.lock').open('a')
    try:
        portable.lock(handle, blocking=False)
    except BlockingIOError:
        handle.close()
        raise Fail('This inbox reader already has a browser run in progress') from None
    return handle


def clean_stopped_tabs():
    for target in (OPEN_TABS.read_text().split() if OPEN_TABS.exists() else []):
        if target.startswith('pending:'):
            found = recover_tab(target[len('pending:'):])
            if not found or close_tab(found):
                remember_tab(target, False)
        elif not close_tab(target) and target not in (tab_ids() or {target}):
            remember_tab(target, False)


def with_tab(url, fn):
    guard = tab_guard()
    target, claimed = None, False
    try:
        claim = lease('claim')
        if claim.returncode == 3:
            raise Fail('the OpenClaw browser is busy (' + lease('status').stdout.strip() + '); try again later')
        if claim.returncode:
            raise Fail('could not claim the browser lease: ' + (claim.stderr or claim.stdout).strip()[:200])
        claimed = True
        clean_stopped_tabs()
        target = open_tab('linkedin-inbox-' + uuid.uuid4().hex[:6], url)
        return fn(target)
    finally:
        try:
            if target:
                close_tab(target)
            if claimed:
                lease('release')
        finally:
            guard.close()


def load_ids():
    ids = dict(DEFAULT_IDS)
    try:
        ids.update({k: v for k, v in json.loads(IDS_FILE.read_text()).items() if k in ids and v})
    except (OSError, ValueError):
        pass
    return ids


def run(mode, which='', since=0, text='', **extra):
    params = {'mode': mode, 'which': which, 'since': since, 'text': text, 'ids': load_ids(), **extra}
    js = JS.replace('__PARAMS__', json.dumps(params))
    res = with_tab(NEUTRAL_URL, lambda label: evaluate_when_ready(label, js))
    err = res.get('error')
    if err == 'signed-out':
        raise Fail('LinkedIn is signed out in the OpenClaw browser; sign in there once and retry')
    if err == 'no-match':
        raise Fail('no conversation matches "' + which + '" (run `linkedin-inbox list`)')
    if err == 'query-failed':
        raise Fail('LinkedIn rejected the query (HTTP ' + str(res.get('status')) + '); its internal ids may have '
                   'changed: run `linkedin-inbox discover` and retry')
    if err:
        raise Fail('LinkedIn read failed: ' + str(res.get('detail') or err))
    return res


def evaluate_when_ready(label, js):
    # `open` can return before the tab has committed to linkedin.com; the script reports that.
    for _ in range(10):
        res = evaluate(label, js)
        if res.get('error') != 'not-ready':
            return res
        time.sleep(1)
    raise Fail('the LinkedIn tab did not load in time; try again')


def when(ms):
    if not ms:
        return ''
    d = datetime.fromtimestamp(ms / 1000).astimezone()
    now = datetime.now().astimezone()
    if d.date() == now.date():
        return d.strftime('%H:%M')
    if (now.date() - d.date()).days < 7:
        return d.strftime('%a %H:%M')
    return d.strftime('%Y-%m-%d')


def one_line(s, n):
    s = ' '.join((s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def cmd_list(a):
    convs = run('list')['conversations']
    for i, c in enumerate(convs, 1):
        c['n'] = i
    if a.unread:
        convs = [c for c in convs if c['unread'] or not c['read']]
    convs = convs[:a.limit]
    if a.json:
        print(json.dumps(convs, ensure_ascii=False, indent=1))
        return
    if not convs:
        print('No unread LinkedIn messages.' if a.unread else 'No LinkedIn conversations.')
        return
    for c in convs:
        mark = '●' if (c['unread'] or not c['read']) else ' '
        last = c['last'] or {}
        who = 'You: ' if last.get('fromMe') else ''
        other = '' if 'PRIMARY_INBOX' in c['categories'] or 'INBOX' in c['categories'] else ' [' + '/'.join(c['categories']).lower() + ']'
        print(f"{c['n']:>2} {mark} {c['with']}{other}  ·  {when(c['lastActivityAt'])}")
        print('      ' + one_line(who + (last.get('text') or ''), 110))


def cmd_show(a):
    res = run('show', a.which)
    c, msgs = res['conversation'], res['messages'][-a.limit:]
    if a.json:
        print(json.dumps({'with': c['with'], 'headline': c['headline'], 'messages': msgs}, ensure_ascii=False, indent=1))
        return
    print(c['with'] + (' · ' + c['headline'] if c.get('headline') else ''))
    print()
    for m in msgs:
        print(('You' if m['fromMe'] else m['from']) + '  ·  ' + when(m['at']))
        if m.get('subject'):
            print('Subject: ' + m['subject'])
        print((m['text'] or '').strip())
        print()


def log_send(entry):
    SENDS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SENDS_LOG.open('a') as f:
        f.write(json.dumps({'at': datetime.now().astimezone().isoformat(timespec='seconds'), **entry}, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def send_entries():
    entries, damaged = [], False
    try:
        for line in SENDS_LOG.read_text().splitlines():
            if not line.strip(): continue
            try:
                entry=json.loads(line)
                if not isinstance(entry,dict): raise ValueError('not an object')
                entries.append(entry)
            except ValueError: damaged=True
    except OSError: damaged=True
    return entries, damaged


def prior_send(token):
    entries, damaged=send_entries()
    for e in reversed(entries):
        if e.get('token') == token and e.get('result') in ('sent', 'uncertain', 'intent'):
            return e
    if damaged and SENDS_LOG.exists():
        raise Fail('send history is unreadable or damaged; reconcile it before sending')
    return None


def cmd_send(a):
    # Serialize intent admission and the one write. Two invocations using the
    # same token cannot both pass the log check before either records intent.
    if not a.yes: return send_locked(a)
    SENDS_LOG.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (SENDS_LOG.parent/'send.lock').open('a') as lock:
        portable.lock(lock)
        return send_locked(a)


def send_locked(a):
    text = (a.text or '').strip()
    if not text:
        raise Fail('--text is empty')
    if len(text) > 8000:
        raise Fail('--text is longer than LinkedIn allows')
    token = a.token or str(uuid.uuid4())
    if a.yes:
        prev = prior_send(token)
        if prev:
            raise Fail('this send (token ' + token + ') was already attempted at ' + prev['at'] + ' with result "'
                       + prev['result'] + '"; not sending again')
    params = {'which': a.to or '', 'profile': a.profile or '', 'conversation': a.conversation or '', 'text': text, 'token': token,
              'send': False, 'ids': load_ids()}

    def go(label):
        res = evaluate_when_ready(label, SEND_JS.replace('__PARAMS__', json.dumps(params)))
        if not a.yes or res.get('error'):
            return res
        # Same tab, second call: preview resolved fine, now write once with the logged intent.
        log_send({'token': token, 'result': 'intent', 'to': res['preview'], 'text': text})
        try:
            out = evaluate(label, SEND_JS.replace('__PARAMS__', json.dumps({**params, 'send': True})))
        except Exception as e:
            out = {'sent': 'uncertain', 'detail': str(e)[:200]}
        log_send({'token': token, 'result': out.get('sent') or out.get('error') or 'uncertain',
                  'to': out.get('target') or res['preview'], 'status': out.get('status'),
                  'message': out.get('messageUrn'), 'conversation': out.get('conversationUrn'),
                  'detail': out.get('detail')})
        return out

    res = with_tab(NEUTRAL_URL, go)
    err = res.get('error')
    if err == 'signed-out':
        raise Fail('LinkedIn is signed out in the OpenClaw browser; sign in there once and retry')
    if err == 'no-match':
        raise Fail('no recipient matches; use a number or name from `linkedin-inbox list`, or --profile SLUG')
    if err == 'ambiguous':
        raise Fail('several conversations match: ' + '; '.join(res.get('options', [])[:8]))
    if err:
        raise Fail('LinkedIn lookup failed: ' + str(res.get('detail') or err))
    if a.json:
        print(json.dumps({'token': token, **res}, ensure_ascii=False, indent=1))
        if res.get('sent') not in (None, 'sent'):
            sys.exit(4)
    elif 'preview' in res:
        t = res['preview']
        print('Would send to ' + t['name'] + (' · ' + t['headline'] if t.get('headline') else '') + ':')
        print(text)
        print('(preview only; add --yes to send)')
    elif res.get('sent') == 'sent':
        print('Sent to ' + res['target']['name'] + '.')
    elif res.get('sent') == 'failed':
        raise Fail('LinkedIn refused the message (HTTP ' + str(res.get('status')) + '); nothing was sent')
    else:
        raise Fail('unclear whether the message was sent; check the conversation before retrying (token ' + token + ')')


def cmd_sync(a):
    state=json.loads(a.state or '{}')
    res = run('sync', since=a.since,state=state)
    print(json.dumps(res, ensure_ascii=False))


def cmd_verify(a):
    # Was an earlier send (by token) delivered? The send log first, then the conversation itself.
    entries, damaged=send_entries()
    mine = [e for e in entries if e.get('token') == a.token]
    done = next((e for e in reversed(mine) if e.get('result') == 'sent'), None)
    if done:
        print(json.dumps({'found': True, 'message': done.get('message'), 'via': 'log'}))
        return
    intent = next((e for e in mine if e.get('result') == 'intent'), None)
    if not intent:
        print(json.dumps({'found': False, 'attempted': None, 'history_damaged': damaged}))
        return
    conv = next((e.get('conversation') for e in reversed(mine) if e.get('conversation')), None) \
        or (intent.get('to') or {}).get('conversationUrn')
    if not conv:
        print(json.dumps({'found': False, 'attempted': True, 'checkable': False}))
        return
    # Text/time alone cannot prove a send: an older identical message may exist.
    # If the provider omits originToken, the outcome stays unknown, never resend.
    res = run('verify', which=conv, token=a.token, text=intent.get('text', ''))
    found = res.get('found') or []
    print(json.dumps({'found': len(found) == 1, 'message': found[0] if len(found) == 1 else None,
                      'attempted': True, 'matches': len(found)}))


def cmd_discover(a):
    print('Opening LinkedIn messaging once to read its current query ids '
          '(this opens your latest conversation, which LinkedIn marks as read).', file=sys.stderr)

    def grab(label):
        for _ in range(30):
            time.sleep(1)
            ids = evaluate(label, DISCOVER_JS, 10000)
            if ids.get('conversations') and ids.get('messages'):
                return ids
        return ids

    ids = with_tab('https://www.linkedin.com/messaging/', grab)
    if not ids.get('conversations'):
        raise Fail('could not find the messaging query ids; is LinkedIn signed in in the OpenClaw browser?')
    IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDS_FILE.write_text(json.dumps({**load_ids(), **ids}, indent=1) + '\n')
    print('Saved: ' + ', '.join(f'{k}={v}' for k, v in ids.items()))


def main():
    ap = argparse.ArgumentParser(prog='linkedin-inbox', description='LinkedIn messages via the OpenClaw browser.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list', help='recent conversations (newest first)')
    p.add_argument('--unread', action='store_true', help='only unread conversations')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser('show', help='messages in one conversation')
    p.add_argument('which', help='number from `list`, or part of the person\'s name')
    p.add_argument('--limit', type=int, default=20, help='last N messages')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser('send', help='send a message (preview unless --yes)')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--to', help='number or name from `list` (existing conversation)')
    g.add_argument('--conversation', help='exact conversation URN (as given by `sync`)')
    g.add_argument('--profile', help='LinkedIn profile slug, e.g. ada-lovelace from /in/ada-lovelace/')
    p.add_argument('--text', required=True)
    p.add_argument('--send-id', '--token', dest='token', help='idempotency key; an id already used is never sent again')
    p.add_argument('--yes', action='store_true', help='really send (only after explicit approval)')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser('sync', help='JSON: messages newer than --since (ms) in recently active conversations')
    p.add_argument('--since', type=int, required=True)
    p.add_argument('--state', help='committed page cursors from the assistant database')
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser('verify', help='JSON: did the send with this token land?')
    p.add_argument('--send-id', '--token', dest='token', required=True)
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser('discover', help='refresh LinkedIn query ids after LinkedIn changes them')
    p.set_defaults(fn=cmd_discover)
    a = ap.parse_args()
    try:
        a.fn(a)
    except Fail as e:
        print('linkedin-inbox: ' + str(e), file=sys.stderr)
        sys.exit(2)
    except subprocess.TimeoutExpired:
        print('linkedin-inbox: the browser did not answer in time; try again', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
