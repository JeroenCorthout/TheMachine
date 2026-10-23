import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings in tabs: Account, General, Work hours, Writing, Skills, Model, each part of Settings in one. A Machine linked
// to a control plane account (hosted, or self-hosted and paired) gets Account first (test_account_dashboard.mjs); one
// with no account has none. A tab with nothing to show is
// left out. Links open a tab (#settings/skills, #settings=model), the URL follows the tab, and the last tab is kept.
// At 390px the row scrolls sideways and the page stays inside the screen.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const hours={days:[0,1,2,3,4],start:'08:00',end:'18:00',morning:'09:00',digest_at:'08:30',timezone:'UTC',saveable:true};
let hosted=true,paired=false,model=true;
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],hours,
  signatures:[{account_id:'mail',email:'owner@example.org',service:'gmail',html:'<b>Owner</b>',source:'detected',saveable:true}],skills:{skills:[],waiting:0,targets:[],folder:'skills/the-machine'},
  ...(model?{model_usage:[],model_status:{model:'openai/example-model',provider:'openai',windows:[],checked_at:new Date().toISOString()}}:{}),
  ...(hosted?{manage_url:'https://account.example/setup',account_url:'https://account.example/account'}:{}),...(paired?{fleet:{on:true,waiting:0},account_url:'https://app.themachine.computer/account'}:{})}));return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
const tabs=page=>page.locator('#settings-tabs>:not([hidden])').allInnerTexts();
const selected=page=>page.locator('#settings-tabs [aria-selected="true"]').innerText();
const visible=(page,sel)=>page.locator(sel).isVisible();
// The footer's Help goes to the help article for what's on screen
const help=page=>page.locator('#help-link').getAttribute('href'),HELP='https://themachine.computer/help/';
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1280,390]){
  hosted=true;model=true;
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="account"]:not([hidden])').waitFor();
  assert.deepEqual(await tabs(page),['Account','General','Work hours','Writing','Skills','Model']);
  // Account first, a tab like the others; General has this device, and no account card (it's in Account now).
  assert.equal(await selected(page),'Account');assert.equal(await page.evaluate(()=>location.hash),'#settings/account');
  assert.equal(await visible(page,'#settings-acct'),true);assert.equal(await visible(page,'#theme-toggle'),false);
  await page.locator('#settings-tabs [data-settings-tab="general"]').click();
  assert.equal(await visible(page,'#theme-toggle'),true);assert.equal(await visible(page,'#settings-account'),false);assert.equal(await visible(page,'#settings-acct'),false);
  for(const sel of ['#hours','#signatures','#settings-skills','#model-panel'])assert.equal(await visible(page,sel),false,sel);
  assert.equal(await help(page),HELP+'settings');
  // Each tab shows its own part.
  for(const [tab,sel] of [['hours','#hours'],['writing','#signatures'],['writing','#settings-voices'],['skills','#settings-skills'],['model','#model-panel']]){
   await page.locator(`#settings-tabs [data-settings-tab="${tab}"]`).click();
   assert.equal(await visible(page,sel),true,sel);assert.equal(await visible(page,'#theme-toggle'),false);
   assert.equal(await page.evaluate(()=>location.hash),'#settings/'+tab);
   assert.equal(await help(page),HELP+({hours:'work-hours'}[tab]||tab))}
  // The pills are one height and, on a phone, scroll sideways inside the screen.
  const row=await page.evaluate(()=>{const bar=document.querySelector('#settings-tabs'),r=[...bar.children].filter(e=>!e.hidden).map(e=>e.getBoundingClientRect());
   return {top:new Set(r.map(x=>Math.round(x.top))).size,height:new Set(r.map(x=>Math.round(x.height))).size,fits:document.documentElement.scrollWidth<=innerWidth,overflow:getComputedStyle(bar).overflowX}});
  assert.deepEqual(row,{top:1,height:1,fits:true,overflow:'auto'});
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/settings-tabs-${width}.png`});
  // The last tab is kept; a link opens another; leaving Settings drops the link.
  await page.reload();await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});await page.locator('#settings:not([hidden])').waitFor();
  assert.equal(await selected(page),'Model');
  await navClick(page.locator('nav [data-tab="events"]'));assert.equal(await page.evaluate(()=>location.hash),'');assert.equal(await help(page),HELP+'dashboard');
  await navClick(page.locator('#open-settings'));assert.equal(await selected(page),'Model');
  await page.goto(base+'/#settings/skills');await page.locator('#settings-skills:visible').waitFor();assert.equal(await selected(page),'Skills');
  await page.goto(base+'/#settings=hours');await page.locator('#hours:visible').waitFor();assert.equal(await selected(page),'Work hours');
  await page.evaluate(()=>{location.hash='#settings/writing'});await page.locator('#signatures:visible').waitFor();
  assert.equal(await page.locator('#settings').isVisible(),true);
  // On your own computer: no Account tab, and a tab with nothing in it (no model reported) is left out.
  hosted=false;model=false;await page.goto('about:blank');await page.goto(base+'/#settings/model');await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});await page.locator('#settings:not([hidden])').waitFor();
  assert.deepEqual(await tabs(page),['General','Work hours','Writing','Skills']);
  assert.equal(await selected(page),'General');assert.equal(await visible(page,'#settings-cloud'),true);
  // Self-hosted but paired with the Network: the Account tab too.
  paired=true;await page.goto('about:blank');await page.goto(base+'/#settings');await page.locator('#settings-tabs [data-settings-tab="account"]:not([hidden])').waitFor();
  assert.equal(await tabs(page).then(t=>t[0]),'Account');
  paired=false;
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('settings tabs dashboard ok');
}finally{await browser?.close();server.close()}
