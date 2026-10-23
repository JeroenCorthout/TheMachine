import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A card whose result waits on the owner's answer (needs_you, written as a question) leads with Answer: it opens the
// Change editor under the question, and Send answer saves it as a Change note. Mark done steps back. A question line
// keeps its "?" (no "?."). A needs line that isn't a question, or one with a draft to send, keeps its usual buttons.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const cards=[
 {id:'q1',title:'Get the quarterly report ready',body:'Filled in every field except the cash-out date.',status:'prepared',scope:'work',created_at:now,event_ids:[1],
  outcome:JSON.stringify({state:'needs_you',needs:'How should infinite runway go in the date-only cash-out field?'})},
 {id:'t1',title:'Renew the parking permit',body:'Filled in the form.',status:'prepared',scope:'personal',created_at:now,event_ids:[2],
  outcome:JSON.stringify({state:'needs_you',needs:'Pay in the bank app.'})},
 {id:'d1',title:'Reply to Noor about the contract',body:'Drafted a reply for the likelier answer.',status:'prepared',scope:'work',created_at:now,event_ids:[3],
  outcome:JSON.stringify({state:'needs_you',needs:'Hold firm on the price?'}),
  next_actions:[{kind:'email',revision:'v1',label:'Send to Noor',to:['noor@example.com'],to_name:'Noor',subject:'Contract',body:'Hi Noor'}]},
 // A run that stopped to ask, on a card a teammate handed back: Answer leads, not Do it myself
 {id:'h1',title:'Contact creation can expose another account',body:'Drafted a handoff.',status:'instructions',scope:'work',created_at:now,event_ids:[4],
  preparation_status:'blocked',preparation_reason:'I couldn’t find the bounty amount; please share it or point me to the message.',
  latest_feedback:{choice:'other',feedback:'Someone else picked it up. I still need to pay the reporter.',created_at:now},fleet_handoff:{name:'Lee',stage:'declined',back:true}},
 // The service's own failure and a sign-in keep their usual lead
 {id:'e1',title:'Collect the invoices',body:'x',status:'instructions',scope:'work',created_at:now,event_ids:[5],preparation_status:'blocked',preparation_error:'ai_run_failed',
  preparation_reason:'I tried twice but couldn’t finish this. Tap Try again, or change the request.'},
 // Stopped to ask with named questions, after a Try again that changed nothing: quick picks, no Try again
 {id:'o1',title:'Finish the partner listing',body:'Your draft is kept.',status:'instructions',scope:'work',created_at:now,event_ids:[7],preparation_status:'blocked',preparation_cause:'retry',
  preparation_reason:'Finishing the listing needs your notes and one choice.',preparation_browser:{since:now,link:'https://example.com/b'},
  preparation_details:'Read /home/owner/.openclaw/workspace/memory/assistant/261022-listing.md and doc 1CLX9Q28esUdnW7YB2q72G5otQBzTZH7XW, run 9c3aed05-3d87-4c9a-adff-55b38d27277a.',
  preparation_questions:[{question:'What did you notice when you tried the tool?'},{question:'Which score fills the gap?',options:['3.9','4.0']}]},
 // The same stop without named questions, as runs wrote them before: Answer still leads, no second Try again
 {id:'r1',title:'Finish the other listing',body:'x',status:'instructions',scope:'work',created_at:now,event_ids:[8],preparation_status:'blocked',preparation_cause:'retry',preparation_error:null,
  preparation_reason:'Your draft is preserved; finishing the listing still needs your testing notes and a choice of score.'},
 {id:'g1',title:'Download the statements',body:'x',status:'instructions',scope:'work',created_at:now,event_ids:[6],preparation_status:'blocked',
  preparation_reason:'The bank needs your code: complete sign-in in the tab, then tap Continue.'}];
