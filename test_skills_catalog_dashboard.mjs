import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings > Skills with everything OpenClaw has: grouped (yours, workspace, bundled, other) with what each still needs,
// one search over names and descriptions, OpenClaw's own on/off, ClawHub search shown as third-party, read before
// installing, and "There's a skill for this" as a quiet line on its card. 390px and 1280px wide.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const shots=process.env.SKILLS_SHOTS||'';
const MD='---\nname: Meeting Prep\ndescription: Prepares briefings\n---\n# Meeting Prep\n<b>Research</b> people before a meeting.';
let hints=[{id:'sh_1',suggestion_id:'s1',skill:'@kim/meeting-prep',kind:'clawhub',name:'Meeting Prep',summary:'Prepares briefings',why:'Briefings before client meetings'},
 {id:'sh_2',suggestion_id:'s1',skill:'trello',kind:'installed',name:'trello',summary:'Manage Trello boards',why:'The client tracks this on a Trello board'}];
const skills=()=>({skills:[{id:'sk_1',name:'Weekly report',description:'When the owner asks for the weekly numbers',status:'on',origin:'mine',version:1,author:'',note:'',update:false,chars:80,shares:[],changed:false}],
 waiting:0,targets:[],folder:'skills/the-machine',openclaw:true,hints});
let installed=[
 {name:'weekly-report',description:'Mine',group:'yours',state:'ready',on:true,needs:'',toggle:false,source:'Workspace',emoji:''},
 {name:'client-meeting-prep',description:'Client meeting preparation and debriefs for sales calls',group:'workspace',state:'ready',on:true,needs:'',toggle:true,source:'Workspace',emoji:''},
 {name:'trello',description:'Manage Trello boards, lists and cards',group:'bundled',state:'off',on:false,needs:'Needs TRELLO_API_KEY',toggle:true,source:'Bundled with OpenClaw',emoji:'📋'},
 {name:'apple-notes',description:'Apple Notes via the memo command',group:'bundled',state:'setup',on:true,needs:'macOS only',toggle:false,source:'Bundled with OpenClaw',emoji:''},
 {name:'gateway-checks-with-a-really-long-name-that-goes-on-and-on-for-testing',description:'Learned checks',group:'other',state:'ready',on:true,needs:'',toggle:true,source:'Learned by OpenClaw',emoji:''}];
const first=installed;
const view=()=>({installed,checked_at:new Date().toISOString(),error:''});
const card={id:'s1',title:'Prepare Thursday’s client meeting',body:'Acme meets you Thursday. I can prepare a briefing.',status:'pending',event_ids:[1],scope:'work',created_at:new Date().toISOString()};
const calls=[];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[card],promises:[],suggestion_counts:{pending:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],connectors:[],skills:skills()}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',c=>body+=c);req.on('end',()=>{const {action,args}=JSON.parse(body);calls.push({action,args});
   if(action==='skill_installed'){res.end(JSON.stringify(view()));return}
   if(action==='skill_installed_get'){res.end(JSON.stringify({name:args.name,description:'Manage Trello boards',source:'Bundled with OpenClaw',state:'off',needs:'Needs TRELLO_API_KEY',body:'# Trello\nUse the API.',install_steps:[],homepage:''}));return}
   if(action==='skill_installed_toggle'){installed=installed.map(i=>i.name===args.name?{...i,on:args.on,state:args.on?'setup':'off'}:i);res.end(JSON.stringify(view()));return}
   if(action==='skill_hub_search'){res.end(JSON.stringify({results:[{ref:'@kim/meeting-prep',owner:'kim',slug:'meeting-prep',name:'Meeting Prep',summary:'Prepares briefings',downloads:1200,official:false,suspicious:false,installable:true,installed:false,url:'https://clawhub.ai/kim/skills/meeting-prep'},
     {ref:'@ann/client-meeting-prep',owner:'ann',slug:'client-meeting-prep',name:'Client meeting prep',summary:'x',downloads:3,installed:true,url:'https://clawhub.ai/ann/skills/client-meeting-prep'}]}));return}
   if(action==='skill_hub_get'){res.end(JSON.stringify({ref:'@kim/meeting-prep',owner:'kim',slug:'meeting-prep',name:'Meeting Prep',summary:'Prepares briefings',version:'1.2.0',body:MD,sha:'a'.repeat(64),
     files:[{path:'SKILL.md',size:90},{path:'scripts/setup.sh',size:40}],code:['scripts/setup.sh'],install_steps:true,asks_keys:false,scan:'clean',scan_warnings:false,scan_summary:'',flagged:false,url:'https://clawhub.ai/kim/skills/meeting-prep'}));return}
   if(action==='skill_hub_install'){installed=[...installed,{name:'meeting-prep',description:'Prepares briefings',group:'workspace',state:'ready',on:true,needs:'',toggle:true,source:'Workspace',emoji:''}];hints=hints.filter(h=>h.skill!==args.ref);res.end(JSON.stringify({installed_skill:'meeting-prep',...view()}));return}
   if(action==='skill_hint_dismiss'){hints=hints.filter(h=>h.id!==args.id);res.end(JSON.stringify(skills()));return}
   res.end('{}')});return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
