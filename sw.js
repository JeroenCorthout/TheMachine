// The installed dashboard app: push notifications, and a copy of the page itself (the app shell) so the app opens at
// once. The shell holds no data and never its CSRF token; the last cards, chats and Graph lists are kept by the page,
// encrypted (docs/architecture.md, "On the device"). Brand images are kept too. Nothing else is cached.
const SHELL = 'tm-shell-1', ASSETS = 'tm-assets-1', SHELL_AGE = 7 * 864e5;
const CSRF = /(\bconst csrf=')[0-9a-f]{64}(')/;
const scopeUrl = () => new URL(self.registration.scope);

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith('tm-') && ![SHELL, ASSETS].includes(name)) await caches.delete(name);
  await self.clients.claim();
})()));

// Keep a page answer as the shell: only a whole, current dashboard page whose CSRF token can be taken out.
async function keepShell(response) {
  if (response.status !== 200 || response.type !== 'basic' || !/text\/html/i.test(response.headers.get('Content-Type') || '')) return;
  const text = await response.text();
  if (!CSRF.test(text)) return;
  const headers = {'Content-Type': 'text/html; charset=utf-8', 'X-Shell-At': String(Date.now())};
  if (response.headers.get('ETag')) headers.ETag = response.headers.get('ETag');
  await (await caches.open(SHELL)).put(scopeUrl().href, new Response(text.replace(CSRF, '$1$2'), {headers}));
}

// The session is gone (the sign-in page, or refused): drop the shell and tell the open pages to drop their data.
async function signedOut() {
  await caches.delete(SHELL);
  await caches.delete(ASSETS);
  for (const c of await self.clients.matchAll({type: 'window', includeUncontrolled: true})) c.postMessage({cache: 'signed-out'});
}

// After the app opened from its copy, ask for the page again: a 304 when nothing changed, a new copy for the next start.
async function revalidate(cached) {
  await new Promise(r => setTimeout(r, 4000));  // not while the page is still loading its first data
  try {
    const etag = cached.headers.get('ETag'), r = await fetch(scopeUrl().href, {headers: etag ? {'If-None-Match': etag} : {}, redirect: 'manual', cache: 'no-store', credentials: 'same-origin'});
    if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) return signedOut();
    if (r.status === 200) await keepShell(r);
  } catch {}
}

async function shell(event) {
  const cached = await (await caches.open(SHELL)).match(scopeUrl().href);
  if (cached && Date.now() - +cached.headers.get('X-Shell-At') < SHELL_AGE) {
    event.waitUntil(revalidate(cached));
    return cached;
  }
  // Always the page itself on this origin (the scope), never another address.
  const r = await fetch(scopeUrl().href, {redirect: 'manual', credentials: 'same-origin'});
  if (r.status === 200) event.waitUntil(keepShell(r.clone()).catch(() => {}));
  else if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) event.waitUntil(signedOut());
  return r;
}

async function asset(event, name) {
  // Only a brand file of this app, by its plain name: the address is built here, not taken from the request.
  const href = new URL('brand/' + name, scopeUrl()).href;
  const cache = await caches.open(ASSETS), cached = await cache.match(href);
  const fresh = fetch(href, {credentials: 'same-origin'}).then(r => {
    if (r.status === 200 && r.type === 'basic' && /^(image|font)\//.test(r.headers.get('Content-Type') || '')) return cache.put(href, r.clone()).then(() => r);
    return r;
  });
  if (cached) { event.waitUntil(fresh.catch(() => {})); return cached; }
  return fresh;
}

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url), scope = scopeUrl();
  if (url.origin !== scope.origin) return;
  if (req.mode === 'navigate' && url.pathname === scope.pathname && !url.search) return event.respondWith(shell(event));
  const name = url.pathname.startsWith(scope.pathname + 'brand/') && !url.search && url.pathname.slice(scope.pathname.length + 6);
  if (name && /^(fonts\/)?[\w.-]+\.(png|svg|webp|jpg|woff2?)$/.test(name)) return event.respondWith(asset(event, name));
});

// The page asks: drop the shell (a new release, or signing out).
self.addEventListener('message', event => {
  if (event.data?.cache === 'drop') event.waitUntil(Promise.all([caches.delete(SHELL), caches.delete(ASSETS)]));
});

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = {title: event.data?.text() || ''}; }
  event.waitUntil(self.registration.showNotification(data.title || 'The Machine', {
    body: data.body || '', tag: data.tag || undefined, renotify: !!data.tag && !data.silent, silent: !!data.silent,
    icon: 'brand/app-icon-192.png', badge: 'brand/app-icon-badge-96.png',
    data: {url: new URL(data.url || '.', self.registration.scope).href},
    // Fleet questions: Answer once and Decline open the dashboard at the question, which asks to confirm.
    actions: Array.isArray(data.actions) ? data.actions.slice(0, 2) : [],
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  let url = event.notification.data?.url || self.registration.scope;
  if (['once', 'decline'].includes(event.action) && /#fleet=q_[a-f0-9]{16}$/.test(url)) url += '&choice=' + event.action;
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const client = open.find(c => c.url.startsWith(self.registration.scope));
    if (client) { await client.focus(); return client.navigate(url).catch(() => client.postMessage({open: url})); }
    return self.clients.openWindow(url);
  })());
});
