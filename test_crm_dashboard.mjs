import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The Graph as a CRM on the dashboard: Mine and Filter on the lists (the filters go to the Machine), the filter sheet,
// Mine and owners on the board, an owner on a page and its change in the timeline, an organization's websites with
// Split off, Same organization? and Add a website…, and a CSV import with its preview, mapping and Undo. On a phone and
// a desktop. CRM_SHOT=<dir> writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const node=(kind,id,name,extra={})=>({id,kind,name,strength:2,last_at:day(1),signals:3,ids:[],links:[],recent:[],around:[],open:{},...extra});
const owners=[{id:'m-kim',name:'Kim Vale',fleet:'Acme'},{id:'m-ola',name:'Ola Berg',fleet:'Acme'}];
const globex=node('org','org:globex.example','Globex',{domain:'globex.example',domains:['globex.example','globex.io'],aliases:['org:globex.io'],profile:{website:'https://globex.example'},
 owner:'m-kim',owner_name:'Kim Vale',changes:[{field:'owner',old:{id:'me'},new:{id:'m-kim',name:'Kim Vale'},at:day(2)},{field:'merged',new:{id:'org:globex.io',name:'globex.io'},at:day(3)}],
 doubles:[{a:{id:'org:globex.example',name:'Globex'},b:{id:'org:globex-group.example',name:'Globex Group',domain:'globex-group.example'},why:'Same company name on another website'}]});
const projects=[{id:'project:1',kind:'project',name:'Globex pilot',state:'active',signals:3,last_at:day(1),value:12000,close_date:'2026-11-30',board:'Sales',stage:'Demo',owner:'m-kim',owner_name:'Kim Vale'},
 {id:'project:2',kind:'project',name:'Umber deal',state:'active',signals:2,last_at:day(2),value:3000,close_date:'2026-12-20',board:'Sales',stage:'Discovery'}];
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Won'}];
const tiles=[{id:'project:1',name:'Globex pilot',stage:'Demo',days:3,value:12000,close_date:'2026-11-30',owner:'m-kim',owner_name:'Kim Vale'},{id:'project:2',name:'Umber deal',stage:'Discovery',days:1,value:3000}];
const boards=[{id:1,name:'Sales',stages,default:true,stuck:0,maybes:[],projects:tiles}];
const ada=node('person','person:a','Ada Lovelace',{ids:['email:ada@globex.example'],org:'org:globex.example',side:'work'});
const preview=mapping=>({kind:'person',columns:[{index:0,header:'Full name',field:'name',samples:['Ada Lovelace','Nia Holm']},{index:1,header:'E-mail',field:'email',samples:['ada@globex.example']},{index:2,header:'Company',field:mapping?.['2']??'org',samples:['Globex','Newco']}],
 fields:[{id:'name',name:'Name'},{id:'email',name:'Email'},{id:'org',name:'Organization'},{id:'title',name:'Title'}],counts:{new:1,match:1,skip:1},total:3,
 rows:[{line:2,status:'match',why:'Same email',match_name:'Ada Lovelace',record:{name:'Ada Lovelace'}},{line:3,status:'new',record:{name:'Nia Holm'}},{line:4,status:'skip',why:'No name',record:{}}]});
