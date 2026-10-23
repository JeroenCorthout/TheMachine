import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Boards the owner makes: a review's suggestion on Projects ("Start a board for Hiring?") with Save, Edit and Not now,
// nothing a board until it's saved; the editor (name, stages renamed, reordered, added and removed, the Stalled row,
// which projects belong, projects added and taken off) for a new board, a suggestion and a board's ⋯ menu; a removed
// stage says where its projects go; Delete asks once more and keeps the projects. On a phone the editor is a sheet.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const stages=[{name:'Contacted'},{name:'Agreed'},{name:'Live'},{name:'Stalled or declined',side:true}];
const tile=(i,name,stage)=>({id:'project:'+i,name,stage,reason:'they asked',by:'review',at:day(2),event_id:100+i,next:'Wait',first:true,days:2,no_reply_days:0,org:null});
const fresh=()=>({boards:[{id:1,name:'Partner listings',about:'Partners we list with',side:true,stages,stuck:0,projects:[tile(1,'Paperkite','Contacted'),tile(2,'Bramblebox','Agreed'),tile(3,'Quillfeather','Agreed')]}],
 suggestions:[{id:7,name:'Hiring',about:'Roles we hire for',reason:'Three open roles',stages:[{name:'Sourced'},{name:'Interviews'},{name:'Hired'}],
  projects:[{id:'project:10',name:'Hire: support lead'},{id:'project:11',name:'Hire: designer'},{id:'project:12',name:'Hire: backend'}]}]});
