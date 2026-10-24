import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A handoff in the card chat. Handed off: a line with its time, the card's buttons under it (not above it on the
// suggestion), and the status says no answer yet. Their answer: a line of its own. Didn't go out: said in the chat
// and on the card, with Try again, which opens the handoff form again. Fakes only; no network beyond this fake server.
// SHOT_DIR=... saves screenshots (390 and 1440, dark).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const hour=3600e3,t0=Date.now()-3*hour,iso=ms=>new Date(ms).toISOString();
const BODY='Triage confirmed a cross-team exposure of user names and tag names. The account issue is tracked, but these two paths are not. I can hand this to Pat’s Machine to get the missing issue and fixes coordinated **today**.';
const card=(id,title,extra)=>({id,title,body:BODY,status:'pending',event_ids:[1],scope:'work',created_at:iso(t0),next_actions:[],urgent:true,...extra});
const waiting=card('a1a1a1a1a1a1a1a1','Confirmed contact owner and tag leaks need engineering follow-up',{fleet_handoff:{name:'Pat Lee',stage:'waiting for them'}});
const declined=card('c1c1c1c1c1c1c1c1','Export leaks rows',{fleet_handoff:{name:'Pat Lee',stage:'declined',note:'Not my area',back:true}});
const accepted=card('d1d1d1d1d1d1d1d1','Fix the export filter',{status:'completed',updated_at:iso(t0+2*hour),fleet_handoff:{name:'Pat Lee',stage:'accepted',note:'On it this week'}});
// Another one with Pat, under Later until its check-in: an urgent card stays there too.
const later=card('e1e1e1e1e1e1e1e1','Patch the tag search endpoint',{status:'snoozed',snoozed_until:iso(Date.now()+40*hour),handoff_wait:{name:'Pat'},fleet_handoff:{name:'Pat Lee',stage:'waiting for them'}});
const failed=card('b1b1b1b1b1b1b1b1','Rotate the shared webhook secret',{fleet_handoff_failed:{name:'Pat Lee',at:iso(t0+60e3)}});
const source={kind:'source',from:'Alex Doe',via:'intercom',title:'Security triage',text:'Security triage\nVerdict: CONFIRMED\nConfidence: 90%',mine:false,at:t0-10*60e3};
const items={
 [waiting.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3}],
 [declined.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3},
  {kind:'event',text:'Pat declined it. Their note: “Not my area”',handoff:true,at:t0+2*hour}],
 [accepted.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3},
  {kind:'event',text:'Pat accepted it. Their note: “On it this week”',handoff:true,at:t0+2*hour}],
 [failed.id]:[source,{kind:'event',text:'Handoff didn’t go out to Pat’s Machine',handoff:true,retry_handoff:failed.id,at:t0+60e3}]};
