import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, Account: the first tab on a Machine linked to a control plane account. Hosted, the control plane answers on
// the dashboard's own host (/_machine/account, the owner's sign-in there) and each button is a POST to
// /_machine/account-hop in a new tab, which opens that page of the account signed in. Self-hosted and paired, this
// Machine asks (api/account) and the buttons are links. Sign out stays (hosted). Not linked: no Account tab, and General
// keeps "Join the Network". Made-up data only. Set SHOTS=<dir> for screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const hours={days:[0,1,2,3,4],start:'08:00',end:'18:00',morning:'09:00',digest_at:'08:30',timezone:'UTC'};
const CP='https://account.example';
const hostedSummary={email:'alex@example.org',hop:true,
 plan:{name:'Standard',price:'€25 a month, excl. VAT',status:'active',status_text:'Active',trial_end:null,cancel_at:null},
 next_invoice:{date:'2026-11-20',amount:'€25, excl. VAT'},
 server:{size:'8 GB memory',region:'Europe, the Netherlands',status:'Running'},
 connections:[{service:'Google',email:'alex@example.org'},{service:'Microsoft',email:'alex@work.example'}],
 fleets:[{name:'Example Co',kind:'work',role:'owner',people:4,seats:3}],
 actions:['account','fleet','plan','portal','cancel','connections'],
 links:{account:CP+'/account',fleet:CP+'/account?tab=fleet',plan:CP+'/account?tab=overview#status',portal:CP+'/account?tab=billing',cancel:CP+'/account/cancel',connections:CP+'/setup#sources'}};
const pairedSummary={on:true,email:'sam@example.org',hop:false,plan:null,next_invoice:null,server:null,connections:[],
 fleets:[{name:'Family',kind:'family',role:'member',people:3}],actions:['account','fleet'],
 links:{account:'https://app.themachine.computer/account',fleet:'https://app.themachine.computer/account?tab=fleet'}};
let mode='hosted',fail=false;const hops=[],posts=[],asked=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');
 if(url.pathname==='/_machine/logout'){posts.push(req.method);res.writeHead(303,{Location:'/bye'});res.end();return}
 if(url.pathname==='/_machine/account-hop'){let body='';for await(const c of req)body+=c;hops.push([req.method,body]);res.setHeader('Content-Type','text/html');res.end('<p>Account page</p>');return}
 if(url.pathname.startsWith('/brand/')){res.setHeader('Content-Type','image/png');res.end(await readFile(new URL('./brand/app-icon-192.png',import.meta.url)));return}  // as the app serves them (assistant.BRAND_ASSETS)
 if(url.pathname==='/bye'){res.setHeader('Content-Type','text/html');res.end('<p>Signed out</p>');return}
 res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/_machine/account'){asked.push('cp');if(fail){res.writeHead(401);res.end('{"error":"signed out"}');return}res.end(JSON.stringify(hostedSummary));return}
 if(url.pathname==='/api/account'){asked.push('machine');res.end(JSON.stringify(mode==='paired'?pairedSummary:{on:false}));return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],hours,
  skills:{skills:[],waiting:0,targets:[],folder:'skills/the-machine'},model_usage:[],model_status:{model:'openai/example-model',provider:'openai',windows:[],checked_at:new Date().toISOString()},
  signatures:[{account_id:'mail',email:'alex@example.org',service:'gmail',html:'<b>Alex</b>',source:'detected',saveable:true}],
  ...(mode==='hosted'?{manage_url:CP+'/setup',account_url:CP+'/account'}:{}),...(mode==='paired'?{fleet:{on:true,waiting:0},account_url:'https://app.themachine.computer/account'}:{})}));return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