let data=fresh(),calls=[];
const names=[{id:'project:1',name:'Paperkite'},{id:'project:2',name:'Bramblebox'},{id:'project:3',name:'Quillfeather'},{id:'project:10',name:'Hire: support lead'},
 {id:'project:11',name:'Hire: designer'},{id:'project:12',name:'Hire: backend'},{id:'project:20',name:'Fernwave'}];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({projects:[],people:[],quiet:[],new:[],orgs:[],boards:data.boards,board_suggestions:data.suggestions,project_names:names}));return}
 if(url.pathname==='/api/action'){const c=JSON.parse(body||'{}');calls.push(c);const a=c.args;
  if(c.action==='board_save'){data.suggestions=data.suggestions.filter(s=>s.id!==a.suggestion_id);const b={id:a.board_id||data.boards.length+1,name:a.name,about:a.about,side:a.side,stuck:0,
    stages:a.stages.map(s=>({name:s.name})).concat(a.side?[{name:'Stalled or declined',side:true}]:[]),projects:a.projects.map(id=>({...tile(+id.split(':')[1],names.find(n=>n.id===id).name,a.stages[0].name),by:null,sorting:true}))};
   data.boards=data.boards.filter(x=>x.id!==b.id).concat(b);res.end(JSON.stringify({board_id:b.id,name:a.name,added:a.projects.length,removed:0,moved:a.board_id?[{from:'Agreed',to:'Contacted',count:2}]:[]}));return}
  if(c.action==='board_dismiss'){data.suggestions=[];res.end('{"status":"dismissed"}');return}
  if(c.action==='board_delete'){const b=data.boards.find(x=>x.id===a.board_id);data.boards=data.boards.filter(x=>x!==b);res.end(JSON.stringify({deleted:b.name,projects:b.projects.length}));return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height)=>{const mobile=width<600;const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base);
 await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
 await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());await page.locator('#people-view .board-suggest, #people-view .proj-board').first().waitFor();return {context,page}};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const stageNames=page=>page.locator('[data-ed-stage]').evaluateAll(x=>x.map(i=>i.value));
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // One calm suggestion, its stages in order; nothing is a board yet
  const box=page.locator('.board-suggest');assert.match(await box.textContent(),/Start a board for Hiring\?/);assert.match(await box.textContent(),/3 projects · Three open roles/);
  assert.deepEqual(await box.locator('.stage-flow li').allTextContents(),['Sourced','Interviews','Hired']);
  assert.equal(await page.locator('[data-board="Hiring"]').count(),0);assert.equal(calls.length,0);
  // Edit: the editor opens filled in; take a project off, add one, rename and reorder stages, add one, drop the Stalled row
  await box.locator('[data-suggest-edit]').click();await page.locator('#board-dialog[open]').waitFor();
  assert.equal(await page.locator('#board-dialog-title').textContent(),'Start a board');
  assert.equal(await page.locator('#board-form [name="name"]').inputValue(),'Hiring');assert.deepEqual(await stageNames(page),['Sourced','Interviews','Hired']);
  await page.locator('[data-ed-drop="project:11"]').click();await page.locator('#board-form [name="add"]').fill('Fernwave');
  assert.deepEqual(await page.locator('.ed-projects li span').allTextContents(),['Hire: support lead','Hire: backend','Fernwave']);
  await page.locator('[data-ed-stage="1"]').fill('Talks');await page.locator('[data-ed-up="1"]').click();assert.deepEqual(await stageNames(page),['Talks','Sourced','Hired']);
  await page.locator('[data-ed-add]').click();await page.locator('[data-ed-stage="3"]').fill('Started');await page.locator('#board-form [name="side"]').uncheck();
  await page.locator('[data-ed-stage="3"]').fill('');await page.locator('[data-ed-stage="0"]').fill('Hired');await page.locator('#board-form button.primary').click();
  assert.match(await page.locator('[data-ed-error]').textContent(),/its own name/);assert.equal(calls.length,0,'nothing saved while two stages share a name');
  await page.locator('[data-ed-stage="0"]').fill('Talks');await page.locator('#board-form button.primary').click();
  await page.locator('#board-dialog:not([open])').waitFor({state:'attached'});
  const save=calls.at(-1);assert.equal(save.action,'board_save');
  assert.deepEqual(save.args,{suggestion_id:7,name:'Hiring',stages:[{name:'Talks'},{name:'Sourced'},{name:'Hired'}],side:false,about:'Roles we hire for',projects:['project:10','project:12','project:20']});
  // Saved: the new board shows, its projects waiting to be sorted from their messages
  await page.locator('.board-title',{hasText:'Hiring'}).waitFor();assert.match(await page.locator('#toast').textContent(),/Saved Hiring\. Sorting its projects from your messages\./);
  assert.equal(await page.locator('.board-suggest').count(),0);assert.match(await page.locator('.lane[data-lane="Talks"] .tile-why').first().textContent(),/Sorting from your messages/);
  assert.equal(await page.locator('.lane[data-lane="Stalled or declined"]').count(),0);
  // Edit a board from its ⋯ menu: removing a stage with projects says where they go
  await page.locator('[data-board="Partner listings"]').click();await page.locator('.board-title',{hasText:'Partner listings'}).waitFor();
  await page.locator('.board-menu summary').click();await page.locator('[data-board-edit]').click();await page.locator('#board-dialog[open]').waitFor();
  assert.equal(await page.locator('#board-dialog-title').textContent(),'Edit board');assert.equal(await page.locator('#board-form [name="about"]').inputValue(),'Partners we list with');
  await page.locator('[data-ed-del="1"]').click();assert.match(await page.locator('#board-form .ed-note.warn[role="status"]').textContent(),/2 in Agreed go to Contacted\./);
  await page.locator('[data-ed-stage="0"]').fill('Asked');await page.locator('#board-form button.primary').click();await page.locator('#board-dialog:not([open])').waitFor({state:'attached'});
  assert.deepEqual(calls.at(-1).args.stages,[{name:'Asked',was:'Contacted'},{name:'Live',was:'Live'}]);assert.equal(calls.at(-1).args.board_id,1);
  assert.match(await page.locator('#toast').textContent(),/2 from Agreed moved to Contacted\./);
  // Delete asks once more and keeps the projects
  await page.locator('.board-menu summary').click();await page.locator('[data-board-edit]').click();await page.locator('#board-dialog[open]').waitFor();
  const n=calls.length;await page.locator('[data-ed-delete]').click();assert.equal(calls.length,n);assert.match(await page.locator('[data-ed-delete]').textContent(),/Its projects stay/);
  await page.locator('[data-ed-delete]').click();await page.locator('#board-dialog:not([open])').waitFor({state:'attached'});
  assert.deepEqual(calls.at(-1),{action:'board_delete',args:{board_id:1}});assert.match(await page.locator('#toast').textContent(),/Deleted Partner listings\. Its 3 projects stay\./);
  // New board from scratch from the menu
  await page.locator('.board-menu summary').click();await page.locator('[data-board-new]').click();await page.locator('#board-dialog[open]').waitFor();
  assert.equal(await page.locator('#board-dialog-title').textContent(),'New board');assert.equal(await page.locator('#board-form [name="name"]').inputValue(),'');
  await page.keyboard.press('Escape');await context.close()}
 // Not now: the suggestion goes and is not posted as anything else
 data=fresh();calls=[];
 {const {context,page}=await open(390,844);
  await page.locator('[data-suggest-dismiss]').click();await page.locator('.board-suggest').waitFor({state:'detached'});
  assert.deepEqual(calls.map(c=>c.action),['board_dismiss']);assert.deepEqual(calls[0].args,{suggestion_id:7});await context.close()}
 // A phone: the editor is a sheet across the screen; an edit flow by touch; Save stays in reach
 data=fresh();calls=[];
 for(const width of [390,360]){const {context,page}=await open(width,780);await fits(page,width+' suggestion');
  await page.locator('[data-suggest-edit]').tap();await page.locator('#board-dialog[open]').waitFor();
  const box=await page.locator('#board-dialog').boundingBox();assert.ok(box.width>=width-1&&box.x<=1,'a sheet as wide as the screen');
  await page.locator('[data-ed-down="0"]').tap();assert.deepEqual(await stageNames(page),['Interviews','Sourced','Hired']);
  await page.locator('[data-ed-del="2"]').tap();assert.deepEqual(await stageNames(page),['Interviews','Sourced']);
  assert.ok(await page.locator('#board-form button.primary').isVisible(),'Save is in view');
  const dims=await page.evaluate(()=>{const d=document.querySelector('#board-dialog');return {sw:d.scrollWidth,cw:d.clientWidth}});assert.ok(dims.sw<=dims.cw,'nothing in the sheet is wider than it');
  await page.locator('#board-form button.primary').tap();await page.locator('#board-dialog:not([open])').waitFor({state:'attached'});
  assert.deepEqual(calls.at(-1).args.stages,[{name:'Interviews'},{name:'Sourced'}]);await fits(page,width+' saved');
  data=fresh();calls=[];await context.close()}
 assert.deepEqual(errors,[]);
 console.log('board editor dashboard ok');
}finally{await browser?.close();server.close()}
