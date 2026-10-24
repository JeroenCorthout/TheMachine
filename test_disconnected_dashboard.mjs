import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections, Disconnected lately (mail_grants.py receipts): "Removed, confirmed" only once confirmed, else "Pending"
// with why; it fits a phone. SHOTS=<dir> also saves screenshots at 390 and 1280.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const at=new Date().toISOString(),earlier=new Date(Date.now()-3*3600e3).toISOString();
const connectors=[{id:'g',service:'gmail',display_name:'alex@acme.example',boundary:'work:Acme',total:812,signals_24h:14,last_success:at,sync_activity:[]}];
const disconnected=[{provider:'google',email:'alex.long.address@subsidiary.acme-holdings.example',at,state:'pending'},
 {provider:'microsoft',email:'sam@contoso.example',at:earlier,state:'removed'},{provider:'google',email:'old@acme.example',at:earlier,state:'removed'}];
const server=createServer(async(req,res)=>{
 if(brand[req.url]){res.setHeader('Content-Type',req.url.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[req.url],import.meta.url)));return}
 res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url.startsWith('/api/status')){res.end(JSON.stringify({suggestions:[],promises:[],connectors,disconnected,suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1280,900],[390,844]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#connections');
  const block=page.locator('#disconnected');await block.locator('article').first().waitFor();
  const rows=await block.locator('article').evaluateAll(a=>a.map(x=>x.innerText.replace(/\s+/g,' ')));
  assert.equal(rows.length,3);
  assert.match(rows[0],/Pending: Google hasn’t confirmed\. Retrying/);
  assert.match(rows[1],/Removed, confirmed/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  if(process.env.SHOTS){await block.scrollIntoViewIfNeeded();await page.screenshot({path:`${process.env.SHOTS}/connections-disconnected-${width}.png`,fullPage:width<500})}
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('disconnected ok');
}finally{await browser?.close();server.close()}
