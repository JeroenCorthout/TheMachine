import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Graph › Projects as a board: Board is the default view, List one click away and remembered on this device. Chips switch
// boards, a long column shows "Show N more", a tile opens its project page, a drag or the ⋯ menu moves it (graph_stage)
// and nothing else is posted. On a phone one stage at a time with stage tabs. Nothing is wider than the screen.
// An Only-you board says so in its header; a project page sets who sees that project. BOARDS_SHOT=<dir> also writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const stages=[{name:'Contacted',hint:'Asked, no yes yet'},{name:'Agreed',hint:'They said yes'},{name:'In review'},{name:'Partly live'},{name:'Live'},{name:'Stalled or declined',side:true}];
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const proj=(i,name,stage,extra={})=>({id:'project:'+i,name,stage,reason:'they asked for a listing',by:'review',at:day(2),event_id:100+i,next:'Wait for their reply',first:stage==='Contacted',days:2,no_reply_days:0,org:{name,logo:null},...extra});
const partners=['Paperkite','Bramblebox','Quillfeather','Otterly Mail','Fernwave','Copperleaf Apps','Puffin Metrics','Tinker Lane','Hazelbrook Software','Driftwood Docs','Pebblepath'];
const boards=[
 {name:'Partner listings',stages,stuck:1,sorting:1,projects:[...partners.map((n,i)=>proj(i+1,n,'Contacted',i===1?{no_reply_days:14,card:{id:'0123456789abcdef',title:'Follow up with Bramblebox'}}:{})),
  proj(20,'Larkfield Labs','Agreed'),proj(23,'Mossgate','Contacted',{by:null,sorting:true,reason:null,at:null}),
  proj(24,'Nettlebay','Contacted',{by:null,sorting:false,reason:null,at:null,sort_note:'only an intro call so far',last_at:day(3),last_event_id:124}),
  proj(25,'Oakhollow','Contacted',{by:null,sorting:false,reason:null,at:null,sort_failed:'Sorting failed, trying again',last_at:day(1)}),proj(21,'Willowtick','Live',{by:'owner',event_id:null}),proj(22,'Lanternfish','Stalled or declined',{reason:'they said no'})]},
 {name:'Customer onboarding',visibility:'only_you',stages:stages.slice(0,2).concat(stages.slice(-1)),stuck:0,projects:[proj(30,'Saffron Sync','Agreed')]}];
const list=[{id:'project:1',kind:'project',name:'Paperkite',strength:1,last_at:day(2),signals:3}];
const detail={id:'project:1',kind:'project',name:'Paperkite',strength:1,last_at:day(2),signals:3,ids:[],links:[],recent:[],around:[],open:{},
 board:{board:'Partner listings',stage:'Contacted',next:'Wait for their reply',visibility:null,board_visibility:'team',moves:[{stage:'Contacted',reason:'they asked for a listing',source:'review',at:day(2),event_id:101}]}};
