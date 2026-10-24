import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, Account, Your addresses (own_addresses.py): the owner's addresses with where each came from, Remove on the
// ones found (never on a connected account), and the maybes with Confirm and Not mine. One tap, then the list as the
// service returns it. Also on a Machine with no control plane account, where it's the Account tab's only part.
// At 390px nothing leaves the screen.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const hours={days:[0,1,2,3,4],start:'08:00',end:'18:00',morning:'09:00',digest_at:'08:30',timezone:'UTC',saveable:true};
const yours=[{address:'sam@acme.example',sources:['Connected account','Slack profile'],removable:false},
 {address:'sam.lee@acme-labs.example',sources:['Gmail alias','Your calendar'],removable:true},
 {address:'samuel.lee.longer.address@subsidiary.acme-international.example',sources:['Gmail alias'],removable:true}];
let addresses,posted=[];
const reset=()=>{addresses={yours:[...yours],maybe:[{address:'sales@acme.example',sources:['Gmail alias'],why:'Looks like a team address'},
 {address:'desk@acme.example',sources:[],why:'Only seen in mail you sent'}]};posted=[]};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],hours,own_addresses:addresses}));return}
 if(url.pathname==='/api/action'){let body='';for await(const c of req)body+=c;const {action,args}=JSON.parse(body);posted.push([action,args]);
  const m=addresses.maybe.find(x=>x.address===args.address);addresses={yours:args.choice==='confirm'&&m?[...addresses.yours,{address:m.address,sources:[...m.sources,'You confirmed it'],removable:true}]:addresses.yours.filter(x=>x.address!==args.address),
   maybe:addresses.maybe.filter(x=>x.address!==args.address)};res.end(JSON.stringify({own_addresses:addresses}));return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1440,390]){
  reset();
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto(base);await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="account"]:not([hidden])').waitFor();
  assert.equal(await page.locator('#settings-tabs [aria-selected="true"]').innerText(),'Account');
  assert.equal(await page.locator('#settings-addresses').isVisible(),true);
  const rows=await page.locator('#addr-list>div').allInnerTexts();
  assert.equal(rows.length,3);assert.match(rows[0],/sam@acme\.example\s+Connected account · Slack profile/);assert.match(rows[1],/Gmail alias · Your calendar\s+Remove/);
  assert.equal(await page.locator('#addr-list>div').first().locator('button').count(),0);  // a connected account stays
  assert.deepEqual(await page.locator('#addr-maybe-list>div').allInnerTexts(),['sales@acme.example\nLooks like a team address · Gmail alias\nConfirm\nNot mine','desk@acme.example\nOnly seen in mail you sent\nConfirm\nNot mine']);
  const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&[...document.querySelectorAll('#settings-addresses button')].every(b=>b.getBoundingClientRect().right<=innerWidth));
  assert.equal(fits,true);
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/own-addresses-${width}.png`,fullPage:true});
  // Confirm: one tap, and it's on the list
  await page.locator('#addr-maybe-list [data-addr="sales@acme.example"][data-addr-choice="confirm"]').click();
  await page.locator('#addr-list>div',{hasText:'sales@acme.example'}).waitFor();
  assert.deepEqual(posted.at(-1),['own_address',{address:'sales@acme.example',choice:'confirm'}]);
  assert.match(await page.locator('#addr-list>div',{hasText:'sales@acme.example'}).innerText(),/You confirmed it/);
  // Not mine, then Remove on a found one (asks first)
  await page.locator('#addr-maybe-list [data-addr="desk@acme.example"][data-addr-choice="remove"]').click();
  await page.locator('#addr-maybe').waitFor({state:'hidden'});
  await page.locator('#addr-list [data-addr="sam.lee@acme-labs.example"]').click();
  await page.locator('#addr-list>div',{hasText:'sam.lee@acme-labs.example'}).waitFor({state:'detached'});
  assert.deepEqual(posted.map(p=>p[1].choice),['confirm','remove','remove']);
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/own-addresses-after-${width}.png`,fullPage:true});
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('own addresses dashboard ok');
}finally{await browser?.close();server.close()}
