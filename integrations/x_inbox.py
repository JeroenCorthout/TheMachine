#!/usr/bin/env python3
"""x-inbox: X (Twitter) mentions, replies and DMs through the signed-in OpenClaw browser.

Works through the owner's own signed-in x.com session: a tab of the openclaw Chrome profile makes
the same requests x.com itself makes (not X's developer API; no API key), so no cookies leave the
browser. The tab sits on x.com/robots.txt, so nothing opens in the UI, and nothing is marked as
read or seen. Reading never posts, likes, follows or sends anything.

Several accounts can be signed in at once (X's account switcher). To read another one the
script switches to it and always switches back to the primary account afterwards, also after
an error.

  x-inbox accounts [--json]                          (signed-in accounts)
  x-inbox mentions --handle H [--primary P] [--limit N] [--json]
  x-inbox dms --handle H [--primary P] [--limit N] [--json]
  x-inbox sync --handle H --primary P --since MS --state JSON   (JSON for the personal assistant)
  x-inbox send --handle H --primary P (--reply-to ID | --conversation ID) --text T [--long] [--send-id ID] [--check] [--yes] [--json]
  x-inbox verify --send-id ID                         (JSON: did that send land?)

`send` only previews (the account, and the post replied to or the conversation partner)
unless --yes is given; --check also opens X's reply composer with the text and checks it,
without posting. Only run it with --yes for a reply or message the owner explicitly approved.
A reply goes through X's own composer (x.com/intent/post), so X builds the request itself; the
tab lets out only that one request, with exactly the approved text and post. A DM goes through
the DM inbox API (dm/new2), like x.com does. Every send is logged before and after in SENDS_LOG;
a --send-id that already has an entry is never sent again.

Encrypted X Chat conversations aren't read or sent to; only the DM inbox is. Holds the
browser lease (exclusive during account selection/restoration) and always closes its own tab. Used
by connectors/x.py and next_actions.XTransport.
"""
import argparse, importlib.util, json, re, subprocess, sys, time, unicodedata, urllib.parse, uuid, os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_cdp  # noqa: E402
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import portable  # noqa: E402

LEASE = Path(__file__).resolve().parent / 'browser-lease.py'
OWNER = 'x-inbox-' + str(os.getpid()) + '-' + uuid.uuid4().hex[:6]
PROFILE = os.environ.get('X_INBOX_BROWSER_PROFILE', 'openclaw')
# A DevTools address (e.g. http://127.0.0.1:9222) to talk to the browser directly instead of through OpenClaw.
CDP = os.environ.get('X_INBOX_BROWSER_CDP', '')
NEUTRAL_URL = 'https://x.com/robots.txt'
# An account switch changes the cookies of every x.com tab in that browser, so x-inbox runs go
# one at a time, and a run that has to switch waits while another job holds the browser.
RUN_LOCK = Path.home() / '.local/state/x-inbox/run.lock'
LEASE_API = None
SENDS_LOG = Path.home() / '.local/share/x-inbox/sends.jsonl'
REPLY_LIMIT, LONG_LIMIT, DM_LIMIT = 280, 25000, 10000  # long posts need X Premium on that account

