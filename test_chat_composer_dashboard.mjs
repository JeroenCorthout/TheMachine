import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A card's chat on a phone: the message box stays at the bottom of what you see while the conversation scrolls under
// it, like the ChatGPT and Claude apps. The thread scrolls in its own box inside a window-high column (the page itself
// never scrolls, so the browser's address bar doesn't collapse under a finger), with and without the keyboard, while
// the address bar grows or shrinks, and while a run's answer streams in and the page keeper (placeKeep) is busy.
// The keyboard is played with a visual viewport the test drives: on an iPhone the window keeps its height and the
// visible part (visualViewport) shrinks and slides (offsetTop); Android Chrome resizes the window
// (interactive-widget=resizes-content).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),id='c1c1c1c1c1c1c1c1';
const cards=[{id,title:'Come by the paint shop in the afternoon for the order',body:'Lotte confirmed you can come this afternoon.',status:'prepared',scope:'personal',created_at:now,event_ids:[1]}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const items=Array.from({length:30},(_,n)=>n%3===2?{kind:'you',text:'Question number '+n+'?',at:Date.now()-100000+n*1000}:{kind:'reply',text:'Reply '+n+'. '+'Some words about the order and the shop. '.repeat(3),at:Date.now()-100000+n*1000});
let tick=0,failRead=null;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 // A run is going: the dashboard asks once a second and the live answer grows, so the thread changes under the reader
 if(url.pathname==='/api/action'){const r=JSON.parse(body);tick++;
  // failRead: the Gateway is slow or away, so the service can't read the run's side ('running' or 'done')
  if(r.action==='card_chat'&&failRead){res.end(JSON.stringify({session:true,status:failRead,active:failRead==='running',items:[{kind:'you',text:'how much did they quote incl vat?',at:Date.now()}],cursor:null,unavailable:true}));return}
  res.end(JSON.stringify(r.action==='card_chat'?{session:true,status:'running',active:true,cursor:'k'+tick,items,live:{text:'Looking it up. '.repeat(1+tick%6)}}:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const SHOTS=process.env.SHOT_DIR;
const errors=[];let browser;
// A visual viewport the test moves: {kb} is the keyboard's height (0: none), {pan} how far an iPhone slid the view down
const FAKE_VV=()=>{const t=new EventTarget();window.__vv={kb:0,pan:0,fire:n=>t.dispatchEvent(new Event(n))};
 for(const [k,f] of Object.entries({width:()=>innerWidth,height:()=>innerHeight-__vv.kb,offsetTop:()=>__vv.pan,offsetLeft:()=>0,pageTop:()=>scrollY+__vv.pan,pageLeft:()=>0,scale:()=>1}))Object.defineProperty(t,k,{get:f});
 Object.defineProperty(window,'visualViewport',{get:()=>t,configurable:true})};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,deviceScaleFactor:2,colorScheme:'dark',locale:'en-US',timezoneId:'Europe/Brussels'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 await context.addInitScript(FAKE_VV);
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const cdp=await context.newCDPSession(page);
 await page.goto('http://127.0.0.1:'+server.address().port+'/#chat='+id);
 await page.locator('#chat-input').waitFor();await page.waitForTimeout(1200);
 const vv=(kb,pan=0)=>page.evaluate(([kb,pan])=>{__vv.kb=kb;__vv.pan=pan;__vv.fire('resize');__vv.fire('scroll')},[kb,pan]);
 // Where the box is in what you see (the visual viewport), and what the thread shows at its top
 const measure=()=>page.evaluate(()=>{const c=document.querySelector('.chat-compose').getBoundingClientRect(),b=document.querySelector('.chat-box').getBoundingClientRect(),v=visualViewport,
  log=document.querySelector('#chat-log'),first=[...log.children].find(n=>n.getBoundingClientRect().bottom>document.querySelector('.chat-head').getBoundingClientRect().bottom+1);
  return {top:Math.round(b.top-v.offsetTop),bottom:Math.round(b.bottom-v.offsetTop),left:Math.round(b.left),width:Math.round(b.width),under:Math.round(v.height-(c.bottom-v.offsetTop)),view:v.height,
   pageY:scrollY,logY:log.scrollTop,first:first?.textContent.slice(0,24)}});
 // A real finger on the thread (positive: back in time), in moves of at most 300px; the page may glide on after it
 const swipe=async(dy)=>{for(let left=dy;Math.abs(left)>=1;){const d=Math.sign(left)*Math.min(300,Math.abs(left)),y0=d>0?170:480;left-=d;
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:195,y:y0}]});
  for(let i=1;i<=12;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:195,y:y0+d*i/12}]});await new Promise(r=>setTimeout(r,16))}
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]})}await page.waitForTimeout(400)};
 const shot=async(n,clip)=>{if(SHOTS)await page.screenshot({path:`${SHOTS}/${n}.png`,...(clip?{clip}:{})})};
 const still=(a,b,where)=>{for(const k of ['top','bottom','left','width'])assert.ok(Math.abs(a[k]-b[k])<=1,`the message box stays put ${where}: ${JSON.stringify({before:a,after:b})}`)};

 // No keyboard: at the end of the thread, then back in time, then forward again
 const start=await measure();await shot('1-at-end');
 assert.ok(start.under<=1&&start.bottom>start.view-120,'the box sits at the bottom of the screen: '+JSON.stringify(start));
 await swipe(500);const up=await measure();await shot("2-scrolled-back");
 assert.notEqual(up.first,start.first,'the thread itself scrolled');assert.equal(up.pageY,0,'the page itself stays put');
 still(start,up,'while the thread scrolls back');
 await swipe(1200);still(start,await measure(),'at the top of the thread');
 await swipe(-300);still(start,await measure(),'scrolling forward');
 // The answer keeps streaming in for a few seconds (the page keeper sees every change): still put
 for(let n=0;n<3;n++){await page.waitForTimeout(1000);still(start,await measure(),'while the answer streams in')}
 // The address bar shrinks and grows again (the window's height changes): the box follows the bottom edge
 for(const h of [900,800,844]){await page.setViewportSize({width:390,height:h});await vv(0);await page.waitForTimeout(200);const m=await measure();
  assert.ok(m.under<=1&&Math.abs(m.bottom-(start.bottom+h-844))<=1,`the box on the bottom edge at ${h}px: `+JSON.stringify(m));await swipe(200);still(m,await measure(),'after the address bar moved')}

 // The keyboard on an iPhone: the visible part shrinks and slides; the box sits right on the keyboard and stays there
 await page.locator('#chat-input').tap();await page.locator('#chat-input').fill('how much did they quote incl vat?');
 await vv(336);await page.waitForTimeout(200);const kb=await measure();
 assert.ok(kb.under<=1,'the box sits right on the keyboard: '+JSON.stringify(kb));
 await shot('3-keyboard-ios',{x:0,y:0,width:390,height:844-336});
 for(const pan of [0,120,40,200,0]){await vv(336,pan);await swipe(pan%80?350:-350);const m=await measure();
  assert.ok(m.under<=1,`on the keyboard with the view slid ${pan}px: `+JSON.stringify(m));still(kb,m,'with the keyboard open (slid '+pan+'px)')}
 assert.equal(await page.evaluate(()=>document.activeElement?.id),'chat-input','typing goes on');
 await vv(0);

 // Android Chrome resizes the window for the keyboard (resizes-content): the box rides on it the same way
 await page.setViewportSize({width:390,height:844-300});await page.waitForTimeout(200);const ak=await measure();
 assert.ok(ak.under<=1&&ak.bottom>ak.view-120,'on the Android keyboard: '+JSON.stringify(ak));await shot('4-keyboard-android');
 await swipe(400);still(ak,await measure(),'with the Android keyboard open');await swipe(-800);still(ak,await measure(),'back at the end with the Android keyboard');
 await page.setViewportSize({width:390,height:844});

 // The installed app (standalone, no address bar): the same column
 await page.emulateMedia({media:'screen'});await page.evaluate(()=>{const m=matchMedia;window.matchMedia=q=>/standalone/.test(q)?{matches:true,media:q,addEventListener(){},removeEventListener(){}}:m(q)});
 await page.locator('#chat-input').evaluate(n=>n.blur());await page.waitForTimeout(200);
 const pwa=await measure();await swipe(500);still(pwa,await measure(),'in the installed app');

 // A read that fails mid-answer (a busy Gateway) changes nothing on screen: no aside under the typing dots, the thread
 // and the live answer stay. A chat that couldn't be read at all says so plainly once the run is over, and catches up.
 const shown=()=>page.evaluate(()=>({n:document.querySelectorAll('#chat-log>*').length,note:document.querySelector('#chat-log .chat-note')?.textContent||'',live:!!document.querySelector('.chat-live')}));
 const ok=await shown();failRead='running';
 for(let n=0;n<3;n++){await page.waitForTimeout(1100);const m=await shown();assert.equal(m.note,'','no note mid-answer');assert.equal(m.n,ok.n,'the thread stays');assert.ok(m.live,'the answer keeps its place')}
 failRead='done';await page.reload();await page.locator('#chat-input').waitFor();
 await page.waitForFunction(()=>document.querySelector('#chat-log .chat-note'));
 assert.equal((await shown()).note,'I couldn’t load my replies just now. Trying again…');
 failRead=null;await page.waitForFunction(()=>!document.querySelector('#chat-log .chat-note')&&document.querySelectorAll('#chat-log>*').length>20,null,{timeout:8000});
 assert.deepEqual(errors,[]);
 await context.close();
}finally{await browser?.close();server.close()}
console.log('chat composer dashboard ok');
