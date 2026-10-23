import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Graph and Connections have sub-tabs like Fleet: a pill row over panes. Links open the right one (#graph=roles,
// #connections=chat, and the old #people and #health), a source that needs attention shows on its sub-tab, section
// heads keep their heading on one line, and every tab has the same column width. Graph's tab counts the roles to review. At 390px the row scrolls with a fade.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),day=now.slice(0,10);
const person=(n,i)=>({id:'email:p'+i+'@example.org',kind:'person',name:n,strength:3-i/10,closeness:'regular',last_at:day,signals:4,ids:['p'+i+'@example.org']});
let graph={people:['Ann Example','Bo Sample','Cy Test'].map(person),quiet:[],new:[],
 projects:[{id:'project:1',kind:'project',name:'Renewal',state:'active',last_at:day,signals:3,people:['Ann Example']}],
 orgs:[{id:'org:example.org',kind:'org',name:'Example Org',signals:2,last_at:day}],warmup:[],
 whos_who:[{...person('Ann Example',0),role:'head of operations',quote:'I run operations here.',event_id:1},{...person('Bo Sample',1),role:'finance lead',quote:null,event_id:null}]};
const conn=(id,service,extra={})=>({id,service,display_name:id,email:'owner@example.org',boundary:'work',last_success:now,state:'{}',...extra});
const connectors=[conn('mail','gmail'),conn('cal','calendar'),conn('team-chat','slack',{error:'slack_auth_failed'}),conn('wa','whatsapp'),conn('phone','android'),conn('crm','salesflare')];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({graph_waiting:graph.whos_who.length,suggestions:[],promises:[],connectors,suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(graph));return}
 if(url.pathname==='/api/events'){res.end('[]');return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const {action}=JSON.parse(body);
  if(action==='graph_role'){graph={...graph,whos_who:graph.whos_who.filter(w=>w.id!==JSON.parse(body).args.node_id)};res.end('{}');return}
  if(action==='browser_logins'){res.end(JSON.stringify({logins:[{site:'crm.example.org',username:'owner'}]}));return}}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
const tabs=(page,row)=>page.locator(row+' [role=tab]').allInnerTexts();
const selected=(page,row)=>page.locator(row+' [role=tab][aria-selected="true"]').innerText();
const rows=page=>page.locator('.side-sub a:not([hidden])').evaluateAll(a=>a.map(x=>x.querySelector('.nl').textContent+(x.querySelector('.badge').textContent?'\n'+x.querySelector('.badge').textContent:'')));
const lit=page=>page.locator('.side-sub a[aria-current="page"] .nl').textContent();
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 // Graph: its panes are rows in the sidebar (no pills on the page): People, Organisations, Projects, and Who's who with
 // its count last. The Graph row counts it too while the rows are folded away.
 await page.locator('#badge-people',{hasText:/^2$/}).waitFor({state:'attached'});
 await navClick(page.locator('nav [data-tab="people"]'));
 await page.locator('[data-graph-panel="people"]:not([hidden])').waitFor();assert.equal(await page.locator('#people-view .sub-tabs').count(),0);
 assert.deepEqual((await rows(page)).map(r=>r.replace(/\n\d+$/,m=>m)),['People','Organisations','Projects','Who’s who\n2']);
 assert.equal(await lit(page),'People');assert.equal(await page.locator('#page-head .page-title').textContent(),'People');
 assert.equal(await page.locator('#people-view .whos').first().isVisible(),false);
 await navClick(page.locator('.side-sub a[data-graph-sub="roles"]'));
 await page.locator('#people-view .whos').first().waitFor();
 assert.equal(await page.locator('#people-view [data-graph-panel="people"]').isHidden(),true);
 // The Who's who head: heading and count pill on one line, the short explainer on its own line below.
 const head=await page.locator('[data-graph-panel="roles"] .section-head').evaluate(e=>{const h=e.querySelector('h2'),c=e.querySelector('.count'),p=e.querySelector('p'),hr=h.getBoundingClientRect(),cr=c.getBoundingClientRect(),pr=p.getBoundingClientRect();
  return {lines:Math.round(hr.height/parseFloat(getComputedStyle(h).lineHeight)),inline:cr.top<hr.bottom&&cr.left>hr.left,below:pr.top>=hr.bottom-1,text:p.textContent}});
 assert.deepEqual(head,{lines:1,inline:true,below:true,text:'Roles your Machine picked up. Confirm or edit; it learns from your edits.'});
 // The choice is kept; a link picks one.
 await page.goto(base);await navClick(page.locator('nav [data-tab="people"]'));await page.locator('[data-graph-panel="roles"]:not([hidden])').waitFor();
 assert.equal(await lit(page),'Who’s who');assert.equal(await page.locator('#page-head .page-sub').textContent(),'2 roles to confirm');
 await page.goto(base+'/#graph=people');await page.locator('[data-graph-panel="people"]:not([hidden])').waitFor();
 await page.goto(base+'/#graph=roles');await page.locator('[data-graph-panel="roles"]:not([hidden])').waitFor();
 // Confirming one drops both counts.
 page.once('dialog',d=>d.accept());await page.locator('[data-graph-panel="roles"] [data-role-confirm]').first().click();
 await page.locator('#badge-people',{hasText:/^1$/}).waitFor({state:'attached'});await page.locator('#count-roles',{hasText:/^1$/}).waitFor({state:'attached'});
 await page.goto(base+'/#graph=projects');await page.locator('[data-graph-panel="projects"]:not([hidden])').waitFor();
 assert.equal(await page.locator('#people').isVisible(),true);assert.equal(await page.evaluate(()=>location.hash),'#graph=projects');  // the pane stays in the address
 await page.goto(base+'/#people');await page.locator('#people:not([hidden])').waitFor();
 // Connections: sources by kind; the one with a failing source shows its count and opens first.
 await page.goto(base);await navClick(page.locator('nav [data-tab="health"]'));await page.locator('#health-tabs [role=tab]').first().waitFor();
 for(let i=0;i<100&&(await tabs(page,'#health-tabs')).length<6;i++)await page.waitForTimeout(20);
 assert.deepEqual(await tabs(page,'#health-tabs'),['Mail and calendar','Chat\n1','Other sources','+ Add','Sign-ins\n1','Reviews']);
 assert.equal(await page.locator('#health-tabs [data-health-pane="chat"] .badge.warn').count(),1);
 assert.equal(await selected(page,'#health-tabs'),'Chat\n1');
 assert.deepEqual(await page.locator('#connectors>article:not([hidden]) h3').allInnerTexts(),['team-chat','wa']);
 await page.locator('#health-tabs [data-health-pane="signins"]').click();
 await page.locator('#saved-logins-block').waitFor();assert.equal(await page.locator('#connectors>article:not([hidden])').count(),0);
 assert.equal(await page.locator('#review-summary').isVisible(),false);
 await page.goto(base+'/#connections=reviews');await page.locator('#review-summary').waitFor({state:'attached'});
 for(let i=0;i<100&&await selected(page,'#health-tabs').catch(()=>'')!=='Reviews';i++)await page.waitForTimeout(20);
 assert.equal(await selected(page,'#health-tabs'),'Reviews');
 // Android is in Other sources; the old phone link opens it.
 await page.goto(base+'/#connections=phone');await page.locator('#connectors>article:not([hidden]) h3',{hasText:'phone'}).waitFor();
 assert.deepEqual(await page.locator('#connectors>article:not([hidden]) h3').allInnerTexts(),['phone','crm']);
 await page.goto(base+'/#health');await page.locator('#health:not([hidden])').waitFor();
 // One column for every tab.
 const widths=[];
 for(const t of ['inbox','events','people','feedback','health']){await page.locator(`nav [data-tab="${t}"]`).click();await page.waitForTimeout(100);widths.push(await page.locator('#'+t).evaluate(e=>Math.round(e.getBoundingClientRect().width)))}
 assert.equal(new Set(widths).size,1,widths.join());
 // A phone: the sub-tab row scrolls sideways inside itself, with a fade on the side that has more.
 await page.setViewportSize({width:390,height:844});await page.goto(base+'/#connections=mail');await page.locator('#health-tabs [role=tab]').first().waitFor();
 for(let i=0;i<100&&!(await page.locator('#health-tabs .sub-tabs.more-right').count());i++)await page.waitForTimeout(20);
 assert.equal(await page.locator('#health-tabs .sub-tabs.more-right').count(),1);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#health-tabs [data-health-pane="reviews"]').click();
 for(let i=0;i<100&&!(await page.locator('#health-tabs .sub-tabs.more-left').count());i++)await page.waitForTimeout(20);
 assert.equal(await page.locator('#health-tabs .sub-tabs.more-left').count(),1);  // the chosen one scrolled into view
 assert.deepEqual(errors,[]);
 console.log('sub-tabs dashboard ok');
}finally{await browser?.close();server.close()}
