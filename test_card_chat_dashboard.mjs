import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Card chat: the card's title, its "You asked" note or a sent Change opens #chat=<id>: a header with only back and
// the title, the conversation from the service's card_chat strictly by time (the source message first, the owner's
// own taps on the right and repeats folded, the steps before the reply, drafts inline with what changed), polled once
// a second while the run goes, an action bar (status, the lead button, More) and suggestions above the message box,
// and the task list beside it (a drawer on a phone). A message is a Change: decide with choice other, a chat-
// decision id and the CSRF token.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),t0=Date.now()-60000;
const cards=[
 {id:'c1c1c1c1c1c1c1c1',title:'Reply to the venue',body:'They asked for a date.',status:'prepared',scope:'work',created_at:now,event_ids:[1],
  latest_feedback:{choice:'other',feedback:'Make it shorter',created_at:now}},
 {id:'c2c2c2c2c2c2c2c2',title:'Renew the passport',body:'It runs out in May.',status:'pending',scope:'personal',created_at:now,event_ids:[2]},
 {id:'c3c3c3c3c3c3c3c3',title:'Old finished task',body:'Done.',status:'completed',scope:'work',created_at:now,event_ids:[3]},
 {id:'c4c4c4c4c4c4c4c4',title:'Pay the deposit',body:'Due Friday.',status:'snoozed',snoozed_until:new Date(Date.now()+86400000).toISOString(),scope:'personal',created_at:now,event_ids:[4]}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[{suggestion_id:'c3c3c3c3c3c3c3c3',choice:'complete',created_at:now}],analyses:[],deliveries:[],
 review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const d0=t0-86400000*2,mail=b=>({kind:'email',to:['Sam'],subject:'Re: Dates',body:b,reply:true});
const history={session:true,status:'ok',active:false,cursor:'k1',items:[
 {kind:'source',from:'Sam Lee',via:'email',title:'Dates',text:'Can we do **Tuesday** instead?',mine:false,at:d0},
 ...[1,2,3].map(n=>({kind:'event',text:'You snoozed it',mine:true,at:d0+n*1000})),
 {kind:'event',text:'You asked me to do it',mine:true,at:t0+500},
 {kind:'steps',steps:['Reading the email','Writing the draft'],at:t0+1000},
 {kind:'reply',text:'Here is a **draft** <b>not bold</b>:\n\n- one\n- two',drafts:[mail('Hi Sam, Monday works.')],noted:true,at:t0+2000},
 {kind:'you',text:'Make it shorter',at:t0+3000},
 {kind:'reply',text:'Changed Monday to Tuesday.',drafts:[mail('Hi Sam, Tuesday works.')],at:t0+4000},
 {kind:'event',text:'The reply to Sam didn’t go out',mine:false,at:t0+5000}]};
const actions=[];let live=null;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);actions.push({...r,csrf:req.headers['x-csrf-token']});
  if(r.action==='card_chat'){
   if(r.args.suggestion_id==='c2c2c2c2c2c2c2c2'){res.end(JSON.stringify({session:false,active:false,status:null,items:[],cursor:null}));return}
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
 // No Chat link on the card: its title opens the chat
 assert.equal(await page.locator('#task-c1c1c1c1c1c1c1c1 .chat-link').count(),0);
 await page.locator('#task-c1c1c1c1c1c1c1c1 h3 a').click();
 await page.locator('.chat-layout .chat-head').waitFor();
 assert.equal(new URL(page.url()).hash,'#chat=c1c1c1c1c1c1c1c1');
 // The header is back and the title, nothing else; the actions sit in a bar above the message box
 assert.equal(await page.locator('#suggestions article.card').count(),0);
 assert.equal(await page.locator('.chat-head>*').count(),2);
 assert.equal(await page.locator('.chat-head h2.chat-title').textContent(),'Reply to the venue');
 assert.ok(!(await page.locator('#suggestion-stats').isVisible()));
 assert.equal(await page.locator('.chat-bar [data-choice="change"]').count(),0);
 assert.equal(await page.locator('.chat-bar>button.primary[data-choice="complete"]').count(),1);
 assert.equal(await page.locator('.chat-bar .more-menu [data-choice="snooze"]').count(),1);
 // The task list beside it, with this task marked
 assert.equal(await page.locator('.chat-side a[aria-current="page"]').textContent(),'Reply to the venue');
 assert.deepEqual(await page.locator('.chat-side a').allTextContents(),['Reply to the venue','Renew the passport','Pay the deposit','Old finished task']);
 // The conversation, by time: who wrote what first, day lines, 24 h times in Belgium
 await page.locator('#chat-log .chat-msg.machine .bubble ul').waitFor();
 assert.equal(await page.locator('#chat-log .chat-intro').count(),0);
 assert.equal(await page.locator('#chat-log>*').first().getAttribute('class'),'chat-day');
 assert.match(await page.locator('#chat-log .chat-source .chat-from').textContent(),/^Sam Lee · email/);
 assert.equal(await page.locator('#chat-log .chat-source .bubble strong').textContent(),'Tuesday');
 assert.ok((await page.locator('#chat-log .chat-day').count())>=2);
 for(const t of await page.locator('#chat-log .chat-time').allTextContents())assert.match(t,/^\d\d:\d\d$/);
 // The owner's own taps sit on the right, the same one in a row is one chip; what went wrong stays in the middle
 assert.equal(await page.locator('#chat-log .chat-chip',{hasText:'You snoozed it'}).count(),1);
 assert.match(await page.locator('#chat-log .chat-chip.mine').first().textContent(),/^You snoozed it 3 times · last on .+ \d\d:\d\d$/);
 await page.locator('#chat-log .chat-chip.mine summary').click();
 assert.equal(await page.locator('#chat-log .chat-chip.mine li').count(),3);
 assert.equal(await page.locator('#chat-log .chat-chip.mine').count(),2);
 assert.equal(await page.locator('#chat-log .chat-chip:not(.mine)').textContent().then(t=>t.startsWith('The reply to Sam didn’t go out')),true);
 // The steps come before the reply they led to, folded into one quiet line
 const order=await page.locator('#chat-log>*').evaluateAll(ns=>ns.map(n=>n.classList.contains('chat-work')?'steps':n.matches('.chat-msg.machine')?'reply':n.matches('.chat-msg.you')?'you':''));
 assert.ok(order.indexOf('steps')>=0&&order.indexOf('steps')<order.indexOf('reply'));
 assert.equal(await page.locator('#chat-log .chat-steps summary').textContent(),'Checked 2 things');
 // Markdown (no HTML gets in), "Noted for next time" under the reply, the owner's note on the right
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble strong').first().textContent(),'draft');
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble b').count(),0);
 assert.equal(await page.locator('#chat-log .chat-msg.machine .bubble li').count(),2);
 assert.equal(await page.locator('#chat-log .chat-noted').allTextContents().then(t=>t.join('|')),'Noted for next time');
 assert.match(await page.locator('#chat-log .chat-msg.you .bubble').textContent(),/^Make it shorter/);
 // Drafts inline after the reply that made them: the earlier one marked, the newest with what changed
 assert.equal(await page.locator('#chat-log .chat-draft').count(),2);
 assert.match(await page.locator('#chat-log .chat-draft').first().textContent(),/Reply to Sam.*Earlier draft/);
 assert.ok(await page.locator('#chat-log .chat-draft').last().evaluate(n=>n.classList.contains('current')));
 await page.locator('#chat-log .chat-draft button',{hasText:'Show what changed'}).click();
 assert.equal(await page.locator('#chat-log .chat-diff ins').textContent(),'Tuesday');
 assert.equal(await page.locator('#chat-log .chat-diff del').textContent(),'Monday');
 // Suggestions from the card fill the message box
 const says=await page.locator('#chat-picks [data-chat-say]').allTextContents();
 assert.ok(says.includes('Make it shorter')&&says.includes('What did Sam say before?'),says.join('|'));
 await page.locator('#chat-picks [data-chat-say]',{hasText:'What did Sam say before?'}).click();
 assert.equal(await page.locator('#chat-input').inputValue(),'What did Sam say before?');
 const reads=actions.filter(a=>a.action==='card_chat');
 assert.deepEqual(reads[0].args,{suggestion_id:'c1c1c1c1c1c1c1c1'});assert.equal(reads[0].csrf,'__CSRF__');
 // An opened step list stays open through a poll
 await page.locator('#chat-log .chat-steps summary').click();
 await page.waitForTimeout(1200);
 assert.ok(await page.locator('#chat-log .chat-steps').last().evaluate(d=>d.open));
 // The composer: one line, "Message", a send icon, no keyboard hint
 assert.equal(await page.locator('#chat-input').getAttribute('placeholder'),'Message');
 assert.equal(await page.locator('#chat-input').getAttribute('rows'),'1');
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
 // Another task from the list; a pending one has no session yet
 await page.locator('.chat-side a',{hasText:'Renew the passport'}).click();
 await page.locator('h2.chat-title',{hasText:'Renew the passport'}).waitFor();
 // A chat with nothing yet opens with the card itself
 await page.locator('#chat-log .chat-intro').waitFor();
 assert.match(await page.locator('#chat-log .chat-intro').textContent(),/It runs out in May/);
 // A snoozed task: Unsnooze is the main action, and a message still goes (it brings the task back)
 await page.locator('.chat-side a',{hasText:'Pay the deposit'}).click();
 await page.locator('h2.chat-title',{hasText:'Pay the deposit'}).waitFor();
 assert.equal(await page.locator('.chat-bar>button.primary[data-choice="accept"]').textContent(),'Unsnooze');
 assert.match(await page.locator('.chat-status').textContent(),/^Snoozed until /);
 assert.equal(await page.locator('#chat-input').count(),1);
 // A closed task: read only
 await page.locator('.chat-side a',{hasText:'Old finished task'}).click();
 await page.locator('h2.chat-title',{hasText:'Old finished task'}).waitFor();
 assert.equal(await page.locator('#chat-input').count(),0);
 assert.match(await page.locator('.chat-compose').textContent(),/closed/);
 // Back leaves the chat and shows the card in its list
 await page.locator('.chat-back').click();
 await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});
 assert.ok(await page.locator('#task-c3c3c3c3c3c3c3c3').count()===1);
 assert.ok(await page.locator('#suggestion-stats').isVisible());
 // The "You asked" note opens the chat too
 await page.locator('#task-c1c1c1c1c1c1c1c1 .saved-feedback').click();
 await page.locator('h2.chat-title',{hasText:'Reply to the venue'}).waitFor();
 await page.evaluate(()=>{location.hash=''});await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});
 // A Change sent from the card carries on in its chat
 await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="change"]').click().catch(async()=>{
  await page.locator('#task-c2c2c2c2c2c2c2c2 .more-actions summary').click();await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="change"]').click()});
 await page.locator('#feedback-c2c2c2c2c2c2c2c2').fill('Use the new address');
 await page.locator('#task-c2c2c2c2c2c2c2c2 [data-choice="other"]').click();
 await page.locator('h2.chat-title',{hasText:'Renew the passport'}).waitFor();
 assert.equal(new URL(page.url()).hash,'#chat=c2c2c2c2c2c2c2c2');
 assert.equal(actions.filter(a=>a.action==='decide').at(-1).args.feedback,'Use the new address');
 // On a phone: the page header steps aside, the task list is a drawer under More
 await page.setViewportSize({width:390,height:800});
 await page.evaluate(()=>{location.hash='#chat=c1c1c1c1c1c1c1c1'});
 await page.locator('h2.chat-title',{hasText:'Reply to the venue'}).waitFor();await page.waitForTimeout(1500);
 assert.ok(!(await page.locator('main>header').isVisible()));
 assert.ok(!(await page.locator('.chat-side').evaluate(n=>n.getBoundingClientRect().right>0)));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'nothing wider than the phone');
 await page.evaluate(()=>scrollTo(0,0));await shot('phone-top');
 await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await shot('phone-bottom');
 // The last message clears the bar
 const clear=await page.evaluate(()=>{const l=[...document.querySelectorAll('#chat-log>*')].at(-1).getBoundingClientRect().bottom,c=document.querySelector('.chat-compose').getBoundingClientRect().top;return l<=c});
 assert.ok(clear,'the last message is not under the action bar');
 await page.locator('.chat-bar .more-actions summary').click();
 await page.locator('.chat-bar [data-chat-drawer]').click();
 await page.waitForFunction(()=>document.querySelector('.chat-side').getBoundingClientRect().left>=0);
 await page.locator('.chat-side a',{hasText:'Renew the passport'}).click();
 await page.locator('h2.chat-title',{hasText:'Renew the passport'}).waitFor();
 assert.ok(!(await page.locator('.chat-layout').evaluate(n=>n.classList.contains('drawer'))));
 await context.close();
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('card chat dashboard ok');
