import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The first card shows while the first look still runs: the dashboard looks every 5 s while a review runs and refreshes
// as soon as the review saved a card, instead of waiting for its 30 s refresh or the end of the review. While the
// owner types, it waits. Nothing is sent anywhere: the fixture server answers everything.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const started=new Date(Date.now()-60e3).toISOString();
const run={run_id:'r1',status:'running',started_at:started,preview:1,auto:1,window_hours:48,planned:20,done_count:4,account_ids:['gmail'],steps:[]};
let cards=[],statusCalls=0,running=true;
const live=()=>({runs:running?[run]:[],queue:null,first_look:null,cards:{count:cards.length,newest:cards.at(-1)?.created_at||null}});
const card={id:'c1',title:'Send Sam the pricing',body:'Sam asked for pricing.',status:'pending',event_ids:[1],scope:'work',created_at:new Date().toISOString()};
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:live(),
 connectors:[{id:'gmail',email:'pat@example.com',service:'gmail'}]});
const errors=[];
const server=createServer((req,res)=>{const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){statusCalls++;res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/reviews/live'){res.end(JSON.stringify(live()));return}
 if(url.pathname.startsWith('/brand/')){res.statusCode=404;res.end();return}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#suggestions .first-look-empty').waitFor();
 // The review saves its first card: on screen within one 5 s look, well before the 30 s refresh
 const before=statusCalls,saved=Date.now();cards=[card];
 await page.locator('#suggestions article', {hasText:'Send Sam the pricing'}).waitFor({timeout:8000});
 assert.ok(Date.now()-saved<8000);
 assert.equal(statusCalls-before,1);  // one refresh for it, not one per look
 await page.waitForTimeout(5500);
 assert.equal(statusCalls-before,1);  // nothing new: no further refresh
 // Typing: the next card waits until the owner is done, also when the review ends meanwhile
 await page.evaluate(()=>{const t=document.createElement('textarea');t.id='typing';document.body.append(t);t.focus()});
 cards=[card,{...card,id:'c2',title:'Book the venue',created_at:new Date(Date.now()+1000).toISOString()}];running=false;
 await page.waitForTimeout(5500);
 assert.equal(await page.locator('#suggestions article', {hasText:'Book the venue'}).count(),0);
 await page.evaluate(()=>document.getElementById('typing').blur());
 await page.locator('#suggestions article', {hasText:'Book the venue'}).waitFor({timeout:8000});
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('first card stream dashboard ok');
