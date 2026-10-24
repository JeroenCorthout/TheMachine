import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Share board… on a board's ⋯ menu: a sheet with the Work Fleet's confirmed teammates, one line saying what they get
// (its projects and their threads, deals too; personal never), and Share. The board header says who it's shared with;
// the editor keeps it; Stop sharing takes it back to Only you. On a phone the sheet fits the screen.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const shots=process.env.SHOTS||'';
const day=n=>new Date(Date.now()-n*864e5).toISOString();
const stages=[{name:'First talks'},{name:'Due diligence'},{name:'Offer'},{name:'Stalled or declined',side:true}];
const tile=(i,name,stage,visibility=null)=>({id:'project:'+i,name,stage,reason:'they asked',by:'review',at:day(2),event_id:100+i,next:'Wait',first:true,days:2,no_reply_days:0,org:null,visibility});
const fresh=()=>({id:4,name:'Acquisition conversations',about:'Possible buyers',side:true,stages,stuck:0,visibility:'only_you',shared_with:[],
 projects:[tile(1,'Sale: Northwind','First talks'),tile(2,'Sale: Harbor Labs','Due diligence'),tile(3,'Sale: Pinecrest','Offer'),tile(5,'Sale: Old lead','First talks','only_you')]});
let board=fresh(),calls=[];
const teammates=[{fleet:'fl_1',member:'m_ada',name:'Ada'},{fleet:'fl_1',member:'m_robin',name:'Robin'}];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify({projects:[],people:[],quiet:[],new:[],orgs:[],boards:[board],board_suggestions:[],project_names:[],teammates}));return}
 if(url.pathname==='/api/action'){const c=JSON.parse(body||'{}');calls.push(c);const a=c.args;
  if(c.action==='graph_board_share'){const chosen=teammates.filter(t=>a.people.some(p=>p.fleet===t.fleet&&p.member===t.member));
   board={...board,visibility:chosen.length?'people':'only_you',shared_with:chosen};res.end(JSON.stringify({ok:true,visibility:board.visibility,shared_with:chosen.map(x=>x.name)}));return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height)=>{const mobile=width<600;const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base);
 await page.evaluate(()=>document.querySelector('nav [data-tab="people"]').click());await page.locator('#people-view').waitFor({state:'attached'});
 await page.evaluate(()=>document.querySelector('[data-graph-sub="projects"]').click());await page.locator('#people-view .board-title').first().waitFor();return {context,page}};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const shot=async(page,name,el)=>{if(shots)await (el?page.locator(el):page).screenshot({path:`${shots}/${name}.png`})};
const shareFlow=async(page,w)=>{
 assert.match(await page.locator('.board-sub').textContent(),/^Only you · 4 projects/);
 await page.locator('.board-menu summary').click();await page.locator('[data-board-share]').click();await page.locator('#share-people[open]').waitFor();
 assert.equal(await page.locator('#share-people-title').textContent(),'Share Acquisition conversations');
 assert.deepEqual(await page.locator('.sp-people label').allTextContents(),[' Ada',' Robin']);
 assert.equal(await page.locator('.sp-line').textContent(),'Choose who gets it.');assert.ok(await page.locator('#share-people-form button.primary').isDisabled());
 await page.locator('.sp-people input[value="fl_1|m_robin"]').check();
 assert.equal(await page.locator('.sp-line').textContent(),'Robin gets these 3 projects and their threads, including deals. Personal never goes.');
 await fits(page,w+' sheet');await shot(page,`board-share-sheet-${w}`);
 await page.locator('#share-people-form button.primary').click();await page.locator('#share-people:not([open])').waitFor({state:'attached'});
 const c=calls.at(-1);assert.deepEqual(c,{action:'graph_board_share',args:{board_id:4,people:[{fleet:'fl_1',member:'m_robin'}]}});
 await page.waitForFunction(()=>/^Shared with Robin · 4 projects/.test(document.querySelector('.board-sub')?.textContent||''));
 assert.match(await page.locator('#toast').textContent(),/Shared with Robin\./);
 await fits(page,w+' header');await shot(page,`board-share-header-${w}`);
 // The editor keeps it: a third choice, named
 await page.locator('.board-menu summary').click();await page.locator('[data-board-edit]').click();await page.locator('#board-dialog[open]').waitFor();
 assert.ok(await page.locator('#board-form [name="visibility"][value="people"]').isChecked());assert.match(await page.locator('.ed-seen').textContent(),/Only you\s*Robin\s*Your team/);
 await page.locator('#board-form [data-ed-close]').first().click();
 // Two people, then nobody: Stop sharing, back to Only you
 await page.locator('.board-menu summary').click();await page.locator('[data-board-share]').click();await page.locator('#share-people[open]').waitFor();
 assert.ok(await page.locator('.sp-people input[value="fl_1|m_robin"]').isChecked());
 await page.locator('.sp-people input[value="fl_1|m_ada"]').check();
 assert.equal(await page.locator('.sp-line').textContent(),'Ada and Robin get these 3 projects and their threads, including deals. Personal never goes.');
 await page.locator('.sp-people input[value="fl_1|m_ada"]').uncheck();await page.locator('.sp-people input[value="fl_1|m_robin"]').uncheck();
 assert.equal(await page.locator('.sp-line').textContent(),'Nobody else. It goes back to Only you.');assert.equal(await page.locator('#share-people-form button.primary').textContent(),'Stop sharing');
 await page.locator('#share-people-form button.primary').click();await page.waitForFunction(()=>/^Only you · 4 projects/.test(document.querySelector('.board-sub')?.textContent||''));
 assert.deepEqual(calls.at(-1).args.people,[]);assert.match(await page.locator('#toast').textContent(),/Back to Only you\./);
 board=fresh()};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [w,h] of [[1440,900],[390,844]]){const {context,page}=await open(w,h);await shareFlow(page,w);await context.close()}
 assert.deepEqual(errors,[]);
 console.log('board share dashboard ok');
}finally{await browser?.close();server.close()}
