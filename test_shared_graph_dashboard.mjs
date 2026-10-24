import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The Shared Graph on the dashboard (shared_graph.py): Mine / Team on People, a quiet "via <name>", a thread's Team or
// Only you label that changes with one tap, Keep private, the teammates' lines and Ask <name>, and the member prompt on
// the Fleet tab, a member's ask to stop sharing (Approve) and the owner's own Stop sharing my Work, and a teammate's
// thread with its attachments (one to open, one too large to share). No sideways scroll at 390 and 1440.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'c'.repeat(16),day=new Date().toISOString().slice(0,10),at=new Date().toISOString();
const sharing=[{fleet:FL,name:'Acme',admin:true,confirmed:true,prompts:[],off:null,waiting:0,week:null,teammates:1}];
let noTeam=false;  // nobody OK'd yet: nothing from the team, and nothing goes
const TID=FL+':m_ada:'+'e'.repeat(32),FILE='f'.repeat(32);
const thread={subject:'Rollout timeline',at,via:'Ada',messages:[{at,from:'Ada <ada@acme.example>',body:'Here is the plan.',attachments:[{name:'rollout-plan.pdf',size:182000,file:FILE},{name:'all-hands.mp4',size:48000000,too_large:true}]}]};
const lee={id:'email:lee@client.example',kind:'person',name:'Lee Client',strength:3,closeness:'close',last_at:day,signals:2,ids:['lee@client.example'],via:'via Ada'};
const robin={id:'team:'+'d'.repeat(32),kind:'person',name:'Robin Shore',strength:1,closeness:'regular',last_at:day,signals:0,team_only:true,role:'Founder',org_name:'Shore Co',via:'via Ada'};
let share={share:'team',fixed:false,why:'Client'};
const detail=()=>({...lee,links:[],open:{you_promised:[],they_promised:[],cards:[],facts:[]},around:[],keep_private:false,
 recent:[{id:41,kind:'gmail.message',account_id:'work',from:'Lee Client',from_me:false,occurred_at:at,title:'Renewal',preview:'Can you send the quote?',share}],
 team:{via:'via Ada',people:[{fleet:FL,member:'m_ada',name:'Ada',score:2,left:false}],threads:[{id:TID,subject:'Rollout timeline',at,messages:1,via:'Ada'}],timeline:[{at,line:'Lee asked for the renewal quote',via:'Ada'}]}});
const fleet={on:true,kinds:{},kind_rules:{},no_details:[],scoped:[],scopes:{},waiting:[],asked:[],log:[],notices:[],
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:[],people:[{member_id:'me',name:'Sam Doe',role:'owner',me:true,email:'sam@acme.example'},
  {member_id:'m_kim',name:'Kim Lee',role:'member',email:'kim@acme.example'}],members:[],rules:{},exceptions:{}}],
 shared:{prompts:[{id:'p1',kind:'member',fleet:FL,fleet_name:'Acme',name:'Kim Lee',email:'kim@acme.example',fp:'ab12 cd34 ef56 ab12 cd34 ef56'},
  {id:'p2',kind:'optout',fleet:FL,fleet_name:'Acme',name:'Tom Ray',email:'tom@acme.example',fp:'cd34 ef56 ab12 cd34 ef56 ab12'}],shares:[],accounts:[],can_share:[],graph:sharing}};
