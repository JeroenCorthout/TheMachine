import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Network discovery (discovery.py): a permission card once; a person card under Suggestions only with a concrete
// reason; colleague gets Connect, anyone else Friend or Family; the Fleet tab's Network block has the two switches,
// the quiet list and Leave. Never scrolls sideways (390px and 1000px, dark and light).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FR='fr_'+'b'.repeat(16);
const soon=new Date(Date.now()+3*864e5).toISOString();
const base={on:true,kinds:{},ask_meetings:false,kind_rules:{},no_details:[],scoped:[],scopes:{},
 fleets:[{id:FR,kind:'friends',label:'Friends',name:'Friends',asks:[],people:[{member_id:'fp_me',name:'Sam Doe',me:true}],members:[]}],
 friends:{id:FR,username:'sam',in:[],out:[],blocked:[]},waiting:[],asked:[],log:[],notices:[],splits:[]};
const ask={...base,network:{find:null,discoverable:null,asked:false,last:null,people:[]}};
const found={...base,network:{find:true,discoverable:true,asked:true,last:'2026-10-20T10:00:00Z',people:[
 {email:'nia@nia.example',username:'nia',name:'Nia North',hint:'personal',role:null,relation:null,status:'new',found_at:'x',reasons:[{kind:'meeting',at:soon,title:'Picnic'}]},
 {email:'sam@acme.example',username:'samb',name:'Sam Beam',hint:'colleague',role:null,relation:null,status:'new',found_at:'x',reasons:[{kind:'card',card:'s1',title:'Send the quote'}]},
 {email:'zed@zed.example',username:'zed',name:'Zed Zee',hint:'personal',role:null,relation:null,status:'new',found_at:'x',reasons:[]}],
 addresses:[{email:'sam@work.example',account:true,on:true,findable:true},{email:'sam.doe-with-a-long-address@personal-mail.example',account:false,on:true,findable:true},
  {email:'sam@other.example',account:false,on:false,findable:false}]}};
const calls=[];let shown=ask;
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;calls.push(JSON.parse(body));res.end('{"ok":true}');return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(shown));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 for(const [scheme,width] of [['dark',390],['light',1000]]){
  shown=ask;
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.locator('#net-ask').waitFor();
  assert.match(await page.locator('#net-ask').innerText(),/Which of your contacts have a Machine\?/);assert.match(await page.locator("#net-ask").innerText(),/Check my contacts/);
  calls.length=0;await page.locator('#net-ask [data-net-do="find_on"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_network',args:{do:'find_on',email:'',relation:''}});
  shown=found;await page.reload();await page.locator('#net-nia').waitFor();
  assert.equal(await page.locator('#net-ask').count(),0);
  assert.equal(await page.locator('#net-zed').count(),0);  // no reason, no card
  const nia=await page.locator('#net-nia').innerText();
  assert.match(nia,/Nia North is on the Network/);assert.match(nia,/You meet .*Picnic/);assert.match(nia,/Friend or family/);
  assert.match(await page.locator('#net-samb').innerText(),/Open: Send the quote/);
  assert.equal(await page.locator('#net-samb [data-net-do="connect"]').count(),1);
  calls.length=0;await page.locator('#net-nia [data-net-rel="family"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_network',args:{do:'connect',email:'nia@nia.example',relation:'family'}});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${scheme} ${width}px cards scroll sideways`);
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('[data-fleet-pane="network"]').click();await page.locator('#fleet-network').waitFor();
  const block=page.locator('#fleet-network');
  assert.match(await block.innerText(),/Check which of my contacts have a Machine/);assert.match(await block.innerText(),/Zed Zee/);
  assert.ok(await block.locator('[data-net-toggle="find_off"]').isChecked());
  // Which addresses find them: the account one fixed, each connected one can be turned off
  assert.match(await block.locator('.net-addrs').innerText(),/Findable by/);
  assert.ok(await block.locator('[data-net-addr="sam@work.example"]').isDisabled());
  assert.equal(await block.locator('[data-net-addr="sam@other.example"]').isChecked(),false);
  calls.length=0;await block.locator('[data-net-addr="sam.doe-with-a-long-address@personal-mail.example"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_network',args:{do:'address_off',email:'sam.doe-with-a-long-address@personal-mail.example'}});
  calls.length=0;await block.locator('[data-net-toggle="discoverable_off"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_network',args:{do:'discoverable_off'}});
  calls.length=0;await block.locator('[data-net-do="leave"]').click();await page.waitForTimeout(200);
  assert.deepEqual(calls[0],{action:'fleet_network',args:{do:'leave',email:'',relation:''}});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${scheme} ${width}px Fleet tab scrolls sideways`);
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/network-${scheme}-${width}.png`,fullPage:true});
  assert.deepEqual(errors,[]);
 }
 console.log('ok network dashboard');
}finally{await browser.close();server.close()}
