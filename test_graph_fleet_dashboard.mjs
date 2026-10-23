import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Fleet members in the Graph: a marker on their row and detail ("Has a Machine · Work Fleet"), their Fleet name with
// Keep, See in Fleet and Ask your Fleet; the Fleet tab links back to them. No sideways scroll at 390 and 1280.
// GRAPH_FLEET_SHOTS=<prefix> saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),FR='fr_'+'b'.repeat(16),at='2026-10-20T08:00:00+00:00',day=new Date().toISOString().slice(0,10);
const fleetMark={machine:true,named:true,name:'Kim Lee-Park',in:[{kind:'work',label:'Work Fleet',fleet:FL,fleet_name:'Acme',member_id:'fm_kim'},{kind:'friends',label:'Friend',fleet:FR,fleet_name:'Friends',member_id:'fp_kim'}]};
let kim={id:'email:kim@acme.example',kind:'person',name:'Kim Lee-Park',name_from_messages:'kim',strength:3.2,closeness:'close',last_at:day,signals:9,ids:['Slack','kim@acme.example'],
 role:'software developer',role_by:'review',fleet:fleetMark,links:[],open:{you_promised:[],they_promised:[],cards:[],facts:[]},recent:[],around:[]};
const pat={id:'email:pat@client.example',kind:'person',name:'Pat Client',strength:1.2,closeness:'regular',last_at:day,signals:4,ids:['pat@client.example']};
const rules=()=>({relationships:{rule:'ask',details:false}});
const view={on:true,ask_meetings:false,brief:{every:'off',tell:false},kinds:{relationships:'who knows someone'},kind_rules:{relationships:['ask','always','never']},
 no_details:[],scoped:[],scopes:{},intro_offers:[],waiting:[],asked:[],log:[],notices:[],free:[],briefs:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['relationships','anything'],
   people:[{member_id:'fm_me',name:'Sam Doe',role:'owner',me:true,answers_with:'Self-hosted: Desk',email:'sam@acme.example'},
    {member_id:'fm_kim',name:'Kim Lee-Park',role:'member',answers_with:'Hosted: kim.example.test',email:'kim@acme.example',graph_node:'email:kim@acme.example'}],
   members:[{member_id:'fm_kim',name:'Kim Lee-Park',rules:rules()}],rules:rules(),exceptions:{}}],friends:null};
const calls=[],errors=[];
const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?kim:{projects:[],people:[kim,pat],quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/fleet'){res.end(JSON.stringify(view));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_rename'){kim={...kim,name:call.args.name,fleet:{...fleetMark,named:false}};delete kim.name_from_messages;res.end(JSON.stringify({node_id:kim.id,name:call.args.name}));return}res.end('{}');return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('nav [data-tab="people"]'));
 const row=page.locator('#people-view article[data-node="email:kim@acme.example"]');await row.waitFor();
 assert.equal(await row.locator('.fleet-chip').textContent(),'Fleet · Friend');
 assert.equal(await row.locator('.fleet-chip').getAttribute('title'),'Has a Machine · Work Fleet (Acme) · Friend');
 assert.equal(await row.locator('button.node').evaluate(b=>b.scrollWidth<=b.clientWidth),true);  // the name keeps its room; the chip gives way
 assert.match(await row.locator('.graph-sub').textContent(),/^Has a Machine · Software developer/);
 assert.equal(await page.locator('#people-view article[data-node="email:pat@client.example"] .fleet-chip').count(),0);
 const fits=async label=>{for(const width of [390,1280]){await page.setViewportSize({width,height:844});
  const s=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));assert.ok(s.page<=s.viewport,`overflow at ${width} (${label})`);
  if(process.env.GRAPH_FLEET_SHOTS)await page.screenshot({path:`${process.env.GRAPH_FLEET_SHOTS}-${label}-${width}.png`,fullPage:true})}await page.setViewportSize({width:390,height:844})};
 await fits('list');
 await row.locator('button.node').click();
 await page.locator('.fleet-mark').waitFor();
 assert.equal(await page.locator('.fleet-mark').textContent(),'Work Fleet (Acme) · Friend · Has a Machine');
 assert.match(await page.locator('#people-view').textContent(),/NameFrom your Fleet\. Your messages say “kim”\./);
 assert.equal(await page.locator('[data-fleet-ask]').count(),0);
 await page.setViewportSize({width:320,height:844});for(const sel of ['.fleet-mark','.fleet-named'])assert.ok(await page.locator(sel).evaluate(e=>e.scrollWidth<=e.clientWidth),sel+' wraps');await page.setViewportSize({width:390,height:844});  // no "who knows" for a teammate
 await fits('detail');
 await page.locator('[data-fleet-keep]').click();
 for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[0],{action:'graph_rename',args:{node_id:'email:kim@acme.example',name:'Kim Lee-Park'}});
 await page.waitForFunction(()=>!document.querySelector('[data-fleet-keep]'));
 await page.locator('[data-fleet-member="fm_kim"]').click();
 await page.locator('#fleet-m-fm_kim.flash').waitFor();
 assert.ok(await page.locator('#fleet-m-fm_kim').isVisible());
 await fits('fleet');
 const link=page.locator('#fleet-m-fm_kim [data-graph-open]');  // a quiet muted text link, not a bordered button
 assert.equal(await link.evaluate(e=>e.tagName),'A');assert.equal(await link.getAttribute('aria-label'),'Open Kim Lee-Park in your Graph');
 assert.equal(await link.evaluate(e=>getComputedStyle(e).borderStyle),'none');
 assert.ok(await link.evaluate(e=>e.getBoundingClientRect().height<24),'small');
 await link.click();
 await page.locator('.fleet-mark').waitFor();
 assert.equal(await page.locator('.graph-detail h3').textContent(),'Kim Lee-Park');
 await page.locator('[data-fleet-free]').click();
 await page.waitForFunction(()=>document.activeElement?.closest('.fleet-free-form'));
 assert.deepEqual(errors,[]);
 console.log('graph fleet dashboard ok');
}finally{await browser?.close();server.close()}
