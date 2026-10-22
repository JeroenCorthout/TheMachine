import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Reviews still off: every tab says so at the top with the way to turn them on (hosted: the setup step; self-hosted:
// the command), and Signal reviews shows one line for it instead of a first-look row next to "No reviews yet".
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
let on=false,manage='https://control.example/setup',cards=[];
let look={state:'waiting',why:'sources',days:7};
const live=()=>({runs:[],queue:null,first_look:look});
const state=()=>({suggestions:cards,promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},
 review_runs:[],review_live:live(),reviews_on:on,manage_url:manage});
const errors=[];
const server=createServer((req,res)=>{const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/reviews/live'){res.end(JSON.stringify(live()));return}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const open=async()=>{const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('#needs-you').waitFor({state:on?'hidden':'visible'});return page};
 let page=await open();
 assert.match(await page.locator('#needs-you').textContent(),/Reviews are off/);
 assert.equal(await page.locator('#needs-you a',{hasText:'Turn on reviews'}).getAttribute('href'),manage+'#turn-on');
 const rows=page.locator('#review-live .review-live');
 assert.equal(await rows.count(),1);
 const text=await rows.textContent();
 assert.ok(text.includes('Reviews are off.')&&text.includes('first look at your last 7 days')&&text.includes('it starts once a source'),text);
 assert.ok(!(await page.locator('#analyses').textContent()).includes('No reviews yet'));
 // Suggestions: while the first look is pending it says so, not "All clear"
 const empty=await page.locator('#suggestions').textContent();
 assert.ok(empty.includes('A first look at your last 7 days starts soon')&&!empty.includes('All clear'),empty);
 await page.close();
 look={state:'due',quick:true,hours:48,waiting:20,days:7};page=await open();
 assert.match(await page.locator('#suggestions').textContent(),/A first look at your last 2 days starts soon.*Starting now/);
 await page.close();look={state:'waiting',why:'sources',days:7};
 cards=[{id:'s1',title:'Reply to Sam',body:'',status:'pending',scope:'work',created_at:'2026-10-21T10:00:00+00:00',event_ids:[],account_ids:[]}];
 page=await open();  // cards from the first look: the notice says where they came from
 assert.match(await page.locator('#needs-you').textContent(),/Reviews are off: the cards here come from a one-time first look/);
 await page.close();cards=[];
 manage=null;page=await open();  // self-hosted: the command instead of a link
 assert.match(await page.locator('#needs-you').textContent(),/install\.sh --activate/);
 assert.equal(await page.locator('#review-live code').textContent(),'./install.sh --activate');
 await page.close();
 on=true;page=await open();  // on: no notice; only the first look row, no "No reviews yet" beside it
 assert.equal(await page.locator('#needs-you').isHidden(),true);
 assert.equal(await page.locator('#review-live .review-live').count(),1);
 assert.ok((await page.locator('#review-live').textContent()).includes('A first review of your last 7 days'));
 assert.ok(!(await page.locator('#analyses').textContent()).includes('No reviews yet'));
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('reviews off dashboard ok');
