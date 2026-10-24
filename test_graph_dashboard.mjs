import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A person in the Graph tab: their role (with Edit), what's open between you, and signals only probably in a project.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const soon=new Date(Date.now()+2*864e5).toISOString().slice(0,10);
let person={id:'email:ann@client.example',kind:'person',name:'Ann Example',strength:2.4,closeness:'regular',last_at:soon,signals:5,ids:['ann@client.example'],
 usual_gap_days:6,quiet:false,role:'customer',role_by:'review',org:'org:named:globex',org_name:'Globex',org_by:'review',org_via:'LinkedIn headline',
 org_at:'2026-10-15',org_quote:'Engineering at Globex',org_event_id:40,links:[{id:'project:1',kind:'project',name:'Acme renewal',link:'project',strength:0.5}],
 open:{you_promised:[{id:7,who:'Ann',what:'send the renewal quote with the new seat count',due:soon}],they_promised:[{id:8,who:'Ann',what:'sign the order form',due:null}],
       cards:[{id:'0123456789abcdef',title:'Answer Ann about pricing',status:'pending',due:null}],known:[{id:5,text:'Ann prefers calls before 10'}]},
 recent:[{id:41,account_id:'work',kind:'gmail.message',occurred_at:new Date().toISOString(),title:'Seat count for next year',probable:true},
         {id:40,account_id:'work',kind:'gmail.message',occurred_at:new Date(Date.now()-864e5).toISOString(),title:'Renewal',probable:false}],around:[]};
const calls=[],errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?person:{projects:[],people:[person],quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);if(call.action==='graph_person_org')person={...person,org:'org:named:'+call.args.org.toLowerCase(),org_name:call.args.org,org_by:'owner'};if(call.action==='graph_role')person={...person,role:call.args.role,role_by:'owner'};if(call.action==='graph_person_edit'){const f=call.args.fields;person={...person,...(f.name?{name:f.name}:{}),...(f.role?{role:f.role,role_by:'owner'}:{}),...(f.org?{org:'org:named:'+f.org.toLowerCase(),org_name:f.org,org_by:'owner'}:{})}}res.end('{}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('nav [data-tab="people"]'));
 await page.locator('#people-view article[data-node="email:ann@client.example"]').waitFor();
 assert.match(await page.locator('#people-view').textContent(),/Customer · last contact/);
 await page.locator('#people-view article[data-node="email:ann@client.example"] button.node').click();
 await page.locator('.graph-open').waitFor();
 const text=await page.locator('#people-view').textContent();
 assert.ok(text.includes('To youCustomerA review read it in your messages'),text);
 assert.ok(text.includes('You promised: send the renewal quote')&&text.includes('They promised: sign the order form'));
 assert.ok(text.includes('Card: Answer Ann about pricing')&&!text.includes('Fact:'));
 // What the owner told about her is no open item: its own quiet section.
 assert.equal(await page.locator('.graph-open',{hasText:'Ann prefers calls'}).count(),0);
 assert.ok((await page.locator('.graph-known').textContent()).includes('Ann prefers calls before 10')&&text.includes('Known about Ann'));
 assert.equal(await page.locator('.graph-open a[href="#card=0123456789abcdef"]').count(),1);
 assert.match(text,/probably part of it/i);
 // Where they work, as their LinkedIn headline states it, linked to the organisation.
 assert.equal(await page.locator('.person-job button.node[data-node="org:named:globex"]').count(),1);
 assert.match(await page.locator('.person-form [name=org]+small').textContent(),/^From their LinkedIn headline, .*: “Engineering at Globex”$/);
 const widths=[];
 for(const width of [320,390,768]){
  await page.setViewportSize({width,height:844});
  const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
  assert.ok(sizes.page<=sizes.viewport,'overflow at '+width);widths.push({width,...sizes});
  if(process.env.GRAPH_SHOT)await page.screenshot({path:process.env.GRAPH_SHOT+'-'+width+'.png',fullPage:true});
 }
 // Edit, in the header: what they are to you, in the owner's words.
 await page.locator('[data-org-edit]').click();
 await page.locator('.person-form [name=role]').fill('CFO at Client Ltd');
 await page.locator('[data-org-save]').click();
 for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[0],{action:'graph_person_edit',args:{node_id:'email:ann@client.example',fields:{role:'CFO at Client Ltd'}}});
 await page.locator('.person-about b',{hasText:'CFO at Client Ltd'}).waitFor();
 await page.locator('.graph-open [data-promise="7"][data-close="kept"]').click();
 for(let i=0;i<100&&calls.length<2;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[1],{action:'promise_close',args:{id:7,status:'kept'}});
 // Closing it refreshes the page, which draws the person anew: open the menu after that, not into a page about to be replaced
 await page.locator('#toast',{hasText:'Marked done.'}).waitFor();await page.waitForFunction(()=>refreshes===0);
 // The owner renames them and sets where they work, in one Save.
 await page.locator('[data-org-edit]').click();
 assert.equal(await page.locator('.person-form [name=name]').inputValue(),'Ann Example');
 await page.locator('.person-form [name=name]').fill('Ann Exemplar');await page.locator('.person-form [name=org]').fill('Initech');
 await page.locator('[data-org-save]').click();
 for(let i=0;i<100&&calls.length<3;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[2],{action:'graph_person_edit',args:{node_id:'email:ann@client.example',fields:{name:'Ann Exemplar',org:'Initech'}}});
 await page.locator('.graph-detail h3',{hasText:'Ann Exemplar'}).waitFor();
 await page.locator('.person-job button.node',{hasText:'Initech'}).waitFor();
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({widths,actions:calls.length,page_errors:errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