const posts=[];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:true,fleets:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'&&req.method==='POST'){posts.push(JSON.parse(body||'{}'));res.end('{"status":"instructions"}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1280,390]){
  posts.length=0;
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-q1').waitFor();
  const line=id=>page.locator(`#task-${id} .status-line`).textContent();
  const primary=id=>page.locator(`#task-${id} .actions button.primary`).allTextContents();
  // The question keeps its own mark; a plain line gets one full stop
  assert.equal(await line('q1'),'Needs you: How should infinite runway go in the date-only cash-out field?');
  assert.equal(await line('t1'),'Needs you: Pay in the bank app.');
  // Answer leads; Mark done is there but not primary; no separate Change (Answer is that path)
  assert.deepEqual(await primary('q1'),['Answer']);
  assert.equal(await page.locator('#task-q1 [data-choice="complete"]').getAttribute('class'),'ghost');
  assert.equal(await page.locator('#task-q1 [data-choice="change"]').count(),0);
  // Not a question: Mark done still leads. A draft to send: Review and send leads, no Answer
  assert.deepEqual(await primary('t1'),['Mark done']);
  assert.deepEqual(await primary('d1'),['Review and send to Noor']);
  assert.equal(await page.locator('#task-d1 [data-choice="answer"]').count(),0);
  // Stopped to ask: the blocker leads as the amber Needs you line, once; Answer leads; Hand to someone else and Mark done follow
  assert.equal(await line('h1'),'Needs you: I couldn’t find the bounty amount; please share it or point me to the message.');
  assert.equal(await page.locator('#task-h1 .status-line.outcome').count(),1);
  assert.equal((await page.locator('#task-h1').innerText()).split('bounty amount').length,2);
  assert.deepEqual(await primary('h1'),['Answer']);
  assert.equal(await page.locator('#task-h1 [data-handoff-mine]').count(),0);
  assert.deepEqual((await page.locator('#task-h1 .actions > button').allTextContents()).slice(0,3),['Answer','Hand to someone else','Mark done']);
  assert.equal(await page.locator('#task-h1 [data-choice="retry"]').count(),1);
  assert.deepEqual(await primary('e1'),['Try again']);assert.deepEqual(await primary('g1'),['Continue']);
  // Named questions: each its own line with quick picks; no Try again after a fruitless one; details collapsed and plain
  assert.deepEqual(await page.locator('#task-o1 .ask-q p').allTextContents(),['What did you notice when you tried the tool?','Which score fills the gap?']);
  assert.deepEqual(await page.locator('#task-o1 .pick').allTextContents(),['3.9','4.0']);
  assert.deepEqual(await primary('o1'),['Answer']);assert.equal(await page.locator('#task-o1 [data-choice="retry"]').count(),0);
  assert.equal(await page.locator('#task-o1 details.tech summary').textContent(),'Technical details');
  assert.equal(await page.locator('#task-o1 details.tech').getAttribute('open'),null);
  {const tech=await page.locator('#task-o1 details.tech pre').textContent();for(const gone of ['/home/','1CLX9Q28','9c3aed05'])assert.ok(!tech.includes(gone),gone)}
  assert.equal(await page.locator('#task-o1').innerText().then(t=>t.includes('needs your notes and one choice')),false);
  assert.deepEqual(await primary('r1'),['Answer']);assert.equal(await page.locator('#task-r1 [data-choice="retry"]').count(),0);
  assert.match(await line('r1'),/^Needs you: Your draft is preserved/);
  // A pick fills the answer box; another pick for the same question replaces it; the box takes the rest
  await page.locator('#task-o1 .pick[data-pick="4.0"]').click();
  assert.equal(await page.locator('#feedback-o1').inputValue(),'Which score fills the gap? 4.0');
  await page.locator('#task-o1 .pick[data-pick="3.9"]').click();
  assert.equal(await page.locator('#feedback-o1').inputValue(),'Which score fills the gap? 3.9');
  assert.equal(await page.locator('#task-o1 .pick[data-pick="3.9"]').getAttribute('aria-pressed'),'true');
  await page.locator('#task-o1 [data-choice="cancel-change"]').click();
  // The button row fits the card on a phone
  assert.ok(await page.locator('#task-q1 .actions').evaluate(a=>[...a.children].every(c=>c.getBoundingClientRect().right<=a.closest('article').getBoundingClientRect().right+1)));
  // Answer opens the answer box under the question
  await page.locator('#task-q1 [data-choice="answer"]').click();
  assert.equal(await page.locator('#task-q1 .answer-editor label').textContent(),'Your answer');
  assert.ok(await page.locator('#task-q1 .status-line').isVisible());
  assert.equal(await page.evaluate(()=>document.activeElement?.id),'feedback-q1');
  const send=page.locator('#task-q1 [data-choice="other"]');
  assert.equal(await send.textContent(),'Send answer');
  // Empty: asks for the answer, sends nothing
  await send.click();assert.match(await page.locator("#toast").textContent(),/Type your answer first/);assert.equal(posts.length,0);
  // Ctrl+Enter sends it the Change way
  await page.locator('#feedback-q1').fill('Use 31 Dec 2099 and say so in the notes.');await page.locator('#feedback-q1').press('Control+Enter');
  await page.waitForFunction(()=>/carry on/.test(document.querySelector('#toast')?.textContent||''));
  const sent=posts.find(p=>p.action==='decide');
  assert.equal(sent.args.suggestion_id,'q1');assert.equal(sent.args.choice,'other');assert.equal(sent.args.feedback,'Use 31 Dec 2099 and say so in the notes.');
  // The same box and path on the blocked, handed-back card
  {const box=await page.locator('#task-h1').boundingBox();await page.screenshot({path:`/tmp/answer-blocked-${width}.png`,fullPage:true,clip:box})}
  posts.length=0;await page.locator('#task-h1 [data-choice="answer"]').click();
  assert.equal(await page.locator('#task-h1 .answer-editor label').textContent(),'Your answer');
  await page.locator('#feedback-h1').fill('It is 500 euros, by bank transfer.');await page.locator('#task-h1 [data-choice="other"]').click();
  // The toast from the first answer may still show: wait for this answer's own post
  for(let i=0;i<100&&!posts.some(p=>p.action==='decide');i++)await page.waitForTimeout(50);
  assert.deepEqual([posts.find(p=>p.action==='decide')?.args.suggestion_id,posts.find(p=>p.action==='decide')?.args.choice],['h1','other']);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator("#task-q1").screenshot({path:`/tmp/answer-card-${width}.png`,timeout:5000}).catch(()=>{});
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('answer question dashboard ok');
