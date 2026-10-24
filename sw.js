// Push notifications for the installed dashboard app. No caching: the dashboard stays live-only.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

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
