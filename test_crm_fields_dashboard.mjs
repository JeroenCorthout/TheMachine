import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The board's Pipeline (value per stage and expected per month from close dates; Mine counts only your own), and the
// owner's own fields and tags on a record: shown on its page, edited in a sheet (a new field, a value, a field removed),
// tags added and removed, a tag in the lists' filter, and a teammate's fields on a record only they have. On a phone
// and a desktop. CRM_SHOT=<dir> writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const month=k=>{const d=new Date();d.setDate(15);d.setMonth(d.getMonth()+k);return d.toISOString().slice(0,10)};
const node=(kind,id,name,extra={})=>({id,kind,name,strength:2,last_at:day(1),signals:3,ids:[],links:[],recent:[],around:[],open:{},...extra});
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Won'}];
const tiles=[{id:'project:1',name:'Globex pilot',stage:'Demo',days:3,value:12000,close_date:month(1)},
 {id:'project:2',name:'Umber deal',stage:'Discovery',days:1,value:3000,close_date:month(0)},
 {id:'project:3',name:'Late one',stage:'Discovery',days:9,value:5000,close_date:month(-2),owner:'m-kim',owner_name:'Kim Vale'},
 {id:'project:4',name:'Someday',stage:'Won',days:2,value:7000},{id:'project:5',name:'No value',stage:'Demo',days:2}];
const boards=[{id:1,name:'Sales',stages,default:true,stuck:0,maybes:[],projects:tiles}];
const FIELDS=[{id:1,kind:'org',name:'Plan',type:'choice',choices:['Starter','Pro']},{id:2,kind:'org',name:'Seats',type:'number'}];let fields=[...FIELDS];
let values={1:'Pro',2:null},tags=['partner'];
const globex=()=>node('org','org:globex.example','Globex',{domain:'globex.example',profile:{website:'https://globex.example'},
 fields:fields.filter(f=>f.kind==='org').map(f=>({...f,value:values[f.id]??null})),tags:[...tags],changes:[]});
const ada=node('person','person:a','Ada Lovelace',{ids:['email:ada@globex.example'],org:'org:globex.example',side:'work',tags:['VIP'],fields:[]});
const teamOnly={id:'team:x',kind:'org',name:'Initech',team_only:true,role:null,org_name:null,last_at:day(4),team_fields:[{name:'Plan',type:'choice',value:'Starter'}],tags:['beta'],
 team:{via:'via Kim',people:[{member:'m-kim',fleet:'w1',name:'Kim',score:1}],timeline:[],threads:[]}};
let calls=[],lists=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){const b=JSON.parse(body),a=b.args;calls.push(b);let out={ok:true};
  if(b.action==='graph_field_save'){const f={id:Math.max(0,...fields.map(x=>x.id))+1,kind:a.kind,name:a.name,type:a.type,...(a.type==='choice'?{choices:String(a.choices).split('\n').filter(Boolean)}:{})};fields.push(f);out=f}
  if(b.action==='graph_field_delete'){fields=fields.filter(f=>f.id!==a.id)}
  if(b.action==='graph_field_set'){if(a.field_id===2&&!/^\d*$/.test(a.value)){res.statusCode=400;res.end(JSON.stringify({error:'Seats: a number, like 12000'}));return}values[a.field_id]=a.value||null}
  if(b.action==='graph_tag'){tags=a.add?[...tags,a.tag]:tags.filter(t=>t!==a.tag);out={tags}}
  res.end(JSON.stringify(out));return}
 if(url.pathname==='/api/graph/list'){const p=Object.fromEntries(url.searchParams);lists.push(p);const all=p.list==='orgs'?[globex()]:p.list==='projects'?[]:[ada];
  const items=all.filter(x=>!p.tag||(x.tags||[]).some(t=>t.toLowerCase()===p.tag.toLowerCase()));
  res.end(JSON.stringify({items,next:null,sort:p.sort,total:items.length}));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node');
  if(n){res.end(JSON.stringify(n==='team:x'?teamOnly:n==='org:globex.example'?globex():ada));return}
  res.end(JSON.stringify({list_counts:{people:1,quiet:0,new:0,work:1,personal:0,orgs:1,warmup:0,projects:5,projects_done:0},counts:{},people:[ada],orgs:[globex()],projects:[],boards,
   owners:[{id:'m-kim',name:'Kim Vale',fleet:'Acme'}],crm_fields:fields,tags:[{tag:'partner',count:1},{tag:'VIP',count:1}],person_names:[],project_names:[]}));return}
 res.statusCode=url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shotDir=process.env.CRM_SHOT,errors=[];let browser;
