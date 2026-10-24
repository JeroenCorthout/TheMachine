import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Projects like a CRM: New project from the board header, a lane's + Add, a person's page and the command palette; Edit,
// Won and Lost with why, Archive and Delete from a tile's ⋯ and the project page; a board to open on; the projects a
// review thought could belong here (Add or Leave out); value on tiles and lanes; on a phone the stage tabs start on a
// whole tab and the active one shows. Each action posts exactly one call. CRM_SHOT=<dir> also writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Trial'},{name:'Decision'},{name:'Won'},{name:'Stalled or declined',side:true}];
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const proj=(i,name,stage,extra={})=>({id:'project:'+i,name,stage,reason:'asked for a demo',by:'review',at:day(2),event_id:100+i,days:2,no_reply_days:0,org:{name,logo:null},...extra});
const fresh=()=>[{id:1,name:'CRM evaluations',stages,default:true,stuck:0,projects:[proj(1,'Paperkite','Demo',{value:12000}),proj(2,'Bramblebox','Demo',{value:3500}),proj(3,'Quillfeather','Trial'),
  proj(4,'Fernwave','Stalled or declined',{by:'owner',outcome:'lost',reason:'chose another tool'})],maybes:[{id:'project:9',name:'Tinker Lane',why:'asked about pricing, not a trial yet'},{id:'project:10',name:'Puffin Metrics',why:'an onboarding call'}]},
 {id:2,name:'Partner listings',stages:stages.slice(0,2).concat(stages.slice(-1)),stuck:0,projects:[proj(30,'Saffron Sync','Demo')],maybes:[]}];
let boards=fresh();
const card={node_id:'project:1',name:'Paperkite',board_id:1,stage:'Demo',org:'paperkite.example',value:12000,notes:'Wants SSO',people:[{id:'person:a',name:'Ada Lovelace'}],archived:false,outcome:null,outcome_reason:null};
const detail={id:'project:1',kind:'project',name:'Paperkite',strength:1,last_at:day(2),signals:3,state:'open',ids:[],links:[],recent:[],around:[],open:{},project:card,
 board:{board:'CRM evaluations',stage:'Demo',moves:[{stage:'Demo',reason:'asked for a demo',source:'review',at:day(2),event_id:101}]}};
const person={id:'person:a',kind:'person',name:'Ada Lovelace',strength:1,last_at:day(1),signals:2,ids:[],links:[],recent:[],around:[],open:{}};
const people=[{id:'person:a',name:'Ada Lovelace'},{id:'person:b',name:'Grace Hopper'}];
let calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node');res.end(JSON.stringify(n?(n.startsWith('person:')?person:detail):{projects:[{id:'project:1',kind:'project',name:'Paperkite',strength:1,last_at:day(2),signals:3}],people:[],quiet:[],new:[],orgs:[],boards,person_names:people,project_names:[]}));return}
 if(req.method!=='GET'&&!url.pathname.startsWith('/brand/'))calls.push(JSON.parse(body||'{}'));
 if(url.pathname==='/api/action'){const c=JSON.parse(body||'{}'),a=c.args||{};
  if(c.action==='project_save'){if(!a.node_id)boards[0].projects.push(proj(50,a.name,a.stage||'Discovery',{by:'owner'}));res.end(JSON.stringify({node_id:a.node_id||'project:50',name:a.name,created:!a.node_id}));return}
  if(c.action==='project_delete'){res.end(JSON.stringify({deleted:'Paperkite'}));return}
  if(c.action==='board_fit'){boards[0].maybes=boards[0].maybes.filter(m=>m.id!==a.node_id);res.end('{}');return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height,scheme='light')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base);
 await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
 await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());
 await page.locator('#people-view .proj-board:not([hidden]) .pboard').waitFor();return {context,page}};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const shot=async(page,name,full=true)=>{if(process.env.CRM_SHOT)await page.screenshot({path:`${process.env.CRM_SHOT}/${name}.png`,fullPage:full})};
