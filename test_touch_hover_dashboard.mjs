import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');
// On a touch screen a tap leaves nothing highlighted in the drawer: no hover sticks to Graph's chevron or a row after
// the finger lifts (hover styles are for a mouse only), and the chevron's box starts after the word Graph, never under
// it. With a mouse, hovering the chevron lights the Graph row as one and only darkens the arrow.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const cards=Array.from({length:3},(_,n)=>({id:String(n).padStart(2,'0').repeat(8),title:'Card '+n,body:'Text.',status:'prepared',scope:'work',created_at:now,event_ids:[n+1]}));
const server=createServer(async(req,res)=>{for await(const _ of req);const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},agents_settings:{}}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({counts:{person:1121,org:634,project:40},people:[],quiet:[],new:[],projects:[],orgs:[],whos_who:[],waiting:0}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const look=page=>page.evaluate(()=>{const bg=q=>getComputedStyle(document.querySelector(q)).backgroundColor,r=q=>document.querySelector(q).getBoundingClientRect();
 return {fold:bg('#graph-fold'),row:bg('nav [data-tab="people"]'),gap:r('#graph-fold').left-r('nav [data-tab="people"] .nl').right}});
const clear=c=>c==='transparent'||Number((/\/\s*([\d.e-]+)\)$|rgba\([^)]*,\s*([\d.e-]+)\)$/.exec(c)||[])[1]??(/rgba\([^)]*,\s*([\d.e-]+)\)$/.exec(c)||[])[1]??1)<0.01;  // none, or the last of a press fading out
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,360]){
  const context=await browser.newContext({viewport:{width,height:800},isMobile:true,hasTouch:true});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#task-'+cards[0].id).waitFor();
  assert.ok(await page.evaluate(()=>matchMedia('(hover:none)').matches),'a touch screen');
  await page.locator('#drawer-open').tap();await page.waitForTimeout(450);
  for(let i=0;i<2;i++){await page.locator('#graph-fold').tap();await page.waitForTimeout(600);
   const l=await look(page);assert.ok(clear(l.fold),`no box left on the chevron after a tap at ${width}: ${l.fold}`);assert.ok(clear(l.row),`no highlight left on the row at ${width}: ${l.row}`);
   assert.ok(l.gap>=1,`the chevron's box starts after the word Graph at ${width}: ${l.gap}`)}
  if(process.env.TOUCH_SHOT)await page.screenshot({path:`${process.env.TOUCH_SHOT}-${width}.png`,clip:{x:0,y:0,width,height:520}});
  await context.close()}
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.locator('#task-'+cards[0].id).waitFor();
 await page.locator('#graph-fold').hover();await page.waitForTimeout(250);
 const l=await look(page);assert.ok(clear(l.fold),'the chevron has no box of its own on hover: '+l.fold);assert.ok(!clear(l.row),'the Graph row lights as one');assert.ok(l.gap>=3,'a gap after Graph: '+l.gap);
 assert.deepEqual(errors,[]);
 console.log('touch hover dashboard ok');
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