let calls=[],lists=[],uploads=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){const b=JSON.parse(body);calls.push(b);if(req.headers['x-upload'])uploads.push(b.action+':'+req.headers['x-upload']);
  const out=b.action==='graph_import_preview'?preview(b.args.mapping):b.action==='graph_import_run'?{import_id:8,kind:'person',new:1,updated:1,skipped:1}
   :b.action==='graph_org_merge'?{node_id:'org:globex.example'}:{ok:true};
  res.end(JSON.stringify(out));return}
 if(url.pathname==='/api/graph/list'){const p=Object.fromEntries(url.searchParams);lists.push(p);const all=p.list==='orgs'?[globex]:p.list==='projects'?projects:[ada];
  const items=all.filter(x=>(p.owner!=='me'||!x.owner)&&(!p.value_min||(x.value||0)>=+p.value_min));
  res.end(JSON.stringify({items,next:null,sort:p.sort,total:items.length}));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node'),q=(url.searchParams.get('q')||'').toLowerCase();
  if(q){res.end(JSON.stringify({found:[globex,node('org','org:umber.example','Umber',{domain:'umber.example'})].filter(x=>x.name.toLowerCase().includes(q))}));return}
  if(n){res.end(JSON.stringify({...(n==='org:globex.example'?globex:ada),owners}));return}
  res.end(JSON.stringify({list_counts:{people:1,quiet:0,new:0,work:1,personal:0,orgs:1,warmup:0,projects:2,projects_done:0},counts:{},people:[ada],orgs:[globex],projects,boards,owners,
   org_doubles:globex.doubles,last_import:{id:7,kind:'person',new:3},person_names:[{id:'person:a',name:'Ada Lovelace'}],project_names:[]}));return}
 res.statusCode=url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shotDir=process.env.CRM_SHOT,errors=[];let browser;
const shot=async(page,name)=>{if(shotDir)await page.screenshot({path:`${shotDir}/${name}.png`,fullPage:false})};
const fits=async(page,where)=>{const r=await page.evaluate(()=>{const d=document.querySelector('dialog[open]')?.getBoundingClientRect();return {wide:document.documentElement.scrollWidth>innerWidth,d:d&&{l:d.left,r:d.right},w:innerWidth}});
 assert.ok(!r.wide,where+': wider than the screen');if(r.d)assert.ok(r.d.l>=-1&&r.d.r<=r.w+1,where+': the dialog fits')};