let calls=[],nodeAsks=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node');if(n)nodeAsks.push(n);res.end(JSON.stringify(n?detail:{projects:list,people:[],quiet:[],new:[],orgs:[],boards}));return}
 if(req.method!=='GET'&&!url.pathname.startsWith('/brand/'))calls.push(JSON.parse(body||'{}'));
 if(url.pathname==='/api/action'){const c=JSON.parse(body||'{}');if(c.action==='graph_stage'){const p=boards[0].projects.find(x=>x.id===c.args.node_id);Object.assign(p,{stage:c.args.stage,by:'owner',event_id:null,days:0})}res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height,scheme='light',context)=>{const mobile=width<600;
 context=context||await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base);
 await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
 await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());
 await page.locator('#people-view .proj-board, #people-view .proj-list').first().waitFor({state:'attached'});return {context,page}};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const shot=async(page,name)=>{if(process.env.BOARDS_SHOT)await page.screenshot({path:`${process.env.BOARDS_SHOT}/${name}.png`,fullPage:true})};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // Board is the default, with the List / Board toggle in the header
  await page.locator('#people-view .proj-board:not([hidden]) .pboard').waitFor();
  assert.equal(await page.locator('#people-view .proj-list').getAttribute('hidden'),'');
  assert.equal(await page.locator('#page-head .view-mode [data-proj-view]').count(),2);
  const lanes=await page.locator('.pboard .lane').evaluateAll(l=>l.map(x=>x.dataset.lane));assert.deepEqual(lanes,stages.map(s=>s.name));
  // A long column shows 8, then the rest on "Show N more"
  const contacted=page.locator('.lane[data-lane="Contacted"]');
  assert.equal(await contacted.locator('.tile').count(),8);assert.equal((await contacted.locator('.lane-more').textContent()).trim(),'Show 6 more');
  await contacted.locator('.lane-more').click();assert.equal(await contacted.locator('.tile').count(),14);
  // The tile: next step, why it moved with the date, and the no-reply chip with its card
  const t=await page.locator('.tile[data-node="project:1"]').textContent();assert.match(t,/Wait for their reply/);assert.match(t,/Added: they asked for a listing · \w{3} \d{1,2} \w{3}/);
  const stuck=page.locator('.tile[data-node="project:2"]');assert.match(await stuck.textContent(),/No reply in 14 days/);assert.equal(await stuck.locator('a[href="#card=0123456789abcdef"]').count(),1);
  assert.match(await page.locator('.tile[data-node="project:21"]').textContent(),/Moved to Live by hand/);
  // A project waiting to be sorted says so; one a review couldn't place says why with its latest message; a failed sort shows
  assert.match(await page.locator('.tile[data-node="project:23"] .tile-why').textContent(),/^Sorting from its messages…$/);
  assert.match(await page.locator('.tile[data-node="project:24"] .tile-why').textContent(),/^Not sorted: only an intro call so far · \w{3} \d{1,2} \w{3}$/);
  assert.equal(await page.locator('.tile[data-node="project:24"] .tile-why [data-quote-event="124"]').count(),1);
  assert.match(await page.locator('.tile[data-node="project:25"] .tile-why.failed').textContent(),/^Sorting failed, trying again · /);
  assert.match(await page.locator('.board-sub').textContent(),/17 projects · 1 waiting on a reply · sorting 1 · a review moves them as your messages come in/);
  assert.match(await page.locator('.lane[data-lane="Contacted"] .lane-sum').textContent(),/1 with no reply in 10\+ days/);
  await fits(page,'1440');await shot(page,'board-1440');
  // Chips switch boards, and the board shown is remembered
  await page.locator('[data-board="Customer onboarding"]').click();await page.locator('.board-title',{hasText:'Customer onboarding'}).waitFor();
  assert.equal(await page.evaluate(()=>sideStore.get('board-shown')),'Customer onboarding');assert.match(await page.locator('.board-sub').textContent(),/^Only you · 1 project/);
  await page.locator('[data-board="Partner listings"]').click();await page.locator('.board-title',{hasText:'Partner listings'}).waitFor();
  // The ⋯ menu moves a tile: one graph_stage post, nothing else
  calls=[];const menu=page.locator('.tile[data-node="project:20"] .tile-menu');await menu.locator('summary').click();
  await menu.locator('[data-stage-to="In review"]').click();
  await page.locator('.lane[data-lane="In review"] .tile[data-node="project:20"]').waitFor();
  assert.deepEqual(calls.map(c=>c.action),['graph_stage']);assert.deepEqual(calls[0].args,{node_id:'project:20',stage:'In review'});
  // A drag moves it too
  calls=[];await page.locator('.tile[data-node="project:20"]').dragTo(page.locator('.lane[data-lane="Live"]'));
  await page.locator('.lane[data-lane="Live"] .tile[data-node="project:20"]').waitFor();
  assert.deepEqual(calls.map(c=>[c.action,c.args.stage]),[['graph_stage','Live']]);
  // Clicking a tile opens the project page, with its stage and moves
  calls=[];await page.locator('.tile[data-node="project:1"] .tile-next').click();await page.locator('.graph-stage').waitFor();
  assert.ok(nodeAsks.includes('project:1'));assert.match(await page.locator('.graph-stage').textContent(),/Contacted on Partner listings/);
  assert.equal(calls.length,0,'opening posts nothing');
  // Who sees this project: like its board, or Only you / Team for this one
  const seen=page.locator('[data-project-seen="project:1"]');assert.equal(await seen.inputValue(),'');assert.match(await seen.locator('option').first().textContent(),/Like its board \(team\)/);
  await shot(page,'project-1440');await seen.selectOption('only_you');await page.locator('#toast',{hasText:'Only you see this project now.'}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='project_visibility').map(c=>c.args),[{node_id:'project:1',visibility:'only_you'}]);calls=[];
  // List is remembered on this device
  await page.evaluate(()=>history.back());await page.locator('#page-head .view-mode').waitFor();
  await page.locator('#page-head [data-proj-view="list"]').click();
  assert.equal(await page.locator('#people-view .proj-board').getAttribute('hidden'),'');
  assert.equal(await page.evaluate(()=>sideStore.get('projects-view')),'list');
  const again=await open(1440,900,'light',context);await again.page.locator('#people-view .proj-list:not([hidden])').waitFor();
  assert.equal(await again.page.locator('#people-view .proj-board').getAttribute('hidden'),'');
  await context.close()}
 for(const [w,h] of [[900,1000],[390,844],[360,780]]){for(const scheme of ['light','dark']){const {context,page}=await open(w,h,scheme);
  await page.locator('#people-view .proj-board:not([hidden]) .pboard').waitFor();await fits(page,w+' '+scheme);
  if(w<600){
   // One stage at a time, with stage tabs and a board picker
   assert.equal(await page.locator('.lane:visible').count(),1);assert.ok(await page.locator('.stage-tabs').isVisible());assert.ok(await page.locator('.board-pick').isVisible());
   await page.locator('[data-stage-tab="Agreed"]').click();await page.locator('.lane.on[data-lane="Agreed"]').waitFor();assert.equal(await page.locator('.lane:visible').count(),1);
   assert.ok(!(await page.locator('.lane[data-lane="Contacted"]').isVisible()));
   await page.locator('[data-stage-tab="Contacted"]').click();
   assert.equal(await page.locator('.lane.on .tile').count(),6);
   // The move menu works by touch
   const menu=page.locator('.tile[data-node="project:3"] .tile-menu');await menu.locator('summary').tap();assert.ok(await menu.locator('[data-stage-to="Agreed"]').isVisible());
   await fits(page,w+' menu');
  }else assert.ok(!(await page.locator('.stage-tabs').isVisible()));
  await shot(page,`board-${w}-${scheme}`);await context.close()}}
 assert.deepEqual(errors,[]);
 console.log('boards dashboard ok');
}finally{await browser?.close();server.close()}
