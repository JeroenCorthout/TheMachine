import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');
// Long lists load a page at a time. The sidebar and the drawer: Done opens on its 10 latest, Show more adds 20 (asking
// the Machine for older ones when needed), folding starts over; other groups show 8 first. The main pane: Done loads 30
// at a time as you scroll, with Show more as the fallback, and no hand-off to Feedback. The Graph pages: tabs with
// counts that filter, a quiet order menu, each pair one list paged from the Machine, kept per page; back from a person
// keeps the pages and the scroll. Search: typing waits a moment, an empty box is the list again at once, and a late
// answer to an older search never shows.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const at=n=>new Date(Date.UTC(2026,9,20,12,0,0)-n*60000).toISOString();
const hex=(p,n)=>p+n.toString(16).padStart(15,'0');
const open=Array.from({length:12},(_,n)=>({id:hex('a',n),title:'Ready card '+n,body:'Text.',status:'prepared',scope:'work',created_at:at(n),event_ids:[n+1]}));
const done=Array.from({length:160},(_,n)=>({id:hex('d',n),title:'Done card '+n+(n===150?' about the boat':''),body:'Text.',status:'completed',scope:'work',created_at:at(1000+n),closed_at:at(100+n),event_ids:[n+500]}));
const status=()=>({suggestions:[...open,...done.slice(0,100)],done_counts:{all:160,work:160,personal:0},promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},agents_settings:{},connectors:[]});
const person=n=>({id:'email:p'+String(n).padStart(3,'0')+'@example.com',kind:'person',name:'Person '+String(n).padStart(3,'0'),strength:3-n/100,last_at:at(n*7%95),signals:4,ids:[]});
const people=Array.from({length:95},(_,n)=>person(n));
const sorts={closest:(a,b)=>b.strength-a.strength,last:(a,b)=>b.last_at.localeCompare(a.last_at),name:(a,b)=>a.name.localeCompare(b.name)};
const lists={people,quiet:people.slice(0,3),orgs:Array.from({length:4},(_,n)=>({id:'org:o'+n,kind:'org',name:'Org '+n,strength:1-n/10,last_at:at(n),signals:3,ids:[]})),
 projects:[{id:'project:a',kind:'project',name:'Garden plan',strength:2,last_at:at(1),signals:5,state:'active',ids:[]},{id:'project:b',kind:'project',name:'Attic',strength:1,last_at:at(0),signals:5,state:'cooling',ids:[]}]};
