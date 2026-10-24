import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A phone (390x844, touch) scrolled halfway down any main screen stays put while the page updates by itself: a refresh
// with new data above (a new card, a new person, a new source, a banner), lists drawn anew, folds the reader opened,
// headers fitted after drawing. What is on screen keeps its place (within 2px) and nothing shifts (CLS ~0), with the
// browser's own scroll anchoring and without it (Safari on an iPhone has none).
// Screens: Cards, a card chat, People, Organizations, Projects (board and list), Calendar, Connections, Fleet, Settings.
process.env.TZ='Europe/Brussels';
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const iso=d=>new Date(d).toISOString(),ago=m=>iso(Date.now()-m*60000),hex=(p,n)=>p+n.toString(16).padStart(15,'0');
let tick=0;  // each refresh brings something new
const long='A longer card text, so the list is taller than the screen. '.repeat(4);
const job={id:'aj_0000000000000001',name:'Codex',tool:'codex',status:'result',app:'Open in Codex',url:'codex://threads/example',summary:'The change is in place across the pages, with the wording kept.',
 files:[{path:'build.log',size:72214},{path:'notes.md',size:812}],has_diff:true,links:[],turns:1,can_follow_up:true,place:'local',created_at:ago(90),started_at:ago(80),finished_at:ago(60),updated_at:ago(60)};
const card=(n,extra={})=>({id:hex('a',n),title:'Card number '+n,body:'Card '+n+'. '+long,status:n%3?'prepared':'pending',scope:'work',created_at:ago(n*30),event_ids:[n+1],...extra});
const cards=()=>[...Array.from({length:tick},(_,i)=>card(900+i,{title:'Fresh card '+i,due:'2000-01-0'+(i+1)})),
 ...Array.from({length:16},(_,n)=>card(n,n===4?{agent_job:job,status:'prepared'}:{}))];
const connectors=()=>[...Array.from({length:tick},(_,i)=>({id:'gmail:new'+i,service:'gmail',display_name:'New inbox '+i,email:'new'+i+'@example.com',state:'ok',last_success:ago(1),total:10,signals_24h:1})),
 ...Array.from({length:24},(_,n)=>({id:'gmail:a'+n,service:'gmail',display_name:'Inbox '+n,email:'inbox'+n+'@example.com',state:'ok',last_success:ago(n+2),total:100+n,signals_24h:n})),
 {id:'calendar:a',service:'calendar',display_name:'Calendar',email:'cal@example.com',state:'ok',last_success:ago(3),total:5}];
const status=()=>({suggestions:cards(),promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},
 agents_settings:{},connectors:connectors(),fleet:{on:true,waiting:0},needs_you:tick?[{text:'Something needs you '+tick}]:[]});
const history={session:true,status:'ok',active:false,cursor:'k1',items:Array.from({length:40},(_,n)=>({kind:n%2?'reply':'note',text:'Message '+n+'. '+long.slice(0,90),at:Date.now()-100000+n*1000}))};
const person=(n,name)=>({id:'email:p'+n+'@example.com',kind:'person',name,strength:3-n/100,last_at:ago(n*7%95+1),signals:4,ids:[]});
const people=()=>[...Array.from({length:tick},(_,i)=>person(900+i,'Newcomer '+i)).map(p=>({...p,strength:9})),...Array.from({length:95},(_,n)=>person(n,'Person '+String(n).padStart(3,'0')))];
const orgs=()=>[...Array.from({length:tick},(_,i)=>({id:'org:new'+i,kind:'org',name:'New org '+i,strength:9,last_at:ago(1),signals:3,ids:[]})),
 ...Array.from({length:45},(_,n)=>({id:'org:o'+n,kind:'org',name:'Org '+String(n).padStart(3,'0'),strength:2-n/100,last_at:ago(n+1),signals:3,ids:[]}))];
const projects=()=>Array.from({length:40},(_,n)=>({id:'project:'+n,kind:'project',name:'Project '+String(n).padStart(3,'0'),strength:2-n/100,last_at:ago(n+1),signals:5,state:'active',ids:[]}));
const stages=[{name:'Contacted'},{name:'Agreed'},{name:'Live'},{name:'Stalled',side:true}];
const boards=()=>[{name:'Partner listings',stages,stuck:0,projects:[...Array.from({length:tick},(_,i)=>({id:'project:new'+i,name:'New listing '+i,stage:'Contacted',reason:'they asked',by:'review',at:ago(1),next:'Reply',days:0,org:{name:'New listing '+i,logo:null}})),
 ...Array.from({length:30},(_,n)=>({id:'project:b'+n,name:'Listing '+n,stage:'Contacted',reason:'they asked for a listing',by:'review',at:ago(n+2),next:'Wait for their reply',days:n,org:{name:'Listing '+n,logo:null}}))]}];
