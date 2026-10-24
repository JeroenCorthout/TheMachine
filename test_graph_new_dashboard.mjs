import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Adding people, organizations and projects by hand: "+ New person/organization/project" in the Graph page heads and in
// the top bar's "+" menu; a short form; ones already in the Graph that look the same are offered first; saving opens the
// new page. Also: a list as CSV, New card and Same person as… on a page, and ⋯ menus that stay on the screen and close
// on Escape. On a phone and a desktop. GRAPH_NEW_SHOT=<dir> writes screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const node=(kind,id,name,extra={})=>({id,kind,name,strength:2,last_at:day(1),signals:3,ids:[],links:[],recent:[],around:[],open:{},...extra});
const ada=node('person','person:a','Ada Lovelace',{ids:['email:ada@umicore.example'],org:'org:umicore.example',role:'Customer',side:'work'});
const umi=node('org','org:umicore.example','Umicore',{domain:'umicore.example',profile:{website:'https://umicore.example'},manual:true});
const nodes={'person:a':ada,'org:umicore.example':umi,'email:rae@umber.example':node('person','email:rae@umber.example','Rae Vos',{manual:true}),'org:named:corner-bakery':node('org','org:named:corner-bakery','Corner Bakery',{manual:true})};
const stages=[{name:'Discovery'},{name:'Demo'},{name:'Won'}];
const boards=[{id:1,name:'Sales',stages,default:true,stuck:0,maybes:[],projects:[]}];
let calls=[];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){const b=JSON.parse(body);calls.push(b);
  const out=b.action==='graph_new'?(b.args.kind==='person'?{node_id:'email:rae@umber.example',name:'Rae Vos',created:true}:{node_id:'org:named:corner-bakery',name:'Corner Bakery',created:true}):{ok:true};
  res.end(JSON.stringify(out));return}
 if(url.pathname==='/api/graph'){const n=url.searchParams.get('node'),q=(url.searchParams.get('q')||'').toLowerCase();
  if(q){res.end(JSON.stringify({found:[ada,umi].filter(x=>x.name.toLowerCase().includes(q)||x.ids.some(i=>i.includes(q))||(x.domain||'').includes(q))}));return}
  res.end(JSON.stringify(n?nodes[n]:{projects:[],people:[ada],quiet:[],new:[],orgs:[umi],whos:[],boards,person_names:[{id:'person:a',name:'Ada Lovelace'},{id:'person:b',name:'Bo Kent'}],project_names:[]}));return}
 res.statusCode=url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shotDir=process.env.GRAPH_NEW_SHOT,errors=[];let browser;
