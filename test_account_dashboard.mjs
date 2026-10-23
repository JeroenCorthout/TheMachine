import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, Account: on a hosted Machine it comes first in General, with Open account and Sign out side by side. Sign out posts to
// /_machine/logout, which ends this dashboard's sign-in and the account's. On your own computer it stays at the bottom,
// without Sign out. Set SHOTS=<dir> for screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const hours={days:[0,1,2,3,4],start:'08:00',end:'18:00',morning:'09:00',digest_at:'08:30',timezone:'UTC'};
let hosted=true;const posts=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');
 if(url.pathname==='/_machine/logout'){posts.push(req.method);res.writeHead(303,{Location:'/bye'});res.end();return}
 if(url.pathname==='/bye'){res.setHeader('Content-Type','text/html');res.end('<p>Signed out</p>');return}
 res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],hours,
  ...(hosted?{manage_url:'https://account.example/account'}:{})}));return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1280])for(const scheme of ['light','dark']){
  hosted=true;
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await navClick(page.locator('#open-settings'));
  await page.locator('#sign-out:not([hidden])').waitFor();
  assert.equal(await page.evaluate(()=>document.querySelector('#settings-tabs').nextElementSibling.id),'settings-account-block');
  const box=await page.evaluate(()=>{const r=e=>document.querySelector(e).getBoundingClientRect(),o=r('#manage'),s=r('#sign-out button'),card=r('#settings-account');
   return {row:Math.abs(o.top-s.top)<4,inside:s.right<=card.right&&o.left>=card.left,height:Math.round(o.height)===Math.round(s.height),fits:document.documentElement.scrollWidth<=innerWidth}});
  assert.deepEqual(box,{row:true,inside:true,height:true,fits:true});
  assert.deepEqual(await page.locator('#sign-out').evaluate(f=>[f.method,new URL(f.action).pathname]),['post','/_machine/logout']);
  if(process.env.SHOTS)await page.locator('#settings').screenshot({path:`${process.env.SHOTS}/account-${width}-${scheme}.png`,clip:undefined});
  if(width===390&&scheme==='light'){
   await page.locator('#sign-out button').click();await page.waitForURL(base+'/bye');assert.deepEqual(posts,['POST']);
   // On your own computer: no account page to sign out of, and the card stays last.
   hosted=false;await page.goto(base);await navClick(page.locator('#open-settings'));await page.locator('#settings-cloud:not([hidden])').waitFor();
   assert.equal(await page.locator('#sign-out').isHidden(),true);
   assert.equal(await page.evaluate(()=>document.querySelector('#settings').lastElementChild.id),'settings-account-block');
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('account dashboard ok');
}finally{await browser?.close();server.close()}
