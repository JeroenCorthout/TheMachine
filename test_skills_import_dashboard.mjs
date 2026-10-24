import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings > Skills > Import: Find my skills shows a short list first (Recommended with the reason, then Yours), the
// rest in closed groups with counts, and what can't come in (a license that says no, already here) last; a search box
// finds any of them. Import selected and Close stay on screen at the bottom of a panel that scrolls inside. The
// accounts turn runs on; a signed-out ChatGPT gets a Sign in step with the sign-in dialog, and once in, the list looks
// there by itself. The owner ticks and imports; Read shows what would come in and, for a skill that needs its scripts,
// Install in OpenClaw. Upload sends the file marked as an upload. Once imported, a skill shows under Imported with
// View, never under Can't import; one already here is info with Replace; files left out are named. Fits a 390px phone
// and a 1280px desktop.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const LONG='~/Private/some-project-with-a-really-long-folder-name/.claude/skills/answer-customer-questions-from-the-knowledge-base';
const item=(over)=>({key:'claude:x',name:'x',description:'When to use it',from:'claude',from_label:'Claude',source:'~/.claude/skills/x',url:'',group:'yours',files:1,
 scripts:[],script_count:0,depends:false,references:[],left_out:[],flags:[],licence:'',blocked:'',duplicate:'',recommended:false,why:'',...over});
