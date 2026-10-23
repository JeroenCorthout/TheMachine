import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A long organisation name wraps; its Edit button keeps its shape on a phone. The tagline can say since when The Machine reads.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const made=new Date(Date.now()-3600e3).toISOString(),started=new Date(Date.now()-9*864e5).toISOString();
const org={id:'org:research-hub.example',kind:'org',name:'Example Research and Innovation Centre - A deliberately long synthetic organisation name',strength:3,last_at:made,signals:3,role:'Example partner',
 role_by:'owner',links:[],open:{},recent:[],around:[],about:'Research and Development, 2-10 people, Example City',profile:{industry:'Research and Development'}};
const errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],started_at:started}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?org:{projects:[],people:[],quiet:[],new:[],orgs:[org],whos:[]}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [360,390,1280]){
  const context=await browser.newContext({viewport:{width,height:844}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>typeof people==='function'&&state.started_at);
  // Taglines that know the start date join the others.
  const lines=new Set();for(let i=0;i<200;i++)lines.add(await page.evaluate(()=>{newTagline();return document.querySelector('#tagline').textContent}));
  assert.ok([...lines].some(l=>/^Professionally nosy since /.test(l)),'a start-date tagline');
  assert.ok([...lines].some(l=>l==='Day 10 of reading everything. Still not bored.'),'a day-count tagline');
  assert.ok(![...lines].some(l=>/October 9/.test(l)),'no fixed date');
  // On a phone the tabs are in the drawer
  await navClick(page.locator('nav [data-tab="people"]'));
  await page.evaluate(id=>people(id),org.id);
  const edit=page.locator('#people-view .graph-detail .name-row [data-org-edit]');await edit.waitFor();
  const box=await edit.evaluate(b=>{const r=b.getBoundingClientRect(),lh=parseFloat(getComputedStyle(b).lineHeight)||20;return {h:r.height,lh,w:r.width,sw:b.scrollWidth}});
  assert.ok(box.h<box.lh*2.2,`Edit stays one line at ${width}px (${box.h}px)`);assert.ok(box.sw<=Math.ceil(box.w),'Edit text fits');
  const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert.ok(sizes.page<=sizes.viewport,'overflow at '+width);
  if(process.env.TITLE_SHOT)await page.screenshot({path:process.env.TITLE_SHOT+'-'+width+'.png'});
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('title buttons ok');
}finally{await browser?.close();server.close()}