const shot=async(page,name)=>{if(shotDir)await page.screenshot({path:`${shotDir}/${name}.png`,fullPage:false})};
const fits=async(page,where)=>{const r=await page.evaluate(()=>{const d=document.querySelector('dialog[open]')?.getBoundingClientRect();return {wide:document.documentElement.scrollWidth>innerWidth,d:d&&{l:d.left,r:d.right},w:innerWidth}});
 assert.ok(!r.wide,where+': wider than the screen');if(r.d)assert.ok(r.d.l>=-1&&r.d.r<=r.w+1,where+': the dialog fits')};
const until=async(test,what)=>{for(let i=0;i<80&&!test();i++)await new Promise(r=>setTimeout(r,50));assert.ok(test(),what)};
const text=async loc=>(await loc.evaluateAll(els=>els.map(e=>e.innerText))).map(t=>t.replace(/\s+/g,' ').trim().toLowerCase());
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){const mobile=width<600,tag=String(width);calls=[];lists=[];fields=[...FIELDS];values={1:'Pro',2:null};tags=['partner'];
  const context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile,locale:'en-GB'}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(tag+': '+e.message));page.on('dialog',d=>d.accept());await page.goto(base);await page.waitForFunction(()=>typeof people==='function');
  const click=sel=>mobile?page.locator(sel).first().tap():page.locator(sel).first().click();
  await page.evaluate(()=>{localStorage.removeItem('graph-filters');sideStore.set('projects-view','board');sideStore.set('board-mine','0');document.querySelector('nav [data-tab="people"]').click()});
  await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());
  // The Pipeline: value per stage, and expected per month (overdue first, then this month on), the ones with no date apart
  await page.locator('#people-view .pipeline').waitFor();
  assert.match((await page.locator('#people-view .pipeline summary').textContent()).replace(/\s+/g,' '),/Pipeline ?27k open in 4 with a value/i);
  const stageRows=await text(page.locator('#people-view .pipe-block').first().locator('.pipe-row'));
  assert.deepEqual(stageRows,['discovery 8,000 2','demo 12k 2','won 7,000 1']);
  const monthRows=await text(page.locator('#people-view .pipe-block').nth(1).locator('.pipe-row'));
  assert.equal(monthRows[0],'overdue 5,000 1');assert.match(monthRows[1],/ 3,000 1$/);assert.match(monthRows[2],/ 12k 1$/);assert.equal(monthRows.length,4);
  assert.equal(await page.locator('#people-view .pipe-note').textContent(),'No close date: 7,000 in 1');
  await fits(page,'pipeline '+tag);await page.locator('#people-view .pipeline').scrollIntoViewIfNeeded();await shot(page,`pipeline-${tag}`);
  // Mine: only your own count (a teammate owns the overdue one)
  await click('#people-view [data-board-mine]');await page.waitForFunction(()=>!document.querySelector('#people-view .tile[data-node="project:3"]'));
  assert.match(await page.locator('#people-view .pipeline summary').textContent(),/22k open in 3/i);
  await click('#people-view [data-board-mine]');
  // Closed, it stays closed
  await click('#people-view .pipeline summary');await page.waitForFunction(()=>!document.querySelector('#people-view .pipeline').open);
  assert.equal(await page.evaluate(()=>sideStore.get('board-pipeline')),'0');await page.evaluate(()=>sideStore.set('board-pipeline','1'));
  // An organization's page: its fields and tags
  await page.evaluate(()=>graphOpen('org:globex.example'));await page.locator('#people-view .crm-fields').waitFor();
  assert.deepEqual(await text(page.locator('#people-view .crm-fields .kv .k')),['plan','seats','tags']);
  assert.deepEqual(await text(page.locator('#people-view .crm-fields .kv .v')).then(v=>v.slice(0,2)),['pro','not set']);
  await page.locator('#people-view .crm-fields').scrollIntoViewIfNeeded();await fits(page,'org fields '+tag);await shot(page,`org-fields-${tag}`);
  // Tags: add one (Enter), remove one
  await page.locator('#people-view .tag-add input').fill('renewal 2027');await page.locator('#people-view .tag-add input').press('Enter');
  await until(()=>calls.some(c=>c.action==='graph_tag'&&c.args.add),'tag added');assert.deepEqual(calls.find(c=>c.action==='graph_tag').args,{node_id:'org:globex.example',tag:'renewal 2027',add:true});
  await page.waitForFunction(()=>[...document.querySelectorAll('#people-view .crm-fields .tag-chip')].some(c=>c.textContent.includes('renewal 2027')));
  await click('#people-view .crm-fields [data-tag-remove="partner"]');await until(()=>calls.some(c=>c.action==='graph_tag'&&c.args.add===false),'tag removed');
  await page.waitForFunction(()=>![...document.querySelectorAll('#people-view .crm-fields .tag-chip')].some(c=>c.textContent.startsWith('partner')));
  // Edit fields: a wrong number says so, a new field, a value saved, a field removed
  await click('#people-view [data-fields-edit]');await page.locator('#crm-dialog[open]').waitFor();
  assert.equal(await page.locator('#crm-dialog-title').textContent(),'Fields of Globex');
  assert.deepEqual(await page.locator('#crm-form select[name=f1] option').allTextContents(),['Not set','Starter','Pro']);
  await page.locator('#crm-form [name=f2]').fill('many');await click('#crm-form button.primary');
  await page.waitForFunction(()=>!document.querySelector('#crm-form [data-crm-error]')?.hidden);assert.equal(await page.locator('#crm-form [data-crm-error]').textContent(),'Seats: a number, like 12000');
  await page.locator('#crm-form [name=f2]').fill('40');
  await click('#crm-form .crm-newfield summary');await page.locator('#crm-form [name=nf_name]').fill('Renewal');await page.locator('#crm-form [name=nf_type]').selectOption('date');
  assert.ok(await page.locator('#crm-form [data-nf-choices]').isHidden());await page.locator('#crm-form [name=nf_type]').selectOption('choice');assert.ok(await page.locator('#crm-form [data-nf-choices]').isVisible());
  await page.locator('#crm-form [name=nf_type]').selectOption('date');await click('#crm-form [data-nf-add]');
  await page.locator('#crm-form [name=f3]').waitFor();assert.deepEqual(calls.find(c=>c.action==='graph_field_save').args,{kind:'org',name:'Renewal',type:'date',choices:''});
  assert.equal(await page.locator('#crm-form [name=f2]').inputValue(),'40');  /* what was typed stays */
  await page.locator('#crm-form [name=f3]').fill('2027-03-01');await fits(page,'fields sheet '+tag);await shot(page,`fields-sheet-${tag}`);
  await click('#crm-form [data-nf-remove="1"]');await page.waitForFunction(()=>!document.querySelector('#crm-form [name=f1]'));
  assert.deepEqual(calls.find(c=>c.action==='graph_field_delete').args,{id:1});
  await click('#crm-form button.primary');await page.locator('#crm-dialog[open]').waitFor({state:'detached'});
  const sets=calls.filter(c=>c.action==='graph_field_set').map(c=>c.args);
  assert.deepEqual(sets.slice(-2),[{node_id:'org:globex.example',field_id:2,value:'40'},{node_id:'org:globex.example',field_id:3,value:'2027-03-01'}]);
  await page.waitForFunction(()=>[...document.querySelectorAll('#people-view .crm-fields .kv .k')].map(k=>k.textContent).join()==='Seats,Renewal,Tags');
  // A tag in the lists' filter
  await page.evaluate(()=>graphBack('people'));await page.evaluate(()=>document.querySelector('[data-graph-sub="people"]').click());
  await page.locator('#people-view [data-graph-panel="people"] .graph-list article .tag-chip').first().waitFor();
  await click('#people-view [data-crm-filter="people"]');await page.locator('#crm-dialog[open]').waitFor();
  await page.locator('#crm-form select[name=tag]').selectOption('VIP');await click('#crm-form button.primary');
  await until(()=>lists.some(l=>l.list==='people'&&l.tag==='VIP'),'filtered by tag');
  assert.match(await page.locator('[data-graph-panel="people"] .graph-active').textContent(),/Tag: VIP/);
  await page.locator('#people-view [data-graph-panel="people"]').scrollIntoViewIfNeeded();await shot(page,`people-tag-filter-${tag}`);
  // A record only a teammate has: their fields and tags, read-only
  await page.evaluate(()=>graphOpen('team:x'));await page.locator('#people-view .crm-fields').waitFor();
  assert.deepEqual(await text(page.locator('#people-view .crm-fields .kv')),['plan starter','tags beta']);
  assert.equal(await page.locator('#people-view .crm-fields [data-tag-remove], #people-view .crm-fields .tag-add').count(),0);
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('crm fields dashboard ok');
}finally{await browser?.close();server.close()}
