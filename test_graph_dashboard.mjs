import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A person in the Graph tab: their role (with Edit), what's open between you, and signals only probably in a project.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const soon=new Date(Date.now()+2*864e5).toISOString().slice(0,10);
let person={id:'email:ann@client.example',kind:'person',name:'Ann Example',strength:2.4,closeness:'regular',last_at:soon,signals:5,ids:['ann@client.example'],
 usual_gap_days:6,quiet:false,role:'customer',role_by:'review',org:'org:named:globex',org_name:'Globex',org_by:'review',org_via:'LinkedIn headline',
 org_at:'2026-10-15',org_quote:'Engineering at Globex',org_event_id:40,links:[{id:'project:1',kind:'project',name:'Acme renewal',link:'project',strength:0.5}],
 open:{you_promised:[{id:7,who:'Ann',what:'send the renewal quote with the new seat count',due:soon}],they_promised:[{id:8,who:'Ann',what:'sign the order form',due:null}],
       cards:[{id:'0123456789abcdef',title:'Answer Ann about pricing',status:'pending',due:null}],facts:['Ann prefers calls before 10']},
 recent:[{id:41,account_id:'work',kind:'gmail.message',occurred_at:new Date().toISOString(),title:'Seat count for next year',probable:true},
         {id:40,account_id:'work',kind:'gmail.message',occurred_at:new Date(Date.now()-864e5).toISOString(),title:'Renewal',probable:false}],around:[]};
const calls=[],errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?person:{projects:[],people:[person],quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);if(call.action==='graph_person_org')person={...person,org:'org:named:'+call.args.org.toLowerCase(),org_name:call.args.org,org_by:'owner'};if(call.action==='graph_role')person={...person,role:call.args.role,role_by:'owner'};if(call.action==='graph_rename'){person={...person,name:call.args.name};res.end(JSON.stringify({node_id:person.id,name:call.args.name}));return}res.end('{}');return}
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
 await page.locator('nav [data-tab="people"]').click();
 await page.locator('#people-view article[data-node="email:ann@client.example"]').waitFor();
 assert.match(await page.locator('#people-view').textContent(),/Customer · last contact/);
 await page.locator('#people-view article[data-node="email:ann@client.example"] button.node').click();
 await page.locator('.graph-open').waitFor();
 const text=await page.locator('#people-view').textContent();
 assert.ok(text.includes('To you: customer · a review read it in your messages'));
 assert.ok(text.includes('You promised: send the renewal quote')&&text.includes('They promised: sign the order form'));
 assert.ok(text.includes('Card: Answer Ann about pricing')&&text.includes('Fact: Ann prefers calls before 10'));
 assert.equal(await page.locator('.graph-open a[href="#card=0123456789abcdef"]').count(),1);
 assert.ok(text.includes('probably part of it'));
 // Where they work, as their LinkedIn headline states it, linked to the organisation.
 assert.ok(text.includes('At Globex · from their LinkedIn headline')&&text.includes('“Engineering at Globex”'));
 assert.equal(await page.locator('.org-row button.node[data-node="org:named:globex"]').count(),1);
 const widths=[];
 for(const width of [320,390,768]){
  await page.setViewportSize({width,height:844});
  const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
  assert.ok(sizes.page<=sizes.viewport,'overflow at '+width);widths.push({width,...sizes});
  if(process.env.GRAPH_SHOT)await page.screenshot({path:process.env.GRAPH_SHOT+'-'+width+'.png',fullPage:true});
 }
 await page.locator('[data-role-edit]').click();
 await page.locator('.role-form input').fill('CFO at Client Ltd');
 await page.locator('.role-form button').click();
 for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[0],{action:'graph_role',args:{node_id:'email:ann@client.example',role:'CFO at Client Ltd'}});
 await page.locator('.role-row b',{hasText:'CFO at Client Ltd'}).waitFor();
 assert.ok((await page.locator('.role-row').textContent()).includes('To you: CFO at Client Ltd'));
 await page.locator('.graph-open [data-promise="7"][data-close="kept"]').click();
 for(let i=0;i<100&&calls.length<2;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[1],{action:'promise_close',args:{id:7,status:'kept'}});
 // The owner renames them; the detail shows the new name.
 await page.locator('[data-name-edit]').click();
 assert.equal(await page.locator('.name-form input').inputValue(),'Ann Example');
 await page.locator('.name-form input').fill('Ann Exemplar');
 await page.locator('.name-form button').click();
 for(let i=0;i<100&&calls.length<3;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[2],{action:'graph_rename',args:{node_id:'email:ann@client.example',name:'Ann Exemplar'}});
 await page.locator('.graph-detail h3',{hasText:'Ann Exemplar'}).waitFor();
 // The owner sets it in their own words.
 await page.locator('[data-person-org-edit]').click();
 await page.locator('.person-org-form input').fill('Initech');
 await page.locator('.person-org-form button').click();
 for(let i=0;i<100&&calls.length<4;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[3],{action:'graph_person_org',args:{node_id:'email:ann@client.example',org:'Initech'}});
 await page.locator('.org-row',{hasText:'At Initech · you set it'}).waitFor();
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({widths,actions:calls.length,page_errors:errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
