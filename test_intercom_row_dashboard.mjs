import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections: an Intercom card says who replies go out as (the owner's own sign-in), or that it only reads (a Fleet
// share, a pasted token). Its count reads "conversations", never broken mid-word. SHOTS=<folder> saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const at=new Date().toISOString();
const card=(id,name,more)=>({id,service:'intercom',display_name:name,boundary:'work:Acme',total:361,signals_24h:150,pending:0,last_success:at,sync_activity:[],...more});
const connectors=[card('intercom','Acme',{replies_as:'Alex Example'}),card('intercom-fleet','Acme Support',{replies_as:'',shared_by:'Acme'})];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors,suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const shots=process.env.SHOTS;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1280,900],[390,844],[320,700]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#connections');
  const other=page.locator('#connections button',{hasText:'Other sources'});
  if(await other.count())await other.first().click();
  const own=page.locator('article',{hasText:'Alex Example'});
  await own.waitFor();
  assert.match(await own.locator('.conn-replies').innerText(),/^Replies as Alex Example, only after you press Send\.$/);
  assert.match(await own.innerText(),/Intercom · Work · Acme/);
  assert.equal(await page.locator('article',{hasText:'Acme Support'}).locator('.conn-replies').innerText(),'Reads only. Never replies.');
  const label=own.locator('.figures span').first();
  assert.equal(await label.innerText().then(t=>t.replace(/[­\n-]/g,'')),'conversations');
  // a break, if any, is a hyphenation, never a bare split inside the word
  assert.ok(await label.evaluate(s=>s.scrollWidth<=s.clientWidth+1),'the label fits its box');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  if(shots)await own.screenshot({path:`${shots}/intercom-row-${width}.png`});
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('intercom row ok');
}finally{await browser?.close();server.close()}
