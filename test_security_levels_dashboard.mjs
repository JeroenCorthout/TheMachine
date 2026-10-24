import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, Security (security_levels.py): Standard or Careful as one switch, Custom after a change, the switches it
// sets, each Work Fleet's level, and Look up work contacts locked off in a Careful Fleet. In the Fleet tab, a Careful
// Fleet's rules have no Always and no With names. 390px and desktop, no sideways scroll.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16);
const STANDARD={notify_text:'on',save_logins:'on',learnings:'on',people_lookup:'on',keep_days:'90'};
const CAREFUL={notify_text:'off',save_logins:'off',learnings:'off',people_lookup:'off',keep_days:'30'};
let security,calls;
const reset=()=>{calls=[];security={level:'standard',settings:{...STANDARD},standard:{...STANDARD},careful:{...CAREFUL},fleets:[{id:FL,name:'Acme',level:'careful'}],floor:true}};
const label=()=>['standard','careful'].find(l=>Object.keys(security.settings).every(k=>security.settings[k]===security[l][k]))||'custom';
const rules={relationships:{rule:'always',details:true},meeting:{rule:'ask',details:false}};
const fleet={on:true,kinds:{relationships:'who knows someone',meeting:'meeting context'},kind_rules:{relationships:['ask','always','never'],meeting:['ask','always','never']},
 no_details:[],scoped:['relationships','meeting'],scopes:{},waiting:[],asked:[],log:[],notices:[],
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',security:'careful',asks:['relationships','meeting'],people:[{member_id:'m',name:'Sam Doe',role:'member',me:true}],
  members:[{member_id:'p',name:'Robin Vale',rules}],rules,exceptions:{}}]};
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],
  security,lookups:{people_available:true,people:security.settings.people_lookup==='on'&&!security.floor,required_off:security.floor},team_learnings:{offer:security.settings.learnings==='on',network:true},fleet:{on:true,waiting:0}}));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='security_level')Object.assign(security.settings,security[call.args.level]);
  if(call.action==='security_setting')security.settings[call.args.key]=call.args.value;
  security.level=label();res.end(JSON.stringify({security,lookups:{people_available:true,people:false,required_off:true},team_learnings:{offer:security.settings.learnings==='on',network:true}}));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(fleet));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const shots=process.env.SHOTS;
try{
 for(const width of [390,1280]){
  reset();
  const page=await (await browser.newContext({viewport:{width,height:900}})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#updated',{hasText:/\d/}).waitFor({state:'attached'});
  await navClick(page.locator('#open-settings'));
  await page.locator('#settings-tabs [data-settings-tab="security"]').click();
  assert.equal(await page.evaluate(()=>location.hash),'#settings/security');
  assert.equal(await page.locator('#help-link').getAttribute('href'),'https://themachine.computer/help/security-levels');
  assert.equal(await page.locator('[name="sec-level"][value="standard"]').isChecked(),true);
  assert.match(await page.locator('#sec-note').innerText(),/^Standard: today’s defaults\./);
  assert.deepEqual(await page.locator('#sec-list strong').allInnerTexts(),['Text in notifications','Offer to save sign-ins','Keep Do it details 90 days']);
  assert.match(await page.locator('#sec-fleets').innerText(),/Acme: Careful, set by its admin\./);
  // Lookups and learnings live here too; a Careful Fleet keeps lookups off.
  assert.equal(await page.locator('#lookup-people').isVisible(),true);assert.equal(await page.locator('#lookup-people').isDisabled(),true);
  assert.equal(await page.locator('#share-offer').isVisible(),true);
  if(shots)await page.screenshot({path:`${shots}/security-standard-${width}.png`,fullPage:true});
  // One switch: Careful, after a confirm that says how many settings change.
  await page.locator('.sec-pick label:has([value="careful"])').click();
  await page.locator('#sec-note',{hasText:/^Careful:/}).waitFor();
  assert.deepEqual(calls.at(-1),{action:'security_level',args:{level:'careful'}});
  assert.equal(await page.locator('[data-sec="notify_text"]').getAttribute('aria-checked'),'false');
  if(shots)await page.screenshot({path:`${shots}/security-careful-${width}.png`,fullPage:true});
  // A change after: Custom.
  await page.locator('[data-sec="save_logins"]').click();
  await page.locator('#sec-note',{hasText:/^Custom:/}).waitFor();
  assert.deepEqual(calls.at(-1),{action:'security_setting',args:{key:'save_logins',value:'on'}});
  assert.equal(await page.locator('[name="sec-level"]:checked').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(shots)await page.screenshot({path:`${shots}/security-custom-${width}.png`,fullPage:true});
  // The Fleet tab: a Careful Fleet has no Always and no With names; the stored Always shows as Ask me first.
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('[data-fleet-pane="sharing"]').click();
  await page.locator('.fleet-careful').waitFor();
  assert.match(await page.locator('.fleet-careful').innerText(),/^Acme is Careful: answers wait for you and leave out names\./);
  const rel=page.locator('.fleet-rule[data-member="*"][data-kind="relationships"]');
  assert.equal(await rel.locator('[data-fleet-rule][value="always"]').isDisabled(),true);
  assert.equal(await rel.locator('[data-fleet-rule][value="ask"]').isChecked(),true);
  assert.equal(await rel.locator('[data-fleet-details][value="1"]').isDisabled(),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(shots)await page.screenshot({path:`${shots}/fleet-careful-${width}.png`,fullPage:true});
  assert.deepEqual(errors,[]);
 }
 console.log('security levels dashboard: ok');
}finally{await browser.close();server.close()}
