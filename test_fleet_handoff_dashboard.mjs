import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A ready card whose preparation wrote a handoff to a Fleet teammate: its main button is Hand off to them (the Fleet
// form, with that message to read and edit), not a Slack or email send; the message shows as the draft. Once handed
// off, the card waits under Later and offers no Review and send. No network beyond this fake server.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),TEXT='Confirmed P1: the export leaks rows. Can you get someone on this today?';
const view={on:true,kinds:{handoff:'handoffs'},ask_meetings:false,fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['handoff'],people:[],
 members:[{member_id:'k',name:'Kim',rules:{}}],rules:{},exceptions:{}}],waiting:[],asked:[],intro_offers:[],splits:[],log:[],notices:[]};
const handoff={kind:'handoff',label:'Hand off to Kim',revision:'h00000000001',to_email:'kim@acme.example',to_name:'Kim',body:TEXT};
const slack={kind:'slack',label:'Send to Kim',revision:'s00000000001',to_email:'kim@acme.example',to_name:'Kim',body:TEXT,delegate:true,wait_days:1};
const card={id:'c1',title:'Export leaks rows',body:'Prepared a handoff to Kim through The Machine. Nothing sent.',status:'prepared',event_ids:[1],scope:'work',
 created_at:new Date().toISOString(),next_actions:[handoff,slack]};
const state={suggestions:[card],connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='fleet_handoff_draft'){res.end(JSON.stringify({suggestion_id:'c1',title:card.title,summary:TEXT,contact:'',due:null,fleets:[{id:FL,name:'Acme',members:[{member_id:'k',name:'Kim'}]}]}));return}
  if(call.action==='fleet_handoff'){  // what the service does: waiting under Later, sends hidden while it is with them
   Object.assign(card,{status:'snoozed',snoozed_until:new Date(Date.now()+3*864e5).toISOString(),fleet_handoff:{name:'Kim',stage:'asked'},handoff_wait:{name:'Kim'},next_actions:[]});state.suggestion_counts={snoozed:1}}
  res.end(JSON.stringify({status:'answered',ids:{}}));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const page=await (await browser.newContext({viewport:{width:390,height:900}})).newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const c=page.locator('#task-c1');await c.waitFor();
 const primary=c.locator('.actions>button.primary');
 assert.equal(await primary.first().innerText(),'Hand off to Kim');  // the Fleet handoff leads
 assert.equal(await primary.first().getAttribute('data-fleet-handoff'),'c1');  // it opens the form, it never sends
 assert.match(await c.innerText(),/Review and send to Kim/);  // Slack stays as the alternative
 assert.match(await c.locator('.draft').first().innerText(),/To: Kim[\s\S]*Confirmed P1/);  // the message itself
 assert.equal(await c.locator('[data-fleet-handoff]').count(),1);  // no second, generic Hand off
 await primary.first().click();
 const form=page.locator('.fleet-handoff-form');await form.waitFor();
 assert.equal(await form.locator('textarea[name=task]').inputValue(),TEXT);
 await form.locator('button.primary').click();
 await page.waitForTimeout(300);
 assert.deepEqual(calls.at(-1),{action:'fleet_handoff',args:{suggestion_id:'c1',fleet:FL,member_id:'k',title:'Export leaks rows',summary:TEXT,contact:'',sources:[]}});
 // Handed off: under Later, saying to whom, with nothing to send again.
 await page.locator('#later-list>summary').click();
 const later=page.locator('#later-list #task-c1');await later.waitFor();
 assert.match(await later.innerText(),/With Kim · waiting/);
 assert.doesNotMatch(await later.innerText(),/Review and send|Hand off to Kim/);
 assert.equal(await page.locator('#task-c1').count(),1);
 // Later here is when it comes back to check in with them, not a snooze: its line and buttons say so.
 assert.match(await later.locator('.checkin-line').innerText(),/^Check in with Kim \S+ \d\d:\d\d if no answer$/);
 assert.equal(await later.locator('.back-on').count(),0);  // on its own line under the status, not in the source row
 assert.ok(await later.locator('.fleet+.checkin-line').count());
 // A label never wraps: each button is one line high.
 for(const b of await later.locator('.actions>button').all())assert.ok((await b.evaluate(x=>x.scrollWidth<=x.clientWidth+1&&getComputedStyle(x).whiteSpace==='nowrap')),await b.innerText());
 assert.doesNotMatch(await later.innerText(),/Back |Unsnooze|Snoozed/);
 assert.deepEqual(await later.locator('.actions button').evaluateAll(b=>b.map(x=>x.textContent.trim()).filter(Boolean)).then(t=>t.slice(0,3)),['Check in now','Change time','Not needed']);
 assert.equal(await later.locator('.actions [data-choice="accept"]').innerText(),'Check in now');
 const chatLine=await page.evaluate(()=>{const s=state.suggestions[0],d=document.createElement('div');d.innerHTML=chatStatus(s);return d.textContent});
 assert.match(chatLine,/^Check in with Kim \S+ \d\d:\d\d if no answer·Check in now$/);
 assert.deepEqual(errors,[]);
 console.log('fleet handoff dashboard: ok');
}finally{await browser.close();server.close()}
