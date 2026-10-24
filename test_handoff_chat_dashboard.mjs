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
const ASKED='Triage confirmed contact owner and tag names leak across teams. Can you file the missing issue and get both fixes coordinated today? Source: Security triage (https://tracker.example/1)';
const waiting=card('a1a1a1a1a1a1a1a1','Confirmed contact owner and tag leaks need engineering follow-up',{fleet_handoff:{name:'Pat Lee',stage:'waiting for them',asked:ASKED}});
const declined=card('c1c1c1c1c1c1c1c1','Export leaks rows',{fleet_handoff:{name:'Pat Lee',stage:'declined',note:'Not my area',back:true}});
const accepted=card('d1d1d1d1d1d1d1d1','Fix the export filter',{status:'completed',updated_at:iso(t0+2*hour),fleet_handoff:{name:'Pat Lee',stage:'accepted',note:'On it this week'}});
// Another one with Pat, under Later until its check-in: an urgent card stays there too.
const later=card('e1e1e1e1e1e1e1e1','Patch the tag search endpoint',{status:'snoozed',snoozed_until:iso(Date.now()+40*hour),handoff_wait:{name:'Pat'},fleet_handoff:{name:'Pat Lee',stage:'waiting for them',asked:'Can you patch the tag search endpoint so it checks the team?'}});
// Handed to this owner, then taken back by whoever sent it: the card it made goes, saying so
const taken=card('a4a4a4a4a4a4a4a4','Send Sam the pricing',{status:'rejected',body:'Pat asks you to take this on: “Sam wants pricing.”',updated_at:iso(t0+2*hour)});
// Handed to Pat, then the owner asked for Kim instead: the new handoff waits for review, Pat still has it until then.
const redirect=card('f1f1f1f1f1f1f1f1','Lou needs help recovering their account',{status:'prepared',body:'A corrected handoff to Kim is ready for review.',
 fleet_handoff:{name:'Pat Lee',stage:'waiting for them'},next_actions:[{kind:'handoff',label:'Hand off to Kim',to_name:'Kim Doe',to_email:'kim@acme.example',body:'Can you pick up Lou’s account recovery on Monday?'}]});
// Sent to Kim instead: taken back from Pat. And one both have.
const moved=card('a2a2a2a2a2a2a2a2','Reset the shared inbox rules',{status:'snoozed',snoozed_until:iso(Date.now()+40*hour),handoff_wait:{name:'Kim'},fleet_handoff:{name:'Kim Doe',stage:'asked',asked:'Can you reset the shared inbox rules so billing mail goes to finance?'}});
const both=card('a3a3a3a3a3a3a3a3','Plan the support rota',{status:'snoozed',snoozed_until:iso(Date.now()+40*hour),handoff_wait:{name:'Kim'},fleet_handoff:{name:'Kim Doe',stage:'waiting for them',also:['Pat'],asked:'Can you draft the support rota for next week?'}});
const failed=card('b1b1b1b1b1b1b1b1','Rotate the shared webhook secret',{fleet_handoff_failed:{name:'Pat Lee',at:iso(t0+60e3)}});
const source={kind:'source',from:'Alex Doe',via:'intercom',title:'Security triage',text:'Security triage\nVerdict: CONFIRMED\nConfidence: 90%',mine:false,at:t0-10*60e3};
const items={
 [waiting.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3}],
 [declined.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3},
  {kind:'event',text:'Pat declined it. Their note: “Not my area”',handoff:true,at:t0+2*hour}],
 [accepted.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3},
  {kind:'event',text:'Pat accepted it. Their note: “On it this week”',handoff:true,at:t0+2*hour}],
 [moved.id]:[source,{kind:'event',text:'Handed off to Pat’s Machine',mine:true,handoff:true,at:t0+60e3},
  {kind:'event',text:'Taken back from Pat',mine:true,handoff:true,at:t0+hour},{kind:'event',text:'Handed off to Kim’s Machine',mine:true,handoff:true,at:t0+hour}],
 [taken.id]:[{kind:'source',from:'Pat Lee',via:'fleet',title:'Send Sam the pricing',text:'Pat asks you to take this on: “Sam wants pricing.”',mine:false,at:t0},
  {kind:'event',text:'Taken back by Pat',choice:'reject',handoff:true,at:t0+2*hour}],
 [failed.id]:[source,{kind:'event',text:'Handoff didn’t go out to Pat’s Machine',handoff:true,retry_handoff:failed.id,at:t0+60e3}]};
