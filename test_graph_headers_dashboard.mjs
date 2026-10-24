import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Every Graph header (person, organisation, project, board), in view and edit mode, on a phone and a desktop: the name
// stays on one line, never broken mid-word (a long one ends in an ellipsis, the full name on hover); a short one shows
// whole; the actions never cover it; while editing only Cancel and Save are offered. HEADERS_SHOT=<dir> writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const LONG={person:'Maximiliane Konstantinopoulou-Vandenberghe',org:'Internationale Vereniging voor Duurzame Materiaalwetenschappen',project:'Battery materials rollout for the second production line',board:'Enterprise partnerships and strategic accounts'};
const node=(kind,id,name,extra={})=>({id,kind,name,strength:2,last_at:day(1),signals:3,ids:[],links:[],recent:[],around:[],open:{},...extra});
const nodes=long=>({
 'org:umicore.example':node('org','org:umicore.example',long?LONG.org:'Umicore',{about:'Materials technology, 10001+ people',profile:{website:'https://umicore.example',industry:'Materials',location:'Brussels'}}),
 'person:a':node('person','person:a',long?LONG.person:'Ada Lovelace',{org:'org:umicore.example',org_name:'Umicore',profile:{title:'Head of procurement'}}),
 'project:1':node('project','project:1',long?LONG.project:'Umicore',{project:{node_id:'project:1',name:long?LONG.project:'Umicore',board_id:1,stage:'Demo',org:'umicore.example',value:12000,notes:'',people:[],archived:false,outcome:null},board:{board:'Sales',stage:'Demo',moves:[]}})});
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Won'},{name:'Stalled or declined',side:true}];
const boards=long=>[{id:1,name:long?LONG.board:'Sales',stages,default:true,stuck:0,maybes:[],projects:[{id:'project:1',name:'Umicore',stage:'Demo',by:'owner',at:day(1),days:1,no_reply_days:0,org:{name:'Umicore',logo:null}}]}];
let long=false;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node');res.end(JSON.stringify(n?nodes(long)[n]:{projects:[],people:[],quiet:[],new:[],orgs:[],whos:[],boards:boards(long),person_names:[],project_names:[]}));return}
 res.statusCode=url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shotDir=process.env.HEADERS_SHOT,errors=[];let browser;
const shot=async(page,name)=>{if(shotDir)await page.screenshot({path:`${shotDir}/${name}.png`})};
// The name's box: one line tall, and whether it shows whole. The actions' visible buttons, and that none covers the name.
const measure=(page,sel)=>page.locator(sel).evaluate(h=>{const cs=getComputedStyle(h),line=parseFloat(cs.lineHeight)||parseFloat(cs.fontSize)*1.3,r=h.getBoundingClientRect();
 const row=h.closest('.name-row,.board-head'),acts=[...(row?.querySelectorAll('.org-actions>button,.org-actions>details>summary,.board-menu>summary')||[])].filter(b=>b.checkVisibility());
 const hit=acts.find(b=>{const q=b.getBoundingClientRect();return q.left<r.right-1&&q.right>r.left+1&&q.top<r.bottom-1&&q.bottom>r.top+1});
 return {lines:Math.round(r.height/line),whole:h.scrollWidth<=h.clientWidth+1,ellipsis:cs.textOverflow==='ellipsis',title:h.title,text:h.textContent,acts:acts.map(b=>b.textContent.trim()||b.getAttribute('aria-label')),covered:hit?.textContent.trim()||'',wide:document.documentElement.scrollWidth>innerWidth}});
const check=(m,where,{whole})=>{assert.equal(m.lines,1,`${where}: the name is on one line ${JSON.stringify(m)}`);assert.ok(!m.covered,`${where}: ${m.covered} covers the name`);assert.ok(!m.wide,`${where}: wider than the screen`);
 if(whole)assert.ok(m.whole,`${where}: "${m.text}" shows whole ${JSON.stringify(m)}`);else assert.ok(m.whole||m.ellipsis&&m.title===m.text,`${where}: a cut name ends in an ellipsis with the full name on hover ${JSON.stringify(m)}`)};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440])for(const isLong of [false,true]){long=isLong;const mobile=width<600,tag=`${width}${isLong?'-long':''}`;
  const context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile,locale:'en-GB'}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(tag+': '+e.message));await page.goto(base);await page.waitForFunction(()=>typeof people==='function');
  await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
  const whole=!isLong;
  // Board header
  await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());await page.locator('#people-view .proj-board:not([hidden]) .board-title').waitFor();
  let m=await measure(page,'#people-view .board-title');check(m,`board ${tag}`,{whole});await shot(page,`board-${tag}`);
  // Organisation: view, then edit (New project goes; only Cancel and Save)
  await page.evaluate(()=>people('org:umicore.example'));await page.locator('#people-view .org-page').waitFor();
  const org='#people-view .org-page>.name-row h3';
  m=await measure(page,org);check(m,`org view ${tag}`,{whole});assert.deepEqual(m.acts,['New project','Edit','⋯'],`org view actions ${tag}`);await shot(page,`org-view-${tag}`);
  await page.locator('#people-view [data-org-edit]').click();await page.locator('#people-view .org-editing').waitFor();
  m=await measure(page,org);check(m,`org edit ${tag}`,{whole});assert.deepEqual(m.acts,['Cancel','Save'],`org edit: only Cancel and Save ${tag}`);await shot(page,`org-edit-${tag}`);
  // Person: view, then edit
  await page.evaluate(()=>people('person:a'));await page.locator('#people-view .person-page:not(.org-page)').waitFor();
  const per='#people-view .person-page>.name-row h3';
  m=await measure(page,per);check(m,`person view ${tag}`,{whole});assert.deepEqual(m.acts,['Edit','⋯'],`person view actions ${tag}`);await shot(page,`person-view-${tag}`);
  await page.locator('#people-view [data-org-edit]').click();await page.locator('#people-view .org-editing').waitFor();
  m=await measure(page,per);check(m,`person edit ${tag}`,{whole});assert.deepEqual(m.acts,['Cancel','Save'],`person edit: only Cancel and Save ${tag}`);await shot(page,`person-edit-${tag}`);
  // Project page (edits in a dialog, whose title is not the name)
  await page.evaluate(()=>people('project:1'));await page.locator('#people-view .graph-detail [data-proj-edit]').first().waitFor();
  m=await measure(page,'#people-view .graph-detail>.name-row h3');check(m,`project view ${tag}`,{whole});assert.deepEqual(m.acts,['Edit','⋯'],`project actions ${tag}`);await shot(page,`project-view-${tag}`);
  await page.locator('#people-view .graph-detail>.name-row [data-proj-edit]').click();await page.locator('#project-dialog[open]').waitFor();await shot(page,`project-edit-${tag}`);
  await page.keyboard.press('Escape');await page.locator('#project-dialog[open]').waitFor({state:'detached'});
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('graph headers: one line, never covered, only Cancel and Save while editing, at 390 and 1440 px');
}finally{await browser?.close();server.close()}
