import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A CSV import checked by the owner's own model (a fake here): the rules' guess shows first with "Your model is
// checking the columns…", then the model's mapping with its reasons; easy fixes as "N fixes suggested · Review",
// each kind and each row with its own checkbox, and Import sends only the ones kept. On a phone and a desktop.
// CRM_SHOT=<dir> writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const fields=[{id:'name',name:'Name'},{id:'org',name:'Organization'},{id:'value',name:'Value'},{id:'stage',name:'Stage'},{id:'close_date',name:'Expected close'},{id:'tags',name:'Tags'}];
const cols=(ai)=>[{index:0,header:'Klant',field:ai?'name':null,samples:['TEAL ROBOTICS','umber bv'],...(ai?{reason:'Klant is Dutch for client: the deal is named after them'}:{})},
 {index:1,header:'Bedrag',field:ai?'value':null,samples:['€12k','ongeveer vijf'],...(ai?{reason:'Bedrag means amount'}:{})},
 {index:2,header:'Fase',field:'stage',samples:['voorstel','Lead']},{index:3,header:'Datum',field:'close_date',samples:['25/12/2026','03/01/2027']}];
const FIX=[{id:'2:name',line:2,field:'name',field_name:'Name',type:'case',before:'TEAL ROBOTICS',after:'Teal Robotics'},
 {id:'3:name',line:3,field:'name',field_name:'Name',type:'case',before:'umber bv',after:'Umber BV'},
 {id:'2:value',line:2,field:'value',field_name:'Value',type:'value',before:'€12k',after:'12000'},
 {id:'3:value',line:3,field:'value',field_name:'Value',type:'value',before:'ongeveer vijf',after:'5000',why:'vijf is five, about 5k',ai:true},
 {id:'2:close_date',line:2,field:'close_date',field_name:'Expected close',type:'date',before:'25/12/2026',after:'2026-12-25'},
 {id:'3:close_date',line:3,field:'close_date',field_name:'Expected close',type:'date',before:'03/01/2027',after:'2027-01-03'},
 {id:'4:row',line:4,field:'row',field_name:'Row',type:'merge',before:'Line 4: Teal Robotics',after:'Merged into line 2'}];
const TYPES={case:'Capitals',value:'Values',date:'Dates',merge:'Same row twice'};
const preview=args=>{const ai=!!args.ai_run;if(!ai)return {kind:'project',columns:cols(false),fields,counts:{new:0,match:0,skip:0},rows:[],total:0,fixes:[],fix_types:[],fix_total:0,ai:false,problem:'Pick the column with the name'};
 const types=args.fixes?.types,off=new Set(args.fixes?.off||[]);const on=f=>(types==='all'||types.includes(f.type))&&!off.has(f.id);
 return {kind:'project',columns:cols(true),fields,counts:{new:2,match:0,skip:1},total:3,ai:true,
  rows:[{line:2,status:'new',record:{name:'Teal Robotics'}},{line:3,status:'new',record:{name:'Umber BV'}},{line:4,status:'skip',why:on(FIX[6])?'Merged into line 2':'Twice in the file',record:{name:'Teal Robotics'}}],
  fixes:FIX,fix_types:Object.entries(TYPES).map(([type,name])=>({type,name,count:FIX.filter(f=>f.type===type).length})),fix_total:FIX.length}};