JS = r"""
async () => {
  const P = __PARAMS__;
  if (location.host !== 'x.com' || document.readyState === 'loading') return {error: 'not-ready'};
  const cookie = (n) => [...document.cookie.matchAll(new RegExp('(?:^|; )' + n + '=([^;]+)', 'g'))].map(m => m[1]).pop();
  if (!cookie('ct0') || !cookie('twid')) return {error: 'signed-out'};
  const me = () => decodeURIComponent(cookie('twid') || '').replace(/"/g, '').replace('u=', '');
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const fail = (code, extra) => Object.assign(Error(code), {code, ...extra});
  // The web app's public client token, read from its own script so a rotation doesn't break us.
  const html = await (await fetch('/', {credentials: 'include'})).text();
  const main = (html.match(/https:\/\/abs\.twimg\.com\/responsive-web\/client-web[^"]*\/main\.[a-z0-9]+\.js/) || [])[0];
  const bearer = main && ((await (await fetch(main)).text()).match(/"(AAAAAAAAAAAAAAAAAAAAA[A-Za-z0-9%]+)"/) || [])[1];
  if (!bearer) return {error: 'query-failed', detail: 'web client token not found'};
  const headers = () => ({authorization: 'Bearer ' + bearer, 'x-csrf-token': cookie('ct0'), 'x-twitter-active-user': 'yes', 'x-twitter-auth-type': 'OAuth2Session'});
  const call = async (path, opts = {}) => {
    for (let i = 0; ; i++) {
      const r = await fetch(path, {credentials: 'include', ...opts, headers: {...headers(), ...(opts.headers || {})}, signal: AbortSignal.timeout(15000)});
      if (r.ok) return r.json();
      // Right after an account switch the new csrf cookie takes a moment to apply.
      if ((r.status === 403 || r.status === 401) && i < 4) { await sleep(1500); continue; }
      if (r.status === 429) throw fail('rate-limited');
      if (r.status === 401) throw fail('signed-out');
      throw fail('query-failed', {status: r.status});
    }
  };
  const accounts = ((await call('/i/api/1.1/account/multi/list.json')).users || []).map(u => ({id: String(u.user_id), screen_name: u.screen_name, name: u.name}));
  if (P.mode === 'accounts') return {accounts, active: me()};
  const find = (h) => accounts.find(a => a.screen_name.toLowerCase() === String(h || '').replace('@', '').toLowerCase());
  const target = find(P.handle), primary = find(P.primary) || accounts.find(a => a.id === me());
  if (!target) return {error: 'not-signed-in', accounts: accounts.map(a => a.screen_name)};
  const switchTo = async (id) => {
    if (me() === id) return;
    await call('/i/api/1.1/account/multi/switch.json', {method: 'POST', headers: {'content-type': 'application/x-www-form-urlencoded'}, body: 'user_id=' + id});
    for (let i = 0; i < 20 && me() !== id; i++) await sleep(250);
    if (me() !== id) throw fail('switch-failed');
  };
  const newer = (a, b) => a.length !== b.length ? a.length > b.length : a > b;
  const expand = (t) => {
    let text = t.full_text || t.text || '';
    for (const u of (t.entities && t.entities.urls) || []) if (u.url && u.expanded_url) text = text.split(u.url).join(u.expanded_url);
    for (const m of (t.extended_entities || t.entities || {}).media || []) if (m.url) text = text.split(m.url).join('').trim() + ' [' + (m.type || 'media') + ']';
    return text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();
  };
  const out = {account: {id: target.id, screen_name: target.screen_name}, mentions: [], dms: [], profiles: {}, state: {...(P.state || {})}};
  // What their profile says, from the user objects these answers carry anyway (no extra request): bio, place, website, photo.
  const keep = (id, u) => {
    if (!id || id === target.id || !u || out.profiles[id]) return;
    let bio = u.description || '';
    for (const x of ((u.entities || {}).description || {}).urls || []) if (x.url && x.expanded_url) bio = bio.split(x.url).join(x.expanded_url);
    out.profiles[id] = {screen_name: u.screen_name || '', bio, location: u.location || '',
      website: ((((u.entities || {}).url || {}).urls || []).map(x => x.expanded_url).find(Boolean)) || '',
      photo: u.default_profile_image ? '' : (u.profile_image_url_https || '')};
  };
  try {
    await switchTo(target.id);
    // Mentions and replies: the Notifications → Mentions tab. Reading doesn't mark them seen.
    const backlog = P.state.mention_catchup || {};
    let cursor = backlog.cursor || null, sinceId = String(backlog.since_id || P.state.mention_since_id || '0'), top = String(backlog.top || sinceId);
    const sinceMs = backlog.since_ms ?? P.since;
    let mentionsDone = false;
    for (let page = 0; page < 4; page++) {
      const j = await call('/i/api/2/notifications/mentions.json?tweet_mode=extended&include_ext_alt_text=true&count=40' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
      const tweets = (j.globalObjects || {}).tweets || {}, users = (j.globalObjects || {}).users || {};
      let oldest = Infinity, reachedKnown = false;
      for (const t of Object.values(tweets)) {
        const mentioned = t.in_reply_to_user_id_str === target.id || ((t.entities || {}).user_mentions || []).some(m => m.id_str === target.id);
        if (t.user_id_str === target.id || !mentioned) continue;
        const at = Date.parse(t.created_at); oldest = Math.min(oldest, at);
        if (!newer(t.id_str, sinceId)) { reachedKnown = true; continue; }
        if (at < sinceMs) continue;
        if (newer(t.id_str, top)) top = t.id_str;
        const u = users[t.user_id_str] || {}, parent = t.in_reply_to_status_id_str && tweets[t.in_reply_to_status_id_str];
        keep(t.user_id_str, users[t.user_id_str]);
        out.mentions.push({id: t.id_str, at, text: expand(t), lang: t.lang, conversation_id: t.conversation_id_str,
          author: {id: t.user_id_str, name: u.name || '', screen_name: u.screen_name || '', followers: u.followers_count || 0, verified: !!(u.verified || u.ext_is_blue_verified)},
          reply_to: t.in_reply_to_status_id_str ? {id: t.in_reply_to_status_id_str, screen_name: t.in_reply_to_screen_name || '', to_account: t.in_reply_to_user_id_str === target.id,
            text: parent ? expand(parent).slice(0, 600) : null} : null,
          quoted: t.quoted_status_id_str && tweets[t.quoted_status_id_str] ? expand(tweets[t.quoted_status_id_str]).slice(0, 600) : null});
      }
      const entries = JSON.stringify(j.timeline || {});
      cursor = (entries.match(/"cursor":\{"value":"([^"]+)","cursorType":"Bottom"/) || [])[1];
      // Stop once a page reaches what was read before or the start of the window.
      if (!cursor || !Object.keys(tweets).length || reachedKnown || oldest < sinceMs) { mentionsDone = true; break; }
    }
    if (mentionsDone) { out.state.mention_since_id = top; delete out.state.mention_catchup; }
    else out.state.mention_catchup = {cursor, since_id: sinceId, top, since_ms: sinceMs};
    // DMs: the DM inbox (not X Chat, which is end-to-end encrypted). Reading doesn't mark anything read.
    const q = 'include_groups=true&include_inbox_timelines=true&dm_users=true&include_conversation_info=true&count=50';
    let box = null;
    if (P.state.dm_cursor) {
      try { box = (await call('/i/api/1.1/dm/user_updates.json?' + q + '&cursor=' + encodeURIComponent(P.state.dm_cursor))).user_events; } catch (e) { if (e.code !== 'query-failed') throw e; }
    }
    if (!box) box = (await call('/i/api/1.1/dm/inbox_initial_state.json?' + q)).inbox_initial_state;
    const dmUsers = box.users || {}, convs = box.conversations || {};
    const person = (id) => { const u = dmUsers[id] || {}; return {id, name: u.name || '', screen_name: u.screen_name || ''}; };
    for (const e of box.entries || []) {
      const m = e.message && e.message.message_data;
      if (!m || +m.time < P.since) continue;
      const c = convs[e.message.conversation_id] || {};
      const others = (c.participants || []).map(p => p.user_id).filter(id => id !== target.id);
      if (c.type !== 'GROUP_DM') others.forEach(id => keep(id, dmUsers[id]));
      out.dms.push({id: m.id, at: +m.time, conversation_id: e.message.conversation_id, from_me: m.sender_id === target.id, from: person(m.sender_id),
        with: c.name || others.map(id => person(id).name || '@' + person(id).screen_name).join(', ') || person(m.sender_id === target.id ? m.recipient_id : m.sender_id).name,
        group: c.type === 'GROUP_DM', request: c.trusted === false, text: expand(m) + (m.attachment ? (m.text ? ' ' : '') + '[attachment]' : '')});
    }
    if (box.cursor) out.state.dm_cursor = box.cursor;
  } catch (e) {
    out.error = e.code || 'query-failed'; out.status = e.status; out.detail = String(e.message || e).slice(0, 200);
  } finally {
    try { await switchTo((primary || target).id); } catch (e) { out.switch_back = 'failed'; }
  }
  return out;
}
"""