const dialog=page=>page.locator('#project-dialog[open]');
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // Value on the tiles and the lane total; an owner's Lost says why
  assert.match(await page.locator('.tile[data-node="project:1"] .tile-val').textContent(),/12/);
  assert.match(await page.locator('.lane[data-lane="Demo"] .lane-total').textContent(),/15/);
  assert.match(await page.locator('.tile[data-node="project:4"] .tile-why.lost').textContent(),/^Lost: chose another tool/);
  // The projects a review thought could belong: Add posts board_fit and the row goes
  const fitsBox=page.locator('.board-fits');assert.match(await fitsBox.locator('summary').textContent(),/2 projects could belong here/);
  await fitsBox.locator('summary').click();await shot(page,'crm-board-1440',false);
  calls=[];await fitsBox.locator('[data-fit-add="project:9"]').click();await page.locator('.board-fits summary',{hasText:'1 project could belong here'}).waitFor();
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['board_fit',{board_id:1,node_id:'project:9',add:true}]]);
  calls=[];await page.locator('[data-fit-skip="project:10"]').click();await page.locator('.board-fits').waitFor({state:'detached'});
  assert.deepEqual(calls.map(c=>c.args.add),[false]);await page.waitForLoadState('networkidle');
  // New project from the header: the board shown, its first stage; a person from the list; one project_save
  calls=[];await page.locator('.board-head [data-proj-new]').click();await dialog(page).waitFor();
  assert.equal(await page.locator('#project-dialog-title').textContent(),'New project');
  assert.equal(await page.locator('#project-form [name="board"]').inputValue(),'1');assert.equal(await page.locator('#project-form [name="stage"]').inputValue(),'Discovery');
  await page.locator('#project-form [name="name"]').fill('Hazelbrook');await page.locator('#project-form [name="value"]').fill('8000');
  await page.locator('#project-form [name="person"]').fill('Grace Hopper');await page.locator('#project-form .ed-projects li',{hasText:'Grace Hopper'}).waitFor();
  await page.locator('#project-form [name="org"]').fill('hazelbrook.example');await shot(page,'crm-new-1440',false);
  await page.locator('#project-form button.primary').click();await dialog(page).waitFor({state:'detached'});
  assert.deepEqual(calls.map(c=>c.action),['project_save']);
  assert.deepEqual(calls[0].args,{name:'Hazelbrook',board_id:1,stage:'Discovery',org:'hazelbrook.example',value:'8000',close_date:'',notes:'',people:['person:b']});
  await page.locator('.tile[data-node="project:50"]').waitFor();await page.waitForLoadState('networkidle');
  // A lane's + Add picks that stage; switching board picks its first stage; Cancel posts nothing
  calls=[];await page.locator('.lane[data-lane="Trial"] .lane-add').click();await dialog(page).waitFor();
  assert.equal(await page.locator('#project-form [name="stage"]').inputValue(),'Trial');
  await page.locator('#project-form [name="board"]').selectOption('2');assert.equal(await page.locator('#project-form [name="stage"]').inputValue(),'Discovery');
  await page.locator('#project-form [name="board"]').selectOption('');assert.equal(await page.locator('#project-form [name="stage"]').count(),0);
  await page.locator('#project-form [data-pj-close]').last().click();assert.equal(calls.length,0);
  // A tile's ⋯: Edit opens it filled in; Won asks why and posts project_outcome
  const menu=page.locator('.tile[data-node="project:1"] .tile-menu');await menu.locator('summary').click();await menu.locator('[data-proj-edit]').click();await dialog(page).waitFor();
  assert.equal(await page.locator('#project-form [name="name"]').inputValue(),'Paperkite');assert.equal(await page.locator('#project-form [name="value"]').inputValue(),'12000');
  assert.equal(await page.locator('#project-form [name="notes"]').inputValue(),'Wants SSO');assert.deepEqual(await page.locator('#project-form .ed-projects li span').allTextContents(),['Ada Lovelace']);
  await page.locator('#project-form [name="notes"]').fill('Wants SSO and SAML');calls=[];await page.locator('#project-form button.primary').click();await dialog(page).waitFor({state:'detached'});
  assert.equal(calls[0].action,'project_save');assert.equal(calls[0].args.node_id,'project:1');assert.equal(calls[0].args.notes,'Wants SSO and SAML');assert.deepEqual(calls[0].args.people,['person:a']);
  await page.waitForLoadState('networkidle');calls=[];await menu.locator('summary').click();await menu.locator('[data-proj-outcome="won"]').click();await dialog(page).waitFor();
  assert.equal(await page.locator('#project-dialog-title').textContent(),'Won');await page.locator('#project-form [name="reason"]').fill('Signed the yearly plan');
  await page.locator('#project-form button.primary').click();await dialog(page).waitFor({state:'detached'});
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['project_outcome',{node_id:'project:1',outcome:'won',reason:'Signed the yearly plan'}]]);
  // Archive from the ⋯: one call
  await page.waitForLoadState('networkidle');calls=[];await page.locator('.tile[data-node="project:2"] .tile-menu summary').click();await page.locator('.tile[data-node="project:2"] [data-proj-archive]').click();
  await page.waitForFunction(()=>document.querySelector('#toast.show')?.textContent.includes('Archived'));
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['project_archive',{node_id:'project:2',archived:true}]]);
  // The board to open on
  await page.waitForLoadState('networkidle');calls=[];await page.locator('[data-board="Partner listings"]').click();await page.locator('.board-title',{hasText:'Partner listings'}).waitFor();
  await page.locator('.board-menu summary').click();await page.locator('[data-board-default="2"]').click();
  await page.waitForFunction(()=>document.querySelector('#toast.show')?.textContent.includes('opens on this board'));
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['board_default',{board_id:2}]]);
  // E edits a focused tile
  await page.locator('[data-board="CRM evaluations"]').click();await page.locator('.tile[data-node="project:3"]').focus();await page.keyboard.press('e');await dialog(page).waitFor();
  await page.keyboard.press('Escape');await dialog(page).waitFor({state:'detached'});
  // The project page: Edit, and the ⋯ with Won, Lost, Archive and Delete; its value, organisation and notes
  await page.locator('.tile[data-node="project:1"] .tile-name').click();await page.locator('.proj-facts').waitFor();
  assert.match(await page.locator('.proj-facts').textContent(),/Value 12,000 · paperkite\.example/);assert.match(await page.locator('.proj-facts').textContent(),/Wants SSO/);
  await shot(page,'crm-project-1440',false);
  calls=[];await page.locator('.graph-detail .fact-menu summary').click();await page.locator('.graph-detail [data-proj-delete]').click();await dialog(page).waitFor();
  assert.match(await page.locator('#project-form').textContent(),/goes for good/);await page.locator('#project-form [data-pj-delete]').click();await dialog(page).waitFor({state:'detached'});
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['project_delete',{node_id:'project:1'}]]);
  // The command palette has New project and a jump to each board
  const ids=await page.evaluate(()=>ACTIONS.flatMap(a=>a.id==='go-boards'?a.each().map(x=>x.id):[a.id]));
  for(const id of ['project-new','project-edit','project-won','project-lost','board-edit','go-board-1','go-board-2'])assert.ok(ids.includes(id),id+' in the palette');
  await context.close()}
 {boards=fresh();const {context,page}=await open(1440,900);
  // From a person's page: New project with them on it
  await page.evaluate(()=>graphOpen('person:a'));await page.locator('.person-page').waitFor();
  await page.locator('.person-menu summary').click();await page.locator('.person-menu [data-proj-new-here]').click();await dialog(page).waitFor();
  assert.deepEqual(await page.locator('#project-form .ed-projects li span').allTextContents(),['Ada Lovelace']);await context.close()}
 for(const [w,h] of [[390,844],[360,780]]){for(const scheme of ['light','dark']){boards=fresh();const {context,page}=await open(w,h,scheme);await fits(page,w+' '+scheme);
  // Stage tabs: every stage readable at once (they wrap, nothing scrolls or is cut off), the active one marked
  const row=page.locator('.stage-tabs');
  await page.locator('[data-stage-tab="Won"]').click();await page.locator('.lane.on[data-lane="Won"]').waitFor();
  const seen=await row.evaluate(r=>{const b=r.getBoundingClientRect();return {scrolls:r.scrollWidth>r.clientWidth+1,cut:[...r.children].filter(x=>{const t=x.getBoundingClientRect();return t.left<b.left-1||t.right>b.right+1}).length}});
  assert.deepEqual(seen,{scrolls:false,cut:0});assert.equal(await row.locator('[aria-selected="true"]').getAttribute('data-stage-tab'),'Won');
  await page.locator('[data-stage-tab="Demo"]').click();await page.locator('.lane.on[data-lane="Demo"]').waitFor();
  await shot(page,`crm-board-${w}-${scheme}`,false);
  // New project on a phone: the sheet fits
  await page.locator('.board-head [data-proj-new]').click();await dialog(page).waitFor();await fits(page,w+' dialog');await shot(page,`crm-new-${w}-${scheme}`,false);
  await page.locator('#project-form [data-pj-close]').first().click();
  // The list has its own New project in the page head on a phone
  await page.evaluate(()=>projectsViewSet('list'));assert.ok(await page.locator('.head-new[data-graph-new="project"]').isVisible());
  await context.close()}}
 assert.deepEqual(errors,[]);
 console.log('projects crm dashboard ok');
}finally{await browser?.close();server.close()}
