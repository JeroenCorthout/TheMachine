import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The source link under a card on a phone: when it wraps to two lines the mail icon and the arrow both sit on the
// first line, the arrow is never underlined, and a tap leaves no hover background or underline behind.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const cards=[{id:'w1',title:'Reply about the venue',body:'They asked about Friday.',status:'pending',scope:'work',created_at:now,event_ids:[1,2],source_line:'WhatsApp from Alex Example · Thu 22 Oct (+12 more)'},
 {id:'z1',title:'Send the call notes',body:'Notes.',status:'pending',scope:'work',created_at:now,event_ids:[3],source_line:'Zoom meeting · Thu 22 Oct'}];
const state={suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}};
const server=createServer(async(req,res)=>{for await(const _ of req);const url=new URL(req.url,'http://fixture.invalid');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 res.setHeader('Content-Type','application/json');
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();
 await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-z1').waitFor();
 await page.locator('#task-w1 .card-foot .read-original').tap();
 const look=id=>page.locator(`#task-${id} .card-foot .read-original`).evaluate(e=>{
  const r=e.getBoundingClientRect(),a=e.querySelector('span[aria-hidden]').getBoundingClientRect(),cs=getComputedStyle(e),icon=getComputedStyle(e,'::before');
  return {lines:Math.round(r.height/parseFloat(cs.lineHeight||'0')||r.height/20),bg:cs.backgroundColor,underline:cs.textDecorationLine,
   iconTop:parseFloat(icon.marginTop),arrowTop:Math.round(a.top-r.top),arrowFirstLine:a.bottom-r.top<=r.height/2+1}});
 const wrapped=await look('w1'),single=await look('z1');
 assert.ok(wrapped.lines>=2,JSON.stringify(wrapped));
 assert.ok(wrapped.arrowFirstLine,'arrow on the first line: '+JSON.stringify(wrapped));
 assert.ok(wrapped.iconTop>0&&wrapped.iconTop<8,'icon centered on the first line: '+JSON.stringify(wrapped));
 for(const s of [wrapped,single]){assert.equal(s.underline,'none');assert.equal(s.bg,'rgba(0, 0, 0, 0)')}
 console.log('Source link: icon and arrow on the first line, no underline or tap background.');
}finally{await browser?.close();server.close()}
