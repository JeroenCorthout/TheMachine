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
const later=new Date(Date.now()+20*3600e3).toISOString();
// an element screenshot that waits out a re-render of the pane
const fleetPaneReload=async page=>{await page.evaluate(async()=>{await fleetView(true);const p=document.querySelector('[data-shared-fleet]');if(p)await sharedLoad(p.dataset.sharedFleet,true)})};
const shot=async(loc,path)=>{for(let i=0;;i++){try{return await loc.screenshot({path})}catch(err){if(i>4)throw err;await new Promise(r=>setTimeout(r,400))}}};
const sharing=[{fleet:FL,name:'Acme',admin:true,confirmed:true,prompts:[],off:null,waiting:0,week:null,teammates:1,started:false,paused:null,require:['mail'],
 connections:[{id:'work',service:'gmail',email:'olive@acme.example',kind:'mail',work:true,company:true,personal:false,required:true,on:true},{id:'cal',service:'calendar',email:'olive@acme.example',kind:'calendar',work:true,company:true,personal:false,required:false,on:true},
  {id:'side',service:'gmail',email:'olive@sideco.example',kind:'mail',work:true,company:false,personal:false,required:false,on:false},
  {id:'home',service:'gmail',email:'olive@home.example',kind:'mail',work:false,company:false,personal:true,required:false,on:false}],
 people:[{member:'m_kim',name:'Kim Lee',state:'not_started'},{member:'m_tom',name:'Tom Ray',state:'sharing'},{member:'m_old',name:'Pat Old',state:'removed'}],
 new_devices:[{member:'m_kim2',name:'Kim Lee',fp:'ef56 ab12 cd34 ef56 ab12 cd34',from:later}]}];
const T=(id,title,company,why,view='team')=>({event_id:id,title,with:company,company,at,view,share:'team',why});
const overview={sorted:{client:1113,sensitive:265,internal:41,personal:262,unsure:12,other:138},unsorted:4,shared:1100,waiting:3,held:265,sent:0,more:0,
 threads:[T(51,'Renewal quote for 40 seats','Northwind','Goes when you start: client thread with Northwind'),T(52,'Onboarding call notes','Globex','Goes when you start: client thread with Globex','wait')],
 unsure:[T(61,'Quick question about invoices','Umbrella','Only you: not sure it\'s client work','ask'),T(62,'Intro via a mutual friend','Hooli','Only you: not sure it\'s client work','ask')],unsure_more:0,
 by_hand:[{...T(71,'Board pack draft','Vandelay','Only you: you chose this','private'),share:'only_you'}],records:[{node_id:'org:initech',name:'Initech',kind:'org'}],
 rules:[{key:'@stark.example',choice:'private',name:'Stark',at}],preview:{threads:1113,orgs:[{name:'Northwind',key:'@northwind.example',threads:84},{name:'Globex',key:'@globex.example',threads:61},{name:'Umbrella',key:'@umbrella.example',threads:12}],more:212}};
