// An Intercom reply on a card: the card shows the exact text, the Send dialog says who it goes to and as whom, Send
// confirms that revision only, and a sent card says "Sent as <name> in Intercom". Synthetic data, a local fixture
// server, nothing sent. SHOT_DIR=<folder> saves screenshots.
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const sid='1234567890abcdef',done='0011223344556677',revision='1234567890ab';
const body='Hi Casey,\n\nYes: Settings, then Export. You get a CSV by email.\n\nAlex';
const reply={kind:'intercom',label:'Send reply',revision,account_id:'intercom',conversation_id:'7',to_name:'Casey Customer',as_name:'Alex Example',admin_id:'42',max_length:10000,body};
const sentReply={...reply,revision:'555555555555',state:{status:'sent',message_id:'intercom:7:p2',sent_at:new Date().toISOString()}};
const card=(id,title,a,extra={})=>({id,title,body:'Drafted the reply.',status:'prepared',event_ids:[1],next_action:a,next_actions:[a],
  outcome:JSON.stringify({state:'needs_you',needs:'Check the reply and send it'}),scope:'work',created_at:new Date().toISOString(),...extra});
const state=()=>({suggestions:[card(sid,'Casey asks how to export',reply),card(done,'Casey asked about invoices',sentReply,{status:'completed',outcome:null})],
  connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]});
const calls=[],errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){let b='';for await(const c of req)b+=c;const call=JSON.parse(b);calls.push(call);res.end(JSON.stringify({status:'queued'}));return}
 res.statusCode=url.pathname.startsWith('/api/')?200:404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const shots=process.env.SHOT_DIR;if(shots)await mkdir(shots,{recursive:true});
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [320,390,1280]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(()=>refresh());
  const task=page.locator(`#task-${sid}`);
  await task.waitFor();
  // The card shows the exact reply
  assert.equal(await task.locator('.draft .db').textContent(),body);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll at '+width);
  if(shots)await task.screenshot({path:`${shots}/reply-card-${width}.png`});
  // Send opens the review: to whom, as whom, the exact text. Nothing goes out before Send now.
  await task.locator(`[data-smart="${revision}"]`).click();
  await page.locator('#send-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#send-title').textContent(),'Send this reply in Intercom?');
  assert.equal(await page.locator('#send-preview').textContent(),body);
  const head=await page.locator('#send-head').textContent();
  assert.ok(head.includes('Casey Customer')&&head.includes('Alex Example in Intercom'),head);
  assert.ok(await page.locator('#send-subject-label').isHidden());
  assert.equal(calls.filter(c=>c.action==='confirm_action').length,0);
  if(shots)await page.locator('#send-dialog').screenshot({path:`${shots}/reply-dialog-${width}.png`});
  await page.locator('#send-confirm').click();
  for(let i=0;i<100&&!calls.some(c=>c.action==='confirm_action');i++)await page.waitForTimeout(20);
  const confirm=calls.find(c=>c.action==='confirm_action');
  assert.equal(confirm.args.suggestion_id,sid);assert.equal(confirm.args.revision,revision);
  calls.length=0;
  // A sent reply says as whom it went, without "You"
  const sent=page.locator(`#task-${done}`);
  const text=await sent.evaluate(n=>n.textContent);
  assert.match(text,/Sent as Alex Example in Intercom( at \d\d:\d\d)?\./);
  assert.ok(!/\bYou (sent|replied)/.test(text));
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('ok');