const items=[
 item({key:'claude:~/.claude/skills/meeting-prep',name:'meeting-prep',description:'Before a meeting: brief the owner on who they meet and what is open.',recommended:true,why:'Your cards are mostly meeting preparations'}),
 item({key:'claude:'+LONG,name:'answer-customer-questions-from-the-knowledge-base',description:'When a customer asks how something works.',source:LONG,group:'project',references:['FAQ.md']}),
 item({key:'claude:~/.claude/skills/omarchy',name:'omarchy',description:'Desktop settings on Omarchy.',group:'vendor',link:'/usr/share/omarchy/skills/omarchy',flags:['Renamed tools to OpenClaw\'s: Bash → exec']}),
 item({key:'chatgpt:~/.codex/skills/report',name:'weekly-report',description:'Weekly KPI report.',from:'chatgpt',from_label:'ChatGPT',source:'~/.codex/skills/report',scripts:['scripts/build.py'],script_count:1,depends:true,left_out:['1 script (scripts/build.py)']}),
 item({key:'claude:pdf',name:'pdf',description:'PDF files.',group:'builtin',source:'claude.ai, built in (pdf)',licence:'Proprietary. LICENSE.txt has complete terms',blocked:'Can\'t import: its license doesn\'t allow it',script_count:8,scripts:['scripts/a.py']}),
];
// Many more, like a computer with lots of projects and vendor plugins: these must not crowd the first view.
for(let n=0;n<51;n++)items.push(item({key:'claude:proj'+n,name:'project-skill-'+n,description:'A project skill.',group:'project',source:'~/work/p'+n+'/.claude/skills/s'}));
for(let n=0;n<49;n++)items.push(item({key:'claude:vendor'+n,name:'vendor-skill-'+n,description:'A vendor skill.',group:'vendor',source:'claude.ai plugin v'+n}));
for(let n=0;n<13;n++)items.push(item({key:'claude:builtin'+n,name:'builtin-skill-'+n,description:'Built in.',group:'builtin'}));
for(let n=0;n<11;n++)items.push(item({key:'claude:mine'+n,name:'my-skill-'+n,description:'Mine.',source:'~/.claude/skills/m'+n}));
const src=(claude,chatgpt,c=0,g=0)=>[{key:'local',state:'done',found:items.length},{key:'claude',state:claude,found:c},{key:'chatgpt',state:chatgpt,found:g}];
let list={token:'tok1',items,errors:[],finding:{run_id:'r1',status:'running'},browser:{},local:true,sources:src('looking','looking')};
let skills={skills:[],waiting:0,targets:[],folder:'skills/the-machine',openclaw:true,import_local:true,hints:[]};
const calls=[];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],connectors:[],skills}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',c=>body+=c);req.on('end',()=>{const {action,args}=JSON.parse(body);calls.push({action,args,upload:req.headers['x-upload']||''});
   if(action==='skill_installed'){res.end(JSON.stringify({installed:[],checked_at:null,error:''}));return}
   if(action==='skill_import_find'){if(args.token){list={...list,finding:{run_id:'r2',status:'running'},browser:{claude:'ok'},sources:src('done','starting',1)}}res.end(JSON.stringify(list));return}
   if(action==='skill_import_signin'){res.end(JSON.stringify({opened:true,site:'chatgpt.com',view:''}));return}
   if(action==='browser_login_pages'){res.end(JSON.stringify({pages:[{id:'0:t1',host:'auth.openai.com',site:'auth.openai.com',brand:'OpenAI',title:'Log in',username:false,password:false,code:true,code_kind:'email',sent_to:'j•••@example.com',saved:null}]}));return}
   if(action==='browser_login_fill'){res.end(JSON.stringify({result:'done'}));return}
   if(action==='skill_import_status'&&list.finding?.run_id==='r2'){list={...list,finding:{run_id:'r2',status:'ok'},browser:{claude:'ok',chatgpt:'ok'},sources:src('done','done',1,1),
     items:[...list.items,item({key:'chatgpt:account:pricing',name:'pricing-answers',description:'Answer pricing questions.',from:'chatgpt',from_label:'ChatGPT',source:'chatgpt.com, your account'})]};res.end(JSON.stringify(list));return}
   if(action==='skill_import_status'){list={...list,finding:{run_id:'r1',status:'ok'},browser:{claude:'ok',chatgpt:'signed_out'},sources:src('done','signed_out',1),
     items:[...list.items,item({key:'claude:account:follow-ups',name:'follow-ups',description:'Chase replies that are late.',source:'claude.ai, your account',url:'https://claude.ai/customize/skills',recommended:true,why:'You get many follow-up cards'})]};
     res.end(JSON.stringify(list));return}
   if(action==='skill_import_read'){const found=list.items.find(i=>i.key===args.key);res.end(JSON.stringify({...found,body:'# Steps\n<b>not bold</b>\n1. Run scripts/build.py',original:'',sha:'abc',installable:found.script_count>0&&!found.blocked}));return}
   if(action==='skill_import_run'){const sid=k=>args.replace?list.items.find(i=>i.key===k).existing.id:'sk_'+k.replace(/\W/g,'_');
     const done=args.keys.map(k=>({key:k,name:list.items.find(i=>i.key===k).name,status:args.replace?'replaced':'imported',id:sid(k),left_out:[],flags:[]}));
     list={...list,items:list.items.map(i=>args.keys.includes(i.key)?{...i,duplicate:'Already in your skills',done:args.replace?'Replaced':'Imported',skill_id:sid(i.key),existing:null}:i)};
     skills={...skills,skills:[...skills.skills.filter(s=>!done.some(d=>d.id===s.id)),...done.map(d=>({id:d.id,name:d.name,description:'x',status:'on',origin:'mine',version:1,author:'',note:'',update:false,chars:9,shares:[],changed:false,imported:{from:'claude'}}))]};
     res.end(JSON.stringify({results:done,list,...skills}));return}
   if(action==='skill_import_install'){res.end(JSON.stringify({installed_skill:'weekly-report',list,installed:[],checked_at:null,error:''}));return}
   if(action==='skill_import_upload'){const name=args.data.name;
     const one=name==='writer.zip'?item({key:'claude:upload:writer.zip/writer',name:'writer',source:'writer.zip › writer',left_out:['agents/openai.yaml','assets/icon.svg']})
      :name==='here.zip'?item({key:'claude:upload:here.zip/here',name:'here',source:'here.zip',duplicate:'Already in your skills',existing:{id:'sk_old',replaceable:true,same:false}})
      :item({key:'claude:upload:x.zip/',name:'from-zip',source:'x.zip'});
     list={...list,token:'tok2',finding:null,sources:[],items:[one]};res.end(JSON.stringify(list));return}
   res.end('{}')});return}
 res.statusCode=404;res.end();
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
const fits=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
const shots=process.env.SCREENSHOTS_DIR;if(shots)await mkdir(shots,{recursive:true});
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,theme] of [[390,'dark'],[1280,'light'],[390,'light'],[1280,'dark']]){
  list={...list,token:'tok1',items,finding:{run_id:'r1',status:'running'},browser:{},sources:src('looking','looking')};calls.length=0;skills={...skills,skills:[]};
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:theme}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="skills"]').click();
  await page.locator('#skill-import-open').click();
  await page.locator('#skill-import:not([hidden])').waitFor();
  await page.locator('#import-find').click();
  await page.locator('[data-import]').first().waitFor();
  // A short list first: Recommended with its reason, then Yours (8, and Show all); the rest in closed groups with
  // counts; the licence-limited built-in is under Can't import, can't be ticked and says why.
  assert.deepEqual(await page.locator('#import-list h4').allTextContents(),['Recommended 1','Yours 12']);
  assert.deepEqual(await page.locator('#import-list details > summary').allTextContents(),['In your projects 52','From vendors 50','Built in 13','Can’t import 1']);
  assert.equal(await page.locator('#import-list details[open]').count(),0);
  assert.equal(await page.locator('#import-all').textContent(),'Show all 12');
  // Import selected and Close are on screen without scrolling the page; the panel scrolls inside the viewport.
  const bar=await page.locator('#import-actions').boundingBox(),panel=await page.locator('#skill-import').boundingBox();
  assert.ok(bar.y+bar.height<=900+1&&bar.y>=0,'action bar in view at '+width);
  assert.ok(panel.height<=900,'panel fits the viewport at '+width);
  assert.ok(await page.locator('#import-body').evaluate(b=>b.scrollHeight>b.clientHeight),'panel body scrolls');
  await page.locator('#import-list summary',{hasText:'Can’t import'}).click();
  assert.match(await page.locator('[data-import="claude:~/.claude/skills/meeting-prep"]').textContent(),/Recommended: Your cards are mostly meeting preparations/);
  const pdf=page.locator('[data-import="claude:pdf"]');
  assert.equal(await pdf.locator('input').isDisabled(),true);
  assert.match(await pdf.textContent(),/Can't import: its license doesn't allow it/);
  assert.match(await page.locator('[data-import="chatgpt:~/.codex/skills/report"]').textContent(),/Needs its 1 script/);
  // Each place it looks has its own state, a spinner only on the ones still being read, and the list says it is
  // what has come in so far.
  assert.deepEqual(await page.locator('#import-progress .import-src').allTextContents(),['✓This computer 129 found','claude.ai looking…','ChatGPT looking…']);
  assert.equal(await page.locator('#import-progress .spin').count(),2);
  assert.match(await page.locator('#import-status').textContent(),/^Showing what’s in so far\. More can still come in\.$/);
  if(shots){await page.locator('#import-body').evaluate(b=>b.scrollTop=0);await page.locator('#skill-import').screenshot({path:`${shots}/skills-import-looking-${width}-${theme}.png`});}
  assert.ok(await fits(page),'import list fits at '+width);
  // The accounts turn ends: its skill joins the list, and a signed-out site says what to do.
  await page.locator('[data-import="claude:account:follow-ups"]').waitFor({timeout:9000});
  assert.match(await page.locator('#import-signin').textContent(),/Sign in to ChatGPT.*signed out of ChatGPT/);
  // Done: no spinner and no "looking" left; each place says what it found, or plainly why it couldn't.
  assert.deepEqual(await page.locator('#import-progress .import-src').allTextContents(),['✓This computer 129 found','✓claude.ai 1 found','ChatGPT not signed in']);
  assert.equal(await page.locator('#import-progress .spin').count(),0);
  assert.equal(await page.locator('#import-status').textContent(),'Found 130 skills, 2 picked for your work.');
  if(shots){await page.locator('#import-body').evaluate(b=>b.scrollTop=0);await page.locator('#skill-import').screenshot({path:`${shots}/skills-import-done-${width}-${theme}.png`});}
  assert.ok(calls.some(c=>c.action==='skill_import_status'&&c.args.token==='tok1'));
  // Search finds one in a closed group.
  await page.locator('#import-search').fill('vendor-skill-42');
  assert.deepEqual(await page.locator('#import-list [data-import] strong').allTextContents(),['vendor-skill-42']);
  await page.locator('#import-search').fill('');
  if(shots)await page.locator('#skill-import').screenshot({path:`${shots}/skills-import-${width}-${theme}.png`});
  // Read: what would come in, shown as text; a skill that needs its scripts offers OpenClaw's install instead.
  await page.locator('[data-import-read="chatgpt:~/.codex/skills/report"]').click();
  await page.locator('#skill-look[open]').waitFor();
  assert.equal(await page.locator('#skill-look-body b').count(),0);
  assert.match(await page.locator('#skill-look-notes').textContent(),/It needs its 1 script \(scripts\/build\.py\)/);
  assert.ok(await page.locator('#skill-look').evaluate(d=>d.getBoundingClientRect().right<=innerWidth));
  if(shots)await page.locator('#skill-look').screenshot({path:`${shots}/skills-import-read-${width}-${theme}.png`});
  await page.locator('[data-import-install]').click();
  await page.locator('#notice',{hasText:'Installed in OpenClaw with its scripts'}).waitFor();
  assert.deepEqual(calls.find(c=>c.action==='skill_import_install').args,{token:'tok1',key:'chatgpt:~/.codex/skills/report',sha:'abc'});
  // Sign in to ChatGPT: the sign-in page opens on the Machine, the dialog asks for the code ChatGPT sent, and once
  // in, the list looks in that account by itself.
  await page.locator('[data-import-signin="chatgpt"]').click();
  await page.locator('#signin-dialog[open] #signin-code-row:not([hidden])').waitFor();
  assert.match(await page.locator('#signin-title').textContent(),/Sign in to ChatGPT/);assert.match(await page.locator('#signin-code-help').textContent(),/Sent to j•••@example\.com/);
  if(shots)await page.locator('#signin-dialog').screenshot({path:`${shots}/skills-import-signin-${width}-${theme}.png`});
  await page.locator('#signin-code').fill('123456');
  await page.locator('#signin-go').click();
  await page.locator('[data-import="chatgpt:account:pricing"]').waitFor({timeout:9000});
  assert.deepEqual(await page.locator('#import-progress .import-src').allTextContents(),['✓This computer 129 found','✓claude.ai 1 found','✓ChatGPT 1 found']);
  assert.equal(await page.locator('#signin-dialog[open]').count(),0);
  assert.deepEqual(calls.find(c=>c.action==='skill_import_signin').args,{site:'chatgpt'});
  assert.deepEqual(calls.find(c=>c.action==='skill_import_find'&&c.args.token).args,{token:'tok1',sites:['chatgpt']});
  assert.equal(await page.locator('#import-signin').isHidden(),true);
  // Tick two and import them.
  await page.locator('[data-import="claude:~/.claude/skills/meeting-prep"] input').check();
  await page.locator('[data-import="claude:account:follow-ups"] input').check();
  assert.equal(await page.locator('#import-go').textContent(),'Import selected (2)');
  await page.locator('#import-go').click();
  await page.locator('#notice',{hasText:'Imported 2 skills.'}).waitFor();
  assert.deepEqual(calls.find(c=>c.action==='skill_import_run').args,{token:'tok1',keys:['claude:~/.claude/skills/meeting-prep','claude:account:follow-ups']});
  // Imported: shown as done, with View, under Imported; never under Can't import, and nothing left to tick.
  const doneRow=page.locator('[data-import="claude:account:follow-ups"]');
  assert.match(await doneRow.textContent(),/✓ Imported/);
  assert.doesNotMatch(await doneRow.textContent(),/Already in your skills/);
  assert.equal(await doneRow.locator('input').count(),0);
  assert.equal(await doneRow.locator('[data-import-view]').count(),1);
  assert.equal((await page.locator('#import-list h4').allTextContents())[0],'Imported 2');
  assert.match(await page.locator('#import-status').textContent(),/^Imported 2 skills\./);
  assert.equal(await page.locator('#import-go').textContent(),'Import selected');
  assert.equal(await page.locator('#skills-list [data-skill]').count(),2);
  // An upload goes as one, marked as an upload, with the chosen origin.
  assert.equal(await page.locator('#import-own').evaluate(d=>d.open),false);  // out of the way once there is a list
  await page.locator('#import-own > summary').click();
  await page.locator('input[name="import-from"][value="chatgpt"]').check();
  await page.locator('#import-zip').setInputFiles({name:'x.zip',mimeType:'application/zip',buffer:Buffer.from('PK fake')});
  await page.locator('[data-import="claude:upload:x.zip/"]').waitFor();
  const up=calls.find(c=>c.action==='skill_import_upload');
  assert.equal(up.upload,'skill-import');assert.equal(up.args.from,'chatgpt');assert.equal(up.args.data.name,'x.zip');
  assert.equal(Buffer.from(up.args.data.zip,'base64').toString(),'PK fake');
  assert.ok(await fits(page));
  // The reported case: one uploaded skill with two non-script files. They are named; after Import it is done, the
  // list has no Can't import and no Import selected left, and View finds it in the list.
  await page.locator('#import-zip').setInputFiles({name:'writer.zip',mimeType:'application/zip',buffer:Buffer.from('PK fake')});
  const writer=page.locator('[data-import="claude:upload:writer.zip/writer"]');await writer.waitFor();
  assert.match(await writer.textContent(),/Left out: openai\.yaml, icon\.svg/);
  assert.doesNotMatch(await writer.textContent(),/more file/);
  await writer.locator('input').check();await page.locator('#import-go').click();
  await page.locator('#import-status',{hasText:'Imported 1 skill.'}).waitFor();
  assert.deepEqual(await page.locator('#import-list h4').allTextContents(),['Imported 1']);
  assert.equal(await page.locator('#import-list').textContent().then(t=>/Can’t import/.test(t)),false);
  assert.equal(await page.locator('#import-go').isHidden(),true);
  await writer.locator('[data-import-view]').click();
  await page.locator('#skills-list [data-skill="sk_claude_upload_writer_zip_writer"].skill-flash').waitFor();
  // A skill that is here already: info, not an error, with View and Replace; Replace updates the owner's own.
  skills={...skills,skills:[...skills.skills,{id:'sk_old',name:'here',description:'x',status:'on',origin:'mine',version:1,author:'',note:'',update:false,chars:9,shares:[],changed:false}]};
  await page.locator('#import-zip').setInputFiles({name:'here.zip',mimeType:'application/zip',buffer:Buffer.from('PK fake')});
  const here=page.locator('[data-import="claude:upload:here.zip/here"]');await here.waitFor();
  assert.deepEqual(await page.locator('#import-list h4').allTextContents(),['Already in your skills 1']);
  assert.equal(await page.locator('#import-list .import-no').count(),0);
  assert.equal(await here.locator('input').isDisabled(),true);
  await here.locator('[data-import-replace]').click();
  await page.locator('#import-status',{hasText:'Replaced 1 skill.'}).waitFor();
  assert.deepEqual(calls.findLast(c=>c.action==='skill_import_run').args,{token:'tok2',keys:['claude:upload:here.zip/here'],replace:true});
  assert.match(await here.textContent(),/✓ Replaced/);
  assert.equal(await page.locator('#skills-list [data-skill="sk_old"]').count(),1);
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('skills import dashboard ok');
}finally{await browser?.close();server.close()}
