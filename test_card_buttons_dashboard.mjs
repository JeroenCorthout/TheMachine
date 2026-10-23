import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Card buttons: the main action first, then the two most useful others, the rest under More. A draft's button says it
// opens the preview ("Review and send to …"), Hand off sits with the other buttons, and Not needed is one compact choice
// that hides the card's buttons until it is confirmed or cancelled.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),posts=[];
const cards=[{id:'p1',title:'Reply to Sam about pricing',body:'Sam asked for pricing.',status:'pending',scope:'work',created_at:now,event_ids:[1],source_line:'Source: email from Sam · Mon 19 Oct'},
 {id:'r1',title:'Send Noor the contract',body:'Draft ready.',status:'prepared',scope:'work',created_at:now,preparation_status:'done',event_ids:[2],
  next_actions:[{kind:'email',revision:'v1',label:'Send to Noor',to:['noor@example.com'],to_name:'Noor',subject:'Contract',body:'Hi Noor'}]},
 {id:'h1',title:'Personal errand',body:'Buy a gift.',status:'pending',scope:'personal',created_at:now,event_ids:[3]}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:true,fleets:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname.startsWith('/brand/')){res.statusCode=404;res.end();return}
 if(req.method==='POST'&&body){const b=JSON.parse(body);posts.push({action:b.action,...b.args});
  if(b.action==='fleet_handoff_draft'){res.end(JSON.stringify({suggestion_id:b.args.suggestion_id,fleets:[{id:'f1',name:'Work',members:[{member_id:'m1',name:'Ann Example (ann@example.com)'}]}],title:'Reply to Sam about pricing for the forty seats',summary:'Sam asked for pricing.',contact:'sam@example.com'}));return}}
 res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1100,390]){
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-p1').waitFor();
  const shown=id=>page.locator(`#task-${id} .actions>:is(button,a.open,.more-actions)`).evaluateAll(n=>n.map(x=>x.tagName==='DETAILS'?'More':x.textContent.trim()));
  const menu=id=>page.locator(`#task-${id} .more-menu>button`).evaluateAll(n=>n.map(x=>x.textContent.trim()));
  // A decision: Do it, Snooze, Not needed; the rest under More, Hand off among them, never above the buttons
  assert.deepEqual(await shown('p1'),['Do it','Snooze','Not needed','More']);
  assert.deepEqual(await menu('p1'),['Mark done','Change','Hand off']);
  assert.equal(await page.getByText('Hand off to a teammate').count(),0);
  // A ready draft: the button says it opens the preview first
  assert.deepEqual(await shown('r1'),['Review and send to Noor','Mark done','Snooze','More']);
  assert.deepEqual(await menu('r1'),['Change','Not needed','Hand off']);
  // A personal card has no Hand off
  assert.ok(!(await menu('h1')).includes('Hand off'));
  // The buttons line up: one row height on a wide screen
  if(width>600){const tops=await page.locator('#task-p1 .actions>*').evaluateAll(n=>n.map(x=>Math.round(x.getBoundingClientRect().top)+':'+Math.round(x.getBoundingClientRect().height)));assert.equal(new Set(tops).size,1,tops.join(' '))}
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // More opens a menu; tapping elsewhere closes it
  await page.locator('#task-r1 .more-actions>summary').click();await page.locator('#task-r1 .more-menu').getByRole('button',{name:'Change'}).waitFor();
  await page.mouse.click(2,2);assert.equal(await page.locator('#task-r1 .more-actions').evaluate(d=>d.open),false);
  // The source line's date never splits over two lines
  {const d=page.locator('#task-p1 .card-foot .src-date');assert.equal(await d.innerText(),'Mon 19 Oct');
   assert.equal(await d.evaluate(e=>getComputedStyle(e).whiteSpace),'nowrap');
   assert.equal(await page.locator('#task-p1 .card-foot .read-original').textContent(),'Email from Sam · Mon 19 Oct ↗')}
  // Hand off: the sheet sits below the source line, the task and the note full width, and the card's buttons hide meanwhile
  {await page.locator('#task-p1 .more-actions>summary').click();await page.locator('#task-p1 [data-fleet-handoff]').click();
   const form=page.locator('#task-p1 .fleet-handoff-form');await form.waitFor();
   assert.equal(await page.locator('#task-p1 .actions>[data-choice="accept"],#task-p1 .more-actions').count(),0);
   const box=sel=>form.locator(sel).evaluate(e=>e.getBoundingClientRect().toJSON());const task=await box('textarea[name="task"]'),note=await box('textarea[name="note"]'),sheet=await box(':scope');
   assert.ok(Math.abs(task.width-note.width)<2&&task.width>sheet.width-40,'The task and the note take the sheet’s width');
   const foot=await page.locator('#task-p1 .card-foot').evaluate(e=>e.getBoundingClientRect().bottom),top=(await form.evaluate(e=>e.getBoundingClientRect().top));
   assert.ok(foot<=top,'The source line stays with the card body, above the form');
   await page.locator('#task-p1').screenshot({path:`/tmp/card-handoff-${width}.png`});
   await form.getByRole('button',{name:'Cancel'}).click();await page.locator('#task-p1 [data-choice="accept"]').waitFor()}
  // Not needed: reason chips, a note, Confirm; the card's buttons hide meanwhile
  await page.locator('#task-p1').getByRole('button',{name:'Not needed',exact:true}).click();
  const card=page.locator('#task-p1');await card.getByText('Why isn’t this needed?').waitFor();
  assert.equal(await card.locator('[data-choice="accept"],.more-actions').count(),0);
  await card.getByRole('button',{name:'Wrong facts'}).click();await card.locator('#dismiss-p1').fill('price is old');
  assert.equal(await card.getByRole('button',{name:'Wrong facts'}).getAttribute('aria-pressed'),'true');
  await card.getByRole('button',{name:'Confirm not needed'}).click();
  await page.waitForFunction(()=>true);await new Promise(r=>setTimeout(r,300));
  const saved=posts.filter(p=>p.choice==='reject').at(-1);assert.equal(saved.suggestion_id,'p1');assert.equal(saved.feedback,'Wrong facts: price is old');
  await page.screenshot({path:`/tmp/card-buttons-${width}.png`,fullPage:true});
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('card buttons ok');