const calls=[];
const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status')return res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));
 if(url.pathname==='/api/graph'){
  if(url.searchParams.get('node'))return res.end(JSON.stringify(detail()));
  if(url.searchParams.get('thread')===TID)return res.end(JSON.stringify(thread));
  const team=url.searchParams.get('view')==='team';
  return res.end(JSON.stringify({projects:[],quiet:[],new:[],orgs:[],boards:[],sharing,people:team?(noTeam?[]:[lee,robin]):[{...lee,via:undefined}],...(team?{team:true,held:[]}:{})}))}
 if(url.pathname==='/api/fleet')return res.end(JSON.stringify(fleet));
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_share')share={share:call.args.share,fixed:false,why:'By hand'};
  if(call.action==='graph_optout')sharing[0].off=call.args.off?'asked':null;
  return res.end(JSON.stringify({ok:true}))}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 // Before an admin OKs a teammate's Machine: Team says nothing came yet and when sharing starts, not that it already does
 for(const width of [390,1440]){
  noTeam=true;sharing[0].teammates=0;
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph=people');
  await page.locator('[data-graph-team="team"]').click();
  await page.locator('.empty',{hasText:'Nothing from the team yet.'}).waitFor();
  assert.match(await page.locator('.share-note').innerText(),/^Sharing starts once you OK a teammate's Machine\. Personal never\.$/);
  await page.goto('http://127.0.0.1:'+server.address().port+'/#fleet');
  await page.locator('.graph-own').waitFor({state:'attached'});
  assert.match(await page.locator('.graph-own').textContent(),/Your Work goes to the team once you OK a teammate's Machine\./);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  await context.close();
  noTeam=false;sharing[0].teammates=1;
 }
 for(const width of [390,1440]){
  share={share:'team',fixed:false,why:'Client'};calls.length=0;sharing[0].off=null;
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph=people');
  await page.locator('[data-graph-team="team"]').waitFor();
  // Mine has no via; Team has the teammates' people with a quiet "via Ada"
  assert.equal(await page.locator('.via').count(),0);
  await page.locator('[data-graph-team="team"]').click();
  await page.locator('article[data-node]',{hasText:'Robin Shore'}).waitFor();
  assert.match(await page.locator('article[data-node]',{hasText:'Robin Shore'}).innerText(),/via Ada/);
  assert.match(await page.locator('.share-note').innerText(),/^Work is shared with Acme\. Personal never\.$/);
  assert.equal(await page.locator('[data-share-now]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // A record: the thread's label changes with one tap, Keep private, the team's line and Ask Ada
  await page.locator('article[data-node]',{hasText:'Lee Client'}).click();
  const chip=page.locator('[data-share-event="41"]');await chip.waitFor();
  assert.equal((await chip.innerText()).trim(),'Team');
  await chip.click();
  await page.waitForFunction(()=>document.querySelector('[data-share-event="41"]')?.textContent.trim()==='Only you');
  assert.deepEqual(calls.find(c=>c.action==='graph_share'),{action:'graph_share',args:{event_id:41,share:'only_you'}});
  assert.match(await page.locator('.team-block').innerText(),/Lee asked for the renewal quote/);
  await page.locator('[data-keep-private]').first().click();
  await new Promise(r=>setTimeout(r,300));
  assert.equal(calls.some(c=>c.action==='graph_private'&&c.args.node_id===lee.id&&c.args.private===true),true);
  await page.locator('.team-thread summary').click();
  await page.locator('.team-files li').nth(1).waitFor();
  const link=page.locator('.team-files a',{hasText:'rollout-plan.pdf'});
  assert.equal(await link.getAttribute('href'),'api/team-file?thread='+encodeURIComponent(TID)+'&file='+FILE);
  assert.match(await page.locator('.team-files li').nth(1).innerText(),/all-hands\.mp4\s+too large to share/);
  assert.equal(await page.locator('.team-files a').count(),1);
  await page.locator('.team-ask summary').click();
  await page.locator('.team-ask-form button.primary').click();
  await new Promise(r=>setTimeout(r,300));
  const ask=calls.find(c=>c.action==='graph_ask');
  assert.equal(ask?.args.member_id,'m_ada');assert.equal(ask?.args.fleet,FL);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // The Fleet tab: OK for a new member's Machine to get the team's Graph
  await navClick(page.locator('nav [data-tab="fleet"]'));
  await page.locator('.fleet-box h3',{hasText:'Team Graph'}).waitFor();
  const box=page.locator('.fleet-box',{hasText:'Team Graph'});
  assert.match(await box.innerText(),/Kim Lee/);
  assert.match(await box.innerText(),/Tom Ray asks to stop sharing their Work with the team\./);
  await box.locator('[data-share-ok="p2"][data-choice="yes"]').click();
  await new Promise(r=>setTimeout(r,300));
  assert.deepEqual(calls.find(c=>c.action==='fleet_share_ok'),{action:'fleet_share_ok',args:{id:'p2',choice:'yes'}});
  await box.locator('[data-graph-optout]',{hasText:'Stop sharing my Work'}).click();
  await page.locator('[data-graph-optout]',{hasText:'Keep sharing'}).waitFor();
  assert.deepEqual(calls.find(c=>c.action==='graph_optout'),{action:'graph_optout',args:{fleet:FL,off:true}});
  assert.match(await page.locator('.graph-own').innerText(),/Asked an admin to stop sharing your Work\. It stays on until they approve\./);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  await context.close();
 }
}finally{await browser.close();server.close()}
console.log('shared graph dashboard ok');