const calls=[];let searchHold=null;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid'),q=url.searchParams;res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname!=='/api/status')calls.push(url.pathname+url.search);
 if(url.pathname==='/api/status'){res.end(JSON.stringify(status()));return}
 if(url.pathname==='/api/cards/done'){const cursor=Number(q.get('cursor')||0),limit=Number(q.get('limit')||30),term=(q.get('q')||'').toLowerCase(),all=done.filter(c=>c.title.toLowerCase().includes(term));
  res.end(JSON.stringify({cards:all.slice(cursor,cursor+limit),next:cursor+limit<all.length?String(cursor+limit):null,total:all.length}));return}
 if(url.pathname==='/api/graph/list'){const name=q.get('list'),sort=q.get('sort')||'closest',cursor=Number(q.get('cursor')||0),all=[...(lists[name]||[])].sort(sorts[sort]||sorts.closest);
  await new Promise(r=>setTimeout(r,60));res.end(JSON.stringify({items:all.slice(cursor,cursor+30),next:cursor+30<all.length?String(cursor+30):null,total:all.length,sort}));return}
 if(url.pathname==='/api/graph'){const term=q.get('q'),node=q.get('node');
  if(node){const p=people.find(x=>x.id===node)||people[0];res.end(JSON.stringify({...p,links:[],events:[],promises:[],cards:[]}));return}
  if(term){if(term==='slow')await searchHold;const found=people.filter(p=>p.name.toLowerCase().includes(term.toLowerCase()));res.end(JSON.stringify({found:found.slice(0,30),next:found.length>30?'30':null}));return}
  res.end(JSON.stringify({counts:{person:95,org:4,project:2},list_counts:{people:95,quiet:3,new:0,orgs:4,warmup:0,projects:2,projects_done:0},whos_who:[],waiting:0}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const page_=async(width,height)=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-US',timezoneId:'Europe/Brussels'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));
 await page.goto(base);await page.locator('#task-'+open[0].id).waitFor({state:'attached'});return {context,page}};
const group=(page,key)=>page.locator(`#side-cards .side-group:has([data-side-fold="${key}"])`);
const until=async(fn,what)=>{for(let i=0;i<150;i++){if(await fn())return;await new Promise(r=>setTimeout(r,20))}assert.fail('timed out: '+what)};
// The sidebar (desktop) or the drawer (phone): Done 10, +20 a tap, older ones fetched, folding starts over; 8 elsewhere
async function sidebar(page,label){
 if(await page.locator('#drawer-open').isVisible())await page.locator('#drawer-open').click();
 const d=group(page,'done'),fold=page.locator('[data-side-fold="done"]');
 assert.match(await fold.textContent(),/Done\s*160/,'Done counts every done card '+label);
 if(await fold.getAttribute('aria-expanded')==='false')await fold.click();
 assert.equal(await d.locator('a').count(),10,'Done opens on its 10 latest '+label);
 assert.equal(await d.locator('a').first().textContent(),'Done card 0');
 // A phone searches with the palette: done cards come in their own group under the open ones, also ones not loaded yet
 if(await page.locator('#drawer-open').isVisible()){await page.keyboard.press('Escape');await page.locator('#find-open').click();await page.locator('#pal-q').fill('boat');
  await until(async()=>await page.locator('#pal-list .pal-item',{hasText:'about the boat'}).count()===1,'phone search finds done card 150 '+label);
  assert.ok((await page.locator('#pal-list .pal-group').allTextContents()).includes('Done'),'in a Done group '+label);
  await until(async()=>calls.some(c=>c.startsWith('/api/cards/done?limit=8&q=boat')),'asked of the Machine '+label);
  await page.keyboard.press('Escape');await page.locator('#drawer-open').click();await page.waitForTimeout(400)}
 // a search finds a done card that was never loaded in the list (on a phone, from the top bar's search)
 if(await page.locator('#side-find').isVisible()){await page.locator('#side-find').fill('boat');await until(async()=>await page.locator('#side-cards a',{hasText:'about the boat'}).count()===1,'search finds done card 150 '+label);
  await page.locator('#side-find').fill('')}await until(async()=>await d.locator('a').count()===10,'back to 10 '+label);
 for(const n of [30,50,70,90,110]){await d.locator('[data-side-more]').click();await until(async()=>await d.locator('a').count()===n,n+' done '+label)}
 assert.ok(calls.some(c=>c.startsWith('/api/cards/done?cursor=')),'older done cards come from the Machine '+label);
 for(const n of [130,150,160]){await d.locator('[data-side-more]').click();await until(async()=>await d.locator('a').count()===n,n+' done '+label)}
 assert.equal(await d.locator('[data-side-more]').count(),0,'no Show more once all are shown '+label);
 await fold.click();await fold.click();assert.equal(await d.locator('a').count(),10,'folding starts over at 10 '+label);
 const r=group(page,'ready');assert.equal(await r.locator('a').count(),8,'other groups show 8 first '+label);
 await r.locator('[data-side-more]').click();await until(async()=>await r.locator('a').count()===12,'ready all '+label);assert.equal(await r.locator('[data-side-more]').count(),0);
 const w=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert.ok(w.page<=w.viewport,'nothing wider than the screen '+label);
 if(await page.locator('#drawer-open').isVisible())await page.keyboard.press('Escape')}
// The main pane's Done: 30 at a time as you scroll, Show more as a fallback, a quiet loading row, no Feedback hand-off
async function mainDone(page,label){
 await page.locator('#finished-list>summary').click();const rows=page.locator('#finished-list [data-pager] .pager-rows>article');
 await until(async()=>await rows.count()===30,'first page '+label);
 assert.ok(!(await page.locator('#suggestions').textContent()).includes('under Feedback'),'no hand-off to Feedback '+label);
 await page.evaluate(()=>scrollTo(0,document.body.scrollHeight));await until(async()=>await rows.count()>=60,'scroll loads the next page '+label);
 const more=page.locator('#finished-list [data-pager-more]');await until(async()=>await more.count()>0||await rows.count()===160,'more '+label);
 assert.equal(await page.locator('#finished-list .spinner').count(),0);
 for(let i=0;i<40&&await rows.count()<160;i++){if(await more.count())await more.first().click({timeout:1000}).catch(()=>{});await page.waitForTimeout(80)}
 await until(async()=>await rows.count()===160,'all done cards '+label);
 assert.equal(new Set(await rows.evaluateAll(a=>a.map(x=>x.id))).size,160,'no card twice '+label)}
// The Graph: tabs with counts, empty ones hidden; the order menu; each pair paged; kept per page and on back
async function graphPage(page,label){
 await page.evaluate(()=>showTab('people','people'));const pane=page.locator('[data-graph-panel="people"]');
 await until(async()=>await pane.locator('.graph-list article').count()===30,'people page 1 '+label);
 assert.deepEqual(await pane.locator('[data-graph-list]').evaluateAll(b=>b.map(x=>x.textContent.replace(/\s+/g,' ').trim())),['All 95','Gone quiet 3'],'tabs with counts, the empty one hidden '+label);
 const sort=pane.locator('.graph-sort>summary');assert.equal((await sort.textContent()).replace(/\s+/g,' ').trim(),'Closest ▾');
 const bar=await page.evaluate(()=>{const t=document.querySelector('[data-graph-panel="people"] .sub-tabs').getBoundingClientRect(),s=document.querySelector('[data-graph-panel="people"] .graph-sort').getBoundingClientRect();return {same:Math.abs(t.top-s.top)<12,right:Math.round(innerWidth-s.right)}});
 assert.ok(bar.same,'the order menu sits on the tabs\' row when it fits '+label);
 await page.evaluate(()=>scrollTo(0,document.body.scrollHeight));await until(async()=>await pane.locator('.graph-list article').count()>=60,'people scroll '+label);
 await sort.click();await pane.locator('[data-graph-sort="name"]').click();
 await until(async()=>(await pane.locator('.graph-list article').count())>0&&(await pane.locator('.graph-sort>summary').textContent()).includes('Name'),'sorted by name '+label);
 const names=await pane.locator('.graph-list article .node').evaluateAll(a=>a.map(x=>x.textContent.trim()));assert.deepEqual(names,[...names].sort(),'by name '+label);
 assert.ok(calls.includes('/api/graph/list?list=people&sort=name&cursor=0'),'the order is asked of the Machine '+label);
 await pane.locator('[data-graph-list="quiet"]').click();await until(async()=>await pane.locator('.graph-list article').count()===3,'gone quiet '+label);
 assert.equal(await page.evaluate(()=>localStorage.getItem('graph-tab-people')+' '+localStorage.getItem('graph-sort-people')),'quiet name');
 await pane.locator('[data-graph-list="people"]').click();
 await page.evaluate(()=>scrollTo(0,document.body.scrollHeight));await until(async()=>await pane.locator('.graph-list article').count()>=60,'people again '+label);
 // open a person and come back: same tab, order, pages and scroll
 await until(async()=>!(await page.locator('[data-graph-panel="people"] .pager-loading').count()),'pages settled '+label);
 const y=await page.evaluate(()=>{const n=document.querySelectorAll('[data-graph-panel="people"] .graph-list article .node')[5];scrollTo(0,0);scrollTo(0,n.getBoundingClientRect().top-200);return scrollY}),had=await pane.locator('.graph-list article').count();
 assert.equal(await page.title(),'The Machine · People','the tab title follows the page '+label);
 await page.evaluate(()=>document.querySelectorAll('[data-graph-panel="people"] .graph-list article .node')[5].click());await page.locator('.graph-crumbs').waitFor();  // no scroll on the way
 assert.match(await page.title(),/^The Machine · Person \d{3}$/,'the person is in the tab title '+label);
 const before=calls.length;await page.goBack();
 await until(async()=>await page.locator('[data-graph-panel="people"] .graph-list article').count()===had,'pages kept on back '+label);
 await until(async()=>Math.abs(await page.evaluate(()=>scrollY)-y)<4,'scroll kept on back '+label);
 assert.ok(!calls.slice(before).some(c=>c.startsWith('/api/graph/list')),'no page asked again on back '+label);
 // search: debounced typing; empty box is the list again at once, with no network call; a late answer never shows
 const q=page.locator('#people-query');await q.fill('Person 00');const yq=await page.evaluate(()=>scrollY);await until(async()=>await page.locator('#people-view .graph-found').count()===1,'found '+label);
 const n0=calls.length,took=await page.evaluate(()=>{const i=document.querySelector('#people-query'),t=performance.now();i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));
  return document.querySelector('[data-graph-panel="people"]:not([hidden]) .graph-list article')?performance.now()-t:1e9});assert.ok(took<100,'the list is back at once '+label+': '+took+'ms');assert.equal(calls.length,n0,'no network call to clear '+label);
 assert.equal(await page.locator('[data-graph-panel="people"] .graph-list article').count(),had,'pages kept after a search '+label);
 await until(async()=>Math.abs(await page.evaluate(()=>scrollY)-yq)<4,'scroll kept after a search '+label);  // set in the next frame, before it paints
 let release;searchHold=new Promise(r=>release=r);
 await q.fill('slow');await until(async()=>calls.some(c=>c.startsWith('/api/graph?q=slow')),'slow asked '+label);
 assert.equal(await page.locator('#people-view').textContent().then(t=>t.includes('Nobody by that name')),false,'no "Nobody" while it searches '+label);
 assert.equal(await page.locator('#people-view .pager-loading').count(),1,'a quiet loading row while it searches '+label);
 await q.fill('');release();await page.waitForTimeout(200);
 assert.equal(await page.locator('#people-view .graph-found').count(),0,'a late answer to an older search changes nothing '+label);
 assert.equal(await page.locator('[data-graph-panel="people"] .graph-list article').count(),had);
 const n1=calls.length;await page.evaluate(()=>{const i=document.querySelector('#people-query'),type=v=>{i.value=v;i.dispatchEvent(new Event('input',{bubbles:true}))};type('Person 01');setTimeout(()=>type('Person 012'),60)});
 await until(async()=>(await page.locator('#people-view .graph-found article').count())===1,'debounced search '+label);
 assert.deepEqual(calls.slice(n1).filter(c=>c.startsWith('/api/graph?q=')),['/api/graph?q=Person%20012&cursor=0'],'typing waits for a pause '+label);
 await q.fill('');
 // Projects: Open only (no Done tab while it is empty), ordered by last activity first
 await page.evaluate(()=>showTab('people','projects'));const pp=page.locator('[data-graph-panel="projects"]');
 await until(async()=>await pp.locator('.graph-list article').count()===2,'projects '+label);
 assert.deepEqual(await pp.locator('[data-graph-list]').evaluateAll(b=>b.map(x=>x.dataset.graphList)),['projects']);
 assert.equal((await pp.locator('.graph-sort>summary').textContent()).replace(/\s+/g,' ').trim(),'Last activity ▾');
 assert.equal(await pp.locator('.graph-list article .node').first().textContent().then(t=>t.trim()),'Attic');
 const w=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert.ok(w.page<=w.viewport,'nothing wider than the screen '+label)}
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [w,h] of [[1440,900],[390,844],[360,740]]){
  let {context,page}=await page_(w,h);calls.length=0;await sidebar(page,'at '+w);await context.close();
  ({context,page}=await page_(w,h));calls.length=0;await mainDone(page,'at '+w);await graphPage(page,'at '+w);await context.close()}
 assert.deepEqual(errors,[]);
 console.log('long lists dashboard ok');
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