const state=()=>({suggestions:[waiting,declined,failed,redirect,later,moved,both,accepted,taken],connectors:[],suggestion_counts:{pending:3,prepared:1,snoozed:3,completed:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}});
const view={on:true,kinds:{},ask_meetings:false,fleets:[],waiting:[],asked:[],intro_offers:[],splits:[],log:[],notices:[]};
const calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state()));return}
 if(req.url==='/api/action'){const call=JSON.parse(body);calls.push(call);
  if(call.action==='card_chat'){res.end(JSON.stringify({session:false,active:false,status:null,cursor:null,items:items[call.args.suggestion_id]||[]}));return}
  if(call.action==='fleet_handoff_draft'&&call.args.suggestion_id===redirect.id){res.end(JSON.stringify({suggestion_id:redirect.id,title:redirect.title,summary:'Can you pick up Lou’s account recovery on Monday?',contact:'',sources:[],
   fleets:[{id:'fl1',name:'Acme',members:[{member_id:'kim1',name:'Kim Doe'},{member_id:'pat1',name:'Pat Lee'}]}]}));return}
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
  assert.equal((await w.locator('.body').textContent()).trim(),'Asked Pat: '+ASKED);  // what was asked, not the old suggestion
  assert.ok((await w.locator('.body p').evaluate(p=>p.getBoundingClientRect().height/parseFloat(getComputedStyle(p).lineHeight)))<=2.1,'two lines at most');
  assert.equal(await w.locator('.more').count(),0);
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
  // Redirected: Pat isn't shown as current; the new handoff is the primary button and opens the form on Kim
  await page.goto('http://127.0.0.1:'+server.address().port+'/');
  const r=page.locator('#task-'+redirect.id);await r.waitFor();
  assert.equal((await r.locator('.note.fleet').textContent()).trim(),'Still with Pat until you send it to Kim');
  const review=r.locator('button.primary',{hasText:'Review handoff to Kim'});
  assert.equal(await review.count(),1);
  if(dir){await r.scrollIntoViewIfNeeded();await r.screenshot({path:`${dir}/handoff-redirect-card-${width}.png`})}
  const sent=calls.filter(c=>c.action==='fleet_handoff').length;
  await review.click();
  const f=r.locator('.fleet-handoff-form');await f.waitFor();
  assert.equal(await f.locator('input[name=to]:checked').getAttribute('value'),'fl1|kim1');
  assert.doesNotMatch(await f.textContent(),/told/);  // their Machine is told now
  assert.match(await r.locator('.body').textContent(),/corrected handoff to Kim/);  // the new suggestion, not what Pat was asked
  assert.equal(await f.locator('textarea[name=task]').inputValue(),'Can you pick up Lou’s account recovery on Monday?');
  const take=f.locator('input[name=replace]');
  assert.ok(await take.isChecked());
  assert.equal((await f.locator('.handoff-back').textContent()).trim(),'Take it back from Pat');
  assert.equal(calls.filter(c=>c.action==='fleet_handoff').length,sent);  // nothing sent without the owner's Hand off
  if(dir){await f.scrollIntoViewIfNeeded();await f.screenshot({path:`${dir}/handoff-redirect-form-${width}.png`})}
  await f.locator('button.primary').click();await page.waitForTimeout(300);
  assert.deepEqual(calls.filter(c=>c.action==='fleet_handoff').at(-1).args,{suggestion_id:redirect.id,fleet:'fl1',member_id:'kim1',title:redirect.title,summary:'Can you pick up Lou’s account recovery on Monday?',contact:'',sources:[],replace:true});
  // Under Later: who has it now, both when both do
  const ll=page.locator('#later-list');if(!await ll.evaluate(d=>d.open))await ll.locator(':scope > summary').click();
  assert.equal((await ll.locator('#task-'+moved.id+' .note.fleet').textContent()).trim(),'With Kim · waiting');
  assert.equal((await ll.locator('#task-'+both.id+' .note.fleet').textContent()).trim(),'With Kim and Pat · waiting');
  // Its chat: handed to Pat, taken back, handed to Kim
  await open(moved.id);
  assert.deepEqual((await page.locator('#chat-log .chat-event').allTextContents()).map(t=>t.replace(/ · \d\d:\d\d$/,'').trim()),
   ['Handed off to Pat’s Machine','Taken back from Pat','Handed off to Kim’s Machine']);
  if(dir)await page.screenshot({path:`${dir}/handoff-redirect-chat-${width}.png`});
  // The original suggestion stays in the chat
  await open(waiting.id);
  assert.match(await page.locator('#chat-log').textContent(),/I can hand this to Pat’s Machine/);
  // Taken back by whoever handed it: their line in the chat of the card it made
  await open(taken.id);
  assert.equal((await page.locator('#chat-log .chat-event').last().textContent()).trim(),'Taken back by Pat · '+hm(t0+2*hour));
  await page.evaluate(()=>document.querySelector('#toast')?.classList.remove('show'));await page.waitForTimeout(500);  // the earlier Hand off's toast
  if(dir)await page.screenshot({path:`${dir}/handoff-taken-back-chat-${width}.png`});
  assert.deepEqual(errors,[]);
  await context.close();
 }
 console.log('handoff chat dashboard: ok');
}finally{await browser.close();server.close()}
