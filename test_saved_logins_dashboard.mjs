import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections, Sign-ins: one row per saved sign-in (a letter tile, the site, the username, "Password saved" and the
// day), Forget as a quiet button, one line on what they are for, and an empty state once the last one is forgotten.
// No password value, nor a mask of its length, ever reaches the page: even a list that wrongly carried one shows
// only "Password saved". Made-up sign-ins only. SHOTS=<folder> saves 1280 and 390 shots, light and dark, 0/1/4.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const LEAK='fixture-pass-7Q2x';
const four=[
 {site:'crm.example.org',username:'owner@example.org',saved_at:'2026-03-04T09:00:00+00:00',password_saved:true,password:LEAK},
 {site:'billing.example.net',username:'finance-team@example.net',saved_at:'2026-02-11T09:00:00+00:00',password_saved:true},
 {site:'portal.example.com',username:'test-user',saved_at:'2025-12-01T09:00:00+00:00',password_saved:true},
 {site:'a-very-long-subdomain.intranet.example.invalid',username:'someone.with.a.long.address@subsidiary.example.invalid',saved_at:'2026-01-20T09:00:00+00:00',password_saved:false}];
let logins=[];const forgotten=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 const asset={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'}[url.pathname];
 if(asset){res.setHeader('Content-Type',asset.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+asset,import.meta.url)));return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],review_runs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const {action,args}=JSON.parse(body);
  if(action==='browser_logins'){res.end(JSON.stringify({logins}));return}
  if(action==='browser_login_forget'){forgotten.push(args.site);logins=logins.filter(l=>l.site!==args.site);res.end(JSON.stringify({forgotten:true}));return}}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const shots=process.env.SHOTS;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
const text=page=>page.locator('#saved-logins-block').innerText();
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,ignoreDefaultArgs:['--hide-scrollbars'],args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1280,900],[390,844]])for(const scheme of ['light','dark']){
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  for(const n of [4,1,0]){
   logins=four.slice(0,n);
   await page.goto('about:blank');await page.goto(base+'/#connections=signins');  // a fresh load each time, not just a hash change
   await page.locator('#saved-logins-block').waitFor();
   if(n)await page.locator('#saved-logins .saved-login').nth(n-1).waitFor();else await page.locator('#saved-logins.empty').waitFor();
   await page.locator('#health-tabs [role=tab][aria-selected="true"]').filter({hasText:n?new RegExp(`^Sign-ins\\s+${n}$`):/^Sign-ins$/}).waitFor();
   const shown=await text(page);
   assert.match(shown,/sign it back in with one tap/);assert.match(shown,/encrypted on this Machine/);
   const html=await page.content();
   assert.ok(!html.includes(LEAK),'no password value on the page');
   assert.ok(!/[•●*]{4,}/.test(shown),'no mask that gives away a length');
   assert.ok(!/—/.test(shown),'no em dashes');
   if(n){
    assert.equal(await page.locator('#saved-logins .saved-login').count(),n);
    const first=page.locator('#saved-logins .saved-login').first();
    assert.equal(await first.locator('.login-mark').innerText(),'C');
    assert.match(await first.locator('.login-pass').innerText(),/^Password saved · /);
    assert.equal(await first.locator('button').innerText(),'Forget');
    assert.ok(await first.locator('button.quiet').count(),'Forget is a quiet button');
    if(n===4)assert.match(await page.locator('#saved-logins .saved-login').nth(3).locator('.login-pass').innerText(),/^No password saved · /);
    // rows line up: every tile, text column and Forget button at the same x; nothing wider than the screen
    const xs=await page.locator('#saved-logins .saved-login').evaluateAll(rows=>rows.map(r=>[r.querySelector('.login-mark'),r.querySelector('.grow'),r.querySelector('button')].map(e=>Math.round(e.getBoundingClientRect().left)).join()));
    assert.equal(new Set(xs).size,1,xs.join(' / '));
    const rights=await page.locator('#saved-logins .saved-login button').evaluateAll(bs=>bs.map(b=>Math.round(b.getBoundingClientRect().right)));
    assert.equal(new Set(rights).size,1,rights.join());
   }else assert.match(shown,/No saved sign-ins/);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
   if(shots){await page.locator('#health-tabs').scrollIntoViewIfNeeded();await page.evaluate(()=>scrollTo(0,Math.max(0,document.querySelector('#health-tabs').getBoundingClientRect().top+scrollY-24)));
    await page.screenshot({path:`${shots}/signins-${width}-${scheme}-${n}.png`})}
  }
  await context.close();
 }
 // Forget: the row goes; after the last one, the empty state stays in its tab.
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 logins=four.slice(0,1);
 await page.goto(base+'/#connections=signins');await page.locator('#saved-logins .saved-login').waitFor();
 await page.locator('#saved-logins [data-forget-login]').click();
 await page.locator('#saved-logins.empty').waitFor();
 assert.deepEqual(forgotten,['crm.example.org']);
 assert.equal(await page.locator('#health-tabs [role=tab][aria-selected="true"]').innerText(),'Sign-ins');
 // Without sign-ins and without a link to them, Connections has no Sign-ins tab.
 await page.goto(base);await page.locator('nav [data-tab="health"]').click();await page.locator('#health-tabs [role=tab]').first().waitFor();
 assert.ok(!(await page.locator('#health-tabs [role=tab]').allInnerTexts()).some(t=>t.startsWith('Sign-ins')));
 assert.deepEqual(errors,[]);
 console.log('saved sign-ins dashboard ok');
}finally{await browser?.close();server.close()}