# Sending. Resolve, DM, verify and switch back run here on the neutral tab.
SEND_JS = r"""
async () => {
  const P = __PARAMS__;
  if (location.host !== 'x.com' || document.readyState === 'loading') return {error: 'not-ready'};
  const cookie = (n) => [...document.cookie.matchAll(new RegExp('(?:^|; )' + n + '=([^;]+)', 'g'))].map(m => m[1]).pop();
  if (!cookie('ct0') || !cookie('twid')) return {error: 'signed-out'};
  const me = () => decodeURIComponent(cookie('twid') || '').replace(/"/g, '').replace('u=', '');
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const fail = (code, extra) => Object.assign(Error(code), {code, ...extra});
  const norm = (s) => String(s || '').replace(/\r\n?/g, '\n').replace(/ /g, ' ').split('\n').map(l => l.trimEnd()).join('\n').trim();
  const html = await (await fetch('/', {credentials: 'include'})).text();
  const mainUrl = (html.match(/https:\/\/abs\.twimg\.com\/responsive-web\/client-web[^"]*\/main\.[a-z0-9]+\.js/) || [])[0];
  const main = mainUrl ? await (await fetch(mainUrl)).text() : '';
  const bearer = (main.match(/"(AAAAAAAAAAAAAAAAAAAAA[A-Za-z0-9%]+)"/) || [])[1];
  if (!bearer) return {error: 'query-failed', detail: 'web client token not found'};
  const headers = () => ({authorization: 'Bearer ' + bearer, 'x-csrf-token': cookie('ct0'), 'x-twitter-active-user': 'yes', 'x-twitter-auth-type': 'OAuth2Session'});
  const call = async (path, opts = {}) => {
    for (let i = 0; ; i++) {
      const r = await fetch(path, {credentials: 'include', ...opts, headers: {...headers(), ...(opts.headers || {})}, signal: AbortSignal.timeout(15000)});
      if (r.ok) return r.json();
      if ((r.status === 403 || r.status === 401) && i < 4) { await sleep(1500); continue; }
      if (r.status === 429) throw fail('rate-limited');
      if (r.status === 401) throw fail('signed-out');
      throw fail('query-failed', {status: r.status});
    }
  };
  // GraphQL reads with the web client's own query ids and feature flags, from its script and page state.
  let flags = {};
  try { const s = html.indexOf('window.__INITIAL_STATE__='), fs = JSON.parse(html.slice(s + 25, html.indexOf(';window.__META_DATA__', s))).featureSwitch || {};
        flags = {...fs.defaultConfig, ...(fs.user || {}).config}; } catch (e) {}
  const gql = (name, variables) => {
    const m = main.match(new RegExp('queryId:"([^"]+)",operationName:"' + name + '",operationType:"query",metadata:\\{featureSwitches:\\[([^\\]]*)\\],fieldToggles:\\[([^\\]]*)\\]'));
    if (!m) throw fail('query-failed', {detail: name + ' not found in the web client'});
    const list = (s) => (s.match(/"[^"]+"/g) || []).map(x => x.slice(1, -1)), enc = (o) => encodeURIComponent(JSON.stringify(o));
    return call('/i/api/graphql/' + m[1] + '/' + name + '?variables=' + enc(variables) + '&features=' + enc(Object.fromEntries(list(m[2]).map(k => [k, (flags[k] || {}).value === true])))
      + '&fieldToggles=' + enc(Object.fromEntries(list(m[3]).map(k => [k, false]))));
  };
  const expand = (t) => {
    let text = t.full_text || t.text || '';
    for (const u of (t.entities && t.entities.urls) || []) if (u.url && u.expanded_url) text = text.split(u.url).join(u.expanded_url);
    for (const m of (t.extended_entities || t.entities || {}).media || []) if (m.url) text = text.split(m.url).join('').trim() + ' [' + (m.type || 'media') + ']';
    return text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();
  };
  const tweetOf = (r) => {
    r = r && r.__typename === 'TweetWithVisibilityResults' ? r.tweet : r;
    if (!r || !r.legacy || !r.rest_id) return null;
    const u = ((r.core || {}).user_results || {}).result || {}, note = (((r.note_tweet || {}).note_tweet_results || {}).result || {}).text;
    return {id: r.rest_id, user_id: r.legacy.user_id_str, screen_name: (u.core || {}).screen_name || (u.legacy || {}).screen_name || '',
            name: (u.core || {}).name || (u.legacy || {}).name || '', text: note || expand(r.legacy), in_reply_to: r.legacy.in_reply_to_status_id_str || null, at: Date.parse(r.legacy.created_at)};
  };
  const tweets = (j) => { const out = []; const walk = (o) => { if (!o || typeof o !== 'object') return;
    if ((o.__typename === 'Tweet' || o.__typename === 'TweetWithVisibilityResults') && tweetOf(o)) return out.push(tweetOf(o));
    Object.values(o).forEach(walk); }; walk(j); return out; };
  const post = async (id) => tweetOf((((await gql('TweetResultByRestId', {tweetId: id, withCommunity: false, includePromotedContent: false, withVoice: false})).data || {}).tweetResult || {}).result);
  const conversation = async () => {
    try { return (await call('/i/api/1.1/dm/conversation/' + encodeURIComponent(P.conversation) + '.json?include_conversation_info=true&dm_users=true&count=50&context=FETCH_DM_CONVERSATION')).conversation_timeline || {}; }
    catch (e) { if (e.status === 404 || e.status === 403) throw fail('no-conversation'); throw e; }
  };
  const accounts = ((await call('/i/api/1.1/account/multi/list.json')).users || []).map(u => ({id: String(u.user_id), screen_name: u.screen_name, name: u.name}));
  const find = (h) => accounts.find(a => a.screen_name.toLowerCase() === String(h || '').replace('@', '').toLowerCase());
  const target = find(P.handle), primary = find(P.primary) || accounts.find(a => a.id === me());
  if (!target) return {error: 'not-signed-in', accounts: accounts.map(a => a.screen_name)};
  const switchTo = async (id) => {
    if (me() === id) return;
    await call('/i/api/1.1/account/multi/switch.json', {method: 'POST', headers: {'content-type': 'application/x-www-form-urlencoded'}, body: 'user_id=' + id});
    for (let i = 0; i < 20 && me() !== id; i++) await sleep(250);
    if (me() !== id) throw fail('switch-failed');
  };
  const out = {account: {id: target.id, screen_name: target.screen_name}};
  let keep = false;
  const work = async () => {
    if (P.mode === 'back') return out;
    if (P.switch) await switchTo(target.id);
    if (me() !== target.id && P.mode !== 'verify-reply' && (P.mode !== 'resolve' || !P.reply_to)) throw fail('wrong-account');
    if (P.mode === 'resolve') {
      if (P.reply_to) {
        const t = await post(P.reply_to);
        if (!t) throw fail('no-post');
        out.reply_to = {id: t.id, screen_name: t.screen_name, name: t.name, text: t.text.slice(0, 600)};
      } else {
        // Encrypted X Chat conversations have ids starting with "e"; only the DM inbox can be sent to.
        if (/^e/i.test(P.conversation)) throw fail('encrypted');
        const tl = await conversation(), c = (tl.conversations || {})[P.conversation], users = tl.users || {};
        if (!c) throw fail('no-conversation');
        if (c.read_only) throw fail('read-only');
        const others = (c.participants || []).map(p => p.user_id).filter(id => id !== target.id), who = (id) => users[id] || {};
        out.conversation = {id: P.conversation, with: c.name || others.map(id => who(id).name || '@' + who(id).screen_name).join(', '),
          screen_names: others.map(id => who(id).screen_name).filter(Boolean), group: c.type === 'GROUP_DM', request: c.trusted === false};
      }
      keep = !!P.keep;
      return out;
    }
    if (P.mode === 'dm') {
      // The one write, sent like x.com's own DM composer: JSON body, request_id from the send token.
      const q = 'ext=mediaColor%2CaltText&include_ext_alt_text=true&include_ext_limited_action_results=true&include_reply_count=1&tweet_mode=extended&include_ext_views=true'
        + '&include_groups=true&include_inbox_timelines=true&include_ext_media_color=true&supports_reactions=true&supports_edit=true';
      let r, body = '';
      try {
        r = await fetch('/i/api/1.1/dm/new2.json?' + q, {method: 'POST', credentials: 'include', headers: {...headers(), 'content-type': 'application/json'}, signal: AbortSignal.timeout(30000),
          body: JSON.stringify({conversation_id: P.conversation, recipient_ids: false, request_id: P.request_id, text: P.text, cards_platform: 'Web-12', include_cards: 1, include_quote_count: true, dm_users: false})});
        body = await r.text();
      } catch (e) { return {...out, sent: 'uncertain', detail: String(e).slice(0, 200)}; }
      if (!r.ok) return {...out, sent: r.status >= 500 || r.status === 408 ? 'uncertain' : 'failed', status: r.status, detail: body.slice(0, 300)};
      let id = null;
      try { const e = (JSON.parse(body).entries || []).find(e => e.message); id = e ? (e.message.message_data || {}).id || e.message.id : null; } catch (e) {}
      return {...out, sent: 'sent', status: r.status, id, conversation: P.conversation};
    }
    if (P.mode === 'verify-reply') {
      // By the id X returned, else the account's reply to that post with exactly this text, after the attempt.
      if (P.id) { const t = await post(P.id); return {...out, found: t && t.user_id === target.id ? [t.id] : []}; }
      const j = await gql('TweetDetail', {focalTweetId: P.reply_to, referrer: 'tweet', with_rux_injections: false, rankingMode: 'Recency', includePromotedContent: false,
        withCommunity: true, withQuickPromoteEligibilityTweetFields: false, withBirdwatchNotes: false, withVoice: true});
      return {...out, found: [...new Set(tweets(j).filter(t => t.user_id === target.id && t.in_reply_to === P.reply_to && norm(t.text) === norm(P.text) && t.at >= P.since).map(t => t.id))]};
    }
    if (P.mode === 'verify-dm') {
      const msgs = ((await conversation()).entries || []).map(e => e.message).filter(m => m && m.message_data && m.message_data.sender_id === target.id);
      const byRequest = msgs.filter(m => m.request_id === P.request_id);
      return {...out, found: [...new Set((byRequest.length ? byRequest : msgs.filter(m => norm(expand(m.message_data)) === norm(P.text) && +m.time >= P.since)).map(m => m.message_data.id))]};
    }
    throw fail('query-failed', {detail: 'unknown mode'});
  };
  let res;
  try { res = await work(); }
  catch (e) { keep = false; res = {...out, error: e.code || 'query-failed', status: e.status, detail: String(e.detail || e.message || e).slice(0, 200)}; }
  // A read-only preview/verification must not restore some other job's account.
  if (!keep && (P.switch || P.mode === 'back')) { try { await switchTo((primary || target).id); } catch (e) { res.switch_back = 'failed'; } }
  return res;
}
"""

