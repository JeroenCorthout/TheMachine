import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The one-line rows on a person or organisation page (To you, the organisation row, addresses, place, about, links,
// relations) wrap on a phone instead of being cut off, and their Edit and ✓ Right buttons stay on screen and tappable.
// Checked at 360 and 390 with long made-up values. SHOT_DIR=… saves shots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=new Date(Date.now()-864e5).toISOString();
const long='potential acquirer and VP of ecosystem operations for the partner programme across Northwind';
const mira={id:'email:mira.vandenberghe-oyelaran@northwind-logistics.example',kind:'person',name:'Mira Vandenberghe-Oyelaran',strength:2.1,closeness:'close',last_at:day,signals:9,
 ids:['mira.vandenberghe-oyelaran@northwind-logistics.example','+32 470 00 00 00'],usual_gap_days:2,quiet:false,role:long,role_by:'review',
 org:'org:northwind-logistics.example',org_name:'Northwind Logistics and Fulfilment Partners',org_by:'review',org_via:'LinkedIn headline',org_at:day,
 profile:{title:'VP, Platform and Ecosystem Operations for Partner Programmes',company:'Northwind',city:'Saint-Jean-sur-Richelieu',country:'CA',linkedin_url:'https://www.linkedin.com/in/mira-example'},
 relations:[{a:'email:mira.vandenberghe-oyelaran@northwind-logistics.example',b:'email:sam@umbrella.example',a_name:'Mira Vandenberghe-Oyelaran',b_name:'Samuel Reed-Castellanos',relation:'the manager and longtime mentor of',source:'review'}],
 recent:[],around:[],open:{},links:[{id:'email:sam@umbrella.example',kind:'person',name:'Samuel Reed-Castellanos'},{id:'project:p1',kind:'project',name:'Northwind partner programme rollout'}]};
const north={id:'org:northwind-logistics.example',kind:'org',name:'Northwind Logistics and Fulfilment Partners',strength:1.4,last_at:day,signals:3,
 ids:['northwind-logistics.example','northwind-fulfilment-partners.example'],role:'prospect evaluating the whole platform for every regional fulfilment hub',role_by:'review',
 about:'Owner note: talk with them about the partner programme before the regional rollout starts next quarter',
 profile:{about:'Northwind runs regional fulfilment hubs and last-mile delivery for retailers across Europe and North America.',linkedin_url:'https://www.linkedin.com/company/northwind-example',website:'https://northwind-logistics.example'},
 links:[{id:mira.id,kind:'person',name:mira.name}],recent:[],around:[],open:{},people:[]};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');res.end(JSON.stringify(node?[mira,north].find(p=>p.id===node):{projects:[],people:[mira],quiet:[],new:[],orgs:[north]}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
// Every text row on the page shows all of its text inside the card, and every button in it can be tapped.
const measure=page=>page.evaluate(()=>{const card=document.querySelector('#people-view .graph-detail'),edge=card.getBoundingClientRect();
 const rows=[...card.querySelectorAll('p,h3,.person-job,.relation,.graph-links')].filter(e=>e.getBoundingClientRect().height&&!e.closest('[hidden]'));
 const cut=rows.filter(e=>e.scrollWidth>e.clientWidth+1||[...e.querySelectorAll('*')].some(c=>c.getBoundingClientRect().right>edge.right+1||c.scrollWidth>c.clientWidth+1&&getComputedStyle(c).overflow!=='visible')).map(e=>e.textContent.slice(0,60));
 const buttons=[...card.querySelectorAll('button,summary')].filter(b=>b.getBoundingClientRect().height&&!b.closest('[hidden],form,.fact-menu-list,.person-delete'));
 const unreachable=buttons.filter(b=>{const r=b.getBoundingClientRect();if(r.left<0||r.right>innerWidth||r.right>edge.right+1)return true;b.scrollIntoView({block:'center'});const q=b.getBoundingClientRect();
  const hit=document.elementFromPoint(q.left+q.width/2,q.top+q.height/2);return !(hit&&(hit===b||b.contains(hit)))}).map(b=>b.textContent);
 scrollTo(0,0);
 return {rows:rows.length,buttons:buttons.length,cut,unreachable,overflow:document.documentElement.scrollWidth-innerWidth}});
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 if(process.env.SHOT_DIR)await mkdir(process.env.SHOT_DIR,{recursive:true});
 const report={};
 for(const width of [360,390]){
  const context=await browser.newContext({viewport:{width,height:800},deviceScaleFactor:2}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="people"]'));
  await page.locator(`#people-view [data-node="${mira.id}"] button.node`).first().click();
  await page.locator('#people-view .person-page').waitFor();await page.waitForTimeout(300);
  const person=await measure(page);report['person-'+width]=person;
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/person-${width}.png`,fullPage:true});
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph=orgs');
  await navClick(page.locator('nav [data-tab="people"]'));
  await page.locator(`#people-view [data-node="${north.id}"] button.node`).first().click();
  await page.locator('#people-view [data-org-edit]').waitFor();await page.waitForTimeout(300);
  const org=await measure(page);report['org-'+width]=org;
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/org-${width}.png`,fullPage:true});
  for(const [what,m] of [['person',person],['org',org]]){const label=`${what} page at ${width}: ${JSON.stringify(m)}`;
   assert.ok(m.rows>=6&&m.buttons>=3,'the rows and buttons are there, '+label);
   assert.deepEqual(m.cut,[],'no row is cut off, '+label);assert.deepEqual(m.unreachable,[],'every button can be tapped, '+label);
   assert.ok(m.overflow<=0,'no page overflow, '+label)}
  // Edit on the To you row still opens its form.
  await page.locator('#people-view [data-role-edit]').first().click();assert.ok(await page.locator('#people-view .role-form').isVisible(),'Edit opens the To you form at '+width);
  assert.deepEqual(errors,[]);await context.close();
 }
 if(process.env.WRAP_REPORT)console.log(JSON.stringify(report));
 console.log('person wrap dashboard ok');
}finally{await browser?.close();server.close()}
