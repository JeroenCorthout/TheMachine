import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Fleet tab, Friends: a request waiting shows Accept, Decline and Block; adding goes by exact username; a friend's
// busy times show as times only; the rules for friends are the Fleets' component: one rule per kind for all friends,
// an exception per friend, examples once (not per friend). Apart from the Work Fleet, and never scrolls sideways (390px, 1280px).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),FR='fr_'+'b'.repeat(16);
const rules=()=>({relationships:{rule:'ask',details:false},meeting:{rule:'ask',details:false},intro:{rule:'ask',details:false},availability:{rule:'ask',details:false}});
const view={on:true,kinds:{relationships:'who knows someone',meeting:'meeting context',intro:'an intro',availability:'when you are busy'},ask_meetings:false,
 kind_rules:{relationships:['ask','always','never'],meeting:['ask','always','never'],intro:['ask','always','never'],availability:['ask','always','never']},
 no_details:['availability'],scoped:[],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['relationships'],people:[{member_id:'m',name:'Sam Doe',role:'owner',me:true}],members:[]},
  {id:FR,kind:'friends',label:'Friends',name:'Friends',asks:['relationships','meeting','intro','availability'],
   people:[{member_id:'fp_me',name:'Sam Doe',me:true},{member_id:'fp_lou',name:'Lou',label:'Lou (@lou)',answers_with:'Hosted: lou.example.test'},{member_id:'fp_kim',name:'Kim',label:'Kim (@kim)',answers_with:'Hosted: kim.example.test'}],
   rules:rules(),exceptions:{availability:[{member_id:'fp_kim',name:'Kim (@kim)',rule:'always',details:false,own_scope:false}]},
   members:[{member_id:'fp_lou',name:'Lou',rules:rules()},{member_id:'fp_kim',name:'Kim',rules:{...rules(),availability:{rule:'always',details:false}}}]}],
 friends:{id:FR,username:'sam',in:[{id:'frq_1',name:'Max (@max)',at:'2026-10-20T10:00:00Z'}],out:[{id:'frq_2',username:'someone',at:'2026-10-20T10:00:00Z'}],blocked:[]},
 waiting:[],asked:[{id:'x1',fleet:FR,name:'Lou',kind:'availability',question:'When are you busy, 2026-10-21?',status:'answered',busy:[['2026-10-21T09:00Z','2026-10-21T10:30Z']]}],log:[],notices:[]};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;calls.push(JSON.parse(body));res.end('{"ok":true}');return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 for(const [scheme,width] of [['dark',390],['light',1280]]){
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('[data-fleet-pane="friends"]').click();await page.locator('#fleet-friends').waitFor();
  assert.deepEqual(errors,[]);
  const friends=page.locator('#fleet-friends');
  assert.match(await friends.innerText(),/Your username: sam/);
  assert.match(await friends.innerText(),/Max \(@max\) wants to be friends/);
  assert.match(await friends.innerText(),/Lou \(@lou\)/);
  assert.match(await friends.innerText(),/Busy: /);
  assert.doesNotMatch(await friends.innerText(),/Work Fleet|Sam Doe/);  // nothing of the Work Fleet, not the owner as a friend
  // The rules per friend live under Sharing, with the Fleets' rules.
  const sharing=page.locator('[data-pane="sharing"]');assert.equal(await friends.locator('.fleet-rule').count(),0);
  for(const k of ['relationships','meeting','intro','availability'])
   assert.equal(await sharing.locator(`.fleet-rule[data-fleet="${FR}"][data-member="*"][data-kind="${k}"] [data-fleet-rule]`).count(),3,k);
  assert.equal(await sharing.locator(`.fleet-rule[data-fleet="${FR}"]`).count(),4);  // one block for all friends, not one per friend
  assert.equal(await sharing.locator('.fleet-rules-head h3').filter({hasText:'your friends'}).innerText(),'What your Machine answers your friends');
  assert.match(await sharing.locator(`.fleet-rule[data-fleet="${FR}"][data-kind="availability"] .fleet-ex`).innerText(),/^Kim \(@kim\): Always/);
  assert.equal(await sharing.locator(`.fleet-rule[data-fleet="${FR}"] .fleet-preview`).filter({hasText:/Lou|Kim/}).count(),0);  // examples say "they"
  assert.doesNotMatch(await sharing.innerText(),/\u2014/);
  // An exception for one friend: picked from the friends without one, saved for that friend only
  await page.locator('[data-fleet-pane="sharing"]').click();calls.length=0;const meet=sharing.locator(`.fleet-rule[data-fleet="${FR}"][data-kind="meeting"]`);
  await meet.locator('.fleet-ex-add summary').click();
  assert.deepEqual(await meet.locator('[data-fleet-ex-member] option').evaluateAll(o=>o.map(x=>x.value)),['fp_lou','fp_kim']);
  await meet.locator('[data-fleet-ex-member]').selectOption('fp_lou');await meet.locator('[data-fleet-ex-rule="never"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_rule',args:{fleet:FR,member_id:'fp_lou',kind:'meeting',rule:'never'}});
  await page.locator('[data-fleet-pane="friends"]').click();
  calls.length=0;
  await friends.locator('[data-friend-do="accept"]').click();await page.waitForFunction(()=>true);
  await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_friend',args:{do:'accept',id:'frq_1',member_id:''}});
  await friends.locator('.friend-add-form input').fill('lou');await friends.locator('.friend-add-form button').click();await page.waitForTimeout(200);
  assert.deepEqual(calls.at(-1),{action:'fleet_friend',args:{do:'add',username:'lou'}});
  await friends.locator('.friend-avail-form [name=start]').fill('2026-10-21');await friends.locator('.friend-avail-form button').click();await page.waitForTimeout(200);
  assert.deepEqual(calls.at(-1),{action:'fleet_availability',args:{member_id:'fp_lou',start:'2026-10-21',days:3}});
  const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  assert.ok(fits,`${scheme} ${width}px scrolls sideways`);
  assert.deepEqual(errors,[]);
 }
 console.log('friends dashboard: ok');
}finally{await browser.close();server.close()}
