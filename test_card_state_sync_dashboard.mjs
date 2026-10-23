import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A card whose run stopped after the owner asked for a change (status instructions, preparation blocked): the card's chip
// and the chat's status line say the same thing, never a stale "Preparing" in the chat. A run that named no questions asks
// nothing: Stuck, Try again, no Answer. The owner's own note is "Your note", never "Asked". In the chat the email the owner
// asked to change steps back as an earlier draft; the WhatsApp draft that replaced it is the one to review and send.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),t0=Date.now()-600000;
const note={choice:'other',feedback:'they don’t know about this yet, and I talk to them on WhatsApp',created_at:now};
const cards=[
 {id:'a1a1a1a1a1a1a1a1',title:'Arrange the installer: speakers and cables',body:'Drafted an email.',status:'instructions',scope:'work',origin:'owner',created_at:now,event_ids:[1],
  preparation_status:'blocked',preparation_reason:'The draft is ready, but this card can’t send on that channel yet.',preparation_questions:[],preparation_no_questions:true,
  latest_feedback:note},
 {id:'b2b2b2b2b2b2b2b2',title:'Arrange the painter',body:'Drafted a WhatsApp message.',status:'prepared',scope:'work',origin:'owner',created_at:now,event_ids:[2],
  outcome:JSON.stringify({state:'needs_you',needs:'check the message and send it'}),latest_feedback:note,
  next_actions:[{kind:'whatsapp',revision:'w1',label:'Send',account_id:'personal-whatsapp',chat_id:'32470000000@s.whatsapp.net',to_name:'Sam',body:'Hi Sam! We’d like ceiling speakers put in. Could you help?',max_length:4000}]},
 {id:'c3c3c3c3c3c3c3c3',title:'Answer the venue',body:'x',status:'instructions',scope:'work',created_at:now,event_ids:[3],
  preparation_status:'blocked',preparation_reason:'I need one detail from you.',preparation_questions:[{question:'Which date suits you?'}]},
 // A run that hung in the browser and timed out: the service stopped it, nothing is asked. Stuck with Try again, not Needs your help.
 {id:'d4d4d4d4d4d4d4d4',title:'Order the cable',body:'x',status:'accepted',scope:'work',created_at:now,event_ids:[4],
  preparation_status:'blocked',preparation_reason:'This didn’t finish, also on the second try. Tap Try again, or change the request.',preparation_error:'completion_unconfirmed',
  preparation_browser:{since:now,page:'https://shop.example.com/checkout',sign_in:false}}];
const email={kind:'email',to:['sam@example.com'],subject:'Ceiling speakers',body:'Hi Sam, when can you do the speakers?',reply:false};
const whatsapp={kind:'whatsapp',to:['Sam'],subject:'',body:'Hi Sam! We’d like ceiling speakers put in. Could you help?',reply:false};
const items={
 a1a1a1a1a1a1a1a1:[{kind:'reply',text:'Your draft asks Sam when they can do it.',drafts:[email],at:t0},{kind:'you',text:note.feedback,at:t0+1000},{kind:'steps',steps:['Reading the card'],at:t0+2000}],
 b2b2b2b2b2b2b2b2:[{kind:'reply',text:'Your draft asks Sam when they can do it.',drafts:[email],at:t0},{kind:'you',text:note.feedback,at:t0+1000},
  {kind:'reply',text:'Switched to WhatsApp and said what it’s about first.',drafts:[whatsapp],at:t0+3000}]};
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:true,fleets:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body||'{}');
  if(r.action==='card_chat'){res.end(JSON.stringify({session:true,status:'blocked',active:false,cursor:'k',items:items[r.args.suggestion_id]||[]}));return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1280,390]){
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-a1a1a1a1a1a1a1a1').waitFor();
  const card=id=>page.locator('#task-'+id);
  // Stuck, not Needs your help: nothing is asked, so no Answer; Try again is offered and the reason is said once
  assert.equal((await card('a1a1a1a1a1a1a1a1').locator('.card-badges .chip').first().textContent()).trim(),'Stuck');
  assert.equal(await card('a1a1a1a1a1a1a1a1').locator('[data-choice="answer"]').count(),0);
  assert.equal(await card('a1a1a1a1a1a1a1a1').locator('[data-choice="retry"]').count(),1);
  assert.equal(await card('a1a1a1a1a1a1a1a1').locator('.status-line.outcome').count(),0);
  // The owner's own message is their note, never shown as a question
  assert.equal((await card('a1a1a1a1a1a1a1a1').locator('.saved-feedback strong').textContent()).trim(),'Your note');
  assert.ok(!(await card('a1a1a1a1a1a1a1a1').innerText()).includes('Asked'));
  // A run that did ask still leads with Answer under Needs your help
  assert.equal((await card('c3c3c3c3c3c3c3c3').locator('.card-badges .chip').first().textContent()).trim(),'Needs your help');
  assert.equal(await card('c3c3c3c3c3c3c3c3').locator('[data-choice="answer"]').count(),1);
  // A run the service stopped after browser steps (a timeout) is Stuck with Try again, never Needs your help
  assert.equal((await card('d4d4d4d4d4d4d4d4').locator('.card-badges .chip').first().textContent()).trim(),'Stuck');
  assert.equal(await card('d4d4d4d4d4d4d4d4').locator('[data-choice="retry"]').count(),1);
  assert.equal(await card('d4d4d4d4d4d4d4d4').locator('[data-choice="answer"]').count(),0);
  // The chat says what the card says, not Preparing
  for(const [id,want] of [['a1a1a1a1a1a1a1a1','Stuck'],['c3c3c3c3c3c3c3c3','Needs your help']]){
   await page.evaluate(id=>{location.hash='chat='+id},id);
   await page.locator('#chat-status').waitFor({state:'attached'});await page.waitForFunction(()=>document.querySelector('#chat-log .chat-msg,#chat-log .chat-result'));
   const status=(await page.locator('#chat-status').textContent()).trim();
   assert.equal(status,want,id);assert.ok(!/Preparing/.test(status))}
  // The email the owner asked to change is an earlier draft, not the one to send
  await page.evaluate(()=>{location.hash='chat=a1a1a1a1a1a1a1a1'});await page.locator('#chat-log .chat-draft').waitFor();
  assert.equal(await page.locator('#chat-log .chat-result.current').count(),0);
  assert.equal((await page.locator('#chat-log .chat-draft-head').first().innerText()).includes('Earlier draft'),true);
  // After the switch: the WhatsApp draft is the current result, with Review and send; the email stays an earlier one
  await page.evaluate(()=>{location.hash='chat=b2b2b2b2b2b2b2b2'});await page.locator('#chat-log .chat-draft.current').waitFor();
  const current=await page.locator('#chat-log .chat-draft.current').innerText();
  assert.match(current,/^WhatsApp to Sam/);assert.ok(current.includes('ceiling speakers put in'));
  assert.equal(await page.locator('#chat-log .chat-draft.current [data-smart="w1"]').count(),1);
  assert.equal(await page.locator('#chat-log .chat-draft').count(),2);
  assert.ok(!(await page.locator('#chat-log .chat-draft').first().getAttribute('class')).includes('current'));
  await context.close()}
 assert.deepEqual(errors,[])}
finally{await browser?.close();server.close()}
console.log('card state sync ok');
