import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A promise has its own page (#promise/<id>), laid out like a person's: who promised what to whom and by when, where
// it stands, the message it came from, the people and project it is about, its card as a link and a short history,
// with Done, Change date and Not needed while it is open. Every promise link leads there, never to Suggestions: the
// Promises list, the Calendar and a person's page. Nothing is wider than the screen at 360, 390 and 1440, light and dark.
// PROMISE_SHOT=<dir> also writes screenshots.
process.env.TZ='Europe/Brussels';
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const day=n=>{const d=new Date();d.setDate(d.getDate()+n);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const ago=d=>new Date(Date.now()-d*864e5).toISOString(),now=new Date().toISOString();
const promises=[
 {id:7,direction:'mine',who:'Wren Fairhaven',what:'send the written recap of the pilot call, with the ten users who start on the third and the check-in two weeks later',due:day(1),state:'open',made_at:ago(2),check_at:now},
 {id:8,direction:'theirs',who:'Sam Lee',what:'send the signed contract',due:day(3),state:'open',made_at:ago(1),check_at:now},
 {id:9,direction:'mine',together:1,who:'Robin Vale',what:'plan the offsite',due:null,state:'card',suggestion_id:'c'.repeat(16),made_at:ago(4),check_at:now}];
let page7={id:7,direction:'mine',together:0,who:'Wren Fairhaven',what:promises[0].what,due:day(1),check_at:now,status:'open',state:'open',note:null,made_at:ago(2),event_id:41,account_id:'work',created_at:ago(1.9),updated_at:ago(1.9),
 card:{id:'a'.repeat(16),title:'Send Wren the pilot recap',status:'pending'},
 links:[{id:'email:wren@harborline.example',kind:'person',name:'Wren Fairhaven'},{id:'org:harborline',kind:'org',name:'Harborline'},{id:'project:harborline-pilot',kind:'project',name:'Harborline pilot'}],
 history:[{at:ago(2),kind:'said',detail:'gmail.message'},{at:ago(1.9),kind:'noted'},{at:ago(1),kind:'due',detail:day(1)}],
 source:[{id:41,account_id:'work',kind:'gmail.message',occurred_at:ago(2),title:'Pilot call Tuesday',from:'You',from_me:true,preview:'Looking forward to Tuesday. I will send a written recap after the call.',source_url:'https://mail.google.com/mail/u/0/#all/abc'}]};
const person={id:'email:wren@harborline.example',kind:'person',name:'Wren Fairhaven',strength:2,closeness:'close',last_at:ago(1),signals:4,ids:['wren@harborline.example'],links:[],
 open:{you_promised:[{id:7,who:'Wren Fairhaven',what:promises[0].what,due:day(1)}]},recent:[]};
const card=(id,title,extra={})=>({id:id.repeat(16),title,body:'Made-up card.',status:'pending',created_at:now,event_ids:'[]',...extra});
const calls=[],errors=[];
const state=()=>({suggestions:[card('a','Send Wren the pilot recap'),card('c','Plan the offsite')],promises,connectors:[{id:'cal',service:'calendar',status:'ok'}],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(brand[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[url.pathname],import.meta.url)));return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/calendar'){res.end(JSON.stringify({meetings:[]}));return}
 if(url.pathname==='/api/promise'){const id=url.searchParams.get('id');if(id!=='7'){res.statusCode=404;res.end('{"error":"No such promise"}');return}res.end(JSON.stringify(page7));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?person:{people:[person],projects:[],orgs:[],quiet:[],new:[]}));return}
 if(url.pathname==='/api/action'){const call=JSON.parse(body||'{}');calls.push(call);
  if(call.action==='promise_due')page7={...page7,due:call.args.due,history:[...page7.history,{at:new Date().toISOString(),kind:'due',detail:call.args.due}]};
  if(call.action==='promise_close')page7={...page7,state:call.args.status,status:call.args.status,history:[...page7.history,{at:new Date().toISOString(),kind:call.args.status}]};
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
let browser;
const open=async(width,height,scheme='dark')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',timezoneId:'Europe/Brussels',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base);await page.locator('#task-'+'a'.repeat(16)).waitFor();return {context,page}};
const onPage=async page=>{await page.locator('#people-view .promise-page').waitFor();await page.waitForFunction(()=>!document.querySelector('#people').hidden)};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth,wide:[...document.querySelectorAll('main *')].filter(e=>e.offsetParent&&e.getBoundingClientRect().right>innerWidth+.5).map(e=>e.className||e.tagName).slice(0,3)}))
 .then(s=>assert.ok(s.page<=s.vw&&!s.wide.length,`${where}: wider than the screen ${JSON.stringify(s)}`));
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // The Promises list on Suggestions: each promise's text links to its page; nothing anywhere links to the old list
  await page.evaluate(()=>{const l=document.querySelector('#promises-list');if(l)l.open=true});
  assert.deepEqual(await page.locator('#promises-list a.promise-link').evaluateAll(a=>a.map(x=>x.getAttribute('href')).sort()),['#promise/7','#promise/8','#promise/9']);
  await page.locator('#promises-list a.promise-link[href="#promise/7"]').click();await onPage(page);
  assert.equal(await page.evaluate(()=>location.hash),'#promise/7');
  const text=await page.locator('.promise-page').innerText();
  for(const want of ['Send the written recap','Promised to Wren Fairhaven · due','Open','Came from','Pilot call Tuesday','About','Harborline pilot','Card','Send Wren the pilot recap','History','Promised to Wren Fairhaven in an email','Noted by a review','Date moved to'])
   assert.ok(text.includes(want),'the page shows '+want);
  // Its card is a link, never the page itself; the message opens in its app; people and the project open their pages
  assert.equal(await page.locator('.promise-card a').getAttribute('href'),'#card='+'a'.repeat(16));
  assert.equal(await page.locator('.promise-page a[target="_blank"]').getAttribute('href'),page7.source[0].source_url);
  assert.deepEqual(await page.locator('.promise-about button.node').evaluateAll(b=>b.map(x=>x.dataset.node)),['email:wren@harborline.example','org:harborline','project:harborline-pilot']);
  assert.ok(!(await page.locator('.promise-history li').allInnerTexts()).some(t=>/^You\b/.test(t)),'no event line starts with You');
  assert.equal(await page.locator('#page-head .page-title').textContent(),'Promise');
  if(process.env.PROMISE_SHOT)for(const scheme of ['dark','light']){await page.emulateMedia({colorScheme:scheme});await page.waitForTimeout(400);await page.screenshot({path:`${process.env.PROMISE_SHOT}/promise-1440-${scheme}.png`,fullPage:true})}
  await fits(page,'1440');
  // Change date, then Done: quiet actions, each one call
  await page.locator('[data-promise-due]').click();await page.locator('.promise-due-form input').fill(day(5));await page.locator('.promise-due-form button').click();
  await page.waitForFunction(()=>document.querySelectorAll('.promise-history li').length===4);
  assert.deepEqual(calls.at(-1),{action:'promise_due',args:{id:7,due:day(5)}});
  await page.locator('.promise-actions [data-close="kept"]').click();await page.waitForFunction(()=>!document.querySelector('.promise-actions'));
  assert.deepEqual(calls.at(-1),{action:'promise_close',args:{id:7,status:'kept'}});
  assert.ok((await page.locator('.promise-page').innerText()).includes('Marked done'));
  // The breadcrumb leads to the person; their Open between you links back to the promise
  await page.locator('.graph-crumbs a').click();await page.locator('#people-view .graph-open').waitFor();
  assert.equal(await page.locator('#people-view .graph-open a.promise-link').getAttribute('href'),'#promise/7');
  await page.goBack();await onPage(page);
  // The Calendar: a promise's row opens its page
  page7={...page7,state:'open',status:'open'};
  await page.evaluate(()=>showTab('calendar'));await page.locator('#calendar-view a.cal-item[href^="#promise/"]').first().waitFor();
  assert.deepEqual(await page.locator('#calendar-view a.cal-item[href^="#promise/"]').evaluateAll(a=>a.map(x=>x.getAttribute('href'))),['#promise/7','#promise/8']);
  await page.locator('#calendar-view a.cal-item[href="#promise/7"]').click();await onPage(page);
  // No link to a promise goes to Suggestions
  assert.equal(await page.evaluate(()=>document.querySelectorAll('a[href="#promises"]').length),0);
  // Its card link opens the card on Suggestions
  await page.locator('.promise-card a').click();await page.waitForFunction(id=>!document.querySelector('#inbox').hidden&&document.getElementById('task-'+id)?.classList.contains('flash'),'a'.repeat(16));
  await context.close()}
 // Phone: the person page's layout, and it fits
 for(const [width,height] of [[390,844],[360,780]])for(const scheme of ['dark','light']){const {context,page}=await open(width,height,scheme);
  await page.evaluate(()=>{location.hash='#promise/7'});await onPage(page);await page.waitForTimeout(300);
  assert.ok(await page.locator('#people-view .graph-detail.person-page.promise-page').count());
  await fits(page,`${width} ${scheme}`);
  if(process.env.PROMISE_SHOT)await page.screenshot({path:`${process.env.PROMISE_SHOT}/promise-${width}-${scheme}.png`,fullPage:true});
  await context.close()}
 // A promise that isn't there says so, without breaking the page
 {const {context,page}=await open(390,844);await page.evaluate(()=>{location.hash='#promise/404'});await page.waitForFunction(()=>/No such promise/.test(document.body.innerText));await context.close()}
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log(JSON.stringify({calls:calls.map(c=>c.action),page_errors:errors}));