const shot=async(page,name)=>{if(shotDir)await page.screenshot({path:`${shotDir}/${name}.png`})};
const fits=async(page,where)=>{const r=await page.evaluate(()=>{const d=document.querySelector('dialog[open]')?.getBoundingClientRect();return {wide:document.documentElement.scrollWidth>innerWidth,d:d&&{l:d.left,r:d.right},w:innerWidth}});
 assert.ok(!r.wide,where+': wider than the screen');if(r.d)assert.ok(r.d.l>=-1&&r.d.r<=r.w+1,where+': the dialog fits')};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){const mobile=width<600,tag=String(width);calls=[];
  const context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile,locale:'en-GB',acceptDownloads:true}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(tag+': '+e.message));await page.goto(base);await page.waitForFunction(()=>typeof people==='function');
  const click=sel=>mobile?page.locator(sel).first().tap():page.locator(sel).first().click();
  await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
  await page.evaluate(()=>document.querySelector('[data-graph-sub="people"]').click());
  // People: + New person in the head; a look-alike first; clicking it opens that person instead
  const head='#page-head [data-graph-new="person"]';await page.locator(head).waitFor();await fits(page,'people '+tag);await shot(page,`people-head-${tag}`);
  await click(head);await page.locator('#record-dialog[open]').waitFor();assert.equal(await page.locator('#record-dialog-title').textContent(),'New person');
  await page.locator('#record-form [name="name"]').fill('Ada');await page.locator('#record-form [data-rec-open="person:a"]').waitFor();
  assert.equal(await page.locator('#record-form [data-rec-dupes] p').textContent(),'Already in your Graph?');await fits(page,'new person '+tag);await shot(page,`new-person-dupe-${tag}`);
  await click('#record-form [data-rec-open="person:a"]');await page.locator('#record-dialog[open]').waitFor({state:'detached'});
  await page.waitForFunction(()=>graphNode==='person:a');
  // A new person: name required; then name, email and organization; it opens their page
  await page.evaluate(()=>recOpen('person'));await page.locator('#record-dialog[open]').waitFor();
  await click('#record-form button.primary');await page.waitForFunction(()=>document.querySelector('#record-form [data-rec-error]')?.textContent);assert.equal(await page.locator('#record-form [data-rec-error]').textContent(),'Give them a name.');
  await page.locator('#record-form [name="name"]').fill('Rae Vos');await page.locator('#record-form [name="email"]').fill('rae@umber.example');await page.locator('#record-form [name="org"]').fill('Umber Labs');
  await shot(page,`new-person-${tag}`);await click('#record-form button.primary');await page.locator('#record-dialog[open]').waitFor({state:'detached'});
  assert.deepEqual(calls.find(c=>c.action==='graph_new').args,{kind:'person',name:'Rae Vos',email:'rae@umber.example',org:'Umber Labs'});
  await page.waitForFunction(()=>graphNode==='email:rae@umber.example');await page.locator('#people-view .graph-detail h3').waitFor();
  // Same person as… from the ⋯ menu
  await click('#people-view .person-menu summary');await click('#people-view [data-same-as]');await page.locator('#record-dialog[open]').waitFor();
  await page.locator('#record-form [name="name"]').fill('Bo Kent');await click('#record-form button.primary');await page.locator('#record-dialog[open]').waitFor({state:'detached'});
  assert.deepEqual(calls.find(c=>c.action==='graph_same').args,{a:'email:rae@umber.example',b:'person:b',same:true});
  // The "+" in the top bar: New card, person, organization, project and event; New organization opens its form
  // (a desktop has New card in the sidebar and these in the page heads and the command palette)
  if(mobile){await click('#new-card-top');await page.locator('#new-menu:not([hidden])').waitFor();
   assert.deepEqual(await page.locator('#new-menu button').allTextContents(),['New card','New person','New organization','New project','New event']);
   {const r=await page.locator('#new-menu').boundingBox();assert.ok(r.x>=0&&r.x+r.width<=width,'the + menu fits')}await shot(page,`plus-menu-${tag}`);
   await click('#new-menu [data-new-what="org"]')}
  else{await page.evaluate(()=>graphBack('orgs'));await click('#page-head [data-graph-new="org"]')}await page.locator('#record-dialog[open]').waitFor();assert.equal(await page.locator('#record-dialog-title').textContent(),'New organization');
  await page.locator('#record-form [name="website"]').fill('umicore');await page.locator('#record-form [data-rec-open="org:umicore.example"]').waitFor();
  await page.locator('#record-form [name="name"]').fill('Corner Bakery');await page.locator('#record-form [name="website"]').fill('');await shot(page,`new-org-${tag}`);
  await click('#record-form button.primary');await page.waitForFunction(()=>graphNode==='org:named:corner-bakery');
  // An organization added by hand: ⋯ has New card and Delete…
  await page.locator('#people-view .org-page').waitFor();await click('#people-view .org-actions .person-menu summary');
  assert.ok(await page.locator('#people-view [data-org-delete]').isVisible());await click('#people-view [data-card-here]');await page.locator('#new-card').waitFor();
  assert.deepEqual(await page.evaluate(()=>nc.about),[{id:'org:named:corner-bakery',name:'Corner Bakery'}]);await page.keyboard.press('Escape');
  // Organizations and projects heads; New project opens the project dialog
  await page.evaluate(()=>{graphBack('orgs')});await page.locator('#page-head [data-graph-new="org"]').waitFor();
  await page.evaluate(()=>{sideStore.set('projects-view','list');document.querySelector('[data-graph-sub="projects"]').click()});
  await click('#page-head [data-graph-new="project"]');await page.locator('#project-dialog[open]').waitFor();await fits(page,'new project '+tag);await shot(page,`new-project-${tag}`);
  await page.keyboard.press('Escape');await page.locator('#project-dialog[open]').waitFor({state:'detached'});
  // A board shows its own New project, not a second one in the head
  await page.evaluate(()=>projectsViewSet('board'));assert.equal(await page.locator('#page-head [data-graph-new]').count(),0);
  // The board's ⋯ menu stays on the screen and closes on Escape
  await click('#people-view .board-menu summary');{const r=await page.locator('#people-view .board-menu .fact-menu-list').boundingBox();assert.ok(r.x>=0&&r.x+r.width<=width,'the board menu fits '+JSON.stringify(r))}
  await page.keyboard.press('Escape');assert.equal(await page.locator('#people-view .board-menu[open]').count(),0,'Escape closes the menu');
  // People as CSV
  await page.evaluate(()=>document.querySelector('[data-graph-sub="people"]').click());await click('#people-view .graph-sort summary');
  const [dl]=await Promise.all([page.waitForEvent('download'),click('#people-view [data-graph-csv]')]);
  const csv=await readFile(await dl.path(),'utf8');assert.ok(csv.startsWith('﻿Name,Email,Phone,Title,Organization,To you,Type,Last contact\r\nAda Lovelace,ada@umicore.example,'),csv);
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('graph new dashboard ok');
}finally{await browser?.close();server.close()}
