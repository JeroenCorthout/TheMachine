// The Machine: a desktop's screen on a phone. Opens on the browser's own window (from the dashboard's
// /browser/region), with pinch to zoom, one or two fingers to move around, a tap to click, and buttons for
// the whole screen, zooming and the keyboard. Only for the dashboard's own view: frame=window in the link, or in
// the dashboard's defaults.json for its short /browser link.
import UI from './app/ui.js';

const params = new URLSearchParams(location.search);
const framed = params.has('frame') ? params.get('frame') === 'window'
    : await fetch('defaults.json', {cache: 'no-store'}).then((r) => (r.ok ? r.json() : {})).then((d) => d?.frame === 'window', () => false);
if (framed) {
    let rfb = null, zoom = 1, region = null, touch = null, whole = false;
    const display = () => rfb && rfb._display;
    const screen = () => rfb._screenSize();
    const fitAll = () => { const s = screen(), d = display(); return Math.min(s.w / d.width, s.h / d.height) || 1; };

    function apply(z, focusX, focusY) {  // zoom to z, keeping the screen point under (focusX, focusY) in place
        const d = display(), vp = d._viewportLoc, s = screen();
        z = Math.min(Math.max(z, fitAll()), 4);
        const fx = focusX ?? s.w / 2, fy = focusY ?? s.h / 2;
        const px = vp.x + fx / zoom, py = vp.y + fy / zoom;
        zoom = z;
        rfb._updateClip();
        rfb._updateScale();
        d.viewportChangePos(px - fx / zoom - vp.x, py - fy / zoom - vp.y);
    }

    function frame(whole) {  // the browser's window, or the whole screen
        const d = display(), s = screen();
        if (whole || !region) {
            zoom = fitAll();
            rfb._updateClip();
            rfb._updateScale();
            d.viewportChangePos(-d.width, -d.height);
            return;
        }
        const x = region.x * d.width, y = region.y * d.height, w = region.w * d.width, h = region.h * d.height;
        zoom = Math.max(s.h > s.w ? s.w / w : Math.min(s.w / w, s.h / h), fitAll());
        rfb._updateClip();
        rfb._updateScale();
        const vp = d._viewportLoc;
        d.viewportChangePos(x - Math.max(0, (vp.w - w) / 2) - vp.x, y - vp.y);
    }

    async function frameWindow() {
        try {
            const r = await fetch('region', {cache: 'no-store'});
            region = r.ok ? (await r.json()).region : null;
        } catch { region = null; }
        whole = !region;
        frame(false);
        label();
    }

    function hook(next) {  // noVNC makes a new connection object on every (re)connect
        rfb = next;
        rfb.scaleViewport = false;
        rfb.clipViewport = true;
        rfb._updateScale = function () { this._display.scale = zoom; this._fixScrollbars(); };
        rfb._updateClip = function () {
            const s = this._screenSize();
            this._display.clipViewport = true;
            this._display.viewportChangeSize(s.w / zoom, s.h / zoom);
            this._fixScrollbars();
            const d = this._display;  // zoomed in: noVNC's drag button moves around with a mouse
            this._setClippingViewport(d._viewportLoc.w < d.width || d._viewportLoc.h < d.height);
        };
        UI.closeControlbar?.();  // noVNC's side bar stays one tap away on its handle
        frameWindow();
    }

    setInterval(() => {
        if (UI.rfb && UI.rfb !== rfb && UI.rfb._rfbConnectionState === 'connected' && UI.rfb._display.width) hook(UI.rfb);
    }, 300);

    // ---------------------------------------------------------------- touch: tap, move around, pinch
    const point = (t) => { const r = rfb._canvas.getBoundingClientRect(); return {x: t.clientX - r.left, y: t.clientY - r.top}; };
    const mid = (ts) => ({x: (ts[0].clientX + ts[1].clientX) / 2, y: (ts[0].clientY + ts[1].clientY) / 2});
    const gap = (ts) => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);

    function click(t) {
        const p = point(t);
        rfb._sendMouse(p.x, p.y, 0);
        rfb._sendMouse(p.x, p.y, 1);
        rfb._sendMouse(p.x, p.y, 0);
    }

    function onTouch(e) {
        if (!rfb || !document.getElementById('noVNC_container').contains(e.target)) return;
        e.preventDefault();
        e.stopPropagation();
        const ts = e.touches;
        if (e.type === 'touchstart') {
            if (ts.length === 1) touch = {start: ts[0], last: ts[0], moved: false, at: Date.now(), multi: false};
            else if (ts.length === 2 && touch) Object.assign(touch, {multi: true, gap: gap(ts), mid: mid(ts)});
            return;
        }
        if (!touch) return;
        const r = rfb._canvas.getBoundingClientRect();
        if (e.type === 'touchmove') {
            if (ts.length >= 2 && touch.multi) {
                const m = mid(ts), g = gap(ts);
                display().viewportChangePos((touch.mid.x - m.x) / zoom, (touch.mid.y - m.y) / zoom);
                apply(zoom * g / touch.gap, m.x - r.left, m.y - r.top);
                Object.assign(touch, {gap: g, mid: m});
            } else if (ts.length === 1 && !touch.multi) {
                const t = ts[0];
                if (Math.hypot(t.clientX - touch.start.clientX, t.clientY - touch.start.clientY) > 10) touch.moved = true;
                if (touch.moved) display().viewportChangePos((touch.last.clientX - t.clientX) / zoom, (touch.last.clientY - t.clientY) / zoom);
                touch.last = t;
            }
            return;
        }
        if (ts.length === 0) {  // touchend or touchcancel, last finger up
            if (e.type === 'touchend' && !touch.moved && !touch.multi && Date.now() - touch.at < 800) click(touch.start);
            touch = null;
        }
    }
    for (const type of ['touchstart', 'touchmove', 'touchend', 'touchcancel']) {
        window.addEventListener(type, onTouch, {capture: true, passive: false});
    }
    window.addEventListener('gesturestart', e => e.preventDefault(), {passive: false});  // Safari's own page zoom

    // ---------------------------------------------------------------- buttons
    const bar = document.createElement('div');
    bar.id = 'machine-view-bar';
    bar.innerHTML = '<button data-v="whole"></button><button data-v="out" aria-label="Zoom out">−</button>' +
        '<button data-v="in" aria-label="Zoom in">+</button><button data-v="keys">Keyboard</button>';
    const style = document.createElement('style');
    style.textContent = '#machine-view-bar{position:fixed;right:8px;bottom:8px;z-index:1000;display:flex;gap:6px}' +
        '#machine-view-bar button{font:600 14px system-ui,sans-serif;min-width:44px;height:40px;padding:0 12px;border:0;' +
        'border-radius:20px;background:#1d2026e6;color:#fff;box-shadow:0 1px 4px #0006}';
    document.head.append(style);
    function label() { bar.querySelector('[data-v="whole"]').textContent = whole ? 'Browser window' : 'Whole screen'; }
    if (!('ontouchstart' in window)) bar.querySelector('[data-v="keys"]').hidden = true;  // a real keyboard types already
    bar.addEventListener('click', e => {
        const b = e.target.closest('button');
        if (!b || !rfb) return;
        if (b.dataset.v === 'whole') {
            if (whole) frameWindow();
            else { whole = true; frame(true); label(); }
        }
        if (b.dataset.v === 'in') apply(zoom * 1.4);
        if (b.dataset.v === 'out') apply(zoom / 1.4);
        if (b.dataset.v === 'keys') {
            const input = document.getElementById('noVNC_keyboardinput');
            if (document.activeElement === input) input.blur(); else input?.focus();
        }
    });
    // Keep the on-screen keyboard's text box focused while tapping these buttons.
    bar.addEventListener('mousedown', e => e.preventDefault());
    document.body.append(bar);
    label();
}