let calls=[],status=0;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){const b=JSON.parse(body);calls.push(b);
  const out=b.action==='graph_import_preview'?preview(b.args):b.action==='graph_import_ai'?{ai:true,run_id:'r1',cells:1}
   :b.action==='graph_import_ai_status'?{status:++status<3?'running':'ok',columns:2,fixes:1}:b.action==='graph_import_run'?{import_id:9,kind:'project',new:2,updated:0,skipped:1}:{ok:true};
  res.end(JSON.stringify(out));return}
 if(url.pathname==='/api/graph/list'){res.end(JSON.stringify({items:[],next:null,total:0}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({list_counts:{people:0,quiet:0,new:0,work:0,personal:0,orgs:0,warmup:0,projects:0,projects_done:0},counts:{},people:[],orgs:[],projects:[],boards:[],owners:[],person_names:[],project_names:[]}));return}
 res.statusCode=url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shotDir=process.env.CRM_SHOT,errors=[];let browser;
const shot=async(page,name)=>{if(shotDir)await page.screenshot({path:`${shotDir}/${name}.png`,fullPage:false})};
const fits=async(page,where)=>{const r=await page.evaluate(()=>{const d=document.querySelector('dialog[open]')?.getBoundingClientRect();return {wide:document.documentElement.scrollWidth>innerWidth,d:d&&{l:d.left,r:d.right},w:innerWidth}});
 assert.ok(!r.wide,where+': wider than the screen');if(r.d)assert.ok(r.d.l>=-1&&r.d.r<=r.w+1,where+': the dialog fits')};
const until=async(test,what,n=100)=>{for(let i=0;i<n&&!(await test());i++)await new Promise(r=>setTimeout(r,50));assert.ok(await test(),what)};
const previews=()=>calls.filter(c=>c.action==='graph_import_preview');
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){const mobile=width<600,tag=String(width);calls=[];status=0;
  const context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile,locale:'en-US'}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(tag+': '+e.message));await page.goto(base);await page.waitForFunction(()=>typeof crmImportOpen==='function');
  const click=sel=>mobile?page.locator(sel).first().tap():page.locator(sel).first().click();
  await page.evaluate(()=>crmImportOpen('projects'));await page.locator('#crm-dialog[open]').waitFor();
  assert.equal(await page.locator('#crm-form .crm-file').textContent(),'Choose file');
  await page.locator('#crm-form [name=file]').setInputFiles({name:'deals.csv',mimeType:'text/csv',buffer:Buffer.from('Klant,Bedrag,Fase,Datum\nTEAL ROBOTICS,€12k,voorstel,25/12/2026\number bv,ongeveer vijf,Lead,03/01/2027\nTeal Robotics,,,\n')});
  // the rules' guess first, with no name column yet, while the model checks
  await page.locator('#crm-form .crm-ai.run').waitFor();assert.equal(await page.locator('#crm-form .crm-ai').textContent(),'Your model is checking the columns…');
  assert.equal(await page.locator('#crm-form .crm-problem').count(),0);assert.ok(await page.locator('#crm-form button.primary').isDisabled());  // no error while the model checks
  assert.equal(await page.locator('#crm-form .crm-file').textContent(),'deals.csv· Change');assert.ok(!(await page.locator('#crm-form .ed-note').allTextContents()).some(t=>t.includes('deals.csv')));
  assert.deepEqual(calls.find(c=>c.action==='graph_import_ai').args,{kind:'project',text:'Klant,Bedrag,Fase,Datum\nTEAL ROBOTICS,€12k,voorstel,25/12/2026\number bv,ongeveer vijf,Lead,03/01/2027\nTeal Robotics,,,\n'});
  assert.deepEqual(previews()[0].args.fixes,{types:'all',off:[]});
  await fits(page,'checking '+tag);await shot(page,`import-check-${tag}`);
  // the model's mapping, with its reasons, and the fixes
  await until(()=>page.locator('#crm-form .crm-count').count(),'checked',200);
  assert.equal(previews().at(-1).args.ai_run,'r1');assert.equal(previews().at(-1).args.mapping,undefined);
  assert.equal(await page.locator('#crm-form .crm-ai').textContent(),'Checked by your model. Change anything below.');
  assert.equal(await page.locator('#crm-form [data-crm-col="0"]').inputValue(),'name');
  assert.equal(await page.locator('#crm-form .crm-col .crm-why').first().textContent(),'Klant is Dutch for client: the deal is named after them');
  assert.equal(await page.locator('#crm-form .crm-fixline').textContent(),'7 fixes suggested·Review');
  assert.equal(await page.locator('#crm-form button.primary').textContent(),'Import 2 with 7 fixes');
  await click('#crm-form [data-crm-fixes]');await page.locator('#crm-form .crm-fixes').waitFor();
  assert.deepEqual(await page.locator('#crm-form .crm-fixtype>summary span').allTextContents(),['Capitals 2','Values 2','Dates 2','Same row twice 1']);
  await click('#crm-form .crm-fixtype[data-type="value"]>summary small');await page.locator('#crm-form .crm-fixtype[data-type="value"][open]').waitFor();
  assert.deepEqual(await page.locator('#crm-form .crm-fixtype[data-type="value"] li .ba').allTextContents(),['€12k → 12000','ongeveer vijf → 5000']);
  assert.equal(await page.locator('#crm-form .crm-fixtype[data-type="value"] li small').nth(1).textContent(),'Line 3 · Value · your model: vijf is five, about 5k');
  await fits(page,'fixes '+tag);await shot(page,`import-fixes-${tag}`);
  // one row left out, then a whole kind: each previews again with what is kept
  let n=previews().length;await click('#crm-form [data-crm-fix="3:value"]');await until(()=>previews().length>n,'row off');
  assert.deepEqual(previews().at(-1).args.fixes,{types:'all',off:['3:value']});
  await until(async()=>(await page.locator('#crm-form .crm-fixline').textContent())==='7 fixes suggested, 6 kept·Hide','6 kept');
  n=previews().length;await click('#crm-form [data-crm-fixtype="case"]');await until(()=>previews().length>n,'kind off');
  assert.deepEqual(previews().at(-1).args.fixes,{types:['spaces','split','email','phone','website','date','value','tags','stage','merge'],off:['3:value']});
  await until(async()=>(await page.locator('#crm-form button.primary').textContent())==='Import 2 with 4 fixes','4 kept');
  await click('#crm-form button.primary');await page.locator('#crm-dialog[open]').waitFor({state:'detached'});
  const run=calls.find(c=>c.action==='graph_import_run').args;
  assert.deepEqual([run.mapping,run.ai_run,run.fixes.off],[{0:'name',1:'value',2:'stage',3:'close_date'},'r1',['3:value']]);
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('crm import check dashboard ok');
}finally{await browser?.close();server.close()}
