import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A handoff by email offers a task page (task_pages.py): nothing is made until the owner taps Add a task page, sees
// exactly what the page shows, may leave parts out and pick the days, and taps Add to email. Then the card shows the
// page's line with Stop sharing, and answers from the page show quoted, as text. No network beyond this fake server.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const BODY='Hi there,\n\nCould you take this on?\n\nSend the signed venue contract\nThe venue needs it back by Friday.\n\nJust reply to this email to let me know.\n\nBest,\nSam';
const email={kind:'email',label:'Send',revision:'e00000000001',account_id:'work',to:['alex@other.example'],subject:'Could you take this on: Send the signed venue contract',body:BODY};
const card={id:'c1',title:'Send the signed venue contract',body:'An email handing this to alex@other.example. Read it, change it if you like, and send it yourself.',
 status:'prepared',event_ids:[1],scope:'work',created_at:new Date().toISOString(),next_actions:[email],
 task_page:{state:'offered',days:14,choices:[7,14,30],repeats:false,preview:{title:'Send the signed venue contract',summary:'The venue needs it back by Friday.',due:'2026-10-31',from:'Sam'}}};
const state={suggestions:[card],connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='task_page_share'){email.body=BODY.replace('\n\nBest,','\n\nStatus and replies: https://app.example.test/t/x#k=y\n\nBest,');email.revision='e00000000002';
   card.task_page={state:'shared',until:'2026-10-30',answers:[]}}
  if(call.action==='task_page_stop')card.task_page={state:'stopped',until:'',answers:card.task_page.answers};
  res.end(JSON.stringify({state:'ok'}));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const page=await (await browser.newContext({viewport:{width:390,height:900}})).newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const c=page.locator('#task-c1');await c.waitFor();
 // Offered, off until tapped: the draft and its Send stay as they are
 assert.match(await c.locator('.tp-suggest').innerText(),/Add a task page\?/);
 assert.equal(await c.locator('.tp-approve').count(),0);
 assert.ok(await c.locator('.actions>button.primary').count());
 await c.locator('[data-tp-add]').click();
 // The approve step: exactly what the page shows, the never-shared line, the days, Add to email / Cancel
 const a=c.locator('.tp-approve');await a.waitFor();
 assert.match(await a.locator('.tp-mini').innerText(),/Sam shared a task with you[\s\S]*Send the signed venue contract[\s\S]*Due Sat 31 Oct[\s\S]*back by Friday/);
 assert.match(await a.innerText(),/Never shared: the email thread, your notes or other cards\./);
 assert.equal(await a.locator('[role=radio][aria-checked=true]').innerText(),'14');
 assert.equal(await c.locator('[data-choice="send"],.actions>button.primary:not([data-tp-share])').count(),0);  // no Send while approving
 await a.locator('.tp-part:has-text("Due") button').click();
 assert.equal(await c.locator('.tp-part.off').count(),1);
 assert.equal(await c.locator('.tp-part.off button').innerText(),'Put back');
 await c.locator('[data-days="7"]').click();
 await c.locator('[data-tp-share]').click();
 await c.locator('.tp-shared').waitFor();
 assert.deepEqual(calls.at(-1),{action:'task_page_share',args:{suggestion_id:'c1',leave_out:['due'],days:7}});
 assert.match(await c.locator('.tp-shared').innerText(),/^Task page until Fri 30 Oct\s*·\s*Stop sharing$/);  // one quiet row, no Mark done of its own
 assert.equal(await c.locator('[data-tp-done]').count(),0);
 assert.match(await c.locator('.draft').first().innerText(),/Status and replies: https:\/\/app\.example\.test\/t\//);
 // An answer from the page: quoted, as text, never markup
 card.task_page.answers=[{answer:'on',note:'<img src=x onerror="window.pwned=1">Signing tomorrow.',at:'2026-10-24'}];
 await page.evaluate(()=>refresh());
 const reply=c.locator('.tp-reply');await reply.waitFor();
 assert.match(await reply.innerText(),/Reply on the task page: I’m on it[\s\S]*<img src=x[\s\S]*Signing tomorrow\./);
 assert.equal(await reply.locator('img').count(),0);
 assert.equal(await page.evaluate(()=>window.pwned),undefined);
 // Sent and Done: the card leads with where it stands, the email folds into one line, the row keeps Stop sharing
 email.state={status:'sent',sent_at:'2026-10-23T09:10:00+00:00'};card.task_page.lead='alex@other.example is on it: “Signing tomorrow.”';
 Object.assign(card,{status:'completed'});state.suggestion_counts={completed:1};
 await page.evaluate(()=>refresh());
 for(const d of await page.locator('details:has(#task-c1)').all())await d.evaluate(x=>x.open=true);
 assert.equal(await c.locator('.tp-lead').innerText(),'alex@other.example is on it: “Signing tomorrow.”');
 assert.doesNotMatch(await c.innerText(),/Read it, change it|Email sent/);
 const sent=c.locator('details.tp-sent');
 assert.match(await sent.locator('summary').innerText(),/^Sent to alex@other\.example · Fri 23 Oct\s*›$/);
 assert.equal(await sent.evaluate(x=>x.open),false);
 assert.equal(await c.locator('.tp-reply').count(),0);  // the latest answer leads the card, not repeated below
 await sent.locator('summary').click();
 assert.match(await sent.innerText(),/Could you take this on\?/);
 assert.match(await c.locator('.tp-shared').innerText(),/^Task page until Fri 30 Oct\s*·\s*Stop sharing$/);
 // Back in Ready after Can't do this: same calm card, with its Ready buttons
 card.task_page.answers.push({answer:'cant',note:'Out until Monday.',at:'2026-10-25'});card.task_page.lead='alex@other.example can’t do this: “Out until Monday.”';
 Object.assign(card,{status:'prepared'});state.suggestion_counts={prepared:1};
 await page.evaluate(()=>refresh());
 assert.match(await c.locator('.tp-lead').innerText(),/can’t do this: “Out until Monday\.”/);
 assert.equal(await c.locator('.draft:visible').count(),0);
 assert.equal(await c.locator('.tp-reply').count(),1);  // the earlier answer stays below
 assert.equal(await c.locator('[data-tp-done]').count(),0);
 await c.locator('[data-tp-stop]').click();
 await page.waitForTimeout(300);
 assert.deepEqual(calls.at(-1),{action:'task_page_stop',args:{suggestion_id:'c1'}});
 assert.match(await c.locator('.tp-shared').innerText(),/Task page stopped\. The link no longer works\./);
 assert.deepEqual(errors,[]);
 console.log('task pages dashboard: ok');
}finally{await browser.close();server.close()}
