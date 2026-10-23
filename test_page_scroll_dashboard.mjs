import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Every tab scrolls as a normal page when its content is taller than the window, on a computer and on a phone, also
// after a card chat was open (the chat's fixed, window-high layout belongs to the Suggestions tab only). With the chat
// itself open on a computer, the page stays put and the thread scrolls instead.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const cards=Array.from({length:30},(_,n)=>({id:(n+'').padStart(2,'0').repeat(8),title:'Card number '+n,body:'Some text for card '+n+'.',status:'pending',scope:'work',created_at:now,event_ids:[n+1]}));
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const history={session:true,status:'ok',active:false,cursor:'k1',items:Array.from({length:40},(_,n)=>({kind:'reply',text:'Reply '+n,at:Date.now()-100000+n*1000}))};
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);res.end(JSON.stringify(r.action==='card_chat'?history:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const TABS=['inbox','calendar','events','people','fleet','feedback','health'];
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height,mobile] of [[1440,900,false],[390,844,true]]){
  const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-US',timezoneId:'Europe/Brussels'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#task-'+cards[0].id).waitFor();
  // Tall content in the shown tab, then scroll to the bottom: the page must move
  const scrolls=async(where)=>{const y=await page.evaluate(()=>{const s=[...document.querySelectorAll('main>section')].find(x=>!x.hidden);
   if(!s.querySelector('.scroll-probe')){const d=document.createElement('div');d.className='scroll-probe';d.style.height='3000px';s.append(d)}
   scrollTo(0,0);scrollTo(0,1e6);return scrollY});assert.ok(y>500,`${where} at ${width}px scrolls (scrollY ${y})`)};
  const tab=async t=>{await page.evaluate(t=>t==='settings'?showSettings('general'):document.querySelector(`nav [data-tab="${t}"]`).click(),t);await page.waitForTimeout(150)};
  for(const opened of [false,true]){
   if(opened){await page.goto(base+'#chat='+cards[1].id);await page.locator('#chat-log').waitFor();
    if(!mobile){const y=await page.evaluate(()=>{scrollTo(0,1e6);return scrollY});assert.equal(y,0,'the open chat keeps the page put on a computer')}}
   for(const t of [...TABS,'settings']){if(opened&&t==='inbox')continue;await tab(t);await scrolls(t+(opened?' after a chat':''))}
   await tab('inbox');if(!opened)await scrolls('inbox')}
  await context.close()}
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('page scroll dashboard ok');