let noTeam=false;  // nobody OK'd yet: nothing from the team, and nothing goes
const TID=FL+':m_ada:'+'e'.repeat(32),FILE='f'.repeat(32);
const thread={subject:'Rollout timeline',at,via:'Ada',messages:[{at,from:'Ada <ada@acme.example>',body:'Here is the plan.',attachments:[{name:'rollout-plan.pdf',size:182000,file:FILE},{name:'all-hands.mp4',size:48000000,too_large:true}]}]};
const lee={id:'email:lee@client.example',kind:'person',name:'Lee Client',strength:3,closeness:'close',last_at:day,signals:2,ids:['lee@client.example'],via:'via Ada'};
const robin={id:'team:'+'d'.repeat(32),kind:'person',name:'Robin Shore',strength:1,closeness:'regular',last_at:day,signals:0,team_only:true,role:'Founder',org_name:'Shore Co',via:'via Ada'};
let share={share:'team',fixed:false,why:'Client'};
const detail=()=>({...lee,links:[],open:{you_promised:[],they_promised:[],cards:[],facts:[]},around:[],keep_private:false,
 recent:[{id:41,kind:'gmail.message',account_id:'work',from:'Lee Client',from_me:false,occurred_at:at,title:'Renewal',preview:'Can you send the quote?',share}],
 team:{via:'via Ada',people:[{fleet:FL,member:'m_ada',name:'Ada',score:2,left:false}],threads:[{id:TID,subject:'Rollout timeline',at,messages:1,via:'Ada'},{id:FL+':m_ada:'+'a'.repeat(32),subject:'Due diligence call',at,messages:1,via:'Ada',kind:'meetings'},{id:FL+':m_ada:'+'b'.repeat(32),subject:'Can we test the API?',at,messages:3,via:'Ada',kind:'intercom'}],timeline:[{at,line:'Lee asked for the renewal quote',via:'Ada'}]}});
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
  if(url.searchParams.get('view')==='shared'){assert.equal(url.searchParams.get('fleet'),FL);return res.end(JSON.stringify(overview))}
  const team=url.searchParams.get('view')==='team';
  return res.end(JSON.stringify({projects:[],quiet:[],new:[],orgs:[],boards:[],sharing,people:team?(noTeam?[]:[lee,robin]):[{...lee,via:undefined}],...(team?{team:true,held:[]}:{})}))}
 if(url.pathname==='/api/fleet')return res.end(JSON.stringify(fleet));
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_share'){share={share:call.args.share,fixed:false,why:'By hand'};if(call.args.event_id===41&&call.args.share==='only_you')return res.end(JSON.stringify({ok:true,suggest:{key:'@client.example',name:'Client Co',choice:'private'}}))}
  if(call.action==='graph_start')sharing[0].started=true;
  if(call.action==='graph_connection')sharing[0].connections.find(c=>c.id===call.args.account).on=call.args.on;
  if(call.action==='graph_policy')sharing[0].require=call.args.require;
  if(call.action==='graph_pause')sharing[0].paused=call.args.on?{by:'Ada',at:'2026-10-24T09:00:00+00:00',removed:!!call.args.remove}:null;
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
  assert.match(await page.locator('.share-note').innerText(),/^Sharing starts once you OK a teammate's Machine\. Personal never\. See what's shared$/);
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
  // the card that asks each person to start: what goes, what never does, what the Fleet requires
  await page.goto('http://127.0.0.1:'+server.address().port+'/');
  const startCard=page.locator('#fleet-start-'+FL);
  await startCard.waitFor();
  assert.match(await startCard.innerText(),/Share your Work with Acme\?\s+Acme shares a team Graph\. Your Work contacts and client threads go to your teammates' Machines\. Personal, sensitive and automated mail never does\.\s+Acme requires your Work email\. You can still keep any thread private\.\s+Look over what goes first\. Nothing goes until you start\.\s+Start now\s+Look it over/);
  await shot(startCard,(process.env.SHOTS||'/tmp')+'/shared-start-card-'+width+'.png');
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph=people');
  await page.locator('[data-graph-team="team"]').waitFor();
  // Mine has no via; Team has the teammates' people with a quiet "via Ada"
  assert.equal(await page.locator('.via').count(),0);
  await page.locator('[data-graph-team="team"]').click();
  await page.locator('article[data-node]',{hasText:'Robin Shore'}).waitFor();
  assert.match(await page.locator('article[data-node]',{hasText:'Robin Shore'}).innerText(),/via Ada/);
  assert.match(await page.locator('.share-note').innerText(),/^Not shared with Acme yet\. Nothing goes until you start\. See what's shared$/);
  assert.equal(await page.locator('[data-share-now]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // A record: the thread's label changes with one tap, Keep private, the team's line and Ask Ada
  await page.locator('article[data-node]',{hasText:'Lee Client'}).click();
  const chip=page.locator('[data-share-event="41"]');await chip.waitFor();
  assert.equal((await chip.innerText()).trim(),'Team');
  // the marker says why, and switches in its own small panel; a pattern asks once to make it a rule
  await chip.click();
  await page.locator('#share-pop').waitFor();
  assert.match(await page.locator('#share-pop').innerText(),/^Client\s+Keep private\s+See what's shared$/);  // before the first share nothing went, so nothing to unshare
  if(width===390)await page.screenshot({path:(process.env.SHOTS||'/tmp')+'/shared-pop-390.png'});
  await page.locator('#share-pop [data-pop-set]').click();
  await page.waitForFunction(()=>document.querySelector('[data-share-event="41"]')?.textContent.trim()==='Only you');
  assert.deepEqual(calls.find(c=>c.action==='graph_share'),{action:'graph_share',args:{event_id:41,share:'only_you'}});
  await page.locator('#share-pop',{hasText:'Always keep threads with Client Co private?'}).waitFor();
  await page.locator('#share-pop [data-pop-rule]').click();
  for(let i=0;i<30&&!calls.find(c=>c.action==='graph_rule');i++)await new Promise(r=>setTimeout(r,100));
  assert.deepEqual(calls.find(c=>c.action==='graph_rule'),{action:'graph_rule',args:{key:'@client.example',choice:'private',name:'Client Co'}});
  await page.locator('#share-pop').waitFor({state:'detached'});
  assert.match(await page.locator('.keep-private').innerText(),/Not shared with your team yet\./);
  assert.match(await page.locator('.team-block').innerText(),/Lee asked for the renewal quote/);
  // A shared board's whole timeline: a meeting and an Intercom conversation say what they are
  assert.match(await page.locator('.team-thread',{hasText:'Due diligence call'}).innerText(),/Meeting · [A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2} · Ada/);  // US dates: Sat, Oct 24
  assert.match(await page.locator('.team-thread',{hasText:'Can we test the API?'}).innerText(),/Intercom · 3 messages · /);
  await shot(page.locator('.team-block'),(process.env.SHOTS||'/tmp')+'/team-block-timeline-'+width+'.png');
  await page.locator('[data-keep-private]').first().click();
  await new Promise(r=>setTimeout(r,300));
  assert.equal(calls.some(c=>c.action==='graph_private'&&c.args.node_id===lee.id&&c.args.private===true),true);
  await page.locator('.team-thread summary').first().click();
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
  assert.match(await box.innerText(),/Kim Lee joined Acme\. Share the team's Graph with them\?/);
  assert.match(await box.innerText(),/Kim Lee has a new Machine\. It gets the team's Graph from /);
  if(width===1440)await box.screenshot({path:(process.env.SHOTS||'/tmp')+'/shared-teamgraph-1440.png'});
  assert.match(await box.innerText(),/Tom Ray asks to stop sharing their Work with the team\./);
  await box.locator('[data-share-ok="p2"][data-choice="yes"]').click();
  await new Promise(r=>setTimeout(r,300));
  assert.deepEqual(calls.find(c=>c.action==='fleet_share_ok'),{action:'fleet_share_ok',args:{id:'p2',choice:'yes'}});
  // no more asking an admin to stop: each person picks their connections, except what the Fleet requires
  assert.equal(await box.locator('[data-graph-optout]').count(),0);
  assert.equal(await box.locator('[data-shared-open]',{hasText:'Choose connections'}).count(),1);
  assert.match(await page.locator('.graph-own').innerText(),/Acme requires your Work email\./);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // What's shared: one place with counts, the first share's look-over, Share these?, what went with why and Unshare, and what stays
  await page.goto('http://127.0.0.1:'+server.address().port+'/#shared='+FL);
  const pane=page.locator('[data-shared-fleet="'+FL+'"]');
  await pane.locator('.shared-tile').first().waitFor();
  assert.match(await pane.locator('.shared-status .sub').innerText(),/^Not started\. Nothing goes until you look it over and tap Start now\.$/);
  assert.deepEqual(await pane.locator('.shared-tile span').allInnerTexts(),['Client','Sensitive','Not sure','Personal','Only colleagues','Not client','Not sorted yet','Sent']);
  assert.match(await pane.locator('.shared-tile').first().innerText(),/1,113|1\.113|1113/);
  assert.match(await pane.locator('.shared-first').innerText(),/1,?113 client threads with these organizations go to the team\. Keep any private first\./);
  assert.equal(await pane.locator('.shared-how').getAttribute('open'),'');
  assert.match(await pane.locator('.shared-how').innerText(),/Shared boards\s+The people you pick get each project's full timeline: email, meetings, Intercom, chats, cards and moves\./);
  await shot(pane.locator('.shared-how'),(process.env.SHOTS||'/tmp')+'/shared-how-'+width+'.png');
  if(width===1440)await page.screenshot({path:(process.env.SHOTS||'/tmp')+'/shared-pane-1440.png',fullPage:true});
  if(width===390)await page.screenshot({path:(process.env.SHOTS||'/tmp')+'/shared-pane-390.png',fullPage:true});
  await pane.locator('.shared-first [data-shared-rule]').first().click();
  await new Promise(r=>setTimeout(r,300));
  assert.deepEqual(calls.filter(c=>c.action==='graph_rule').at(-1),{action:'graph_rule',args:{key:'@northwind.example',choice:'private',name:'Northwind'}});
  assert.equal(await pane.locator('[data-shared-many="team"]').isDisabled(),true);
  await pane.locator('[data-shared-pick]').first().check();
  await pane.locator('[data-shared-many="team"]').click();
  await new Promise(r=>setTimeout(r,300));
  assert.deepEqual(calls.find(c=>c.action==='graph_share_many'),{action:'graph_share_many',args:{event_ids:[61],share:'team'}});
  await pane.locator('.shared-out [data-shared-set="51"]').click();
  await page.locator('#toast',{hasText:"Kept private. It won't go."}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='graph_share').at(-1),{action:'graph_share',args:{event_id:51,share:'only_you'}});
  assert.match(await pane.locator('.shared-kept').innerText(),/Stark\s+Always only you/);
  // the admin's switch for the whole Fleet: Off asks once whether what went stays or is removed everywhere
  await new Promise(r=>setTimeout(r,800));  // the pane reloads after the actions above
  // each connection: Work on, personal off, the required one can't be turned off
  const conns=pane.locator('.shared-conns');
  assert.match(await conns.innerText(),/^Your connections\s+What feeds Acme's Graph\. Acme's Work starts on\. Other companies' start off\. Personal never goes\.\s+Gmail · olive@acme\.example\s+Required by Acme\s+Google Calendar · olive@acme\.example\s+Work\s+Gmail · olive@sideco\.example\s+Other company, not shared\s+Gmail · olive@home\.example\s+Personal, never shared$/);
  assert.equal(await conns.locator('[data-graph-conn="work"]').isDisabled(),true);
  if(width===1440)await shot(conns,(process.env.SHOTS||'/tmp')+'/shared-connections-1440.png');
  if(width===390)await shot(conns,(process.env.SHOTS||'/tmp')+'/shared-connections-390.png');
  // personal has no switch at all; another company's mailbox is off until its owner turns it on
  assert.equal(await conns.locator('[data-graph-conn="home"]').count(),0);
  await conns.locator('[data-graph-conn="side"]').click();
  await page.locator('#toast',{hasText:'Shared with the team.'}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='graph_connection').at(-1),{action:'graph_connection',args:{fleet:FL,account:'side',on:true}});
  // an admin requires the Work calendar too
  await new Promise(r=>setTimeout(r,800));
  // requiring something asks the admin to confirm first; Cancel leaves it as it was
  const consent=page.locator('#share-consent');
  await pane.locator('[data-graph-require="calendar"]').check();
  await consent.waitFor({state:'visible'});
  assert.match(await consent.innerText(),/^Require everyone's Work calendar\?[\s\S]*As an admin, you confirm Acme has told its people\.\s+Require it\s+Cancel$/);
  await shot(consent,(process.env.SHOTS||'/tmp')+'/shared-require-consent-'+width+'.png');
  await consent.locator('button[value="cancel"]').click();
  await consent.waitFor({state:'hidden'});
  await page.waitForFunction(()=>!document.querySelector('[data-graph-require="calendar"]').checked);
  assert.equal(calls.filter(c=>c.action==='graph_policy').length,0);
  await pane.locator('[data-graph-require="calendar"]').check();
  await consent.locator('#share-consent-ok').click();
  await page.locator('#toast',{hasText:'Required for everyone in the Fleet.'}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='graph_policy').at(-1),{action:'graph_policy',args:{fleet:FL,require:['mail','calendar']}});
  await new Promise(r=>setTimeout(r,800));
  const sw=pane.locator('[data-graph-switch]');
  assert.equal(await sw.getAttribute('aria-checked'),'true');
  assert.match(await pane.locator('.shared-switch').innerText(),/^Share the Graph in this Fleet\s*Experimental\s+On for everyone\. New and still changing\.$/);
  const offAsk=pane.locator('.shared-off-ask');
  const openOff=async()=>{for(let i=0;i<4;i++){await pane.locator('[data-graph-switch]').click();try{await offAsk.waitFor({state:'visible',timeout:1500});await new Promise(r=>setTimeout(r,300));if(await offAsk.isVisible())return}catch{}}throw new Error('the Off question did not open')};
  await openOff();
  assert.match(await offAsk.innerText(),/^Turn off sharing for everyone\?\s+Nothing new goes\. What went stays on teammates' Machines, unless you remove it\. Someone may have read it already\.\s+Turn off\s+Turn off and remove shared data\s+Cancel$/);
  if(width===1440)await shot(pane.locator('.shared-status'),(process.env.SHOTS||'/tmp')+'/shared-switch-1440.png');
  await offAsk.locator('[data-off-cancel]').click();
  assert.equal(await offAsk.isHidden(),true);
  await openOff();
  await offAsk.locator('[data-remove]').click();
  await page.locator('#toast',{hasText:"Sharing is off. Teammates' Machines delete what they got."}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='graph_pause').at(-1),{action:'graph_pause',args:{fleet:FL,on:true,remove:true}});
  await pane.locator('[data-graph-switch][aria-checked="false"]').waitFor();
  await new Promise(r=>setTimeout(r,800));
  assert.match(await pane.locator('.shared-status .sub').innerText(),/^Off for everyone, turned off by Ada\. Nothing new goes\. What went was removed from the team's Machines\.$/);
  if(width===1440)await shot(pane.locator('.shared-status'),(process.env.SHOTS||'/tmp')+'/shared-switch-off-1440.png');
  // turning it on asks the admin to confirm what goes and that the people were told
  await pane.locator('[data-graph-switch]').click();
  await consent.waitFor({state:'visible'});
  assert.match(await consent.innerText(),/^Share the Graph in Acme\?[\s\S]*client email threads, with attachments[\s\S]*As an admin, you confirm Acme may share this Work mail and has told its people\.[\s\S]*Turn on\s+Cancel$/);
  await shot(consent,(process.env.SHOTS||'/tmp')+'/shared-switch-consent-'+width+'.png');
  await consent.locator('#share-consent-ok').click();
  await page.locator('#toast',{hasText:'Sharing is on for everyone.'}).waitFor();
  assert.deepEqual(calls.filter(c=>c.action==='graph_pause').at(-1),{action:'graph_pause',args:{fleet:FL,on:false}});
  await pane.locator('[data-graph-switch][aria-checked="true"]').waitFor();
  await pane.locator('[data-graph-start]').click();
  await new Promise(r=>setTimeout(r,500));
  assert.deepEqual(calls.find(c=>c.action==='graph_start'),{action:'graph_start',args:{fleet:FL}});
  // once started, what went can be unshared, and the toast says honestly what that does
  await pane.locator('.shared-out h3',{hasText:'Shared'}).waitFor();
  await pane.locator('.shared-out [data-shared-set="51"]',{hasText:'Unshare'}).click();
  await page.locator('#toast',{hasText:"Unshared. Teammates' Machines delete their copy. Someone may have read it already."}).waitFor();
  // a member, in a Fleet where nobody turned it on yet: off by default, experimental, only an admin switches it
  if(width===1440){Object.assign(sharing[0],{paused:{default:true},admin:false,people:[]});await fleetPaneReload(page);
   const st=pane.locator('.shared-status');await st.locator('.sub',{hasText:'experimental'}).waitFor();
   assert.match(await st.innerText(),/^What's shared with Acme\s+Off\. It's experimental, so it starts off until an admin turns it on\.\s+Share the Graph in this Fleet\s*Experimental\s+Off for everyone\. New and still changing\. Only an admin can change this\.\s+Acme requires your Work email and Work calendar\. They belong to the company\. Threads you keep private stay private\./);
   assert.equal(await pane.locator('[data-graph-switch]').isDisabled(),true);
   await shot(st,(process.env.SHOTS||'/tmp')+'/shared-member-off-1440.png');
   Object.assign(sharing[0],{paused:null,admin:true,people:[{member:'m_kim',name:'Kim Lee',state:'not_started'}]})}
  sharing[0].started=false;sharing[0].require=['mail'];sharing[0].connections.find(c=>c.id==='side').on=false;
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  await context.close();
 }
}finally{await browser.close();server.close()}
console.log('shared graph dashboard ok');
