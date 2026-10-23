import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Fleet tab, Family: when everyone is free, to-dos and plans in one compact pane; a member's to-do or plan waits as a
// Family card under Suggestions with Accept and Decline; the Family's rules sit under Sharing. Never scrolls sideways.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),FAM='fl_'+'f'.repeat(16),FL2='fl_'+'c'.repeat(16),FR='fr_'+'b'.repeat(16);
const r=rule=>({rule,details:false});
const view={on:true,kinds:{relationships:'who knows someone',availability:'free/busy times',todo:'to-dos',event:'family plans'},ask_meetings:false,
 kind_rules:{relationships:['ask','always','never'],availability:['ask','always','never'],todo:['ask','always','never'],event:['ask','always','never']},
 no_details:['availability','todo','event'],scoped:[],scopes:{},
 // As the directory lists them: Work first, then friends, then the Family. The tabs go by when each was joined.
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',joined_at:'2026-03-01T09:00:00Z',asks:['relationships'],people:[{member_id:'m',name:'Sam Doe',me:true}],members:[]},
  {id:FL2,kind:'work',label:'Work Fleet',name:'Beta',joined_at:'2026-08-01T09:00:00Z',asks:['relationships'],people:[{member_id:'m2',name:'Sam Doe',me:true}],members:[]},
  {id:FR,kind:'friends',label:'Friends',name:'Friends',asks:[],people:[{member_id:'fp_me',name:'Sam Doe',me:true}],members:[],rules:{},exceptions:{}},
  {id:FAM,kind:'family',joined_at:'2026-05-01T09:00:00Z',label:'Family Fleet',name:'Home',asks:['availability','todo','event'],rules:{availability:r('always'),todo:r('ask'),event:r('ask')},exceptions:{},
   people:[{member_id:'fm_me',name:'Sam Doe',me:true},{member_id:'fm_robin',name:'Robin',answers_with:'Hosted: robin.example.test'},{member_id:'fm_alex',name:'Alex'}],
   members:[{member_id:'fm_robin',name:'Robin',rules:{availability:r('always'),todo:r('ask'),event:r('ask')}},{member_id:'fm_alex',name:'Alex',rules:{availability:r('always'),todo:r('ask'),event:r('ask')}}]}],
 family:{id:FAM,free:[{id:'t1',at:'2026-10-20T10:00:00Z',start:'2026-10-24',days:7,answered:1,asked:2,waiting:['Alex'],
   slots:[{from:'2026-10-24T08:00+02:00',until:'2026-10-24T11:00+02:00',text:'Sat 24 Oct 08:00–11:00'}]}],
  todos:[{id:'t2',title:'Buy milk',name:'Alex',stage:'accepted',due:'2026-10-24'}],
  plans:[{id:'t3',title:'Dinner at grandma’s with a rather long name that should wrap nicely',start:'2026-10-25T18:00+02:00',place:'Grandma’s',yes:['Robin'],no:[],waiting:['Alex'],link:'https://calendar.google.com/calendar/render?action=TEMPLATE&text=Dinner'}],
  mine:[{id:'t4',title:'Pick up Sam',name:'Robin',stage:'accepted',due:'2026-10-21'}]},
 waiting:[{id:'q_'+'1'.repeat(16),fleet:FAM,member_id:'fm_robin',name:'Robin',kind:'todo',question:'To-do: Pick up the kids, due Fri 23 Oct',status:'waiting',at:'2026-10-20T10:00:00Z'}],
 friends:{id:FR,username:'sam',in:[],out:[],blocked:[]},
 asked:[],log:[],notices:[]};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:1}}));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;calls.push(JSON.parse(body));res.end('{"ok":true}');return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 for(const [scheme,width] of [['dark',390],['light',1000]]){
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);
  // A member's to-do waits under Suggestions, as a Family card
  const card=page.locator('#fleet-q_'+'1'.repeat(16));await card.waitFor();
  assert.match(await card.innerText(),/Family/);assert.doesNotMatch(await card.innerText(),/Work/);
  assert.match(await card.innerText(),/Robin asks you to do something/);
  calls.length=0;await card.locator('[data-choice-f="once"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls.at(-1),{action:'fleet_decide',args:{id:'q_'+'1'.repeat(16),choice:'once'}});
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-tabs').waitFor();
  // Every Fleet together, Work and Family, by when it was joined; then Friends, Sharing and Activity
  assert.deepEqual(await page.locator('.fleet-tabs [data-fleet-pane]').allInnerTexts(),['Acme','Home','Beta','Friends','Sharing','Activity']);
  await page.locator('[data-fleet-pane="family"]').click();await page.locator('#fleet-family').waitFor();
  const fam=page.locator('#fleet-family'),text=await fam.innerText();
  for(const want of [/When are we all free/,/Sat 24 Oct 08:00–11:00/,/waiting for Alex/,/Buy milk/,/Pick up Sam/,/In: Robin/,/Add to my calendar/])assert.match(text,want);
  assert.doesNotMatch(text,/Coming later/);
  assert.equal(await fam.locator('a.family-cal').first().getAttribute('href'),view.family.plans[0].link);
  await fam.locator('.family-free-form [name=start]').fill('2026-10-24');await fam.locator('.family-free-form button').click();await page.waitForTimeout(200);
  assert.deepEqual(calls.at(-1),{action:'family_free',args:{start:'2026-10-24',days:7}});
  await fam.locator('.family-todo-form [name=title]').fill('Groceries');await fam.locator('.family-todo-form [name=due]').fill('2026-10-23');await fam.locator('.family-todo-form button').click();await page.waitForTimeout(200);
  assert.deepEqual(calls.at(-1),{action:'family_todo',args:{member_id:'fm_robin',title:'Groceries',due:'2026-10-23'}});
  await fam.locator('.family-event-form [name=title]').fill('Zoo');await fam.locator('.family-event-form [name=start]').fill('2026-10-26T10:00');await fam.locator('.family-event-form button').click();await page.waitForTimeout(200);
  const ev=calls.at(-1);assert.equal(ev.action,'family_event');assert.match(ev.args.start,/^2026-10-26T10:00[+-]\d{2}:\d{2}$/);
  // The Family's rules: under Sharing, the same three pills per kind
  const sharing=page.locator('[data-pane="sharing"]');
  for(const k of ['availability','todo','event'])assert.equal(await sharing.locator(`.fleet-rule[data-fleet="${FAM}"][data-kind="${k}"] [data-fleet-rule]`).count(),3,k);
  const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  assert.ok(fits,`${scheme} ${width}px scrolls sideways`);
  assert.deepEqual(errors,[]);
 }
 console.log('family dashboard: ok');
}finally{await browser.close();server.close()}