const lists=()=>({people:people(),quiet:[],new:[],orgs:orgs(),warmup:[],projects:projects(),projects_done:[]});
const graph=()=>{const l=lists();return {counts:{person:l.people.length,org:l.orgs.length,project:40},list_counts:Object.fromEntries(Object.entries(l).map(([k,v])=>[k,v.length])),whos_who:[],waiting:0,projects:l.projects,boards:boards()}};
const meeting=(id,days,h,title)=>{const d=new Date();d.setDate(d.getDate()+days);d.setHours(h,0,0,0);return {id,account_id:'cal',title,start:iso(d),end:iso(d.getTime()+30*6e4),all_day:false,location:'Room '+id,guests:2,url:null}};
const meetings=()=>[...Array.from({length:tick},(_,i)=>meeting(900+i,0,7,'Early call '+i)),...Array.from({length:28},(_,n)=>meeting(n+1,n%7,9+n%8,'Meeting '+n))];
const FL='fl_'+'a'.repeat(16),rules={relationships:{rule:'ask',details:false},meeting:{rule:'always',details:true}};
const fleet=()=>({on:true,kinds:{relationships:'who knows someone',meeting:'meeting context'},kind_rules:{relationships:['ask','always','never'],meeting:['ask','always','never']},no_details:[],scoped:[],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Example',asks:['relationships'],people:Array.from({length:30+tick},(_,n)=>({member_id:'m'+n,name:'Teammate '+String(n).padStart(2,'0'),role:n?'member':'owner',me:!n,answers_with:'Hosted',email:'t'+n+'@example.com'})),
  members:Array.from({length:30+tick},(_,n)=>({member_id:'m'+n,name:'Teammate '+String(n).padStart(2,'0'),rules})),rules,exceptions:{}}],waiting:[],asked:[],free:[],briefs:{},log:[],notices:[]});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid'),q=url.searchParams;res.setHeader('Content-Type','application/json');
 const send=v=>setTimeout(()=>res.end(JSON.stringify(v)),40);
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status')return send(status());
 if(url.pathname==='/api/graph')return send(graph());
 if(url.pathname==='/api/graph/list'){const all=lists()[q.get('list')]||[],cursor=Number(q.get('cursor')||0);return send({items:all.slice(cursor,cursor+30),next:cursor+30<all.length?String(cursor+30):null,total:all.length})}
 if(url.pathname==='/api/calendar')return send({meetings:meetings()});
 if(url.pathname==='/api/fleet')return send(fleet());
 if(url.pathname==='/api/cards/done')return send({cards:[],next:null,total:0});
 if(url.pathname==='/api/action'){const r=JSON.parse(body||'{}');return send(r.action==='card_chat'?history:{})}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const screens=[
 ['Cards',async page=>{await page.evaluate(()=>showTab('inbox'));await page.locator('#task-'+hex('a',4)+' .agent-head').waitFor();
  // a fold the reader opened in a card stays open through the refresh
  await page.locator('#task-'+hex('a',4)+' details.agent-more>summary').click()}],
 ['Card chat',async page=>{await page.evaluate(id=>{location.hash='#chat='+id},hex('a',1));await page.locator('#chat-log .bubble').first().waitFor()}],
 ['People',async page=>{await page.evaluate(()=>{location.hash='';showTab('people','people')});await page.locator('[data-pager="graph:people:closest"] .pager-rows>*').nth(29).waitFor()}],
 ['Organizations',async page=>{await page.evaluate(()=>showTab('people','orgs'));await page.locator('[data-pager="graph:orgs:closest"] .pager-rows>*').nth(29).waitFor()}],
 ['Projects board',async page=>{await page.evaluate(()=>showTab('people','projects'));await page.locator('#people-view .proj-board:not([hidden]) .tile').first().waitFor();
  await page.evaluate(()=>{for(const b of document.querySelectorAll('.lane-more'))b.click()})}],
 ['Projects list',async page=>{await page.locator('#page-head [data-proj-view="list"]').click();await page.locator('#people-view .proj-list:not([hidden])').waitFor()}],
 ['Calendar',async page=>{await page.evaluate(()=>showTab('calendar'));await page.locator('#calendar-view .cal-block').nth(3).waitFor()}],
 ['Connections',async page=>{await page.evaluate(()=>showTab('health','mail'));await page.locator('#connectors>article:not([hidden])').nth(20).waitFor()}],
 ['Fleet',async page=>{await page.evaluate(()=>showTab('fleet'));await page.locator('#fleet-view').getByText('Teammate 20').first().waitFor()}],
 ['Settings',async page=>{await page.evaluate(()=>showSettings('general'));await page.waitForTimeout(300)}]];
// The element on screen 300px down, tracked through the refresh: the same node, or (drawn anew) the one with its text
const mark=()=>{const words=x=>x.textContent.replace(/\s+/g,' ').trim().slice(0,80),same=x=>[...document.querySelectorAll('main '+x.tagName)].filter(y=>y.getClientRects().length&&words(y)===words(x)).length;
 // the row under the reader: the smallest element there whose words are its own (a date line repeats on every row)
 let el=document.elementFromPoint(innerWidth/2,300);while(el.parentElement&&el.parentElement.tagName!=='MAIN'&&(words(el).length<30||same(el)>1)&&el.parentElement.getBoundingClientRect().height<innerHeight/2)el=el.parentElement;window.__mark={el,tag:el.tagName,text:el.textContent.replace(/\s+/g,' ').trim().slice(0,80),top:el.getBoundingClientRect().top};
 window.__shifts=[];window.__obs=new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)window.__shifts.push(e.value)});window.__obs.observe({type:'layout-shift'});return window.__mark.text};
