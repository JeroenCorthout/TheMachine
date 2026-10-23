import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Silent acceptance on the dashboard. The receiver: a handoff that became theirs by what they did says so, with Not
// mine to undo it. The sender: their card is done, saying the teammate is on it, where and when; the card chat shows
// the same line. Fakes only; no network beyond this fake server. SHOT_DIR=... saves screenshots (390 and 1440, dark).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),LINE='Pat is on it · talked to Sam on Slack, Fri\u00a010:12';
const mine={id:'a1a1a1a1a1a1a1a1',title:'Send Sam the pricing',body:'Kim asks you to take this on: “Sam wants the 40-seat price this week.”',handoff_contact:'sam@client.example',
 status:'pending',event_ids:[1],scope:'work',created_at:now,next_actions:[],handoff_silent:{line:'Marked as yours, since you talked to Sam about it'}};
const theirs={id:'b1b1b1b1b1b1b1b1',title:'Quote for Sam',body:'Sam asked for the 40-seat price.',status:'completed',event_ids:[2],scope:'work',created_at:now,updated_at:now,
 next_actions:[],fleet_handoff:{name:'Pat',stage:'on it',line:LINE}};
const state=()=>({suggestions:[mine,theirs],connectors:[],suggestion_counts:{pending:mine.status==='pending'?1:0,completed:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}});
const view={on:true,kinds:{},ask_meetings:false,fleets:[],waiting:[],asked:[],intro_offers:[],splits:[],log:[],notices:[]};
const calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state()));return}
 if(req.url==='/api/action'){const call=JSON.parse(body);calls.push(call);
  if(call.action==='fleet_handoff_not_mine'){Object.assign(mine,{status:'rejected'});delete mine.handoff_silent}
  if(call.action==='card_chat'){res.end(JSON.stringify({session:true,active:false,status:'ok',cursor:'k1',items:[
   {kind:'event',text:'Handed off to Pat',choice:'handoff',mine:true,at:Date.now()-7200e3},{kind:'event',text:LINE,choice:'complete',at:Date.now()-3600e3}]}));return}
  res.end(JSON.stringify({status:'answered',ids:{}}));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const dir=process.env.SHOT_DIR;
try{
 for(const width of [390,1440]){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:'dark',locale:'en-US'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  // Receiver: marked as theirs, with one quiet way out.
  const r=page.locator('#task-'+mine.id);await r.waitFor();
  const note=r.locator('.silent-note');
  assert.equal((await note.textContent()).trim(),'Marked as yours, since you talked to Sam about it · Not mine');
  assert.equal((await r.locator('.handoff-contact').textContent()).trim(),'Contact: sam@client.example');  // its own line, not in the quote
  assert.doesNotMatch(await r.locator('.body').textContent(),/Contact/);
  assert.equal(await note.locator('[data-handoff-not-mine]').getAttribute('data-handoff-not-mine'),mine.id);
  if(dir){await r.scrollIntoViewIfNeeded();await r.screenshot({path:`${dir}/receiver-note-${width}.png`})}
  // Sender: done, saying who is on it, where and when.
  await page.locator('#finished-list>summary').click();
  const s=page.locator('#finished-list #task-'+theirs.id);await s.waitFor();
  assert.equal((await s.locator('.note.fleet').textContent()).trim(),LINE);
  if(dir){await s.scrollIntoViewIfNeeded();await s.screenshot({path:`${dir}/sender-card-${width}.png`})}
  // The card chat: the same line as an event.
  await s.locator('a.card-chat-link').click();if(!(await page.locator('#chat-log').count()))await page.evaluate(id=>chatGo(id),theirs.id);  // after an element screenshot the tap can miss
  const ev=page.locator('#chat-log .chat-event',{hasText:'is on it'});await ev.waitFor();
  assert.match(await ev.textContent(),/Pat is on it · talked to Sam on Slack, Fri\u00a010:12/);
  // Every event line is centred the same way, also the owner's own (Handed off to Pat)
  for(const e of await page.locator('#chat-log .chat-event').all())assert.equal(await e.evaluate(x=>getComputedStyle(x).textAlign+' '+getComputedStyle(x).alignSelf),'center center',await e.textContent());
  if(dir)await page.screenshot({path:`${dir}/chat-event-${width}.png`});
  if(width===1440){  // Not mine: undone, and the card leaves the list
   await page.goto('http://127.0.0.1:'+server.address().port);await r.waitFor();
   await r.locator('[data-handoff-not-mine]').click();
   await page.locator('#suggestions>article#task-'+mine.id+', #suggestions section>#task-'+mine.id).waitFor({state:'detached'}).catch(()=>{});
   await page.waitForTimeout(300);
   assert.deepEqual(calls.filter(c=>c.action==='fleet_handoff_not_mine').map(c=>c.args),[{suggestion_id:mine.id}]);
   assert.equal(await page.locator('.silent-note').count(),0);
  }
  assert.deepEqual(errors,[]);
  await context.close();
 }
 console.log('handoff silent dashboard: ok');
}finally{await browser.close();server.close()}