const text=(page,sel)=>page.locator(sel).innerText();
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440])for(const scheme of ['light','dark']){
  mode='hosted';fail=false;
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});
  await navClick(page.locator('#open-settings'));await page.locator('#acct-list [data-acct-go="plan"]').waitFor();
  // A normal tab, the first one, and open by default
  assert.deepEqual((await page.locator('#settings-tabs>:not([hidden])').allInnerTexts())[0],'Account');
  assert.equal(await page.locator('#settings-tabs [aria-selected="true"]').innerText(),'Account');
  assert.equal(await page.locator('#settings-tabs a').count(),0);  // no link out any more
  assert.equal(await text(page,'#acct-email'),'alex@example.org');
  const list=await text(page,'#acct-list');
  for(const bit of ['Standard','Active','€25 a month, excl. VAT','Next invoice','Fri, Nov 20','€25, excl. VAT','8 GB memory · Europe, the Netherlands','Google: alex@example.org','Microsoft: alex@work.example','Example Co','Owner · 4 people · 3 seats'])
   assert.ok(list.includes(bit),bit);
  assert.ok(!/—/.test(list));
  // Buttons are POSTs to the hop, each in a new tab; Sign out stays
  const forms=await page.locator('#settings-acct form[action="/_machine/account-hop"]').evaluateAll(fs=>fs.map(f=>[f.method,f.target,f.querySelector('[name=to]').value]));
  assert.deepEqual(forms.map(f=>f[2]).sort(),['account','cancel','connections','fleet','plan','portal']);
  assert.ok(forms.every(f=>f[0]==='post'&&f[1]==='_blank'));
  assert.equal(await page.locator('#settings-acct form[action="/_machine/logout"]').count(),1);
  // Cancel is a quiet text link under the buttons: easy to find, not an equal-weight button
  assert.equal(await page.locator('.acct-cancel .acct-quiet[data-acct-go="cancel"]').count(),1);
  assert.deepEqual((await page.locator('#settings-tabs>:not([hidden])').allInnerTexts()),['Account','General','Work hours','Writing','Skills','Model']);
  assert.equal(await page.locator('#settings-account-block').isHidden(),true);  // General no longer says "on your account page"
  const fits=await page.evaluate(()=>{const r=document.querySelector('#acct-list').getBoundingClientRect();
   return document.documentElement.scrollWidth<=innerWidth&&[...document.querySelectorAll('#settings-acct button,#settings-acct a')].every(b=>{const x=b.getBoundingClientRect();return x.right<=innerWidth&&x.left>=0})});
  assert.equal(fits,true);
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/account-hosted-${width}-${scheme}.png`,fullPage:true});
  if(width===390&&scheme==='light'){
   const [tab]=await Promise.all([context.waitForEvent('page'),page.locator('#acct-list [data-acct-go="portal"]').click()]);
   await tab.waitForLoadState();assert.equal(hops.at(-1)[0],'POST');assert.equal(hops.at(-1)[1],'to=portal');await tab.close();
   // Read once a minute, not on every look
   const before=asked.length;await page.locator('#settings-tabs [data-settings-tab="general"]').click();await page.locator('#settings-tabs [data-settings-tab="account"]').click();
   assert.equal(asked.length,before);
   await page.locator('#settings-acct form[action="/_machine/logout"] button').click();await page.waitForURL(base+'/bye');assert.deepEqual(posts,['POST']);
   // Signed out at the control plane: a short note and the account link, nothing broken
   fail=true;await page.goto(base);await navClick(page.locator('#open-settings'));await page.locator('#acct-list',{hasText:'Can’t reach your account'}).waitFor();
   assert.equal(await page.locator('#acct-foot a[href="https://account.example/account"]').count(),1);
   // Self-hosted and paired: this Machine asks, links instead of hops, no Sign out
   mode='paired';fail=false;await page.goto('about:blank');await page.goto(base+'/#settings/account');await page.locator('#acct-list',{hasText:'Family'}).waitFor();
   assert.equal(asked.at(-1),'machine');
   assert.equal(await page.locator('#settings-acct form[action="/_machine/account-hop"]').count(),0);
   assert.deepEqual(await page.locator('#settings-acct a[data-acct-go]').evaluateAll(as=>as.map(a=>[a.getAttribute('href'),a.target])),
    [['https://app.themachine.computer/account?tab=fleet','_blank'],['https://app.themachine.computer/account','_blank']]);
   assert.equal(await page.locator('#settings-acct form[action="/_machine/logout"]').count(),0);
   assert.ok((await text(page,'#acct-list')).includes('Member · 3 people'));
   if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/account-paired-${width}-${scheme}.png`,fullPage:true});
   // Not linked: no Account tab; General keeps the Network card
   mode='none';await page.goto('about:blank');await page.goto(base+'/#settings');await page.locator('#settings-cloud:not([hidden])').waitFor();
   assert.equal(await page.locator('#settings-tabs [data-settings-tab="account"]').isHidden(),true);
   assert.equal(await page.locator('#settings-tabs [aria-selected="true"]').innerText(),'General');
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('account dashboard ok');
}finally{await browser?.close();server.close()}