// ---------------------------------------------------------------- what this browser is (any view)
// A short note so a blank start page never stands on its own: what this browser is for, what it is doing now (from
// the dashboard's /api/browser-now when the viewer sits under it), and a task that waits here for a sign-in. Not in
// the dashboard's sign-in dialog, which frames this viewer and says what to do itself.
if (window.top === window) {
    const KEY = 'machine-note-closed';
    const note = document.createElement('div');
    note.id = 'machine-note';
    note.setAttribute('role', 'status');
    note.innerHTML = '<strong></strong><span></span><small></small><div><a href="../" target="_top" hidden>Open your dashboard</a>' +
        '<button type="button">Got it</button></div>';
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.id = 'machine-note-pill';
    const css = document.createElement('style');
    css.textContent = '#machine-note,#machine-note-pill{position:fixed;left:50%;transform:translateX(-50%);z-index:1001;' +
        'bottom:var(--machine-note-bottom,8px);font:14px/1.4 system-ui,sans-serif;color:#fff;background:#1d2026f2;box-shadow:0 2px 10px #0008}' +
        '#machine-note{width:min(520px,calc(100% - 16px));box-sizing:border-box;padding:12px 14px;border-radius:14px;display:grid;gap:4px}' +
        '#machine-note small{color:#c9ced6;font-size:13px}#machine-note small:empty{display:none}' +
        '#machine-note div{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:4px}' +
        '#machine-note a{color:#9cc3ff;margin-right:auto}' +
        '#machine-note button,#machine-note-pill{font:600 14px system-ui,sans-serif;border:0;border-radius:18px;height:36px;padding:0 14px;cursor:pointer}' +
        '#machine-note button{background:#fff;color:#1d2026}#machine-note-pill{max-width:calc(100% - 16px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
        '#machine-note[hidden],#machine-note-pill[hidden]{display:none}';
    document.head.append(css);
    document.body.append(note, pill);
    if (document.getElementById('machine-view-bar')) document.documentElement.style.setProperty('--machine-note-bottom', '56px');

    let now = {working: [], waiting: []}, shown = '', closed = localStorage.getItem(KEY) === 'idle' ? 'idle' : '';
    function paint() {
        const wait = now.waiting[0], work = now.working[0];
        const [title, text, hint, key] = wait
            ? ['Your turn', `Sign in to ${wait.site || 'the site'} here for "${wait.title}".`, 'The task carries on by itself once you are in.', 'wait:' + wait.title]
            : work
                ? ['Working on a task', work.title, work.step || '', 'work:' + work.title]
                : ["Your Machine's own browser", 'Your Machine uses it to do tasks on websites for you. Sign in to a site here so it can act there. ' +
                   'Nothing runs in it until you ask.', "To sign in, type the site's address at the top.", 'idle'];
        if (key !== shown && closed !== key) closed = '';  // something new to say: open again
        shown = key;
        const [s, t, h] = note.children;
        s.textContent = title;
        t.textContent = text;
        h.textContent = hint;
        pill.textContent = wait ? 'Your turn: sign in' : work ? 'Working: ' + work.title : 'What is this?';
        note.hidden = closed === key;
        pill.hidden = !note.hidden;
    }
    note.querySelector('button').onclick = () => {
        closed = shown;
        if (shown === 'idle') localStorage.setItem(KEY, 'idle');
        paint();
    };
    pill.onclick = () => {
        closed = '';
        localStorage.removeItem(KEY);
        paint();
    };
    let answered = false, timer = null;
    async function check() {
        if (document.hidden) return;
        let found = null;
        try {
            const r = await fetch('../api/browser-now', {cache: 'no-store', credentials: 'same-origin'});
            found = r.ok && /json/.test(r.headers.get('Content-Type') || '') ? await r.json() : null;
        } catch { found = null; }
        if (found && Array.isArray(found.working) && Array.isArray(found.waiting)) {
            now = found;
            answered = true;
            note.querySelector('a').hidden = false;  // the viewer sits under the dashboard: a way back to it
        } else if (!answered) {
            clearInterval(timer);  // the plain local viewer: no dashboard behind it, nothing to ask
        }
        paint();
    }
    timer = setInterval(check, 5000);
    paint();
    check();
}
