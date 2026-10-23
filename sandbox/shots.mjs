// Screenshots of a running sandbox (sandbox/run.py), with a layout check on each: the key pages at phone (390px) and
// desktop (1280px) width, light and dark. Writes PNGs and report.json to --out, prints what looks wrong, and exits 1
// with --strict when anything does.
//
//   node sandbox/shots.mjs --url http://127.0.0.1:8870 --out /tmp/machine-shots
//   node sandbox/shots.mjs --pages fleet,settings --widths 390 --themes dark
//   node sandbox/shots.mjs --url http://127.0.0.1:8890 --pages /account,/setup,/pricing   # the control plane sandbox
//
// The checks: the page (or an element on it) wider than the screen, buttons and links that overlap each other, and
// dates or times that wrap onto a second line. Chromium: CHROMIUM_PATH, else /opt/pw-browsers, else /usr/bin/chromium.
import {existsSync, mkdirSync, readdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {OPENCLAW} from '../openclaw-root.mjs';

const args = Object.fromEntries(process.argv.slice(2).reduce((out, a, i, all) => a.startsWith('--') ? [...out, [a.slice(2), all[i + 1]?.startsWith('--') || all[i + 1] === undefined ? 'true' : all[i + 1]]] : out, []));
const url = args.url || 'http://127.0.0.1:8870';
const out = args.out || '/tmp/machine-shots';
const widths = (args.widths || '390,1280').split(',').map(Number);
const themes = (args.themes || 'light,dark').split(',');
const PAGES = {
  suggestions: {tab: 'inbox'}, calendar: {tab: 'calendar'}, events: {tab: 'events'}, graph: {tab: 'people'}, projects: {tab: 'people', sub: 'projects'}, fleet: {tab: 'fleet'},
  feedback: {tab: 'feedback'}, connections: {tab: 'health'}, settings: {click: '#open-settings'},
};
const pages = (args.pages || Object.keys(PAGES).join(',')).split(',');

async function playwright() {
  for (const from of [OPENCLAW + '/node_modules/playwright-core/index.mjs', 'playwright-core', 'playwright']) {
    try { return await import(from); } catch {}
  }
  throw new Error('Playwright not found: install OpenClaw, or `npm i playwright-core` somewhere on the module path');
}

function chromiumPath() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  if (existsSync('/opt/pw-browsers')) {
    for (const dir of readdirSync('/opt/pw-browsers').filter(d => d.startsWith('chromium')).sort().reverse()) {
      for (const bin of ['chrome-linux/chrome', 'chrome-linux64/chrome']) if (existsSync(join('/opt/pw-browsers', dir, bin))) return join('/opt/pw-browsers', dir, bin);
    }
    if (existsSync('/opt/pw-browsers/chromium')) return '/opt/pw-browsers/chromium';
  }
  return '/usr/bin/chromium';
}

