import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Editing an organization whose looked-up values are longer than the owner may type (About, Size) or not a year
// (Founded): they never block Save. Save and Cancel take Edit's place in the header, which stays in view on a phone
// while the form scrolls. Only changed fields are sent; a bad one shows its error under the field.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const made=new Date(Date.now()-3600e3).toISOString();
const about='Example Labs is a synthetic research company that builds reliable, interpretable and steerable example systems for testing. '.repeat(7);
const org={id:'org:labs.example',kind:'org',name:'Example Labs',strength:3,last_at:made,signals:3,links:[],open:{},recent:[],around:[],about:'Research Services, 1001-5000 people',
 profile:{website:'https://www.labs.example/a/rather/long/path/that/a/lookup/service/returned/for/this/company/home',linkedin_url:'https://www.linkedin.com/company/example-labs-research',
  industry:'Research Services',size:'1001-5000 employees, according to the profile the lookup service found',founded:'c. 2021',location:'Example City',about}};
assert.ok(about.length>600&&org.profile.size.length>40,'the looked-up values are longer than the owner may type');
let calls=[];const errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?org:{projects:[],people:[],quiet:[],new:[],orgs:[org],whos:[]}));return}
 if(url.pathname==='/api/action'){let b='';for await(const c of req)b+=c;const call=JSON.parse(b);calls.push(call);
  if(call.args.fields?.location==='server says no'){res.statusCode=400;res.end('{"error":"Location is up to 120 characters"}');return}
  res.end('{"node_id":"org:labs.example","edited":[]}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const shot=process.env.ORG_EDIT_SHOT;let browser;
const inView=loc=>loc.evaluate(b=>{const r=b.getBoundingClientRect(),top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.top>=0&&r.bottom<=innerHeight&&r.width>0&&!!top&&b.contains(top)});
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [390,1440]){
  calls=[];
  const context=await browser.newContext({viewport:{width,height:844}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>typeof people==='function');
  await navClick(page.locator('nav [data-tab="people"]'));
  await page.evaluate(id=>people(id),org.id);
  const head=page.locator('#people-view .graph-detail > .name-row'),edit=head.locator('[data-org-edit]'),save=head.locator('[data-org-save]'),cancel=head.locator('[data-org-cancel]'),form=page.locator('#people-view .org-form');
  await edit.click();
  assert.ok(await form.isVisible(),'the form opens');
  assert.ok(!(await edit.isVisible())&&await save.isVisible()&&await cancel.isVisible(),`Save and Cancel take Edit's place at ${width}px`);
  assert.equal(await form.evaluate(f=>f.checkValidity()),true,'looked-up values never make the form invalid');
  const hint=form.locator('p.graph-sub.muted');assert.ok(await hint.evaluate(p=>p.scrollWidth<=p.clientWidth&&getComputedStyle(p).whiteSpace!=='nowrap'),'the hint wraps, whole');
  if(shot)await page.screenshot({path:`${shot}/open-${width}.png`});
  // Change one field, deep in the form: Save stays in view and sends that field only.
  await form.locator('[name=industry]').fill('Example research');
  await form.locator('[name=about]').scrollIntoViewIfNeeded();await page.mouse.wheel(0,400);await page.waitForTimeout(100);
  assert.ok(await inView(save),`Save stays reachable while scrolled at ${width}px`);
  // Stuck, the header covers the card's full width with square corners, so nothing scrolled under it peeks out.
  const stuck=await head.evaluate(h=>{const a=h.closest('.graph-detail').getBoundingClientRect(),r=h.getBoundingClientRect();return {stuck:h.classList.contains('stuck'),left:r.left-a.left,right:a.right-r.right}});
  assert.ok(stuck.stuck,'the header is stuck at '+width);assert.ok(stuck.left<=1.5&&stuck.right<=1.5,`the header spans the card at ${width}px: ${JSON.stringify(stuck)}`);
  if(shot)await page.screenshot({path:`${shot}/scrolled-${width}.png`});
  await save.click();
  await page.waitForFunction(()=>document.querySelector('#people-view .org-form')?.hidden);
  assert.deepEqual(calls,[{action:'graph_org_edit',args:{node_id:org.id,fields:{industry:'Example research'}}}]);
  assert.ok(await edit.isVisible()&&!(await save.isVisible()),'after Save the header shows Edit again');
  // A Founded that is not a year: its error shows under the field and nothing is sent.
  calls=[];await edit.click();await form.locator('[name=founded]').fill('next year');await save.click();
  assert.equal(await form.locator('#org-err-founded').textContent(),'A year, like 2014');
  assert.equal(await form.locator('[name=founded]').getAttribute('aria-invalid'),'true');
  assert.ok(await inView(form.locator('#org-err-founded')),'the error is in view');
  assert.deepEqual(calls,[]);
  if(shot)await page.screenshot({path:`${shot}/error-${width}.png`});
  // The server refusing a field puts its words under that field too.
  await form.locator('[name=founded]').fill('2021');await form.locator('[name=location]').fill('server says no');await save.click();
  await page.waitForFunction(()=>document.querySelector('#org-err-location').textContent);
  assert.equal(await form.locator('#org-err-location').textContent(),'Location is up to 120 characters');
  assert.equal(await form.locator('#org-err-founded').textContent(),'');
  assert.ok(await form.isVisible(),'the form stays open with the owner’s edits');
  assert.equal(await form.locator('[name=founded]').inputValue(),'2021');
  // Cancel closes without saving, and the next Edit starts from what is saved.
  calls=[];await cancel.click();
  assert.ok(await form.isHidden()&&await edit.isVisible());assert.deepEqual(calls,[]);
  await edit.click();assert.equal(await form.locator('[name=location]').inputValue(),'Example City');assert.equal(await form.locator('#org-err-location').textContent(),'');
  const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert.ok(sizes.page<=sizes.viewport,'overflow at '+width);
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('org edit dashboard ok');
}finally{await browser?.close();server.close()}