const fits=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1280]){
  installed=first;calls.length=0;
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  // The card: one quiet line per skill, with Look and Not now.
  const note=page.locator('#task-s1 .skill-hint');
  await note.first().waitFor();
  if(width===390){
   assert.equal(await note.count(),1);  // one per card at most
   assert.match(await note.textContent(),/There’s a skill for this: Meeting Prep on ClawHub/);
   assert.ok(await fits(page));
   if(shots)await page.screenshot({path:`${shots}/skills-card-${width}.png`,fullPage:false});
   await note.locator('[data-hint-dismiss]').click();
   await page.waitForFunction(()=>/trello/.test(document.querySelector('#task-s1 .skill-hint')?.textContent||''));
   assert.deepEqual(calls.at(-1),{action:'skill_hint_dismiss',args:{id:'sh_1'}});
   // Look on an installed one opens it read-only, with OpenClaw's own Turn on.
   await page.locator('#task-s1 [data-hint-look]').click();
   await page.locator('#skill-look[open]').waitFor();
   assert.match(await page.locator('#skill-look-notes').textContent(),/Why: The client tracks this on a Trello board/);
   assert.equal(await page.locator('#skill-look [data-look-toggle="trello"]').textContent(),'Turn on');
   await page.locator('#skill-look [data-look-close]').click();
  }
  // Settings > Skills: yours, then OpenClaw's grouped, each with what it still needs.
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="skills"]').click();
  await page.locator('#skills-installed details').first().waitFor();
  assert.equal(await page.locator('#skills-yours-head').isHidden(),false);
  assert.deepEqual(await page.locator('#skills-installed details summary').allTextContents(),['Workspace 1 of 1 ready','Bundled with OpenClaw 0 of 2 ready','Other 1 of 1 ready']);
  assert.equal(await page.locator('#skills-installed [data-installed="weekly-report"]').count(),0);  // yours are listed above
  await page.locator('details[data-group="bundled"] summary').click();
  assert.match(await page.locator('[data-installed="trello"] .skill-meta').textContent(),/Off · Needs TRELLO_API_KEY/);
  assert.equal(await page.locator('[data-installed="apple-notes"] [data-installed-toggle]').count(),0);  // can't work here: no switch
  assert.ok(await fits(page));
  // One search over names and descriptions.
  await page.locator('#skills-filter').fill('trello');
  assert.equal(await page.locator('[data-installed="trello"]').isVisible(),true);
  assert.equal(await page.locator('[data-installed="client-meeting-prep"]').isVisible(),false);
  assert.equal(await page.locator('#skills-list [data-skill="sk_1"]').isVisible(),false);
  await page.locator('#skills-filter').fill('weekly');
  assert.equal(await page.locator('#skills-list [data-skill="sk_1"]').isVisible(),true);
  assert.equal(await page.locator('details[data-group="bundled"]').isVisible(),false);
  await page.locator('#skills-filter').fill('');
  // OpenClaw's own on/off.
  await page.locator('[data-installed="trello"] [data-installed-toggle]').click();
  await page.waitForFunction(()=>document.querySelector('[data-installed="trello"] [data-installed-toggle]')?.textContent==='Turn off');
  assert.deepEqual(calls.find(c=>c.action==='skill_installed_toggle'),{action:'skill_installed_toggle',args:{name:'trello',on:true}});
  installed=installed.map(i=>i.name==='trello'?{...i,on:false,state:'off'}:i);
  // ClawHub: third-party, read first, then Install with exactly what was read.
  await page.locator('#hub-query').fill('meeting prep');
  await page.locator('#hub-go').click();
  await page.locator('#hub-results [data-hub]').first().waitFor();
  assert.match(await page.locator('#hub-results [data-hub="@kim/meeting-prep"] .skill-meta').textContent(),/^Third-party · @kim · 1,200 downloads/);
  assert.match(await page.locator('#hub-results [data-hub="@ann/client-meeting-prep"]').textContent(),/Installed/);
  assert.ok(await fits(page));
  if(shots)await page.screenshot({path:`${shots}/skills-settings-${width}.png`,fullPage:true});
  await page.locator('[data-hub-read="@kim/meeting-prep"]').click();
  await page.locator('#skill-look[open]').waitFor();
  assert.match(await page.locator('#skill-look-from').textContent(),/Third-party, from ClawHub: by @kim, version 1\.2\.0/);
  const notes=await page.locator('#skill-look-notes').textContent();
  assert.match(notes,/1 script or code file \(scripts\/setup\.sh\)/);
  assert.match(notes,/install steps/);
  assert.match(await page.locator('#skill-look-body').textContent(),/<b>Research<\/b>/);  // shown as text, never rendered
  assert.equal(await page.locator('#skill-look-body b').count(),0);
  if(shots)await page.screenshot({path:`${shots}/skills-clawhub-${width}.png`});
  if(width===390){
   assert.equal(calls.some(c=>c.action==='skill_hub_install'),false);  // reading installs nothing
   await page.locator('[data-hub-install]').click();
   await page.waitForFunction(()=>!document.querySelector('#skill-look').open);
   assert.deepEqual(calls.find(c=>c.action==='skill_hub_install').args,{ref:'@kim/meeting-prep',version:'1.2.0',sha:'a'.repeat(64)});
   assert.match(await page.locator('#hub-results [data-hub="@kim/meeting-prep"]').textContent(),/Installed/);
   assert.equal(await page.locator('#skills-installed [data-installed="meeting-prep"]').count(),1);
  }else await page.locator('#skill-look [data-look-close]').click();
  assert.ok(await fits(page));
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('skills catalog dashboard ok');
}finally{await browser?.close();server.close()}
