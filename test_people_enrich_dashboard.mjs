import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Work contacts from enrichment: a photo saved on this Machine (never the service's own link; lazy, fixed size, their initials behind it and
// alone when there is none or it fails) and job title on People rows; on their page the title
// beside the photo under their name, city and country, LinkedIn, and Delete permanently with a confirm step. Settings has Look up work contacts.
// SHOT_DIR=… saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=new Date(Date.now()-864e5).toISOString();
const FACE=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#c9b8a6"/><circle cx="32" cy="25" r="12" fill="#7a5c48"/><path d="M10 64c2-14 11-21 22-21s20 7 22 21z" fill="#3d5a80"/></svg>`;
const person=(id,name,profile,extra={})=>({id:'email:'+id,kind:'person',name,strength:2.1,closeness:'regular',last_at:day,signals:6,ids:[id],usual_gap_days:7,
 quiet:false,role:null,org:'org:'+id.split('@')[1],org_name:extra.org_name||null,org_by:'email',profile,links:[],recent:[],around:[],open:{},...extra});
let people=[
 person('lee@globex.example','Lee Parker',{title:'Head of Sales',company:'Globex',company_domain:'globex.example',photo:'api/person-photo?p=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  linkedin_url:'https://www.linkedin.com/in/lee-parker-example',city:'Springfield',country:'US'},{org_name:'Globex',role:'customer',role_by:'owner'}),
 person('rae@initech.example','Rae Quinn',{title:'Chief Technology Officer and Co-founder',company:'Initech',photo:'api/person-photo?p=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',city:'Austin',country:'US'},{org_name:'Initech'}),
 person('mia@hooli.example','Mia Torres',{title:'Partnerships',photo:'https://photos.test/plain.jpg'},{org_name:'Hooli'}),
 person('kai@umbrella.example','Kai Novak',null,{org_name:'Umbrella'}),
];
const calls=[],photoRequests=[],outside=[];let lookups={people_available:true,people:true};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],lookups}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');res.end(JSON.stringify(node?people.find(p=>p.id===node):{projects:[],people,quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_person_delete')people=people.filter(p=>p.id!==call.args.node_id);
  if(call.action==='set_people_lookup'){lookups={...lookups,people:call.args.on};res.end(JSON.stringify(lookups));return}
  res.end('{}');return}
 if(url.pathname==='/api/person-photo'){photoRequests.push(url.searchParams.get('p'));if(url.searchParams.get('p')==='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'){res.setHeader('Content-Type','image/svg+xml');res.end(FACE)}else{res.statusCode=404;res.end()}return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const runs=process.env.SHOT_DIR?[[1440,900,'light'],[1440,900,'dark'],[390,844,'light'],[390,844,'dark']]:[[390,844,'light']];
 for(const [width,height,scheme] of runs){
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,deviceScaleFactor:width<500?2:1}),page=await context.newPage();
  await context.route('**/*',route=>{const u=new URL(route.request().url());
   if(u.hostname!=='127.0.0.1')outside.push(u.href);
   return u.hostname==='127.0.0.1'?route.continue():route.abort()});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="people"]'));
  await navClick(page.locator('.side-sub a[data-graph-sub="people"]'));
  const pic=id=>page.locator(`#people-view article[data-node="email:${id}"] .person-pic`);
  await pic('lee@globex.example').waitFor();
  assert.deepEqual(await pic('lee@globex.example').locator('img').evaluate(i=>[i.getAttribute('referrerpolicy'),i.getAttribute('loading'),i.width,i.height,i.alt]),['no-referrer','lazy',26,26,'']);
  await page.waitForFunction(()=>document.querySelector('article[data-node="email:lee@globex.example"] .person-pic img')?.complete);
  await page.waitForFunction(()=>!document.querySelector('article[data-node="email:rae@initech.example"] .person-pic img'));  // failed: initials stay
  assert.equal((await pic('rae@initech.example').textContent()).trim(),'RQ');
  assert.equal(await pic('mia@hooli.example').locator('img').count(),0);  // the service's own link: never loaded
  assert.equal((await pic('kai@umbrella.example').textContent()).trim(),'KN');
  assert.match(await page.locator('article[data-node="email:lee@globex.example"] .graph-sub').textContent(),/^Head of Sales · Customer · last contact/);
  assert.ok(photoRequests.includes('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')&&!outside.some(u=>u.includes('photos.test')),JSON.stringify({photoRequests,outside}));
  const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);assert.ok(fits,'overflow at '+width);
  if(process.env.SHOT_DIR){await mkdir(process.env.SHOT_DIR,{recursive:true});await page.screenshot({path:`${process.env.SHOT_DIR}/people-${width}-${scheme}.png`})}
  await page.locator('#people-view article[data-node="email:lee@globex.example"] button.node').click();
  await page.locator('.graph-detail .person-pic.big img').waitFor();
  assert.equal(await page.locator('.graph-detail .person-pic.big img').evaluate(i=>i.width),44);
  const text=await page.locator('.graph-detail').textContent();
  assert.equal((await page.locator('.graph-detail .person-job').textContent()).trim(),'Head of Sales · Globex');
  assert.equal(await page.locator('.graph-detail .name-row .person-head h3 + .person-job').count(),1);  // title under the name, beside the photo
  assert.equal(await page.locator('[data-person-lookup]').count(),0);
  assert.ok(await page.locator('.graph-detail [data-person-delete]').isHidden());  // in the ⋯ menu, not on the page
  assert.deepEqual(await page.locator('.graph-detail .name-row .org-actions>button:visible').allTextContents(),['Edit']);  // Edit in the header, as on an organization
  assert.ok(await page.locator('.person-delete-confirm').isHidden());
  if(width>=700){const [head,sub]=await page.evaluate(()=>[document.querySelector('.graph-detail .person-head').getBoundingClientRect().left,document.querySelector('.graph-detail .person-rel').getBoundingClientRect().left]);
   assert.ok(Math.abs(head-sub)<=2,`details line up with the name: ${head} ${sub}`)}
  assert.ok(!text.includes('At Globex'),text);
  assert.equal(await page.locator('.graph-detail .person-job button.node[data-node="org:globex.example"]').count(),1);
  assert.equal(await page.locator('.graph-detail .person-place').textContent(),'Springfield, United States');
  assert.ok(text.includes('linkedin.com/in/lee-parker-example ↗'),text);
  assert.equal(await page.locator('.graph-detail a[href="https://www.linkedin.com/in/lee-parker-example"][rel="noreferrer"]').count(),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.ok(await page.locator('.graph-detail .detail-sub').evaluate(e=>e.scrollWidth<=e.clientWidth),'the contact line wraps, never cut off');
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/person-${width}-${scheme}.png`,fullPage:true});
  assert.deepEqual(errors,[]);
  await context.close();
 }
 // Delete permanently: a confirm step of its own, Cancel backs out, Delete removes them and goes back to People.
 const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:2}),page=await context.newPage();
 await context.route('**/*',route=>{const u=new URL(route.request().url());
  return u.hostname==='127.0.0.1'?route.continue():route.abort()});
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('nav [data-tab="people"]'));
 await navClick(page.locator('.side-sub a[data-graph-sub="people"]'));
 await page.locator('#people-view article[data-node="email:lee@globex.example"] button.node').click();
 const menu=page.locator('.graph-detail .person-menu');
 await menu.locator('summary').click();
 assert.deepEqual(await menu.locator('[role=menuitem]').allTextContents(),['New project','New card','Same person as…','Delete permanently…']);
 if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/menu-390-light.png`});
 await menu.locator('[data-person-delete]').click();
 const ask=page.locator('.person-delete-confirm');
 await ask.waitFor();
 assert.equal((await ask.locator('p').first().textContent()).trim(),'Delete Lee Parker and everything The Machine knows about them? This can’t be undone.');
 assert.ok((await ask.textContent()).includes('Won’t be added again.'));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 if(process.env.SHOT_DIR){await ask.scrollIntoViewIfNeeded();await page.screenshot({path:`${process.env.SHOT_DIR}/delete-confirm-390-light.png`})}
 await ask.locator('[data-person-delete-no]').click();
 assert.ok(await ask.isHidden());
 assert.ok(!calls.some(c=>c.action==='graph_person_delete'));
 await menu.locator('summary').click();
 await menu.locator('[data-person-delete]').click();
 await ask.locator('[data-person-delete-yes]').click();
 await page.waitForFunction(()=>!document.querySelector('.graph-detail')&&document.querySelector('#people-view article[data-node]'));
 assert.deepEqual(calls.at(-1),{action:'graph_person_delete',args:{node_id:'email:lee@globex.example'}});
 assert.equal(await page.locator('#people-view article[data-node="email:lee@globex.example"]').count(),0);
 // Settings, Security: the switch, on by default, saves at once.
 await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="security"]').click();
 const sw=page.locator('#lookup-people');
 await sw.waitFor();
 assert.equal(await sw.getAttribute('aria-checked'),'true');
 if(process.env.SHOT_DIR)await page.locator('#settings-lookups').screenshot({path:`${process.env.SHOT_DIR}/settings-390-light.png`});
 await sw.click();
 await page.waitForFunction(()=>document.querySelector('#lookup-people').getAttribute('aria-checked')==='false');
 assert.deepEqual(calls.at(-1),{action:'set_people_lookup',args:{on:false}});
 await context.close();
 console.log('people enrichment ok');
}finally{await browser?.close();server.close()}
