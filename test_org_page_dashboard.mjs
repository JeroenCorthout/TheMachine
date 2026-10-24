import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// An organisation's page is built as a person's: logo, name and its fact line in the header with Edit, then one plain
// line on how you're in touch, Contact (the website as its address, "lumen.example ↗"), About you (what it is to you)
// and Related (People, Projects) as their own sections. "Ask the Fleet who knows" uses its name. Edit opens one form
// grouped the same way, What it is to you included, and Save sends only what changed. At 390 and 1440, light and dark.
// SHOT_DIR=… saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()+n*864e5).toISOString();
const fresh=()=>({id:'org:lumen.example',kind:'org',name:'Lumen',domain:'lumen.example',strength:3,last_at:day(-2),signals:4,role:'accounting firm',role_by:'review',
 role_quote:'Lumen does our books',edited:[],wrong:false,about:'Accounting, 11-50 people, Ghent, founded 2009',
 profile:{website:'https://www.lumen.example/en/home',linkedin_url:'https://www.linkedin.com/company/lumen-example',industry:'Accounting',size:'11-50',location:'Ghent',founded:'2009',
  about:'Lumen keeps the books for small companies and their founders.'},
 links:[{id:'email:ines@lumen.example',kind:'person',name:'Ines Varga'},{id:'email:tom@lumen.example',kind:'person',name:'Tom Ridder'},
  {id:'project:1',kind:'project',name:'Year-end closing'},{id:'project:2',kind:'project',name:'VAT return Q3'}],
 recent:[],around:[],open:{}});
let org=fresh();const calls=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?org:{projects:[],people:[],quiet:[],new:[],orgs:[org]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_org_edit')org={...org,name:call.args.name??org.name,profile:{...org.profile,...call.args.fields}};
  if(call.action==='graph_role')org={...org,role:call.args.role,role_by:'owner'};
  res.end('{}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 if(process.env.SHOT_DIR)await mkdir(process.env.SHOT_DIR,{recursive:true});
 for(const [width,height,scheme] of [[390,844,'dark'],[390,844,'light'],[1440,900,'dark'],[1440,900,'light']]){
  org=fresh();calls.length=0;
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,deviceScaleFactor:width<500?2:1}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph/'+encodeURIComponent(org.id));
  const card=page.locator('#people-view .org-page');await card.waitFor();
  const tag=`${width} ${scheme}`;
  // Header: its fact line; then the description and one plain line.
  assert.equal((await card.locator('.person-job').textContent()).trim(),'Accounting, 11-50 people, Ghent, founded 2009',tag);
  assert.equal(await card.locator('.org-about').textContent(),'Lumen keeps the books for small companies and their founders.');
  assert.match(await card.locator('.person-rel').textContent(),/^4 people · last contact \w+, \w+ \d+$/);
  // Sections with headings, in order, as on a person's page.
  assert.deepEqual(await card.locator('.detail-section>h4').allTextContents(),['Contact','About you','People','Projects']);
  assert.deepEqual(await card.locator('.person-contact .kv .k').allTextContents(),['Website','LinkedIn']);
  assert.equal(await card.locator('.person-contact .kv .v').first().textContent(),'lumen.example ↗');
  assert.equal(await card.locator('.person-contact a[href="https://www.lumen.example/en/home"]').count(),1);
  const about=await card.locator('.person-about').textContent();
  assert.ok(about.includes('Accounting firm')&&about.includes('“Lumen does our books”'),about);
  assert.equal(await card.locator('.person-about [data-role-confirm="org:lumen.example"]').count(),1);
  assert.equal(await card.locator('[data-role-edit],.role-form,.name-form').count(),0);
  assert.equal(await card.locator('.person-view > .detail-fleet-ask [data-fleet-ask]').textContent(),'Ask the Fleet who knows Lumen');  // a quiet row in the card, after Related
  assert.equal(await page.locator('#people-view [data-fleet-ask]').count(),1);
  assert.equal(await page.locator('#people-view [data-fleet-ask]').getAttribute('data-fleet-ask'),'lumen.example');
  for(const v of await card.locator('.kv .v').all())assert.ok(await v.evaluate(e=>e.scrollWidth<=e.clientWidth+1),'a row is cut off at '+tag);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll at '+tag);
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/org-${width}-${scheme}.png`,fullPage:true});
  // Edit: one form grouped as the page, Cancel and Save where Edit was.
  await card.locator('[data-org-edit]').click();
  const form=card.locator('.org-form');await form.waitFor();
  assert.ok(await card.locator('[data-org-save]').isVisible()&&!(await card.locator('[data-org-edit]').isVisible()));
  assert.ok(!(await card.locator('.person-view').isVisible()),'the read view steps aside while editing');
  assert.deepEqual(await form.locator('legend').allTextContents(),['Profile','Contact','About you']);
  for(const i of await form.locator('input').all()){const h=await i.evaluate(e=>e.getBoundingClientRect().height);assert.ok(h>=36&&h<=46,'compact fields at '+tag+': '+h)}
  if(width>=1000)assert.ok(await form.locator('[name=founded]').evaluate(e=>e.getBoundingClientRect().width)<=160,'a year field fits a year');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll while editing at '+tag);
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/org-edit-${width}-${scheme}.png`,fullPage:true});
  // Save: the changed detail, and what it is to you, which becomes the owner's word.
  await form.locator('[name=industry]').fill('Accountancy');await form.locator('[name=role]').fill('our accountant');
  await card.locator('[data-org-save]').click();
  for(let i=0;i<100&&calls.length<2;i++)await page.waitForTimeout(20);
  assert.deepEqual(calls,[{action:'graph_org_edit',args:{node_id:'org:lumen.example',fields:{industry:'Accountancy'}}},{action:'graph_role',args:{node_id:'org:lumen.example',role:'our accountant'}}]);
  await page.locator('#toast',{hasText:'Saved.'}).waitFor();
  await card.locator('.person-about b',{hasText:'Our accountant'}).waitFor();
  assert.equal(await card.locator('[data-role-confirm]').count(),0);
  // Only the role: one call.
  calls.length=0;await card.locator('[data-org-edit]').click();await form.locator('[name=role]').fill('accountant');await card.locator('[data-org-save]').click();
  for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);await page.waitForTimeout(100);
  assert.deepEqual(calls,[{action:'graph_role',args:{node_id:'org:lumen.example',role:'accountant'}}]);
  assert.deepEqual(errors,[]);await context.close();
 }
 console.log('org page dashboard ok');
}finally{await browser?.close();server.close()}