# On x.com/intent/post: check X's own reply composer holds exactly the approved text for the approved
# post, then (send) click Reply once. Until then, and for anything else, the tab blocks CreateTweet.
COMPOSE_JS = r"""
async () => {
  const P = __PARAMS__;
  if (location.host !== 'x.com' || document.readyState === 'loading') return {error: 'not-ready'};
  const cookie = (n) => [...document.cookie.matchAll(new RegExp('(?:^|; )' + n + '=([^;]+)', 'g'))].map(m => m[1]).pop();
  if (!cookie('ct0') || !cookie('twid')) return {error: 'signed-out'};
  const me = decodeURIComponent(cookie('twid') || '').replace(/"/g, '').replace('u=', '');
  if (me !== P.account_id) return {error: 'wrong-account'};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const norm = (s) => String(s || '').replace(/\r\n?/g, '\n').replace(/ /g, ' ').split('\n').map(l => l.trimEnd()).join('\n').trim();
  addEventListener('beforeunload', (e) => e.stopImmediatePropagation(), true);
  const G = window.__xsend || (window.__xsend = {});
  if (!G.guard) {
    G.guard = true;
    const approved = (body) => { try { const v = JSON.parse(body).variables || {};
      return norm(v.tweet_text) === norm(P.text) && String((v.reply || {}).in_reply_to_tweet_id) === P.reply_to && !v.attachment_url && !((v.media || {}).media_entities || []).length; } catch (e) { return false; } };
    const create = (u) => /\/CreateTweet(?:\?|$)/.test(String(u || ''));
    const admit = (body) => { if (G.request || !G.armed || !approved(body)) { G.blocked = G.request ? 'a second post' : !G.armed ? 'not approved yet' : 'the request differs from the approved reply'; return false; } return G.request = true; };
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) { this.__url = String(u); return open.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function (body) {
      if (!create(this.__url)) return send.apply(this, arguments);
      if (!admit(body)) return this.abort();
      this.addEventListener('loadend', () => { G.response = {status: this.status, text: String(this.responseText || '').slice(0, 20000)}; });
      return send.apply(this, arguments);
    };
    const fetch0 = window.fetch;
    window.fetch = function (u, o) {
      if (!create(u && u.url || u)) return fetch0.apply(this, arguments);
      if (typeof (o || {}).body !== 'string' || !admit(o.body)) { G.blocked = G.blocked || 'unreadable request'; return Promise.reject(new TypeError('blocked')); }
      return fetch0.apply(this, arguments).then(async (r) => { G.response = {status: r.status, text: (await r.clone().text()).slice(0, 20000)}; return r; },
                                                (e) => { G.response = {status: 0, text: String(e)}; throw e; });
    };
  }
  let box, btn;
  for (let i = 0; i < 60; i++) {
    box = document.querySelector('[role="dialog"] [data-testid="tweetTextarea_0"]'); btn = document.querySelector('[role="dialog"] [data-testid="tweetButton"]');
    if (box && btn) break;
    await sleep(250);
  }
  if (!box || !btn) return {error: 'no-composer'};
  const dialog = box.closest('[role="dialog"]');
  if (norm(box.innerText).replace(/\n+/g, '\n') !== norm(P.text).replace(/\n+/g, '\n')) return {error: 'composer-text', detail: box.innerText.slice(0, 300)};
  if (!dialog.innerText.toLowerCase().includes('@' + P.author.toLowerCase())) return {error: 'composer-target', detail: dialog.innerText.slice(0, 300)};
  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return {error: 'composer-refused', detail: 'X disabled the Reply button (too long, or replies are limited)'};
  if (!P.send) return {checked: true};
  if (G.request || G.armed) return {sent: 'uncertain', detail: 'this tab already tried to post'};
  G.armed = true;
  btn.click();
  for (let i = 0; i < 160 && !G.response && !(G.blocked && !G.request); i++) await sleep(250);
  if (!G.request) return {sent: G.blocked ? 'not-sent' : 'uncertain', status: G.blocked ? 'blocked' : undefined, detail: G.blocked || 'X sent no request after the click'};
  if (!G.response) return {sent: 'uncertain', detail: 'no answer from X'};
  const {status, text} = G.response;
  let j = null;
  try { j = JSON.parse(text); } catch (e) {}
  const id = ((((j || {}).data || {}).create_tweet || {}).tweet_results || {}).result;
  if (id && id.rest_id) return {sent: 'sent', status, id: id.rest_id};
  const error = ((j || {}).errors || [])[0];
  if (error && error.code) return {sent: 'failed', status: error.code, detail: String(error.message || '').slice(0, 300)};
  return {sent: status >= 400 && status < 500 && status !== 408 ? 'failed' : 'uncertain', status, detail: text.slice(0, 300)};
}
"""


