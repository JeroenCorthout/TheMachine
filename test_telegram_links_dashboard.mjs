import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A link from Telegram or a phone notification opens the card's own page (#chat=<id>), as a tap on the card in the
// list does, on a phone and on a computer. #result=<id> in messages sent before does the same, never the old Result
// modal with Copy draft over the list. Read result on the card itself still opens that modal. SHOTS=<dir> saves
// screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const manifest=JSON.parse(await readFile(new URL('./manifest.webmanifest',import.meta.url),'utf8'));
assert.equal(manifest.handle_links,'preferred');assert.deepEqual(manifest.launch_handler,{client_mode:['focus-existing','auto']});
const now=new Date().toISOString(),t0=Date.now()-3600e3,S='5a5a5a5a5a5a5a5a',R='6b6b6b6b6b6b6b6b';
const cards=[
 {id:S,title:'Buy Starlink cables',body:'Your sealed 15 m Ethernet cable is ready at checkout for €60 total.',status:'instructions',scope:'personal',created_at:now,event_ids:[1],
  preparation_status:'blocked',preparation_reason:'I still cannot reach checkout because browser control is broken; your cable has not been ordered.',
  step_stopped:'Not confirmed yet: Order Ethernet cable (I still cannot reach checkout). Try again carries it on.'},
 {id:R,title:'Answer Robin about the pilot',body:'Drafted a reply to Robin.',status:'prepared',scope:'work',created_at:now,event_ids:[2]}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const chat={session:true,status:'blocked',active:false,cursor:'k1',items:[
 {kind:'you',text:'Buy it, let me know if you need sign-in or payment details',at:t0},
 {kind:'steps',steps:['Opening the checkout','Checking the browser'],at:t0+1000},
 {kind:'reply',text:'I still cannot reach checkout because browser control is broken. Your cable has not been ordered.',at:t0+2000}]};
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/result'){res.end(JSON.stringify({text:'Hi Robin, Thursday works.'}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);res.end(JSON.stringify(r.action==='card_chat'?chat:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[],root='http://127.0.0.1:'+server.address().port+'/';let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[390,844],[1280,900]]){
  const context=await browser.newContext({viewport:{width,height},locale:'en-US',timezoneId:'Europe/Brussels',isMobile:width<500,hasTouch:width<500});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  // The link Telegram and notifications send now, and the one in messages sent before: both open the card's page
  for(const hash of ['#chat='+S,'#result='+S]){
   await page.goto(root+hash);await page.locator('.chat-layout .chat-head').waitFor();
   assert.equal(new URL(page.url()).hash,'#chat='+S,hash);
   assert.equal(await page.locator('.chat-head h2.chat-title').textContent(),'Buy Starlink cables');
   await page.getByText('Your cable has not been ordered.',{exact:false}).first().waitFor();
   assert.equal(await page.locator('#result-dialog[open]').count(),0,hash);
   assert.ok(!(await page.locator('body').innerText()).includes('ready at checkout'),hash);
   if(process.env.SHOTS&&hash.startsWith('#chat')){await page.waitForTimeout(400);await page.screenshot({path:`${process.env.SHOTS}/card-page-${width}.png`})}
  }
  // A link the open app catches (launch_handler focus-existing) goes to that card too
  await page.goto(root);await page.locator('#task-'+R).waitFor();
  await page.evaluate(h=>{location.hash=h},'#result='+R);await page.locator('.chat-head h2.chat-title',{hasText:'Answer Robin'}).waitFor();
  assert.equal(await page.locator('#result-dialog[open]').count(),0);
  // The card in the list says the step isn't done, not the earlier "ready at checkout"
  await page.goto(root);const card=page.locator('#task-'+S);await card.waitFor();
  assert.match(await card.innerText(),/Not confirmed yet: Order Ethernet cable/);assert.doesNotMatch(await card.innerText(),/ready at checkout/);
  if(process.env.SHOTS){await card.scrollIntoViewIfNeeded();await page.screenshot({path:`${process.env.SHOTS}/card-list-${width}.png`})}
  // Read result on a card still opens the result itself
  await page.locator('#task-'+R+' [data-result="read"]').click();await page.locator('#result-dialog[open]').waitFor();
  assert.equal(await page.locator('#result-title').textContent(),'Answer Robin about the pilot');
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('telegram links dashboard ok');
