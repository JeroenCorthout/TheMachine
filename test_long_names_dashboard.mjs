import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A very long address in the People lists is cut off with an ellipsis instead of widening the page.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const long='someone.with.a.long.name@support-0123456789abcdef0123.helpdesk-mail.example';
const today=new Date().toISOString().slice(0,10);
const person={id:'email:'+long,kind:'person',name:long,strength:1,closeness:'regular',last_at:today,signals:2,ids:[long]};
const org={id:'org:x',kind:'org',name:'averyveryverylongorganisationdomainwithoutanyspaces-0123456789.example',signals:3,last_at:today};
const errors=[];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({projects:[],people:[],quiet:[],new:[person],orgs:[org]}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('nav [data-tab="people"]'));
 const row=page.locator(`#people-view article[data-node="email:${long}"]`);
 await row.waitFor();
 for(const width of [320,390]){
  await page.setViewportSize({width,height:844});
  const m=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
  assert.ok(m.page<=m.viewport,`page wider than the screen at ${width}: ${m.page}`);
  // The bar and the name stay on one row, and the name is cut off rather than wrapped.
  const r=await row.evaluate(a=>{const bar=a.querySelector('.graph-bar').getBoundingClientRect(),node=a.querySelector('.node'),n=node.getBoundingClientRect(),sub=a.querySelector('.graph-sub');
   return {sameRow:Math.abs((bar.top+bar.bottom)/2-(n.top+n.bottom)/2)<4,cut:node.scrollWidth>node.clientWidth,ellipsis:getComputedStyle(node).textOverflow,title:node.title,subLines:Math.round(sub.getBoundingClientRect().height/parseFloat(getComputedStyle(sub).lineHeight||'20')),subTitle:sub.title}});
  assert.ok(r.sameRow,'bar and name on one row');
  assert.ok(r.cut&&r.ellipsis==='ellipsis','long name cut off with an ellipsis');
  assert.equal(r.title,long);
  assert.ok(r.subTitle.includes(long));
  if(process.env.LONG_SHOT)await page.screenshot({path:process.env.LONG_SHOT+'-'+width+'.png',fullPage:true});
 }
 // Fleet, Friends and Network lists, chips and a short bold date use the page's own markup helpers.
 await page.evaluate(long=>{const box=document.createElement('section');box.id='long-fixture';box.className='fleet-box';
  box.innerHTML=`<div class="fleet-pane-head"><h2 class="ell">${long}</h2>${chip(long,'person')}</div><ul class="fleet-people"><li><strong>${long}</strong> <small class="muted">@${long}</small><span class="actions"><button>Friend</button></span></li><li><strong>${long}</strong><br><small class="muted">${long}</small></li></ul><p id="long-date">${format('Prep for **Sunday 18 Oct** at the venue with everyone')}</p>`;
  document.querySelector('main').append(box)},long);
 for(const width of [320,390]){
  await page.setViewportSize({width,height:844});
  const m=await page.evaluate(()=>{const cut=[...document.querySelectorAll('#long-fixture .ct,#long-fixture .ell,#long-fixture .fleet-people strong,#long-fixture .fleet-people small')];const d=document.querySelector('#long-date strong'),r=d.getBoundingClientRect();
   return {page:document.documentElement.scrollWidth,viewport:innerWidth,count:cut.length,cut:cut.every(e=>e.scrollWidth>e.clientWidth&&getComputedStyle(e).textOverflow==='ellipsis'&&e.getBoundingClientRect().right<=innerWidth+1),dateLines:Math.round(r.height/parseFloat(getComputedStyle(d).lineHeight))}});
  assert.ok(m.page<=m.viewport,`page wider than the screen with long Fleet names at ${width}: ${m.page}`);
  assert.equal(m.count,6);
  assert.ok(m.cut,'long Fleet names and chips cut off with an ellipsis inside the screen');
  assert.equal(m.dateLines,1,'short bold date stays on one line');
 }
 const name=page.locator('#long-fixture .fleet-people strong').first();
 await name.hover();
 assert.equal(await name.getAttribute('title'),long,'full name on hover');
 assert.deepEqual(errors,[]);
 console.log('long names ok');
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
