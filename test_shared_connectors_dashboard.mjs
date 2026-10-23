import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Shared connectors on the Fleet tab (shared_connectors.py): an admin shares Intercom with the members they tick, says OK
// to a later joiner, confirms an admin, replaces the token after someone left; a member's Connections card says who
// shares it, with no Reconnect or Disconnect. Never scrolls sideways (390px and 1440px).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16);
const base=()=>({on:true,kinds:{},kind_rules:{},no_details:[],scoped:[],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:[],people:[{member_id:'m',name:'Sam Doe',role:'owner',me:true,email:'sam@acme.example'},
  {member_id:'a',name:'Ada Park',role:'admin',email:'ada@acme.example'},{member_id:'k',name:'Kim Lee',role:'member',email:'kim@acme.example'}],
  members:[{member_id:'a',name:'Ada Park',rules:{}},{member_id:'k',name:'Kim Lee',rules:{}}],rules:{},exceptions:{}}],waiting:[],asked:[],log:[],notices:[],
 shared:{prompts:[],shares:[],accounts:[{id:'intercom',service:'intercom',label:'Intercom',workspace:'Acme Support'}],
  can_share:[{fleet:FL,name:'Acme',members:[{member_id:'a',name:'Ada Park',email:'ada@acme.example',role:'admin',keys:true,confirmed:true},
   {member_id:'k',name:'Kim Lee',email:'kim@acme.example',role:'member',keys:true,confirmed:false}]}]}});
let view=base();
const calls=[];
const connectors=[{id:'intercom-acme',service:'intercom',display_name:'Acme Support',boundary:'work:Acme',total:12,signals_24h:2,pending:0,shared_by:'Acme',last_success:new Date().toISOString(),sync_activity:[]}];
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(brand[req.url]){res.setHeader('Content-Type',req.url.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[req.url],import.meta.url)));return}
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url.startsWith('/api/status'))return res.end(JSON.stringify({suggestions:[],connectors,suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='fleet_share'){view.shared.shares=[{fleet:FL,fleet_name:'Acme',service:'intercom',label:'Intercom',workspace:'Acme Support',by:'Sam Doe',held_by:[],mine:true,have:true,admin:true}];view.shared.accounts=[];return res.end(JSON.stringify({ok:true,shared_with:2}))}
  return res.end(JSON.stringify({ok:true,done:true}))}
 if(req.url.startsWith('/api/fleet'))return res.end(JSON.stringify(view));
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const shots=process.env.SHARED_SHOTS;
try{
 for(const width of [390,1440]){
  view=base();calls.length=0;
  const page=await (await browser.newContext({viewport:{width,height:900}})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-box h3',{hasText:'Shared connectors'}).waitFor();
  assert.deepEqual(errors,[]);
  // Sharing: confirmed admins get it (ticked, fixed); members are ticked by the admin
  await page.locator('.share-new summary').click();
  assert.match(await page.locator('.share-form .muted').first().innerText(),/read permissions only/);
  assert.equal(await page.locator('.share-pick input:disabled:checked').count(),1);
  await page.locator('.share-pick input[value="k"]').check();
  if(shots)await page.screenshot({path:`${shots}/oss-share-form-${width}.png`,fullPage:true});
  await page.locator('.share-form button.primary').click();
  await page.locator('.fleet-box article',{hasText:'Shared by Sam Doe'}).waitFor();
  // The toast sits at the bottom and never takes a click meant for a button under it
  assert.equal(await page.locator('#toast').evaluate(t=>getComputedStyle(t).pointerEvents),'none');
  assert.ok(await page.locator('#toast').evaluate(t=>innerHeight-t.getBoundingClientRect().bottom<60));
  assert.doesNotMatch(await page.locator('.fleet-box').filter({hasText:'People'}).first().innerText(),/Nobody else yet/);
  assert.equal(await page.locator('.topbar img, .app-side img.avatar').first().evaluate(i=>i.complete&&i.naturalWidth>0),true);
  assert.deepEqual(calls.at(-1),{action:'fleet_share',args:{fleet:FL,account:'intercom',members:['k']}});
  // Prompts: a later joiner, an unconfirmed admin, a replace after someone left
  view.shared.prompts=[{id:'p1',kind:'recipient',fleet:FL,fleet_name:'Acme',service:'intercom',name:'Robin Vale',email:'robin@acme.example',fp:'ab12 cd34 ef56 ab12 cd34 ef56'},
   {id:'p2',kind:'admin',fleet:FL,fleet_name:'Acme',service:'',name:'Ada Park',email:'ada@acme.example',fp:'1234 5678 9abc 1234 5678 9abc'},
   {id:'p3',kind:'replace',fleet:FL,fleet_name:'Acme',service:'intercom',name:'Mo Ray'}];
  view.shared.shares.push({fleet:FL,fleet_name:'Acme',unconfirmed:['Ada Park']});
  await page.evaluate(()=>fleetView());
  await page.locator('#share-p1').waitFor();
  assert.match(await page.locator('#share-p1').innerText(),/Share Intercom with Robin Vale's Machine\?/);
  assert.match(await page.locator('#share-p2').innerText(),/Confirm Ada Park as an admin of Acme\?/);
  assert.match(await page.locator('#share-p3').innerText(),/Mo Ray left Acme\. Replace the Intercom token so it stops working on their Machine\./);
  assert.match(await page.locator('.fleet-box').filter({hasText:'Shared connectors'}).innerText(),/Not confirmed yet: Ada Park/);
  await page.locator('#toast.show').waitFor({state:'detached'}).catch(()=>{});await page.waitForFunction(()=>!document.querySelector('#toast.show'));
  if(shots)await page.screenshot({path:`${shots}/oss-share-prompts-${width}.png`,fullPage:true});
  await page.locator('#share-p1 [data-choice="yes"]').click();
  await page.waitForFunction(()=>true);
  await page.locator('#share-p3 input[name=token]').fill('replacement-token-for-tests');
  await page.locator('#share-p3 button.primary').click();
  await page.waitForTimeout(200);
  assert.ok(calls.some(c=>c.action==='fleet_share_ok'&&c.args.id==='p1'&&c.args.choice==='yes'));
  assert.ok(calls.some(c=>c.action==='fleet_share_replace'&&c.args.token==='replacement-token-for-tests'));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // A member's Connections: who shares it, nothing to change
  const card=await page.evaluate(c=>connActions(c),connectors[0]);
  assert.match(card,/Shared by Acme/);assert.doesNotMatch(card,/Disconnect|Reconnect/);
  // Alone in the Fleet: the People box says so
  view.fleets[0].people=view.fleets[0].people.filter(p=>p.me);view.fleets[0].members=[];await page.evaluate(()=>fleetView());
  await page.locator('.fleet-box',{hasText:'Nobody else yet'}).waitFor();
  assert.deepEqual(errors,[]);
  await page.context().close()}
 console.log('shared connectors dashboard: ok');
}finally{await browser.close();server.close()}