const last=(list)=>lists.filter(l=>l.list===list).at(-1);
const until=async(test,what)=>{for(let i=0;i<60&&!test();i++)await new Promise(r=>setTimeout(r,50));assert.ok(test(),what)};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){const mobile=width<600,tag=String(width);calls=[];lists=[];uploads=[];
  const context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile,locale:'en-GB'}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(tag+': '+e.message));page.on('dialog',d=>d.accept());await page.goto(base);await page.waitForFunction(()=>typeof people==='function');
  const click=sel=>mobile?page.locator(sel).first().tap():page.locator(sel).first().click();
  await page.evaluate(()=>{localStorage.removeItem('graph-filters');sideStore.set('projects-view','list');document.querySelector('nav [data-tab="people"]').click()});
  await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());
  // Projects list: Mine and Filter; Mine asks the Machine for the owner's own only
  await page.locator('#people-view [data-crm-mine="projects"]').waitFor();await page.locator('#people-view [data-graph-panel="projects"] .graph-list article').first().waitFor();
  await fits(page,'projects '+tag);await shot(page,`projects-list-${tag}`);
  await click('#people-view [data-crm-mine="projects"]');await page.waitForFunction(()=>document.querySelector('[data-crm-mine="projects"]')?.getAttribute('aria-pressed')==='true');
  await page.waitForFunction(()=>document.querySelectorAll('[data-graph-panel="projects"] .graph-list article').length===1);
  assert.equal(last('projects').owner,'me');
  // The filter sheet: board and value; the active filters show with Clear
  await click('#people-view [data-crm-filter="projects"]');await page.locator('#crm-dialog[open]').waitFor();
  assert.equal(await page.locator('#crm-dialog-title').textContent(),'Filter projects');
  await page.locator('#crm-form select[name=board]').selectOption('1');assert.deepEqual(await page.locator('#crm-form select[name=stage] option').allTextContents(),['Any stage','Discovery','Demo','Won']);
  await page.locator('#crm-form [name=value_min]').fill('lots');await click('#crm-form button.primary');assert.equal(await page.locator('#crm-form [data-crm-error]').textContent(),'Value: a number, like 12000.');
  await page.locator('#crm-form [name=value_min]').fill('5000');await fits(page,'filter '+tag);await shot(page,`filter-sheet-${tag}`);
  await click('#crm-form button.primary');await page.locator('#crm-dialog[open]').waitFor({state:'detached'});
  await page.waitForFunction(()=>document.querySelector('[data-graph-panel="projects"] .graph-active'));
  await until(()=>last('projects').value_min,'the filtered list is asked for');
  assert.deepEqual({owner:last('projects').owner,board:last('projects').board,value_min:last('projects').value_min},{owner:'me',board:'1',value_min:'5000'});
  assert.match(await page.locator('[data-graph-panel="projects"] .graph-active').textContent(),/Board: Sales.*Value from 5,000/);
  assert.equal(await page.locator('[data-crm-filter="projects"] .badge').textContent(),'2');
  await page.locator('[data-graph-panel="projects"] .pager-empty').waitFor();assert.equal(await page.locator('[data-graph-panel="projects"] .pager-empty').textContent(),'Nothing matches these filters.');
  await shot(page,`projects-filtered-${tag}`);
  await click('[data-graph-panel="projects"] [data-crm-clear]');await page.waitForFunction(()=>!document.querySelector('[data-graph-panel="projects"] .graph-active'));
  await page.waitForFunction(()=>document.querySelectorAll('[data-graph-panel="projects"] .graph-list article').length===2);  /* the whole list, as loaded before */
  // Sort by value goes to the Machine too
  await click('#people-view [data-graph-panel="projects"] .graph-sort summary');await click('#people-view [data-graph-panel="projects"] [data-graph-sort="value"]');
  await page.waitForFunction(()=>document.querySelector('[data-graph-panel="projects"] .graph-sort summary').textContent.startsWith('Value'));await until(()=>last('projects').sort==='value','sorted by value on the Machine');
  // The board: owners on tiles, and Mine hides the ones a teammate owns
  await page.evaluate(()=>projectsViewSet('board'));await page.locator('#people-view [data-board-mine]').waitFor();
  assert.equal(await page.locator('#people-view .tile[data-node="project:1"] .tile-owner').textContent(),'Kim Vale');
  await shot(page,`board-owners-${tag}`);
  await click('#people-view [data-board-mine]');await page.waitForFunction(()=>!document.querySelector('#people-view .tile[data-node="project:1"]'));
  assert.equal(await page.locator('#people-view [data-board-mine]').getAttribute('aria-pressed'),'true');
  await click('#people-view [data-board-mine]');await page.locator('#people-view .tile[data-node="project:1"]').waitFor({state:'attached'});
  // Organizations: Same organization? merges the second into the first
  await page.evaluate(()=>document.querySelector('[data-graph-sub="orgs"]').click());await page.locator('#people-view [data-graph-panel="orgs"] .same-pair').waitFor();
  await shot(page,`orgs-doubles-${tag}`);
  await click('#people-view [data-graph-panel="orgs"] [data-org-merge]');await until(()=>calls.some(c=>c.action==='graph_org_merge'),'merged');
 assert.deepEqual(calls.find(c=>c.action==='graph_org_merge').args,{node_id:'org:globex-group.example',into:'org:globex.example'});
  // An organization's page: its owner, its websites with Split off, the change in its timeline, and the ⋯ menu
  await page.evaluate(()=>graphOpen('org:globex.example'));await page.locator('#people-view .org-page [data-owner-pick]').waitFor();
  assert.equal(await page.locator('#people-view [data-owner-pick]').inputValue(),'m-kim');
  assert.deepEqual(await page.locator('#people-view .org-sites li span').allTextContents(),['globex.example','globex.io']);
  assert.deepEqual(await page.locator('#people-view .ge-change h4').allTextContents(),['Owner set to Kim Vale','Merged in globex.io']);
  await fits(page,'org page '+tag);await shot(page,`org-page-${tag}`);
  await page.locator('#people-view [data-owner-pick]').selectOption('me');await until(()=>calls.some(c=>c.action==='graph_owner'),'owner set');
  assert.deepEqual(calls.find(c=>c.action==='graph_owner').args,{node_id:'org:globex.example',owner:'me'});
  await click('#people-view [data-org-split]');await until(()=>calls.some(c=>c.action==='graph_org_split'),'split');assert.deepEqual(calls.find(c=>c.action==='graph_org_split').args,{alias:'org:globex.io'});
  await click('#people-view .org-actions .person-menu summary');await click('#people-view [data-org-site]');await page.locator('#crm-dialog[open]').waitFor();
  await page.locator('#crm-form [name=website]').fill('globex.co.uk');await shot(page,`org-website-${tag}`);await click('#crm-form button.primary');
  await page.locator('#crm-dialog[open]').waitFor({state:'detached'});assert.deepEqual(calls.find(c=>c.action==='graph_org_merge'&&c.args.website).args,{node_id:'org:globex.example',website:'globex.co.uk'});
  await click('#people-view .org-actions .person-menu summary');await click('#people-view [data-org-same]');await page.locator('#crm-dialog[open]').waitFor();
  await page.locator('#crm-form [name=q]').fill('Umb');await page.waitForFunction(()=>document.querySelector('#crm-orgs option'));await page.locator('#crm-form [name=q]').fill('Umber');
  await click('#crm-form button.primary');await page.locator('#crm-dialog[open]').waitFor({state:'detached'});
  assert.deepEqual(calls.find(c=>c.action==='graph_org_merge'&&c.args.into==='org:umber.example').args,{node_id:'org:globex.example',into:'org:umber.example'});
  // Import CSV from the list's menu: a preview (sent as an upload), a column changed, Import, and Undo in the toast
  await page.evaluate(()=>graphBack('people'));await page.locator('#people-view [data-graph-panel="people"] .graph-sort summary').waitFor();
  for(let i=0;i<10;i++){await page.evaluate(()=>{const d=document.querySelector('[data-graph-panel="people"] .graph-sort');if(d)d.open=true});  /* a quiet refresh may redraw the list */
   if(await page.locator('#people-view [data-graph-panel="people"] [data-crm-import]').isVisible())break;await new Promise(r=>setTimeout(r,100))}
  assert.ok(await page.locator('#people-view [data-graph-panel="people"] [data-crm-undo-import="7"]').isVisible());
  await page.evaluate(()=>document.querySelector('[data-graph-panel="people"] [data-crm-import]').click());await page.locator('#crm-dialog[open]').waitFor();
  await page.locator('#crm-form [name=file]').setInputFiles({name:'contacts.csv',mimeType:'text/csv',buffer:Buffer.from('Full name,E-mail,Company\nAda Lovelace,ada@globex.example,Globex\nNia Holm,,Newco\n,,\n')});
  await page.locator('#crm-form .crm-count').waitFor();assert.equal(await page.locator('#crm-form .crm-count').textContent(),'1 new · 1 match existing · 1 skipped');
  assert.deepEqual(uploads,['graph_import_preview:graph-import']);assert.equal(calls.find(c=>c.action==='graph_import_preview').args.kind,'person');
  await page.locator('#crm-form [data-crm-col="2"]').selectOption('title');await until(()=>calls.filter(c=>c.action==='graph_import_preview').length===2,'previewed again');
  assert.deepEqual(calls.filter(c=>c.action==='graph_import_preview').at(-1).args.mapping,{0:'name',1:'email',2:'title'});
  await fits(page,'import '+tag);await shot(page,`import-preview-${tag}`);
  assert.equal(await page.locator('#crm-form button.primary').textContent(),'Import 2');await click('#crm-form button.primary');await page.locator('#crm-dialog[open]').waitFor({state:'detached'});
  assert.equal(calls.find(c=>c.action==='graph_import_run').args.name,'contacts.csv');
  assert.match(await page.locator('#toast').textContent(),/Imported: 1 new, 1 updated, 1 skipped\. Undo/);await click('#toast [data-crm-undo-import]');
  await until(()=>calls.some(c=>c.action==='graph_import_undo'),'undone');assert.deepEqual(calls.find(c=>c.action==='graph_import_undo').args,{import_id:8});
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('crm dashboard ok');
}finally{await browser?.close();server.close()}
