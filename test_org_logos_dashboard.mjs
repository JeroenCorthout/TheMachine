import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Organisations show their logo from enrichment: https only, no referrer, lazy and fixed size, the initial behind it,
// and the initial alone when there is no logo, it isn't https, or it fails to load. SHOT_DIR=… also saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=new Date(Date.now()-864e5).toISOString();
const LOGO=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#2b59c3"/><circle cx="32" cy="32" r="16" fill="none" stroke="#fff" stroke-width="7"/></svg>`;
const org=(id,name,logo,extra={})=>({id:'org:'+id,kind:'org',name,domain:id,signals:3,last_at:day,role:null,strength:1,
 profile:{name,industry:'Software Development',size:'51-200',location:'London',...(logo?{logo}:{})},about:'Software Development, 51-200 people, London',links:[],recent:[],around:[],...extra});
const orgs=[org('globex.example','Globex','https://logos.test/globex.png'),org('initech.example','Initech','https://logos.test/missing.png'),
 org('hooli.example','Hooli','http://logos.test/plain.png'),org('acme.example','Acme',null)];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');res.end(JSON.stringify(node?orgs.find(o=>o.id===node):{projects:[],people:[],quiet:[],new:[],orgs}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const logoRequests=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height,scheme] of process.env.SHOT_DIR?[[1440,900,'light'],[1440,900,'dark'],[390,844,'light'],[390,844,'dark']]:[[390,844,'light']]){
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,deviceScaleFactor:width<500?2:1}),page=await context.newPage();
  await context.route('**/*',route=>{const u=new URL(route.request().url());
   if(u.hostname==='logos.test'){logoRequests.push({url:u.href,referer:route.request().headers()['referer']});return u.pathname==='/globex.png'?route.fulfill({status:200,contentType:'image/svg+xml',body:LOGO}):route.fulfill({status:404})}
   return u.hostname==='127.0.0.1'?route.continue():route.abort()});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('nav [data-tab="people"]').click();
  await page.locator('[data-graph-pane="orgs"]').click();
  const row=id=>page.locator(`#people-view article[data-node="org:${id}"] .org-pic`);
  await row('globex.example').waitFor();
  const img=row('globex.example').locator('img');
  assert.deepEqual(await img.evaluate(i=>[i.getAttribute('referrerpolicy'),i.getAttribute('loading'),i.width,i.height,i.alt]),['no-referrer','lazy',22,22,'']);
  await page.waitForFunction(()=>document.querySelector('article[data-node="org:globex.example"] .org-pic img')?.complete);
  await page.waitForFunction(()=>!document.querySelector('article[data-node="org:initech.example"] .org-pic img'));  // failed: the initial stays
  assert.equal((await row('initech.example').textContent()).trim(),'I');
  assert.equal(await row('hooli.example').locator('img').count(),0);  // not https: never loaded
  assert.equal(await row('acme.example').locator('img').count(),0);
  assert.equal((await row('acme.example').textContent()).trim(),'A');
  assert.ok(logoRequests.length&&logoRequests.every(r=>!r.referer&&r.url.startsWith('https://')),JSON.stringify(logoRequests));
  assert.ok(!logoRequests.some(r=>r.url.includes('plain.png')));
  if(process.env.SHOT_DIR){await mkdir(process.env.SHOT_DIR,{recursive:true});await page.screenshot({path:`${process.env.SHOT_DIR}/orgs-${width}-${scheme}.png`})}
  await page.locator('#people-view article[data-node="org:globex.example"] button.node').click();
  await page.locator('.graph-detail .org-pic.big img').waitFor();
  assert.equal(await page.locator('.graph-detail .org-pic.big img').evaluate(i=>i.width),36);
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/org-${width}-${scheme}.png`});
  assert.deepEqual(errors,[]);
  await context.close();
 }
 console.log('org logos ok');
}finally{await browser?.close();server.close()}
