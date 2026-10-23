import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Card chat: the card's title, its Chat › link or its "You asked" note opens #chat=<id>: a header with back, the title
// and a ⋯ menu, the conversation from the service's card_chat strictly by time (the source first, the card itself as
// the first message with its question and choices, the owner's own taps as quiet lines on the right with repeats
// folded, the steps before the reply, drafts inline with what changed, a coding agent's answers with their files),
// polled once a second while the run goes, one compact bottom (a status line with its one way back, at most two
// suggestions, a slim message box), and the task list beside it (a drawer on a phone). A message is a Change: decide
// with choice other, a chat- decision id and the CSRF token; on a done card Reopen goes first.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),t0=Date.now()-60000;
const cards=[
 {id:'c1c1c1c1c1c1c1c1',title:'Reply to the venue',body:'They asked for a date.',status:'prepared',scope:'work',created_at:now,event_ids:[1],
  latest_feedback:{choice:'other',feedback:'Make it shorter',created_at:now}},
 {id:'c2c2c2c2c2c2c2c2',title:'Renew the passport',body:'It runs out in May.',status:'pending',scope:'personal',created_at:now,event_ids:[2]},
 {id:'c3c3c3c3c3c3c3c3',title:'Old finished task',body:'Done.',status:'completed',scope:'work',created_at:now,event_ids:[3]},
 {id:'c4c4c4c4c4c4c4c4',title:'Pay the deposit',body:'Due Friday.',status:'snoozed',snoozed_until:new Date(Date.now()+86400000).toISOString(),scope:'personal',created_at:now,event_ids:[4]},
 {id:'c5c5c5c5c5c5c5c5',title:'Alex needs your call on the venue',body:'Alex found two dates. I can check both and draft a reply for Alex.',status:'pending',scope:'work',created_at:now,event_ids:[5]}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[{suggestion_id:'c3c3c3c3c3c3c3c3',choice:'complete',created_at:now}],analyses:[],deliveries:[],
 review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const d0=t0-86400000*2,mail=b=>({kind:'email',to:['Sam'],subject:'Re: Dates',body:b,reply:true});
const history={session:true,status:'ok',active:false,cursor:'k1',items:[
 {kind:'source',from:'Sam Lee',via:'email',title:'Dates',text:'Can we do **Tuesday** instead?',mine:false,at:d0},
 ...[1,2,3].map(n=>({kind:'event',text:'Snoozed',mine:true,at:d0+n*1000})),
 {kind:'event',text:'Do it',mine:true,at:t0+500},
 {kind:'steps',steps:['Reading the email','Writing the draft'],at:t0+1000},
 {kind:'reply',text:'Here is a **draft** <b>not bold</b>:\n\n- one\n- two',drafts:[mail('Hi Sam, Monday works.')],noted:true,at:t0+2000},
 {kind:'you',text:'Make it shorter',at:t0+3000},
 {kind:'reply',text:'Changed Monday to Tuesday.',drafts:[mail('Hi Sam, Tuesday works.')],at:t0+4000},
 {kind:'event',text:'Didn’t go out to Sam',mine:false,at:t0+5000},
 {kind:'you',text:'Check the venue site',to:'Codex',via:'agent',at:t0+6000},
 {kind:'agent',id:'aj_1',name:'Codex',text:'Both dates are free.',full:'Both dates are free. I wrote summary.md.',files:['summary.md'],nothing_sent:true,at:t0+7000}]};
const actions=[];let live=null;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);actions.push({...r,csrf:req.headers['x-csrf-token']});
  if(r.action==='card_chat'){
   if(['c2c2c2c2c2c2c2c2','c5c5c5c5c5c5c5c5'].includes(r.args.suggestion_id)){res.end(JSON.stringify({session:false,active:false,status:null,items:[],cursor:null}));return}
   if(live){const step=live.shift();res.end(JSON.stringify(r.args.cursor?{session:true,active:true,status:'running',cursor:'k2',unchanged:true,live:{text:step}}:{...history,active:true,status:'running',live:{text:step}}));if(!live.length)live=null;return}
   res.end(JSON.stringify(history));return}
  if(r.action==='decide'){live=['Sure, a shorter one','Sure, a shorter one: Hi, Tuesday works.'];res.end(JSON.stringify({status:'instructions'}));return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1280,height:900},locale:'en-US',timezoneId:'Europe/Brussels'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-c1c1c1c1c1c1c1c1').waitFor();
 const shot=async n=>{if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/${n}.png`})};
 // The title opens the chat; so does a small, optional Chat link on the card's top row
 assert.equal(await page.locator('#task-c1c1c1c1c1c1c1c1 .meta a.card-chat-link[href="#chat=c1c1c1c1c1c1c1c1"]').count(),1);
 await page.locator('#task-c1c1c1c1c1c1c1c1 h3 a').click();
 await page.locator('.chat-layout .chat-head').waitFor();
 assert.equal(new URL(page.url()).hash,'#chat=c1c1c1c1c1c1c1c1');
 // The header: back, the title and a ⋯ menu with the card's other actions
 assert.equal(await page.locator('#suggestions article.card').count(),0);
 assert.equal(await page.locator('.chat-head>*').count(),3);
 assert.equal(await page.locator('.chat-head h2.chat-title').textContent(),'Reply to the venue');
 assert.ok(!(await page.locator('#suggestion-stats').isVisible()));
 assert.equal(await page.locator('.chat-menu-list [data-choice="snooze"]').count(),1);
 assert.equal(await page.locator('.chat-menu-list [data-choice="change"]').count(),0);
 // The task list beside it, with this task marked
 assert.equal(await page.locator('.chat-side a[aria-current="page"]').textContent(),'Reply to the venue');
 assert.ok((await page.locator('.chat-side a').allTextContents()).includes('Old finished task'));
 // The conversation, by time: who wrote what first, day lines, 24 h times in Belgium
 await page.locator('#chat-log .chat-msg.machine .bubble ul').waitFor();
 assert.equal(await page.locator('#chat-log>*').first().getAttribute('class'),'chat-day');
 assert.match(await page.locator('#chat-log .chat-source .chat-from').textContent(),/^Sam Lee · Email/);
 assert.equal(await page.locator('#chat-log .chat-source .bubble strong').textContent(),'Tuesday');
 assert.ok((await page.locator('#chat-log .chat-day').count())>=2);
 for(const t of await page.locator('#chat-log .chat-time').allTextContents())assert.match(t,/^\d\d:\d\d$/);
 // The owner's own taps are quiet lines on the right, the same one in a row folded with a count; others' in the middle; their time shows on hover
 assert.match(await page.locator('#chat-log .chat-event.mine').first().textContent(),/^You snoozed it 3 times\d\d:\d\d/);
 assert.ok((await page.locator('#chat-log .chat-event.mine').allTextContents()).some(t=>/^You chose Do it\d\d:\d\d$/.test(t)));
 await page.locator('#chat-log .chat-event.mine summary').click();
 assert.equal(await page.locator('#chat-log .chat-event.mine li').count(),3);
 assert.match(await page.locator('#chat-log .chat-event:not(.mine)').textContent(),/^Didn’t go out to Sam\d\d:\d\d$/);
 // The steps come before the reply they led to, folded into one quiet line
 const order=await page.locator('#chat-log>*').evaluateAll(ns=>ns.map(n=>n.classList.contains('chat-work')?'steps':n.matches('.chat-msg.machine')?'reply':n.matches('.chat-msg.you')?'you':''));
 assert.ok(order.indexOf('steps')>=0&&order.indexOf('steps')<order.indexOf('reply'));
 assert.equal(await page.locator('#chat-log .chat-steps summary').textContent(),'Checked 2 things');
 // Markdown (no HTML gets in), "Noted for next time" under the reply, the owner's note on the right
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble strong').first().textContent(),'draft');
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble b').count(),0);
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble li').count(),2);
 assert.ok((await page.locator('#chat-log .chat-noted').allTextContents()).includes('Noted for next time'));
 assert.match(await page.locator('#chat-log .chat-msg.you .bubble').first().textContent(),/^Make it shorter/);
 // Drafts inline after the reply that made them: the earlier one marked, the newest with what changed
 assert.equal(await page.locator('#chat-log .chat-draft').count(),2);
 assert.match(await page.locator('#chat-log .chat-draft').first().textContent(),/Reply to Sam.*Earlier draft/);
 assert.ok(await page.locator('#chat-log .chat-draft').last().evaluate(n=>n.classList.contains('current')));
 await page.locator('#chat-log .chat-draft button',{hasText:'Show what changed'}).click();
 assert.equal(await page.locator('#chat-log .chat-diff ins').textContent(),'Tuesday');
 assert.equal(await page.locator('#chat-log .chat-diff del').textContent(),'Monday');
 // The card's choices on the Machine's latest message, the accent only on the main one
 assert.equal(await page.locator('#chat-log .chat-options button.primary').count(),1);
 // A message to a coding agent says to whom; its answer leads with what it found, its files as chips
 assert.equal(await page.locator('#chat-log .chat-msg.you .chat-to').textContent(),'to Codex');
 // An agent's answer is plain text like any reply (never a raised card), its name small above it
 const agent=page.locator('#chat-log .chat-msg.machine',{hasText:'Both dates are free.'});
 assert.equal(await agent.locator('.chat-agent-name').textContent(),'Codex');
 assert.equal(await page.locator('#chat-log .chat-result',{hasText:'Both dates are free.'}).count(),0);
 // The open draft is the one raised result; the earlier one steps back
 assert.equal(await page.locator('#chat-log .chat-result.current').count(),1);
 assert.ok(await page.locator('#chat-log .chat-result.current .chat-draft.current').count());
 assert.equal(await agent.locator('.chat-files a.agent-file').textContent(),'summary.md');
 assert.equal(await agent.locator('.chat-files .chat-noted').textContent(),'Codex sent nothing');
 // A shortened answer has More, which shows the whole answer
 await agent.locator('.chat-more').click();
 await page.locator('#chat-log .chat-msg.machine',{hasText:'I wrote summary.md.'}).waitFor();
 assert.equal(await page.locator('#chat-log .chat-msg.machine',{hasText:'I wrote summary.md.'}).locator('.chat-more').textContent(),'Less');
 // At most two suggestions from the card; one fills the message box
 assert.ok(await page.locator('#chat-picks').isHidden());  // after a message of yours, only while you write
 await page.locator('#chat-input').focus();
 const says=await page.locator('#chat-picks [data-chat-say]').allTextContents();
 assert.deepEqual(says,['Make it shorter','What did Sam ask?']);
 await page.locator('#chat-picks [data-chat-say]',{hasText:'What did Sam ask?'}).click();
 assert.equal(await page.locator('#chat-input').inputValue(),'What did Sam ask?');
 const reads=actions.filter(a=>a.action==='card_chat');
 assert.deepEqual(reads[0].args,{suggestion_id:'c1c1c1c1c1c1c1c1'});assert.equal(reads[0].csrf,'__CSRF__');
 // An opened step list stays open through a poll
 await page.locator('#chat-log .chat-steps summary').click();
 await page.waitForTimeout(1200);
 assert.ok(await page.locator('#chat-log .chat-steps').last().evaluate(d=>d.open));
 // The composer: one line, "Ask or change something", a send icon
 assert.equal(await page.locator('#chat-input').getAttribute('placeholder'),'Ask or change something');
 assert.equal(await page.locator('#chat-input').getAttribute('rows'),'1');
 // On a computer the page stays put: the list and the thread scroll on their own, and a scroll past the end stays inside
 const box=await page.evaluate(()=>{const l=getComputedStyle(document.querySelector('#chat-log')),d=getComputedStyle(document.querySelector('.chat-side'));
  return {page:document.documentElement.scrollHeight<=innerHeight+1,log:l.overflowY,logOver:l.overscrollBehaviorY,side:d.overflowY,sideOver:d.overscrollBehaviorY}});
 assert.deepEqual(box,{page:true,log:'auto',logOver:'contain',side:'auto',sideOver:'contain'});
 await shot('desktop');
 // Send (Enter with a keyboard): the Change path, then the reply grows while the run goes (polled with the cursor)
 await page.locator('#chat-input').fill('Shorter please, and say Tuesday');
 await page.locator('#chat-input').press('Enter');
 await page.locator('#chat-log .chat-msg.chat-live .bubble',{hasText:'Tuesday works'}).waitFor({timeout:8000});
 const decide=actions.find(a=>a.action==='decide');
 assert.equal(decide.args.choice,'other');assert.equal(decide.args.feedback,'Shorter please, and say Tuesday');
 assert.match(decide.args.decision_id,/^chat-[0-9a-f-]{36}$/);assert.equal(decide.csrf,'__CSRF__');
 assert.equal(await page.locator('#chat-input').inputValue(),'');
 assert.ok(actions.some(a=>a.action==='card_chat'&&a.args.cursor),'polls with the cursor');
 // A pending task with no session yet: the card itself is the first message, with its choices
 await page.locator('.chat-side a',{hasText:'Renew the passport'}).click();
 await page.locator('h2.chat-title',{hasText:'Renew the passport'}).waitFor();
 await page.locator('#chat-log .chat-card').waitFor();
 assert.match(await page.locator('#chat-log .chat-card .bubble').textContent(),/It runs out in May/);
 assert.ok((await page.locator('#chat-log .chat-card .chat-options button').count())>=2);
 // A card that waits on a call ends with it in one line; its offer becomes a suggestion
 await page.locator('.chat-side a',{hasText:'Alex needs your call'}).click();
 await page.locator('h2.chat-title',{hasText:'Alex needs your call'}).waitFor();
 await page.locator('#chat-log .chat-card').waitFor();
 // The call it needs is the card's result: the raised card under its message
 assert.equal(await page.locator('#chat-log .chat-result.current .chat-q').textContent(),'Your call: the venue?');
 assert.doesNotMatch(await page.locator('#chat-log .chat-card .bubble').textContent(),/I can check/);
 assert.equal(await page.locator('#chat-picks [data-chat-say]').first().textContent(),'Draft an answer');
 // A snoozed task: the status line with Unsnooze as a text button; a message still goes (it brings the task back)
 await page.locator('.chat-side a',{hasText:'Pay the deposit'}).click();
 await page.locator('h2.chat-title',{hasText:'Pay the deposit'}).waitFor();
 assert.match(await page.locator('.chat-status').textContent(),/^Snoozed until .+·Unsnooze$/);
 assert.equal(await page.locator('.chat-status button.chat-inline[data-choice="accept"]').textContent(),'Unsnooze');
 assert.equal(await page.locator('#chat-log .chat-options').count(),0);
 assert.equal(await page.locator('#chat-input').count(),1);
 // A done task keeps the message box: a message reopens it first, then goes as a Change
 await page.locator('.chat-side a',{hasText:'Old finished task'}).click();
 await page.locator('h2.chat-title',{hasText:'Old finished task'}).waitFor();
 assert.match(await page.locator('.chat-status').textContent(),/Reopen/);
 const before=actions.length;
 await page.locator('#chat-input').fill('One more thing');
 await page.locator('[data-chat-send]').click();
 await page.waitForTimeout(800);
 const sent=actions.slice(before).filter(a=>a.action==='decide').map(a=>a.args.choice);
 assert.deepEqual(sent,['reopen','other']);
 // Back leaves the chat and shows the card in its list
 await page.locator('.chat-back').click();
 await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});
 assert.ok(await page.locator('#task-c3c3c3c3c3c3c3c3').count()===1);
 assert.ok(await page.locator('#suggestion-stats').isVisible());
 // The "You asked" note opens the chat too
 // (a tap right after a scroll is the one that stops a fling, never an open: scroll first, then tap)
 await page.locator('#task-c1c1c1c1c1c1c1c1 .saved-feedback').scrollIntoViewIfNeeded();await page.waitForTimeout(400);
 await page.locator('#task-c1c1c1c1c1c1c1c1 .saved-feedback').click();
 await page.locator('h2.chat-title',{hasText:'Reply to the venue'}).waitFor();
 await page.evaluate(()=>{location.hash=''});await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});
 // A Change sent from the card stays on the card: the chat never opens by itself
 await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="change"]').click().catch(async()=>{
  await page.locator('#task-c2c2c2c2c2c2c2c2 .more-actions summary').click();await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="change"]').click()});
 await page.locator('#feedback-c2c2c2c2c2c2c2c2').fill('Use the new address');
 await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="other"]').click();
 await page.locator('#feedback-c2c2c2c2c2c2c2c2').waitFor({state:'detached'});
 assert.equal(actions.filter(a=>a.action==='decide').at(-1).args.feedback,'Use the new address');
 assert.notEqual(new URL(page.url()).hash,'#chat=c2c2c2c2c2c2c2c2');assert.equal(await page.locator('#suggestions .chat-layout').count(),0);
 // On a phone: the page header steps aside, the task list is a drawer under ⋯
 await page.setViewportSize({width:390,height:800});
 await page.evaluate(()=>{location.hash='#chat=c1c1c1c1c1c1c1c1'});
 await page.locator('h2.chat-title',{hasText:'Reply to the venue'}).waitFor();await page.waitForTimeout(1500);
 assert.ok(!(await page.locator('main>header').isVisible()));
 assert.ok(!(await page.locator('.chat-side').evaluate(n=>n.getBoundingClientRect().right>0)));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'nothing wider than the phone');
 await page.evaluate(()=>scrollTo(0,0));await shot('phone-top');
 await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await shot('phone-bottom');
 // The last message clears the bottom, and nothing but the safe area is left under the message box
 const fit=await page.evaluate(()=>{const l=[...document.querySelectorAll('#chat-log>*')].at(-1).getBoundingClientRect().bottom,c=document.querySelector('.chat-compose').getBoundingClientRect();return {clear:l<=c.top,under:document.documentElement.scrollHeight-scrollY-c.bottom}});
 assert.ok(fit.clear,'the last message is not under the message box');
 assert.ok(fit.under<=1,'no space under the message box: '+fit.under);
 await page.locator('.chat-menu summary').click();
 await page.locator('.chat-menu-list [data-chat-drawer]').click();
 await page.waitForFunction(()=>document.querySelector('.chat-side').getBoundingClientRect().left>=0);
 await page.locator('.chat-side a',{hasText:'Renew the passport'}).click();
 await page.locator('h2.chat-title',{hasText:'Renew the passport'}).waitFor();
 assert.ok(!(await page.locator('.chat-layout').evaluate(n=>n.classList.contains('drawer'))));
 await context.close();
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('card chat dashboard ok');