// Runs in the page: what looks wrong at this size.
function layoutProblems() {
  const width = document.documentElement.clientWidth, problems = [];
  // What can be seen of an element: its box, cut to every ancestor that clips it (null when nothing shows).
  const shown = el => {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) || el.closest('[hidden], details:not([open]) > :not(summary)')) return null;
    let {left, top, right, bottom} = el.getBoundingClientRect();
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.overflowX === 'visible' && s.overflowY === 'visible') continue;
      const c = p.getBoundingClientRect();
      left = Math.max(left, c.left); top = Math.max(top, c.top); right = Math.min(right, c.right); bottom = Math.min(bottom, c.bottom);
    }
    return right - left > 0 && bottom - top > 0 ? {left, top, right, bottom} : null;
  };
  const visible = el => !!shown(el);
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.classList.length ? '.' + [...el.classList].slice(0, 2).join('.') : '') +
    (el.textContent.trim() ? ` "${el.textContent.trim().replace(/\s+/g, ' ').slice(0, 40)}"` : '');
  const scrolls = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true; } return false; };
  if (document.documentElement.scrollWidth > width + 1) problems.push({check: 'overflow', what: `the page is ${document.documentElement.scrollWidth}px wide on a ${width}px screen`});
  const wide = [];
  for (const el of document.querySelectorAll('main *, header *, nav *, dialog[open] *')) {
    if (!visible(el) || scrolls(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > width + 1 || r.left < -1) wide.push(el);
  }
  for (const el of wide.filter(el => !wide.includes(el.parentElement)).slice(0, 8))
    problems.push({check: 'overflow', what: `${name(el)} reaches ${Math.round(el.getBoundingClientRect().right)}px on a ${width}px screen`});
  const controls = [...document.querySelectorAll('button, a[href], [role="button"], input:not([type="hidden"]), select, summary')].filter(visible)
    .filter(el => !el.closest('label')?.contains(el) || el.tagName !== 'INPUT' || el.closest('label').querySelectorAll('input').length === 1);
  for (let i = 0; i < controls.length; i++) for (let j = i + 1; j < controls.length; j++) {
    const a = controls[i], b = controls[j];
    if (a.contains(b) || b.contains(a)) continue;
    // Line by line: a link that wraps covers two short boxes, not the whole rectangle around them.
    const r = shown(a), s = shown(b), boxes = el => [...el.getClientRects()];
    let x = 0, y = 0;
    for (const p of boxes(a)) for (const q of boxes(b)) {
      const w = Math.min(p.right, q.right, r.right, s.right) - Math.max(p.left, q.left, r.left, s.left);
      const h = Math.min(p.bottom, q.bottom, r.bottom, s.bottom) - Math.max(p.top, q.top, r.top, s.top);
      if (w > 2 && h > 2 && w * h > x * y) [x, y] = [w, h];
    }
    if (x > 2 && y > 2) problems.push({check: 'overlap', what: `${name(a)} overlaps ${name(b)} by ${Math.round(x)}x${Math.round(y)}px`});
  }
  const DATE = /^(?:(?:mon|tue|wed|thu|fri|sat|sun)\w*,? )?\d{1,2} (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?: \d{4})?(?:,? \d{1,2}:\d{2})?$|^\d{1,2}:\d{2}(?: ?[ap]m)?$|^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?$|^(?:today|yesterday|tomorrow)(?:,? (?:at )?\d{1,2}:\d{2})?$|^\d+ ?(?:s|min|m|h|d|w|mo|y)(?:ins?|ours?|ays?|eeks?)? ago$|^(?:mon|tue|wed|thu|fri|sat|sun)\w* \d{1,2}:\d{2}$/i;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim().replace(/\s+/g, ' ');
    const el = node.parentElement;
    if (!text || !el || !visible(el) || !(el.tagName === 'TIME' || DATE.test(text))) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    const tops = new Set([...range.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top)));
    if (tops.size > 1) problems.push({check: 'wrapped date', what: `"${text.slice(0, 40)}" in ${name(el).split(' ')[0]} wraps onto ${tops.size} lines`});
  }
  return problems;
}

const {chromium} = await playwright();
mkdirSync(out, {recursive: true});
const browser = await chromium.launch({executablePath: chromiumPath(), headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
const report = [];
try {
  for (const theme of themes) for (const width of widths) {
    const context = await browser.newContext({viewport: {width, height: width < 600 ? 844 : 900}, colorScheme: theme, deviceScaleFactor: 1});
    await context.addInitScript(() => { try { localStorage.removeItem('theme'); } catch {} });
    const local = new URL(url).hostname;
    await context.route('**/*', route => new URL(route.request().url()).hostname === local ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    if (pages.some(k => !k.startsWith('/'))) await page.goto(url, {waitUntil: 'networkidle'});
    for (const key of pages) {
      const spec = PAGES[key];
      if (key.startsWith('/')) await page.goto(new URL(key, url).href, {waitUntil: 'networkidle'});  // any page by path, e.g. the control plane's
      else if (!spec) { console.error(`unknown page ${key}; pages: ${Object.keys(PAGES).join(', ')}, or a path like /account`); continue; }
      else {
        const target = spec.tab ? page.locator(`nav [data-tab="${spec.tab}"]`) : page.locator(spec.click);
        if (!await target.isVisible()) { report.push({page: key, width, theme, skipped: 'not shown in this state'}); continue; }
        await target.click();
        if (spec.sub) await page.evaluate(sub => document.querySelector(`[data-graph-sub="${sub}"]`).click(), spec.sub);  // a Graph pane: Projects opens on its board
      }
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(400);
      const file = join(out, `${key.replace(/^\//, '').replace(/[^\w-]+/g, '_') || 'home'}-${width}-${theme}.png`);
      await page.screenshot({path: file, fullPage: true});
      const problems = await page.evaluate(layoutProblems);
      report.push({page: key, width, theme, file, problems, errors: errors.splice(0)});
    }
    await context.close();
  }
} finally {
  await browser.close();
}
writeFileSync(join(out, 'report.json'), JSON.stringify(report, null, 1));
let bad = 0;
for (const r of report) {
  if (r.skipped) { console.log(`${r.page} ${r.width} ${r.theme}: skipped (${r.skipped})`); continue; }
  const issues = [...r.problems.map(p => `${p.check}: ${p.what}`), ...r.errors.map(e => 'script error: ' + e)];
  bad += issues.length;
  console.log(`${r.page} ${r.width} ${r.theme}: ${issues.length ? issues.length + ' problem' + (issues.length > 1 ? 's' : '') : 'ok'}`);
  for (const line of issues.slice(0, 12)) console.log('  ' + line);
}
console.log(`${report.filter(r => r.file).length} screenshots in ${out}; ${bad} problem${bad === 1 ? '' : 's'}`);
if (args.strict === 'true' && bad) process.exit(1);
