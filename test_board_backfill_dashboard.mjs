import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A new board looks through the history once: its header says "Looking through your history…" while it runs, then how
// many it added and how many wait for review. An organisation from the history with no project yet waits under "could
// belong here" like a project, and Add or Leave out posts one board_fit with its key. Desktop and a 390 px phone, light
// and dark, no sideways scroll. BACKFILL_SHOT=<dir> also writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Trial'},{name:'Decision'},{name:'Won'},{name:'Stalled or declined',side:true}];
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const proj=(i,name,stage,reason)=>({id:'project:'+i,name,stage,reason,by:'review',first:true,at:day(i),event_id:100+i,days:i,no_reply_days:0,org:{name,logo:null}});
const fresh=()=>[{id:1,name:'Demos and trials',stages,default:true,stuck:0,backfill:{state:'done',added:12,review:3},
  projects:[proj(1,'Paperkite','Demo','demo held, they asked for pricing'),proj(2,'Bramblebox','Trial','started a 14-day trial'),proj(3,'Quillfeather','Won','signed the yearly plan'),
   proj(4,'Inkwell','Discovery','asked how the inbox sync works')],
  maybes:[{id:'org:tinkerlane.example',name:'Tinker Lane',why:'asked about pricing once'},{id:'org:puffin.example',name:'Puffin Metrics',why:'an intro call, no demo yet'},
   {id:'project:10',name:'Saffron Sync',why:'a partner, maybe a trial'}]},
 {id:2,name:'Customer support cases',stages:stages.slice(0,3).concat(stages.slice(-1)),stuck:0,backfill:{state:'running'},projects:[proj(30,'Fernwave','Demo','reported a sync gap')],maybes:[]}];
let boards=fresh(),calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({projects:[],people:[],quiet:[],new:[],orgs:[],boards,person_names:[],project_names:[]}));return}
 if(req.method!=='GET'&&!url.pathname.startsWith('/brand/'))calls.push(JSON.parse(body||'{}'));
 if(url.pathname==='/api/action'){const c=JSON.parse(body||'{}'),a=c.args||{};
  if(c.action==='board_fit'){boards[0].maybes=boards[0].maybes.filter(m=>m.id!==a.node_id);boards[0].backfill={...boards[0].backfill,added:boards[0].backfill.added+(a.add?1:0),review:boards[0].maybes.length}}
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
const shot=async(page,name)=>{if(process.env.BACKFILL_SHOT)await page.screenshot({path:`${process.env.BACKFILL_SHOT}/${name}.png`,fullPage:false})};
const sub=page=>page.locator('.board-sub').textContent();
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height,scheme] of [[1440,900,'light'],[390,844,'light'],[390,844,'dark']]){
  boards=fresh();const {context,page}=await open(width,height,scheme);const tag=`${width}-${scheme}`;
  // Done: what it found, in a few words, instead of the usual line
  assert.match(await sub(page),/4 projects · 12 added from your history · 3 to review$/);
  assert.match(await page.locator('.tile[data-node="project:2"] .tile-why').textContent(),/^Added: started a 14-day trial/);
  const box=page.locator('.board-fits');assert.match(await box.locator('summary').textContent(),/3 projects could belong here/);
  await box.locator('summary').click();await fits(page,tag+' done');await shot(page,`backfill-done-${tag}`);
  calls=[];await box.locator('[data-fit-add="org:tinkerlane.example"]').click();await page.locator('.board-fits summary',{hasText:'2 projects could belong here'}).waitFor();
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['board_fit',{board_id:1,node_id:'org:tinkerlane.example',add:true}]]);
  calls=[];await page.locator('[data-fit-skip="org:puffin.example"]').click();await page.locator('.board-fits summary',{hasText:'1 project could belong here'}).waitFor();
  assert.deepEqual(calls.map(c=>[c.action,c.args]),[['board_fit',{board_id:1,node_id:'org:puffin.example',add:false}]]);
  // Running: a short line that says it is looking
  await page.locator(width<600?'.board-pick':'.board-chips [data-board="Customer support cases"]').first().evaluate(el=>el.tagName==='SELECT'?(el.value='Customer support cases',el.dispatchEvent(new Event('change',{bubbles:true}))):el.click());
  await page.locator('.board-title',{hasText:'Customer support cases'}).waitFor({state:'attached'});
  assert.match(await sub(page),/^1 project · Looking through your history…$/);
  assert.equal(await page.locator('.board-sub .backfill-run').count(),1);
  await fits(page,tag+' running');await shot(page,`backfill-running-${tag}`);
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('board backfill dashboard ok');
}finally{await browser?.close();server.close()}