const state=()=>({suggestions:[waiting,declined,failed,later,accepted],connectors:[],suggestion_counts:{pending:3,snoozed:1,completed:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}});
const view={on:true,kinds:{},ask_meetings:false,fleets:[],waiting:[],asked:[],intro_offers:[],splits:[],log:[],notices:[]};
const calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state()));return}
 if(req.url==='/api/action'){const call=JSON.parse(body);calls.push(call);
  if(call.action==='card_chat'){res.end(JSON.stringify({session:false,active:false,status:null,cursor:null,items:items[call.args.suggestion_id]||[]}));return}
  if(call.action==='fleet_handoff_draft'){res.end(JSON.stringify({suggestion_id:call.args.suggestion_id,title:failed.title,summary:'Rotate the shared webhook secret today.',contact:'',sources:[],
   fleets:[{id:'fl1',name:'Acme',members:[{member_id:'pat1',name:'Pat Lee'}]}]}));return}
  res.end('{}');return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const dir=process.env.SHOT_DIR,hm=ms=>new Date(ms).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
try{
 for(const width of [390,1440]){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:'dark',locale:'en-US'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const open=async id=>{await page.goto('http://127.0.0.1:'+server.address().port+'/#chat='+id);await page.locator('#chat-log .chat-event').first().waitFor()};
  // With Pat, no answer yet: the line with its time, then "No answer from Pat yet", then the buttons (no Do it)
  await open(waiting.id);
  const ev=page.locator('#chat-log .chat-event').first();
  assert.equal((await ev.textContent()).trim(),'Handed off to Pat’s Machine · '+hm(t0+60e3));
  assert.ok(await ev.locator('.chat-when').isVisible(),'the time shows on a phone too');
  assert.doesNotMatch(await page.locator('#chat-log').textContent(),/\bYou (handed|snoozed)/i);
  const log=await page.locator('#chat-log').evaluate(l=>[...l.children].map(n=>n.className.includes('chat-handoff-options')?'options':n.className.includes('chat-handoff-wait')?'wait':n.className.includes('chat-event')?'event':n.className.includes('chat-card')?'card':'other'));
  assert.deepEqual(log.slice(-3),['event','wait','options'],log.join());
  assert.equal((await page.locator('#chat-log .chat-handoff-wait').textContent()).trim(),'No answer from Pat yet');
  assert.equal(await page.locator('#chat-log .chat-card .chat-options').count(),0);  // no stale buttons above the line
  assert.deepEqual((await page.locator('#chat-log .chat-handoff-options button').allTextContents()).map(t=>t.trim()),['Mark done','Snooze','Not needed']);
  assert.equal(await page.locator('#chat-log .chat-handoff-options button.primary').textContent(),'Mark done');
  assert.equal((await page.locator('#chat-status').textContent()).trim(),'');  // said once, under the line
  assert.ok(await page.locator('#chat-input').isVisible());  // "remind Pat" or "take it back" still go here
  if(dir)await page.screenshot({path:`${dir}/handoff-waiting-${width}.png`});
  // Declined, with their note: its own line; Do it myself under it
  await open(declined.id);
  assert.match(await page.locator('#chat-log .chat-event').nth(1).textContent(),/^Pat declined it\. Their note: “Not my area” · \d\d:\d\d$/);
  assert.match(await page.locator('#chat-log .chat-handoff-options').textContent(),/Do it myself/);
  if(dir)await page.screenshot({path:`${dir}/handoff-declined-${width}.png`});
  // Accepted: their answer, and the card is done
  await open(accepted.id);
  assert.match(await page.locator('#chat-log .chat-event').nth(1).textContent(),/^Pat accepted it\. Their note: “On it this week” · \d\d:\d\d$/);
  assert.equal(await page.locator('#chat-log .chat-handoff-wait').count(),0);
  if(dir)await page.screenshot({path:`${dir}/handoff-accepted-${width}.png`});
  // Didn't go out: said, with Try again, which opens the handoff form
  await open(failed.id);
  const bad=page.locator('#chat-log .chat-event.failed');
  assert.equal((await bad.textContent()).trim(),'Handoff didn’t go out to Pat’s Machine · '+hm(t0+60e3)+' · Try again');
  if(dir)await page.screenshot({path:`${dir}/handoff-failed-${width}.png`});
  await bad.locator('[data-fleet-handoff]').click();
  await page.locator('.chat-panel .fleet-handoff-form').waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='fleet_handoff_draft').at(-1).args,{suggestion_id:failed.id});
  if(dir)await page.screenshot({path:`${dir}/handoff-retry-form-${width}.png`});
  // The card in the list, back on top with no answer: it asks for a check-in, Mark done first, no Do it
  await page.goto('http://127.0.0.1:'+server.address().port+'/');
  const w=page.locator('#task-'+waiting.id);await w.waitFor();
  assert.equal((await w.locator('.note.fleet').textContent()).trim(),'No answer from Pat yet · Check in');
  assert.equal(await w.locator('.note.fleet a').getAttribute('href'),'#chat='+waiting.id);
  assert.equal(await w.locator('button',{hasText:/^Do it$/}).count(),0);
  assert.equal((await w.locator('.actions button.primary').first().textContent()).trim(),'Mark done');
  if(dir){await w.scrollIntoViewIfNeeded();await w.screenshot({path:`${dir}/handoff-waiting-card-${width}.png`})}
  // Still with Pat: under Coming back later, "With Pat · waiting" and when it checks in
  const lat=page.locator('#later-list');
  if(!await lat.evaluate(d=>d.open))await lat.locator(':scope > summary').click();
  const l=lat.locator('#task-'+later.id);await l.waitFor();
  assert.equal((await l.locator('.note.fleet').textContent()).trim(),'With Pat · waiting');
  assert.match(await l.locator('.checkin-line').textContent(),/^Check in with Pat .+ if no answer$/);
  assert.equal(await page.locator('#suggestions > .card#task-'+later.id).count(),0);
  if(dir){await lat.scrollIntoViewIfNeeded();await lat.screenshot({path:`${dir}/handoff-later-${width}.png`})}
  await w.locator('.note.fleet a').scrollIntoViewIfNeeded();await page.waitForTimeout(300);  // a tap, not the end of a scroll
  await w.locator('.note.fleet a').click();
  await page.locator('#chat-log .chat-handoff-wait').waitFor();
  // The card itself says it too
  await page.goto('http://127.0.0.1:'+server.address().port+'/');
  const c=page.locator('#task-'+failed.id);await c.waitFor();
  assert.equal((await c.locator('.note.handoff-failed').textContent()).trim(),'Handoff didn’t go out to Pat’s Machine · Try again');
  if(dir){await c.scrollIntoViewIfNeeded();await c.screenshot({path:`${dir}/handoff-failed-card-${width}.png`})}
  assert.deepEqual(errors,[]);
  await context.close();
 }
 console.log('handoff chat dashboard: ok');
}finally{await browser.close();server.close()}
