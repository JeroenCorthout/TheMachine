import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections: a card's figure labels ("conversations", "notifications", "not reviewed") never break inside a word or
// hyphenate, with the dashboard's own fonts, for every source, at 1280, 390 and 320. Between words is fine.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const at=new Date().toISOString();
const services=['gmail','calendar','outlook','outlook_calendar','slack','salesflare','whatsapp','linkedin','android','gateway','github','stripe','rss','x','intercom','zoom'];
const connectors=services.map((service,i)=>({id:'c'+i,service,display_name:'Source '+i,boundary:'work:Acme',total:12345,signals_24h:1234,pending:999,last_success:at,sync_activity:[]}));
const server=createServer(async(req,res)=>{
 if(brand[req.url]){res.setHeader('Content-Type',req.url.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[req.url],import.meta.url)));return}
 res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url.startsWith('/api/status')){res.end(JSON.stringify({suggestions:[],promises:[],connectors,suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1280,900],[390,844],[320,700]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#connections');
  await page.locator('article .figures').first().waitFor();await page.evaluate(()=>document.fonts.ready);
  const seen=new Set();
  for(const tab of ['Mail and calendar','Chat','Other sources',null]){
   if(tab){const b=page.locator('button:visible',{hasText:tab});if(!await b.count())continue;await b.first().click()}
   const found=await page.evaluate(()=>[...document.querySelectorAll('article .figures span')].filter(s=>s.getBoundingClientRect().width>0).map(s=>{
    const cs=getComputedStyle(s),ctx=document.createElement('canvas').getContext('2d');ctx.font=`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const inner=s.clientWidth,box=s.parentElement.getBoundingClientRect(),card=s.closest('article').getBoundingClientRect();
    return {label:s.textContent,hyphens:cs.hyphens,wrap:cs.overflowWrap,widest:Math.max(...s.textContent.split(' ').map(w=>ctx.measureText(w).width)),inner,inside:box.right<=card.right+0.5}}));
   for(const f of found){seen.add(f.label);
    assert.equal(f.hyphens,'manual',f.label);assert.equal(f.wrap,'normal',f.label);
    assert.ok(f.widest<=f.inner+0.5,`${width}: "${f.label}" has a word wider than its box (${f.widest} > ${f.inner})`);
    assert.ok(f.inside,`${width}: "${f.label}" sticks out of its card`)}
  }
  assert.ok(seen.has('conversations')&&seen.has('notifications')&&seen.has('not reviewed'),[...seen].join());
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('stat labels ok');
}finally{await browser?.close();server.close()}
