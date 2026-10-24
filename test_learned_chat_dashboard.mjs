import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A card's chat says what a note there taught, from what was stored (the service's `learned` items), never from what
// the reply says: a quiet centered line "Saved to what I’ve learned · Edit · Undo" with what was saved under it. The link
// opens that learning on What I've learned (its block unfolded, the row lit), Edit opens it there in its editor, Undo
// removes it and the line then says so. No line starts with "You".
// All mocked; LEARNED_CHAT_SHOTS=dir saves phone and desktop screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),t0=Date.now()-60000;
const card={id:'d1d1d1d1d1d1d1d1',title:'NDA signed, awaiting Sam',body:'Signed it; the confirmation page said it went through.',status:'prepared',scope:'work',created_at:now,event_ids:[1]};
const rule='Browser tasks: after signing, submitting, paying or booking something, leave the confirmation page open in its tab so the owner can check it; do not close that tab.';
let facts,removed;
const reset=()=>{removed=new Set();facts=[{id:31,kind:'draft',text:rule,created_at:now,merged:0},
 ...[1,2,3,4,5,6].map(n=>({id:n,kind:'draft',text:`Older rule number ${n} about some other kind of task.`,created_at:new Date(Date.parse(now)-n*86400000).toISOString(),merged:0})),
 {id:7,kind:'fact',text:'The team meets on Mondays.',created_at:now,merged:0}]};
const items=()=>[
 {kind:'source',from:'Contract Admin',via:'email',title:'Signature requested',text:'Please review and sign.',mine:false,at:t0-600000},
 {kind:'you',text:'I hope it went well - next time keep it open in the browser for me',at:t0},
 {kind:'steps',steps:['Checking the page'],at:t0+1000},
 {kind:'reply',text:'Yes, it went through: the confirmation page said so.',at:t0+2000},
 {kind:'learned',what:'draft',id:31,text:rule,state:removed.has(31)?'removed':'saved',at:t0+2000}];
const posts=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 const asset={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'}[url.pathname];
 if(asset){res.setHeader('Content-Type',asset.endsWith('png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+asset,import.meta.url)));return}
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[card],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],
  fleet:{on:false,fleets:[]},voices:[{channel:'Email',text:'Hi + first name\nShort paragraphs\nRegards, then the name',updated_at:now}],facts:facts.filter(f=>!removed.has(f.id)),moved:[]}));return}
 if(url.pathname==='/api/action'){const {action,args}=JSON.parse(body);posts.push({action,args});
  if(action==='card_chat'){res.end(JSON.stringify({session:true,status:'ok',active:false,cursor:'k'+removed.size,items:items()}));return}
  if(action==='forget_fact'){removed.add(args.id);res.end(JSON.stringify({removed:true}));return}
  res.end('{}');return}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;const shots=process.env.LEARNED_CHAT_SHOTS;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1440,900],[390,844]])for(const scheme of ['light','dark']){
  reset();
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,locale:'en-US',timezoneId:'Europe/Brussels'}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/#chat=${card.id}`);
  const log=page.locator('#chat-log'),line=log.locator('.chat-learned').first();await line.waitFor();
  // Under the reply it belongs to, centered, quiet, and with what was saved under it.
  const order=await log.locator('.chat-msg,.chat-event,.chat-work').evaluateAll(es=>es.map(e=>e.className));
  const reply=order.findIndex(c=>/chat-msg machine/.test(c)&&!/chat-card/.test(c)),first=order.findIndex(c=>/chat-learned/.test(c));
  assert.ok(reply>=0&&first===reply+1,'the learned line follows the reply: '+order.join(' | '));
  assert.equal((await line.locator('.learned-acts').innerText()).replace(/\s+/g,' ').trim(),'Saved to what I’ve learned · Edit · Undo');
  assert.equal(await line.locator('q').innerText(),rule);
  const [lb,cb]=await Promise.all([line.boundingBox(),log.boundingBox()]);
  assert.ok(Math.abs((lb.x+lb.width/2)-(cb.x+cb.width/2))<12,'centered');
  assert.ok(await line.evaluate(e=>parseFloat(getComputedStyle(e).fontSize)<15),'quiet, small');
  for(const t of await log.locator('.chat-event').evaluateAll(es=>es.map(e=>e.firstElementChild?.innerText??e.innerText)))assert.doesNotMatch(t,/^You\b/,'no line starts with You: '+t);
  // A long rule makes the line nearly as wide as the chat: nothing beside it sticks out sideways (a hover time did once).
  await line.hover();
  assert.ok(await page.evaluate(()=>document.scrollingElement.scrollWidth<=document.scrollingElement.clientWidth),'no horizontal scroll at '+width);
  assert.ok(await log.evaluate(e=>e.scrollWidth<=e.clientWidth),'chat log does not overflow sideways at '+width);
  if(shots){await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:`${shots}/chat-learned-${width}-${scheme}.png`})}
  if(scheme==='dark'){
   // Undo removes it; the line then says so, with no buttons.
   await line.locator('[data-learned-undo="31"]').click();
   await page.locator('#toast',{hasText:'Removed from what I’ve learned.'}).waitFor({state:'attached'});
   assert.deepEqual(posts.filter(p=>p.action==='forget_fact').at(-1),{action:'forget_fact',args:{id:31}});
   await log.locator('.chat-learned.gone').waitFor();
   assert.equal(await log.locator('.chat-learned.gone .learned-acts').innerText(),'Removed from what I’ve learned');
   assert.equal(await log.locator('.chat-learned.gone button').count(),0);
   if(shots)await log.locator('.chat-learned.gone').screenshot({path:`${shots}/chat-learned-${width}-undone.png`});
  }else{
   // The link opens the rule on What I've learned: its block unfolded (it is the 7th rule), the row in view and lit.
   await line.getByRole('button',{name:'Saved to what I’ve learned'}).click();
   const row=page.locator('#learned-31');await row.waitFor();
   assert.equal(await page.locator('#feedback').isVisible(),true);
   assert.equal(await page.locator('#facts [data-group="rules"] .fact-row').count(),7);
   assert.ok(await row.evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}),'row in view');
   assert.ok(await row.evaluate(e=>e.classList.contains('flash')),'row lit');
   if(shots){await page.waitForTimeout(300);await page.screenshot({path:`${shots}/learned-from-chat-${width}.png`})}
   // Back returns to the chat; Edit there opens the same row in its editor, with the text ready to change.
   await page.goBack();await log.locator('.chat-learned').first().waitFor();
   assert.equal(await page.evaluate(()=>location.hash),'#chat='+card.id);
   await log.locator('[data-learned-edit="31"]').click();
   const box=page.locator('#learned-31 textarea');await box.waitFor();
   assert.equal(await box.inputValue(),rule);
   assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'TEXTAREA');
   if(shots)await page.screenshot({path:`${shots}/learned-edit-from-chat-${width}.png`});
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('learned chat dashboard ok');
}finally{await browser?.close();server.close()}
