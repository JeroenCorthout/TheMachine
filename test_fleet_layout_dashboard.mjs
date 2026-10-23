import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Fleet tab layout with a full Fleet, friends and history: one pill per view (the Fleet, Friends, Sharing, Activity),
// one view shown at a time, the choice remembered, every view without sideways scroll (390px and 1000px, light and
// dark), and an open Fleet tab redrawn by the background refresh without losing what you type.
// FLEET_SHOTS=<dir> saves a screenshot of each view.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),FR='fr_'+'b'.repeat(16),at='2026-10-20T08:00:00+00:00';
const rules=()=>({relationships:{rule:'ask',details:false},meeting:{rule:'always',details:true},handling:{rule:'ask',details:false},intro:{rule:'ask',details:false},handoff:{rule:'ask',details:false},brief:{rule:'never',details:false}});
const frules=()=>({relationships:{rule:'ask',details:false},availability:{rule:'always',details:false}});
const view={on:true,ask_meetings:true,brief:{every:'week',tell:false},
 kinds:{relationships:'who knows someone',meeting:'meeting context',handling:'who handles shared emails',intro:'intros',handoff:'handoffs',brief:'brief facts',availability:'when you are busy'},
 kind_rules:Object.fromEntries(['relationships','meeting','handling','intro','handoff','brief','availability'].map(k=>[k,['ask','always','never']])),
 no_details:['handling','brief','availability'],scoped:['relationships','meeting'],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['relationships','meeting','brief'],
   people:[{member_id:'m',name:'Sam Doe',role:'owner',me:true,answers_with:'Self-hosted: Desk',email:'sam@acme.example'},
    {member_id:'p',name:'Pat Long-Lastname',role:'member',answers_with:'Hosted: pat.example.test',email:'pat.long-lastname@acme.example'},
    {member_id:'k',name:'Kim',role:'member',answers_with:'Hosted: kim.example.test',email:'kim@acme.example'}],
   members:[{member_id:'p',name:'Pat Long-Lastname',rules:rules()},{member_id:'k',name:'Kim',rules:rules()}],rules:rules(),
   exceptions:{relationships:[{member_id:'k',name:'Kim',rule:'always',details:true,own_scope:false}]}},
  {id:FR,kind:'friends',label:'Friends',name:'Friends',asks:['relationships','availability'],
   people:[{member_id:'fp_me',name:'Sam Doe',me:true},{member_id:'fp_lou',name:'Lou',label:'Lou (@lou)',answers_with:'Hosted: lou.example.test'}],
   members:[{member_id:'fp_lou',name:'Lou',rules:frules()}],rules:frules(),exceptions:{}}],
 friends:{id:FR,username:'sam',in:[{id:'frq_1',name:'Max (@max)',at}],out:[],blocked:[]},
 briefs:{[FL]:{text:'Your Fleet this week\n\nWent quiet:\n- Globex (globex.example): last touch 2026-09-01 (You)',built_at:at}},
 free:[{id:'abc',fleet:FL,at,question:'Anyone dealt with Stripe disputes?',answers:[{name:'Pat',state:'answered',text:'Yes, twice'}]}],
 intro_offers:[],waiting:[],
 asked:[{id:'a1',fleet:FL,name:'Pat Long-Lastname',question:'Who knows someone at globex.example?',status:'answered',answer:'2 people know them, one well'}],
 log:[{at,direction:'in',name:'Pat',what:'who knows someone at initech.example',outcome:'answered'},{at,direction:'out',name:'Kim',what:'meeting context',outcome:'asked'}],
 notices:[{id:'n1',kind:'joined',text:'Kim joined Acme.',at,buttons:[]}]};
let shown=view,fleetCalls=0;
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(req.url==='/api/action'){res.end('{"ok":true}');return}
 if(req.url.startsWith('/api/fleet')){fleetCalls++;res.end(JSON.stringify(shown));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const shots=process.env.FLEET_SHOTS;
try{
 for(const [scheme,width] of [['dark',390],['light',390],['dark',1000],['light',1000]]){
  shown=view;
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-tabs').waitFor();
  assert.deepEqual(await page.locator('.fleet-tabs [data-fleet-pane]').allInnerTexts(),['Acme','Friends\n1','Sharing','Activity']);
  assert.equal(await page.locator('#fleet-discover').count(),1);  // the slot for suggested connections
  for(const pane of ['fleet-'+FL,'friends','sharing','activity']){
   await page.locator(`[data-fleet-pane="${pane}"]`).click();
   assert.equal(await page.locator('.fleet-pane:not([hidden])').getAttribute('data-pane'),pane);
   assert.equal(await page.locator(`[data-fleet-pane="${pane}"]`).getAttribute('aria-selected'),'true');
   const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&[...document.querySelectorAll('.fleet-pane:not([hidden]) *')].every(e=>e.getBoundingClientRect().right<=innerWidth+.5));
   assert.ok(fits,`${scheme} ${width}px ${pane} scrolls sideways`);
   if(shots){await page.evaluate(()=>window.scrollTo(0,0));await page.locator('#fleet-view').screenshot({path:`${shots}/${pane.replace(/^fleet-.*/,'fleet')}-${scheme}-${width}.png`})}
  }
  // Sharing has every rule: the Fleet's and the friends', the legend once, and the meetings and brief options.
  await page.locator('[data-fleet-pane="sharing"]').click();
  assert.equal(await page.locator('.fleet-legend').count(),1);
  assert.equal(await page.locator(`[data-pane="sharing"] .fleet-rule[data-fleet="${FR}"][data-member="*"]`).count(),2);
  assert.equal(await page.locator('[data-pane="sharing"] #fleet-meetings').count(),1);
  // Remembered: the next visit opens the same view.
  await page.reload();await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-tabs').waitFor();
  assert.equal(await page.locator('.fleet-pane:not([hidden])').getAttribute('data-pane'),'sharing');
  // Live: the background refresh redraws the open tab, and keeps a draft being typed.
  await page.locator('[data-fleet-pane="fleet-'+FL+'"]').click();
  await page.locator('.fleet-free-form input').fill('Anyone know a notary?');
  shown={...view,fleets:[{...view.fleets[0],name:'Acme Inc'},view.fleets[1]]};
  await page.evaluate(()=>document.activeElement.blur());  // while you type it waits; afterwards it redraws, draft kept
  await page.evaluate(()=>fleetSync(true));
  await page.locator('.fleet-pane-head h2',{hasText:/^Acme Inc$/}).waitFor();
  assert.equal(await page.locator('.fleet-free-form input').inputValue(),'Anyone know a notary?');
  assert.deepEqual(errors,[]);
  await page.context().close();
 }
 console.log('fleet layout dashboard: ok');
}finally{await browser.close();server.close()}
