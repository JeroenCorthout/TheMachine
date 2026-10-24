import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// After a snooze the dashboard says how the card comes back, as delivery really does it: Telegram when it is on, a
// notification when some device (this one or another) has them, both when both. With neither it says when the card is
// back here, and offers Turn on notifications when this browser can have them (iPhone Safari: Add to Home Screen first;
// blocked: nothing to offer). SHOTS=<folder> saves phone and desktop screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const ID='cd'.repeat(8),now=new Date().toISOString();
const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);tomorrow.setHours(9,0,0,0);
let reminders={telegram:false,push:0};
const status=()=>({suggestions:[{id:ID,title:'Send Robin the garden dates',body:'Robin asked for the dates.',status:'pending',scope:'personal',created_at:now,event_ids:[1]}],
 promises:[],connectors:[{id:'mail',service:'gmail',email:'owner@example.org',display_name:'Mail',status:'ok'}],suggestion_counts:{pending:1},
 decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],reminders});
const actions=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(status()));return}
 if(url.pathname==='/api/push-key'){res.end(JSON.stringify({key:'B'+'A'.repeat(86)}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);actions.push(r);
  res.end(JSON.stringify(r.action==='decide'?{status:'snoozed',snoozed_until:tomorrow.toISOString()}:r.action==='push_test'?{sent:true}:{subscribed:r.action==='push_subscribe'}));return}
 if(/^\/brand\/[\w.-]+\.(png|webp|woff2)$/.test(url.pathname)){try{const f=await readFile(new URL('.'+url.pathname,import.meta.url));res.setHeader('Content-Type',url.pathname.endsWith('.png')?'image/png':url.pathname.endsWith('.webp')?'image/webp':'font/woff2');res.end(f)}catch{res.statusCode=404;res.end()}return}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const UA={chrome:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
 android:'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
 iphone:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1'};
// The browser's side, made up: notification permission and this device's push subscription.
function fakeBrowser(o){
 const log=window.__log=[];
 try{sessionStorage.setItem('device-session',String(Date.now()-120000))}catch{}
 if(o.noPush){try{delete window.PushManager;delete window.Notification}catch{}}
 if(window.Notification){let p=o.perm||'default';Object.defineProperty(Notification,'permission',{configurable:true,get:()=>p});
  Notification.requestPermission=async()=>{log.push('requestPermission');if(p==='default')p='granted';return p}}
 const sub=()=>({endpoint:'https://push.example/device-1',toJSON(){return {endpoint:this.endpoint,keys:{p256dh:'B'+'x'.repeat(86),auth:'y'.repeat(22)}}},unsubscribe:async()=>true});
 let current=o.sub?sub():null;
 const reg={pushManager:{getSubscription:async()=>current,subscribe:async()=>{log.push('subscribe');current=sub();return current}}};
 if(navigator.serviceWorker)navigator.serviceWorker.register=async()=>reg;
}
const shots=process.env.SHOTS;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
const snooze=async({width=1440,height=900,ua='chrome',shot,...o}={})=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,userAgent:UA[ua],colorScheme:'light',locale:'en-GB'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 await context.addInitScript(fakeBrowser,o);
 const page=await context.newPage();page.on('pageerror',e=>errors.push(ua+' '+width+': '+e.message));
 await page.goto(base);await page.locator('#task-'+ID).waitFor();
 await page.waitForFunction(()=>devicePush.checked||!pushOK);
 await page.evaluate(id=>openSnooze(id),ID);await page.locator('#snooze-dialog [data-snooze="tomorrow"]').click();
 await page.waitForFunction(()=>document.querySelector('#toast').classList.contains('show'));
 const toast=await page.evaluate(()=>{const t=document.querySelector('#toast');return {text:t.innerText.replace(/\s+/g,' ').trim(),button:t.querySelector('button')?.textContent||null}});
 if(shots&&shot){await page.waitForTimeout(250);await page.screenshot({path:`${shots}/${shot}.png`})}
 return {context,page,toast}};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 // Telegram on, no notifications anywhere: Telegram, nothing to turn on.
 reminders={telegram:true,push:0};
 {const {context,toast}=await snooze({shot:'telegram-desktop'});
  assert.deepEqual(toast,{text:'Snoozed. I’ll remind you on Telegram.',button:null});await context.close()}
 // Notifications on another device only: a notification, even though this browser has none.
 reminders={telegram:false,push:1};
 {const {context,toast}=await snooze({});
  assert.deepEqual(toast,{text:'Snoozed. I’ll send you a notification.',button:null});await context.close()}
 // This browser is subscribed (the server's count not caught up yet): a notification.
 reminders={telegram:false,push:0};
 {const {context,toast}=await snooze({perm:'granted',sub:true,width:390,height:844,ua:'android'});
  assert.deepEqual(toast,{text:'Snoozed. I’ll send you a notification.',button:null});await context.close()}
 // Both, as delivery sends both.
 reminders={telegram:true,push:2};
 {const {context,toast}=await snooze({shot:'both-phone',width:390,height:844,ua:'android'});
  assert.deepEqual(toast,{text:'Snoozed. I’ll remind you on Telegram and as a notification.',button:null});await context.close()}
 // Neither: back here tomorrow, and this browser can turn notifications on from the toast.
 reminders={telegram:false,push:0};
 for(const [width,height,ua,name] of [[1440,900,'chrome','neither-desktop'],[390,844,'android','neither-phone']]){
  const {context,page,toast}=await snooze({width,height,ua,shot:name});
  assert.deepEqual(toast,{text:'Snoozed. It’ll be back here tomorrow at 09:00. Turn on notifications',button:'Turn on notifications'});
  const before=actions.length;await page.locator('#toast button').click();
  await page.waitForFunction(()=>document.querySelector('#toast').innerText.includes('Notifications on'));
  assert.deepEqual(await page.evaluate(()=>window.__log),['requestPermission','subscribe']);
  assert.deepEqual(actions.slice(before).map(a=>a.action),['push_subscribe','push_test']);
  await context.close()}
 // iPhone Safari outside the installed app can't have notifications yet: Add to Home Screen, which shows the steps.
 {const {context,page,toast}=await snooze({noPush:true,width:390,height:844,ua:'iphone',shot:'neither-iphone'});
  assert.deepEqual(toast,{text:'Snoozed. It’ll be back here tomorrow at 09:00. Add to Home Screen',button:'Add to Home Screen'});
  await page.locator('#toast button').click();
  await page.waitForFunction(()=>document.querySelector('#toast').innerText.includes('Add to Home Screen. Open The Machine'));
  await context.close()}
 // Notifications blocked in this browser: only when it's back, nothing to tap.
 {const {context,toast}=await snooze({perm:'denied'});
  assert.deepEqual(toast,{text:'Snoozed. It’ll be back here tomorrow at 09:00.',button:null});await context.close()}
 assert.ok(!actions.some(a=>a.action==='decide'&&a.args.choice!=='snooze'));
 assert.deepEqual(errors,[]);
 console.log('snooze reminder dashboard ok');
}finally{await browser?.close();server.close()}