class Fail(Exception):
    pass


def oc(*args, timeout=120):
    r = subprocess.run(['openclaw', 'browser', '--browser-profile', PROFILE, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise Fail('browser command failed: ' + (r.stderr or r.stdout).strip()[:300])
    return r.stdout


def evaluate(target, js, timeout_ms=90000):
    if CDP:
        try:
            res = browser_cdp.evaluate(CDP, target, js, timeout_ms)
        except browser_cdp.CdpError as e:
            if 'context' in str(e):  # the tab was still navigating: same as a page that is not ready yet
                return {'error': 'not-ready'}
            raise Fail('browser command failed: ' + str(e)) from None
        if not isinstance(res, dict):
            raise Fail('unexpected browser output: ' + str(res)[:200])
        return res
    out = oc('evaluate', '--target-id', target, '--timeout-ms', str(timeout_ms), '--fn', js, timeout=timeout_ms // 1000 + 30)
    start = out.find('{') if out.lstrip().startswith('{') else out.find('\n{') + 1
    try:
        return json.loads(out[start:])
    except ValueError:
        raise Fail('unexpected browser output: ' + out.strip()[:200])


def navigate(target, url):
    if CDP:
        try:
            return browser_cdp.navigate(CDP, target, url)
        except browser_cdp.CdpError as e:
            raise Fail('browser command failed: ' + str(e)) from None
    oc('navigate', '--target-id', target, url)


def lease(action, exclusive=False):
    if action == 'claim' and exclusive:
        # Same atomic lease admission as the CLI, without real-Chrome window focus.
        global LEASE_API
        if LEASE_API is None:
            spec = importlib.util.spec_from_file_location('x_browser_lease', LEASE)
            LEASE_API = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(LEASE_API)
        LEASE_API.STATE.parent.mkdir(parents=True, exist_ok=True)
        ok, state = LEASE_API.claim(OWNER, 10, exclusive=True)
        return subprocess.CompletedProcess([], 0 if ok else 3, LEASE_API.describe(state) or 'free', '')
    args = ['python3', str(LEASE), action, '--owner', OWNER] + ([] if action == 'release' else ['--minutes', '10'])
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def one_at_a_time(wait=180):
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = RUN_LOCK.open('a')
    deadline = time.time() + wait
    while True:
        try:
            portable.lock(handle, blocking=False)
            return handle
        except BlockingIOError:
            if time.time() > deadline:
                handle.close()
                raise Fail('the OpenClaw browser is busy (another x-inbox run); try again later')
            time.sleep(2)


# per browser profile: each holds its own tabs (a second account reads in a browser of its own)
OPEN_TABS = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'the-machine' / ('x-inbox-tabs' + ('' if PROFILE == 'openclaw' else '-' + PROFILE))


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


def with_tab(url, fn, switching=False):
    guard = tab_guard()
    target, claimed = None, False
    try:
        claim = lease('claim', exclusive=switching)
        if claim.returncode == 3:
            raise Fail('the OpenClaw browser is busy (' + lease('status').stdout.strip() + '); try again later')
        if claim.returncode:
            raise Fail('could not claim the browser lease: ' + (claim.stderr or claim.stdout).strip()[:200])
        claimed = True
        clean_stopped_tabs()
        target = open_tab('x-inbox-' + uuid.uuid4().hex[:6], url)
        return fn(target)
    finally:
        try:
            if target:
                close_tab(target)
            if claimed:
                lease('release')
        finally:
            guard.close()


def run(mode, **params):
    js = JS.replace('__PARAMS__', json.dumps({'mode': mode, 'state': {}, 'since': 0, **params}))

    def go(target):
        for _ in range(10):
            res = evaluate(target, js)
            if res.get('error') != 'not-ready':
                return res
            time.sleep(1)
        raise Fail('the X tab did not load in time; try again')
    lock = one_at_a_time()
    try:
        # Even a primary-account read can restore a non-primary active account.
        switching = mode != 'accounts'
        res = with_tab(NEUTRAL_URL, go, switching)
    finally:
        lock.close()
    err = res.get('error')
    if err == 'signed-out':
        raise Fail('X is signed out in the OpenClaw browser; sign in there once and retry')
    if err == 'not-signed-in':
        raise Fail('@' + str(params.get('handle')) + ' is not signed in in the OpenClaw browser (signed in: ' + ', '.join(res.get('accounts') or []) + ')')
    if err == 'rate-limited':
        raise Fail('X rate limit reached; try again later')
    if err:
        raise Fail('X read failed: ' + str(res.get('detail') or err) + (' (HTTP ' + str(res['status']) + ')' if res.get('status') else ''))
    if res.get('switch_back') == 'failed':
        print('warning: could not switch the browser back to @' + str(params.get('primary')), file=sys.stderr)
    return res


def when(ms):
    d = datetime.fromtimestamp(ms / 1000).astimezone()
    today = datetime.now().astimezone().date()
    return d.strftime('%H:%M') if d.date() == today else d.strftime('%a %H:%M') if (today - d.date()).days < 7 else d.strftime('%Y-%m-%d')


def one_line(s, n):
    s = ' '.join((s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def week_ago():
    return int((time.time() - 7 * 86400) * 1000)


URLISH = re.compile(r'(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?:/\S*)?')


def x_length(text):
    """Characters as X counts them in a post: every link 23, CJK and emoji 2 (twitter-text v3)."""
    text = unicodedata.normalize('NFC', text)
    return 23 * len(URLISH.findall(text)) + sum(1 if o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247 else 2
                                                 for o in map(ord, URLISH.sub('', text)))


def log_send(entry):
    SENDS_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with SENDS_LOG.open('a') as f:
        f.write(json.dumps({'at': datetime.now().astimezone().isoformat(timespec='seconds'), **entry}, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def send_entries():
    entries, damaged = [], False
    try:
        for line in SENDS_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError('not an object')
                entries.append(entry)
            except ValueError:
                damaged = True
    except OSError:
        damaged = SENDS_LOG.exists()
    return entries, damaged


def prior_send(token):
    entries, damaged = send_entries()
    for e in reversed(entries):
        if e.get('token') == token and e.get('result') in ('sent', 'uncertain', 'intent'):
            return e
    if damaged:
        raise Fail('send history is unreadable or damaged; reconcile it before sending')
    return None


def when_ready(target, js, tries=10):
    # Only for steps that write nothing: the script answers not-ready before doing anything.
    for _ in range(tries):
        res = evaluate(target, js)
        if res.get('error') != 'not-ready':
            return res
        time.sleep(1)
    raise Fail('the X tab did not load in time; try again')


def x_js(mode, **params):
    return SEND_JS.replace('__PARAMS__', json.dumps({'mode': mode, **params}))


def trouble(res, handle):
    """Plain reason for an X error that happened before anything was written."""
    err = res.get('error')
    return {'signed-out': 'X is signed out in the OpenClaw browser; sign in there once and retry',
            'not-signed-in': '@' + handle + ' is not signed in in the OpenClaw browser (signed in: ' + ', '.join(res.get('accounts') or []) + ')',
            'wrong-account': 'the OpenClaw browser is busy (X is on another account right now); try again later',
            'switch-failed': 'the OpenClaw browser is busy (could not switch X to @' + handle + '); try again later',
            'rate-limited': 'X rate limit reached; try again later',
            'no-post': 'the post to reply to is no longer available on X',
            'encrypted': "this is an encrypted X Chat conversation; sending there isn't supported yet",
            'no-conversation': 'this conversation is not in the X DM inbox of @' + handle + ' (it may have moved to encrypted X Chat)',
            'read-only': 'this X conversation is read-only',
            'no-composer': "X's reply composer did not open",
            'composer-text': "X's reply composer did not hold exactly the approved text",
            'composer-target': "X's reply composer was not replying to the approved post",
            'composer-refused': 'X disabled the Reply button (too long for this account, or replies to that post are limited)',
            }.get(err) or 'X lookup failed: ' + str(res.get('detail') or err) + (' (HTTP ' + str(res['status']) + ')' if res.get('status') else '')


def cmd_send(a):
    text, handle, primary = (a.text or '').strip(), a.handle.lstrip('@'), a.primary.lstrip('@')
    if not text or '\x00' in text:
        raise Fail('--text is empty')
    limit = DM_LIMIT if a.conversation else LONG_LIMIT if a.long else REPLY_LIMIT
    size = len(text) if a.conversation else x_length(text)
    if size > limit:
        raise Fail(f'--text is longer than X allows ({size} of {limit} characters)')
    if a.reply_to and not re.fullmatch(r'\d{1,25}', a.reply_to) or a.conversation and not re.fullmatch(r'[\w-]{1,100}', a.conversation):
        raise Fail('--reply-to is a post id, --conversation a DM conversation id')
    token = a.token or 'x-' + uuid.uuid4().hex
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'x-inbox:' + token))
    # Switching accounts needs the browser to itself (no other job's tabs); a reply preview needs no switch.
    # The active browser account may not be primary: protected reads/sends select their
    # target explicitly. A public reply preview remains shared and never switches.
    switching = bool(a.yes or a.check or a.conversation)
    stay = switching and bool(a.yes or a.check)
    base = {'handle': handle, 'primary': primary, 'reply_to': a.reply_to or '', 'conversation': a.conversation or '', 'text': text, 'request_id': request_id}
    wrote = []

    def intent(entry):
        log_send({'token': token, 'result': 'intent', 'handle': handle, 'primary': primary, 'text': text, 'at_ms': int(time.time() * 1000), **entry})
        wrote.append(True)

    def go(target):
        res = when_ready(target, x_js('resolve', switch=switching, keep=stay, **base))
        if res.get('error') or not (a.yes or a.check):
            return res
        try:
            if a.conversation:
                intent({'account': res['account'], 'conversation': a.conversation, 'to': res['conversation'], 'request_id': request_id})
                try:
                    out = evaluate(target, x_js('dm', switch=False, **base))  # once: never retried
                except Exception as e:
                    out = {'sent': 'uncertain', 'detail': str(e)[:200]}
            else:
                navigate(target, 'https://x.com/intent/post?' + urllib.parse.urlencode({'in_reply_to': a.reply_to, 'text': text}, quote_via=urllib.parse.quote))
                compose = {'account_id': res['account']['id'], 'reply_to': a.reply_to, 'author': res['reply_to']['screen_name'], 'text': text}
                checked = when_ready(target, COMPOSE_JS.replace('__PARAMS__', json.dumps({**compose, 'send': False})), tries=20)
                if checked.get('error') or not a.yes:
                    return {**res, **checked}
                intent({'account': res['account'], 'reply_to': a.reply_to, 'to': res['reply_to']})
                try:
                    out = evaluate(target, COMPOSE_JS.replace('__PARAMS__', json.dumps({**compose, 'send': True})), 120000)  # one click
                except Exception as e:
                    out = {'sent': 'uncertain', 'detail': str(e)[:200]}
            if out.get('error'):  # stopped before writing: the account changed under us
                out = {'sent': 'not-sent', 'status': out['error'], 'detail': trouble(out, handle)}
            log_send({'token': token, 'result': out.get('sent') or 'uncertain', 'id': out.get('id'), 'status': out.get('status'), 'detail': out.get('detail')})
            return {**res, **out}
        finally:
            if stay:
                try:
                    back = when_ready(target, x_js('back', switch=False, **base))
                    if back.get('error') or back.get('switch_back'):
                        raise Fail(back.get('error') or 'switch back failed')
                except Exception as e:
                    print('warning: could not switch the browser back to @' + primary + ': ' + str(e), file=sys.stderr)

    lock = one_at_a_time()
    try:
        if a.yes and (prev := prior_send(token)):
            raise Fail('this send (token ' + token + ') was already attempted at ' + prev['at'] + ' with result "' + prev['result'] + '"; not sending again')
        try:
            res = with_tab(NEUTRAL_URL, go, switching)
        except (Fail, subprocess.TimeoutExpired) as e:
            if wrote or not (a.yes and a.json) or 'busy' in str(e):
                raise
            res = {'error': 'browser', 'detail': str(e)[:200]}  # stopped before the intent: nothing was written
    finally:
        lock.close()
    if res.get('switch_back') == 'failed':
        print('warning: could not switch the browser back to @' + primary, file=sys.stderr)
    if res.get('error'):
        reason = trouble(res, handle)
        reason = res['detail'] if res['error'] == 'browser' else reason
        if 'busy' in reason or not (a.yes and a.json):
            raise Fail(reason)
        res = {**res, 'sent': 'not-sent', 'status': res['error'], 'detail': reason}  # nothing was written; the token stays unused
    if a.json:
        print(json.dumps({'token': token, **res}, ensure_ascii=False))
        if a.yes and res.get('sent') != 'sent':
            sys.exit(4)
        return
    where = ('reply on X as @' + res['account']['screen_name'] + ' to @' + res['reply_to']['screen_name'] + ' (' + res['reply_to']['name'] + '): “' + one_line(res['reply_to']['text'], 200) + '”'
             if a.reply_to else 'send an X message as @' + res['account']['screen_name'] + ' to ' + res['conversation']['with'])
    if not a.yes:
        print('Would ' + where + ':\n' + text + '\n' + ('(checked in X’s composer; nothing was posted)' if res.get('checked') else '(preview only; add --yes to send)'))
    elif res.get('sent') == 'sent':
        print('Posted: https://x.com/' + res['account']['screen_name'] + '/status/' + str(res.get('id')) if a.reply_to else 'Sent to ' + res['conversation']['with'] + '.')
    elif res.get('sent') in ('failed', 'not-sent'):
        raise Fail('X refused it (' + str(res.get('status')) + ': ' + str(res.get('detail') or '') + '); nothing was sent')
    else:
        raise Fail('unclear whether it was sent; check X before retrying (token ' + token + ')')


def cmd_verify(a):
    # Did an earlier send (by token) land? The send log first, then X itself; absence never means resend.
    entries, damaged = send_entries()
    mine = [e for e in entries if e.get('token') == a.token]
    done = next((e for e in reversed(mine) if e.get('result') == 'sent'), None)
    if done:
        print(json.dumps({'found': True, 'id': done.get('id'), 'via': 'log'}))
        return
    intent = next((e for e in mine if e.get('result') == 'intent'), None)
    if not intent:
        print(json.dumps({'found': False, 'attempted': None, 'history_damaged': damaged}))
        return
    if any(e.get('result') in ('failed', 'not-sent') for e in mine):
        print(json.dumps({'found': False, 'attempted': True, 'refused': True}))
        return
    handle, primary = intent['handle'], intent.get('primary') or intent['handle']
    known = next((e.get('id') for e in reversed(mine) if e.get('id')), None)
    params = {'handle': handle, 'primary': primary, 'text': intent.get('text', ''), 'since': intent.get('at_ms', 0) - 60000, 'id': known or ''}
    if intent.get('reply_to'):
        js, switching = x_js('verify-reply', switch=False, reply_to=intent['reply_to'], **params), False
    else:
        switching = True
        js = x_js('verify-dm', switch=switching, conversation=intent['conversation'], request_id=intent.get('request_id', ''), **params)
    lock = one_at_a_time()
    try:
        res = with_tab(NEUTRAL_URL, lambda target: when_ready(target, js), switching)
    finally:
        lock.close()
    if res.get('error'):
        raise Fail(trouble(res, handle))
    found = res.get('found') or []
    print(json.dumps({'found': len(found) == 1, 'id': found[0] if len(found) == 1 else None, 'attempted': True, 'matches': len(found)}))


def main():
    p = argparse.ArgumentParser(description='X mentions, replies and DMs through the OpenClaw browser; send only with --yes.')
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('accounts'); a.add_argument('--json', action='store_true')
    for name in ('mentions', 'dms'):
        s = sub.add_parser(name); s.add_argument('--handle', required=True); s.add_argument('--primary', required=True)
        s.add_argument('--limit', type=int, default=20); s.add_argument('--json', action='store_true')
    s = sub.add_parser('sync'); s.add_argument('--handle', required=True); s.add_argument('--primary', required=True)
    s.add_argument('--since', type=int, required=True); s.add_argument('--state', default='{}')
    s = sub.add_parser('send', help='reply to a post or send a DM (preview unless --yes)')
    s.add_argument('--handle', required=True); s.add_argument('--primary', required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--reply-to', help='id of the post to reply to'); g.add_argument('--conversation', help='DM conversation id (as given by `sync`)')
    s.add_argument('--text', required=True); s.add_argument('--long', action='store_true', help='the account has X Premium: replies up to 25000 characters')
    s.add_argument('--send-id', '--token', dest='token', help='idempotency key; an id already used is never sent again')
    s.add_argument('--check', action='store_true', help='also open the reply composer and check it, without posting')
    s.add_argument('--yes', action='store_true', help='really send (only after explicit approval)'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('verify', help='JSON: did the send with this token land?'); s.add_argument('--send-id', '--token', dest='token', required=True)
    a = p.parse_args()
    try:
        if a.cmd == 'send':
            return cmd_send(a)
        if a.cmd == 'verify':
            return cmd_verify(a)
        if a.cmd == 'accounts':
            res = run('accounts')
            if a.json:
                print(json.dumps(res, ensure_ascii=False))
            else:
                for x in res['accounts']:
                    print(('* ' if x['id'] == res['active'] else '  ') + '@' + x['screen_name'] + '  ' + x['name'])
        elif a.cmd == 'sync':
            print(json.dumps(run('sync', handle=a.handle, primary=a.primary, since=a.since, state=json.loads(a.state)), ensure_ascii=False))
        else:
            res = run('sync', handle=a.handle, primary=a.primary, since=week_ago())
            items = sorted(res['mentions' if a.cmd == 'mentions' else 'dms'], key=lambda x: -x['at'])[:a.limit]
            if a.json:
                print(json.dumps(items, ensure_ascii=False))
            for x in [] if a.json else items:
                who = 'You' if x.get('from_me') else (x.get('author') or x.get('from') or {}).get('name') or '?'
                where = (' in ' + x['with']) if a.cmd == 'dms' else ''
                print(f"{when(x['at']):>9}  {who}{where}: {one_line(x['text'], 120)}")
    except Fail as e:
        print('x-inbox: ' + str(e), file=sys.stderr)
        sys.exit(2)
    except subprocess.TimeoutExpired:
        print('x-inbox: the browser did not answer in time', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
