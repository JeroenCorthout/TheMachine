import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Hand to Claude Code / Hand to Codex on a card: shown only for the tools this Machine has; the brief opens to read and
// edit; the card follows the job (running, then a short result line with file chips and the rest folded away). The
// card's own Send stays, the one change box goes to the job and says so, and no local path ever shows. No network
// beyond this fake server.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const card={id:'c1',title:'Sum up the quarterly numbers',body:'Sam asks for a summary.',status:'prepared',event_ids:[1],scope:'work',created_at:new Date().toISOString(),
 next_actions:[{kind:'email',revision:'v1',label:'Send to Sam',to:['sam@client.example'],to_name:'Sam',subject:'Numbers',body:'Hi Sam'}]};
const state={suggestions:[card],connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],
 agent_tools:[{id:'claude',name:'Claude Code',place:'local'},{id:'codex',name:'Codex',place:'local'}]};
const at=new Date().toISOString(),job={id:'aj_'+'1'.repeat(16),tool:'claude',name:'Claude Code',place:'local',turns:1,created_at:at,updated_at:at,started_at:at,files:[],links:[],has_diff:false};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state));return}
 if(req.url==='/api/agent-jobs'){res.end(JSON.stringify({jobs:card.agent_job?{c1:card.agent_job}:{}}));return}
 if(req.url.startsWith('/api/agent-diff')){res.setHeader('Content-Type','text/plain');res.end('+Summary line');return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='agent_draft'){res.end(JSON.stringify({suggestion_id:'c1',brief:'Sum up the quarterly numbers\n\nLinks:\n- https://mail.google.com/mail/u/?authuser=sam#all/1a167\n\nDone looks like: a summary.',tools:state.agent_tools,
   line:'Picks up “Sum up the quarterly numbers” and reports back here. Sends nothing itself.',gets:[{kind:'mail',label:'5 emails'},{kind:'file',label:'messages.md'},{kind:'link',label:'1 link'}]}));return}
  if(call.action==='agent_send'){card.agent_job={...job,status:'running',progress:'Write: summary.md'};res.end(JSON.stringify(card.agent_job));return}
  if(call.action==='agent_follow_up'){card.agent_job={...card.agent_job,status:'running',turns:2};res.end(JSON.stringify(card.agent_job));return}
  res.end('{}');return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const page=await (await browser.newContext({viewport:{width:390,height:900}})).newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const c=page.locator('#task-c1');await c.waitFor();
 assert.equal(await c.locator('[data-agent-hand]').count(),2);
 const more=c.locator('.more-actions>summary');if(await more.count())await more.click();
 await c.locator('[data-agent-hand="claude"]').click();
 // A compact sheet: Hand to with a small switcher, one plain line, chips for what it gets (never a link), an empty note,
 // the whole job only behind See the full job (monospace, thin scrollbar)
 const form=c.locator('.agent-sheet');await form.waitFor();
 assert.equal(await form.locator('.agent-switch input:checked').getAttribute('value'),'claude');
 assert.deepEqual(await form.locator('.agent-switch span').allTextContents(),['Claude Code','Codex']);
 assert.equal(await form.locator('.agent-what').textContent(),'Picks up “Sum up the quarterly numbers” and reports back here. Sends nothing itself.');
 assert.deepEqual(await form.locator('.agent-get').allTextContents(),['5 emails','messages.md','1 link']);
 assert.equal(await form.locator('textarea.agent-note').inputValue(),'');
 assert.equal(await form.locator('textarea.agent-note').getAttribute('placeholder'),'Add a note for Claude Code');
 assert.equal(await form.locator('.agent-full').evaluate(d=>d.open),false);
 assert.doesNotMatch(await form.innerText(),/https?:|Done looks like|The job, as it will get it/);
 assert.ok(await page.evaluate(()=>document.activeElement?.classList.contains('agent-note')),'the note has the focus');
 await form.locator('.agent-switch span',{hasText:'Codex'}).click();
 assert.equal(await form.locator('textarea.agent-note').getAttribute('placeholder'),'Add a note for Codex');
 await form.locator('.agent-switch span',{hasText:'Claude Code'}).click();
 await form.locator('textarea.agent-note').fill('Keep it to one page');
 await form.locator('.agent-full summary').click();
 const full=form.locator('.agent-full textarea');assert.match(await full.inputValue(),/Done looks like/);
 assert.match(await full.evaluate(t=>getComputedStyle(t).fontFamily),/mono/i);
 await full.fill('Summarize the card into summary.md');
 assert.equal(await form.locator('textarea.agent-note').inputValue(),'Keep it to one page');
 await form.locator('button.primary',{hasText:'Hand off'}).click();
 await page.waitForTimeout(300);
 assert.deepEqual(calls.at(-1),{action:'agent_send',args:{suggestion_id:'c1',tool:'claude',brief:'Summarize the card into summary.md',note:'Keep it to one page'}});
 assert.match(await c.innerText(),/Claude Code is working on it/);
 assert.match(await c.innerText(),/Write: summary\.md/);
 assert.equal(await c.locator('[data-agent-hand]').count(),0);  // one job at a time
 assert.equal(await c.locator('[data-agent-cancel]').count(),1);
 // The result comes back: the card follows it without a full refresh.
 const long='Rewrote the reply in [draft.md](/home/owner/.local/share/agent-jobs/aj_1/work/draft.md) and added [summary.md](/home/owner/.local/share/agent-jobs/aj_1/work/summary.md).\n\n## Details\n- Kept the tone of `/tmp/x/notes.txt`\n- Checked the numbers';
 card.agent_job={...job,status:'result',summary:long,url:'codex://threads/01a16539-9d4a-7d83-9620-9203de5acb75',app:'Open in Codex',can_follow_up:true,draft_updated:true,
  files:[{path:'summary.md',size:20},{path:'draft.md',size:20}],has_diff:true,finished_at:new Date().toISOString()};
 await c.locator('.agent-result').waitFor({timeout:10000});
 const text=await c.locator('.agent-result').innerText();
 assert.doesNotMatch(text,/\/home\/|\/tmp\/|\]\(/);  // no local paths, no raw markdown
 assert.match(text,/Rewrote the reply in draft\.md and added summary\.md\./);
 assert.match(text,/Updated the draft below/);
 assert.equal(await c.locator('.agent-result a.agent-app').getAttribute('href'),'codex://threads/01a16539-9d4a-7d83-9620-9203de5acb75');
 assert.equal(await c.locator('.agent-result .agent-files').count(),0);  // both files are already in the line
 const chips=c.locator('.agent-result .agent-line a.agent-file');
 assert.deepEqual(await chips.allInnerTexts(),['draft.md','summary.md']);
 assert.equal(await chips.nth(1).getAttribute('href'),`api/agent-file?job=${job.id}&path=summary.md`);
 assert.equal(await c.locator('.agent-more').getAttribute('open'),null);  // the details are folded away
 await c.locator('.agent-more>summary').click();
 assert.match(await c.locator('.agent-more').innerText(),/Checked the numbers/);
 await c.locator('.agent-diff>summary').click();
 await page.waitForFunction(()=>document.querySelector('.agent-diff pre')?.textContent.includes('+Summary line'));
 // The card's own primary action stays; Change goes to the job and says so.
 const buttons=await c.locator('.actions>button').allInnerTexts();
 assert.ok(buttons.some(b=>/send to Sam/i.test(b)),buttons.join('|'));
 assert.ok(buttons.includes('Ask Claude Code for changes'),buttons.join('|'));
 assert.equal(buttons.filter(b=>/changes/i.test(b)).length,1);
 await c.locator('[data-agent-ask]').click();
 assert.match(await c.innerText(),/Goes to the Claude Code job/);
 await c.locator('#agent-ask-c1').fill('Shorter, please');
 await c.locator('[data-agent-ask-send]').click();
 await page.waitForTimeout(300);
 assert.deepEqual(calls.at(-1),{action:'agent_follow_up',args:{job_id:job.id,text:'Shorter, please'}});
 assert.ok((await c.locator('.actions>button').allInnerTexts()).some(b=>/send to Sam/i.test(b)));  // also while it works
 // Without the tools on this Machine, no Hand to buttons.
 delete card.agent_job;state.agent_tools=[];await page.evaluate(()=>refresh());
 assert.equal(await c.locator('[data-agent-hand]').count(),0);
 assert.deepEqual(errors,[]);
 console.log('agent jobs dashboard: ok');
}finally{await browser.close();server.close()}
