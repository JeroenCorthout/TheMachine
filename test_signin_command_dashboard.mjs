import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections: an expired Google sign-in names the problem with a Reconnect button that runs the sign-in from here,
// and keeps the exact terminal command (the file keyring, by its password file's path) as a fallback with a copy button.
// Each card's Details can reconnect or disconnect it; a hosted machine sends Google and Microsoft to its account page,
// and never shows a terminal command. All mocked.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const command='GOG_KEYRING_BACKEND=file GOG_KEYRING_PASSWORD="$(cat /home/owner/private/keyring-password)" gog auth add owner@example.com --client existing';
let statusCalls=0,manage=null;const actions=[];
const connectors=[{id:'work-gmail',service:'gmail',email:'owner@example.com',boundary:'work:Example',display_name:'owner@example.com',
 error:'gmail:google_signin_expired_run_gog_auth_add',retry_at:new Date(Date.now()+3600e3).toISOString(),failures:2,sign_in_command:command,connect_name:'gmail',
 sync_activity:[{at:new Date().toISOString(),status:'failed',error:'gmail:google_signin_expired_run_gog_auth_add'}]}];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){statusCalls++;res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors,review_runs:[],manage_url:manage}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',d=>body+=d);req.on('end',()=>{const {action,args}=JSON.parse(body);actions.push([action,args]);
  if(action==='connect_start'){res.end(JSON.stringify({session:'s1',state:'running',label:'Gmail',source:args.name,reconnect:args.account,lines:['Checking what this Google sign-in may do now, to ask for the same again...'],question:null}));return}
  if(action==='connect_poll'){res.end(JSON.stringify({session:'s1',state:'running',label:'Gmail',source:'gmail',reconnect:'work-gmail',lines:['Open this address'],question:{id:1,kind:'ask',prompt:'The address Google sent you to',default:''}}));return}
  if(action==='connect_remove'){res.end(JSON.stringify({removed:args.account,restarting:true}));return}
  res.end('{}')});return}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:'http://127.0.0.1:'+server.address().port});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('nav [data-tab="health"]'));
 const pre=page.locator('#connectors .signin-command');await pre.waitFor({state:'attached'});
 assert.equal(await pre.textContent(),command);
 const card=await page.locator('#connectors').textContent();
 assert.match(card,/Google sign-in for Email expired\. Reconnect/);
 assert.doesNotMatch(card,/Please reconnect this account/);
 assert.equal(await pre.isVisible(),false,'the command waits behind "Or use a terminal"');
 await page.locator('#connectors .signin-fallback summary').click();
 await page.locator('#connectors [data-copy-command]').click();
 await page.locator('#toast',{hasText:'Copied.'}).waitFor();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),command);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // Reconnect runs the same sign-in as + Add, for this account, in the + Add pane.
 await page.locator('#connectors .alert [data-reconnect]').click();
 await page.locator('#connect-add .connect-run').waitFor();
 assert.deepEqual(actions.find(a=>a[0]==='connect_start')[1],{name:'gmail',account:'work-gmail'});
 assert.ok(await page.locator('#connect-add').isVisible());
 await page.locator('#connect-answer').waitFor();
 // Details: Reconnect and Disconnect (asks first).
 await page.locator('[data-health-pane="mail"]').click();
 await page.locator('#connectors details[id^="sync-details-"] summary').click();
 page.once('dialog',d=>{assert.match(d.message(),/^Disconnect owner@example\.com\? It stops syncing\./);d.accept()});
 await page.locator('#connectors [data-disconnect="work-gmail"]').click();
 await page.locator('#toast',{hasText:'Disconnected.'}).waitFor();
 assert.deepEqual(actions.find(a=>a[0]==='connect_remove')[1],{account:'work-gmail'});
 // Details: Work (with its company) or Personal, the owner's own marking (a guess until then). Another company asks its name.
 assert.match(await page.locator('#connectors .conn-head small').textContent(),/Email · Work · Example/);
 assert.match(await page.locator('#connectors .conn-scope').textContent(),/Guessed/);
 await page.locator('#connectors select[data-scope="work-gmail"]').selectOption('personal:me');
 await page.locator('#toast',{hasText:'Saved.'}).waitFor();
 assert.deepEqual(actions.find(a=>a[0]==='connect_scope')[1],{account:'work-gmail',boundary:'personal:me'});
 await page.waitForTimeout(800);
 page.once('dialog',d=>{assert.equal(d.message(),'Company name');d.accept('Other Co')});
 await page.locator('#connectors select[data-scope="work-gmail"]').selectOption('');
 for(let i=0;i<50&&actions.filter(a=>a[0]==='connect_scope').length<2;i++)await page.waitForTimeout(100);
 assert.deepEqual(actions.filter(a=>a[0]==='connect_scope')[1][1],{account:'work-gmail',boundary:'work:Other Co'});
 const connection=connectors[0],failure=connection.sync_activity[0];
 Object.assign(connection,{error:null,retry_at:null,failures:0,last_success:new Date().toISOString(),last_sync_signals:1,total:247});
 delete connection.sign_in_command;
 connection.sync_activity.unshift({at:new Date().toISOString(),status:'success',signals:1});
 connection.last_failure={at:failure.at,error:failure.error};
 const before=statusCalls;
 await page.evaluate(()=>{dispatchEvent(new Event('pageshow'));dispatchEvent(new Event('online'));document.dispatchEvent(new Event('visibilitychange'))});
 await page.waitForFunction(()=>document.querySelector('#connectors .connection-status')?.textContent==='Connected');
 assert.equal(statusCalls,before+1,'resume events share one status request');
 assert.equal(await page.locator('#connectors .signin-command').count(),0);
 assert.equal(await page.locator('#connectors [data-copy-command]').count(),0);
 assert.equal(await page.locator('#connectors .state-dot.t-mint').count(),1);
 assert.match(await page.locator('#connectors').textContent(),/Last failure: Google sign-in/);
 // Hosted: no terminal, ever; Google and Microsoft sign in again (and out) on the account page.
 manage='https://cp.example.org/setup';
 Object.assign(connection,{error:'gmail:google_broker',failures:3,account_page:manage+'#google'});delete connection.connect_name;
 await page.evaluate(()=>refresh());
 await page.waitForFunction(()=>document.querySelector('#connectors a[href$="#google"]'));
 assert.equal(await page.locator('#connectors .signin-command').count(),0);
 assert.equal(await page.locator('#connectors [data-disconnect]').count(),0);
 assert.match(await page.locator('#connectors').textContent(),/Reconnect or disconnect ↗/);
 assert.doesNotMatch(await page.locator('main').innerText(),/terminal/i);
 assert.deepEqual(errors,[]);
 console.log('signin command dashboard ok');
}finally{await browser?.close();server.close()}