const moved=()=>{const m=window.__mark;let el=m.el.isConnected&&m.el.getClientRects().length?m.el:[...document.querySelectorAll('main '+m.tag)].find(x=>x.getClientRects().length&&x.textContent.replace(/\s+/g,' ').trim().slice(0,80)===m.text);
 window.__obs.disconnect();return {found:!!el,d:el?Math.round(el.getBoundingClientRect().top-m.top):null,cls:window.__shifts.reduce((a,b)=>a+b,0)}};
const errors=[],failures=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const anchoring of [true,false]){
  tick=0;
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'en-US',timezoneId:'Europe/Brussels'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  if(!anchoring)await context.addInitScript(()=>addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');s.textContent='*{overflow-anchor:none!important}';document.head.append(s)}));
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#task-'+hex('a',0)).waitFor();
  for(const [name,open] of screens){
   await open(page);await page.waitForTimeout(400);
   // A card chat's thread scrolls in its own box (the page itself stays put); every other screen scrolls the page
   const H=await page.evaluate(()=>{const l=document.querySelector('#chat-log'),box=l&&l.getClientRects().length&&getComputedStyle(l).overflowY!=='visible'?l:null;window.__box=box;
    const H=box?box.scrollHeight-box.clientHeight:document.documentElement.scrollHeight-innerHeight;if(box)box.scrollTop=Math.round(H/2);else scrollTo(0,Math.round(H/2));return H});
   assert.ok(H>400,`${name}: the page (or the thread) is taller than the screen (${H})`);
   const at=()=>page.evaluate(()=>Math.round(window.__box?window.__box.scrollTop:scrollY));
   await page.waitForTimeout(300);const y0=await at(),text=await page.evaluate(mark);
   for(let i=0;i<2;i++){tick++;await page.evaluate(()=>refresh());await page.waitForTimeout(700)}
   // a slow machine: give the lists that load after the refresh time to come back before measuring
   await page.waitForFunction(()=>{const m=window.__mark;return m.el.isConnected&&m.el.getClientRects().length||[...document.querySelectorAll('main '+m.tag)].some(x=>x.getClientRects().length&&x.textContent.replace(/\s+/g,' ').trim().slice(0,80)===m.text)},null,{timeout:6000}).catch(()=>{});await page.waitForTimeout(300);
   const r=await page.evaluate(moved),where=`${name}${anchoring?'':' (no scroll anchoring)'}: "${text.slice(0,30)}"`;
   if(!r.found||Math.abs(r.d)>2||r.cls>0.01)failures.push(`${where} moved ${r.d}px, CLS ${r.cls.toFixed(3)}, scrollY ${y0} -> ${await at()}`);
   if(name==='Cards'&&!await page.locator('#task-'+hex('a',4)+' details.agent-more').evaluate(d=>d.open))failures.push(where+': the fold the reader opened closed');
   if(process.env.NO_JUMPS_SHOT)await page.screenshot({path:`${process.env.NO_JUMPS_SHOT}/${name.replace(/\W+/g,'-').toLowerCase()}${anchoring?'':'-noanchor'}.png`})}
  // The phone's drawer scrolls in its own box: a new card above keeps the rows in view where they were
  await page.evaluate(()=>{showTab('inbox');drawerOpen()});await page.waitForTimeout(400);
  const side=await page.evaluate(()=>{const sc=document.querySelector('.app-side');sc.scrollTop=Math.round((sc.scrollHeight-sc.clientHeight)/2);return sc.scrollHeight-sc.clientHeight});
  assert.ok(side>200,`the drawer is taller than the screen (${side})`);await page.waitForTimeout(200);
  const row=await page.evaluate(()=>{const a=[...document.querySelectorAll('#side-cards a')].find(a=>a.getBoundingClientRect().top>300);window.__row={href:a.getAttribute('href'),top:a.getBoundingClientRect().top};return a.textContent});
  for(let i=0;i<2;i++){tick++;await page.evaluate(()=>refresh());await page.waitForTimeout(500)}
  const d=await page.evaluate(()=>Math.round(document.querySelector(`#side-cards a[href="${window.__row.href}"]`).getBoundingClientRect().top-window.__row.top));
  if(Math.abs(d)>2)failures.push(`Drawer${anchoring?'':' (no scroll anchoring)'}: "${row}" moved ${d}px`);
  if(process.env.NO_JUMPS_SHOT)await page.screenshot({path:`${process.env.NO_JUMPS_SHOT}/drawer${anchoring?'':'-noanchor'}.png`});
  await context.close()}
 assert.deepEqual(failures,[],'\n'+failures.join('\n'));
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('no jumps dashboard ok');
