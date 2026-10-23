import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The desktop layout (from 1024px): a fixed sidebar with the tabs as rows, the cards as a list by section (Done folded,
// each fold remembered), a filter and a Work/Personal switch; the main area gets a title per tab. The sidebar folds to
// icons and remembers it, also when the browser blocks storage. Every tab scrolls as a page at 1024, 1440 and 1920,
// folded or not. Graph has its own rows (People, Organisations, Projects) whose pane is in the address. Below 1024px the
// same sidebar is a drawer under a slim top bar: it opens from the menu button or a swipe from the edge, and closes on
// ✕, the shade, Esc, back, a swipe or a pick; nothing is wider than the screen.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const card=(n,status,scope,extra={})=>({id:(n+'').padStart(2,'0').repeat(8),title:'Card number '+n+' with a long title that does not fit the sidebar',body:'Some text for card '+n+'.',status,scope,created_at:now,event_ids:[n+1],...extra});
const cards=[...Array.from({length:12},(_,n)=>card(n,n%3?'pending':'prepared',n%2?'work':'personal')),...Array.from({length:12},(_,n)=>card(20+n,'completed','work'))];
let emptyOn=false;const promises=Array.from({length:3},(_,i)=>({id:i+1,direction:'mine',who:'Sam Lee',what:'send the notes '+i,due:null,state:'open',made_at:now,check_at:now}));
const state=()=>({agents_settings:{claude_session:'session_01Example',device:'Desk',codex_env:'env_1',codex_branch:'',connector:'https://machine.example/mcp',saveable:true,on_network:true},suggestions:emptyOn?cards.filter(c=>c.status==='completed'):cards,promises:emptyOn?promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:fleetOn?{on:true,waiting:0}:{on:false,fleets:[]}});let fleetOn=false;
const history={session:true,status:'ok',active:false,cursor:'k1',items:Array.from({length:30},(_,n)=>({kind:'reply',text:'Reply '+n,at:Date.now()-100000+n*1000}))};
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/graph'){const n=(kind,name,i)=>({id:kind+':'+i,kind,name,strength:2-i/10,last_at:now,signals:9,ids:[]});
  res.end(JSON.stringify({counts:{person:214,org:38,project:9},people:[n('person','Robin Example',1),n('person','Alex Example',2)],quiet:[],new:[],projects:[n('project','Garden plan',3)],orgs:[n('org','Example Ltd',4)],whos_who:[],waiting:0}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);res.end(JSON.stringify(r.action==='card_chat'?history:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const TABS=['inbox','events','people','feedback','health','settings'];
const errors=[];let browser;
const open=async(width,height,opts={})=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-US',timezoneId:'Europe/Brussels'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 if(opts.noStorage)await context.addInitScript(()=>{for(const m of ['getItem','setItem']){const real=Storage.prototype[m];Storage.prototype[m]=function(k,...v){if(/^side/.test(k))throw new DOMException('blocked','SecurityError');return real.call(this,k,...v)}}});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));
 await page.goto(base);await page.locator(opts.wait||'#task-'+cards[0].id).waitFor();return {context,page}};
const tab=(page,t)=>page.evaluate(t=>t==='settings'?showSettings('general'):document.querySelector(`nav [data-tab="${t}"]`).click(),t).then(()=>page.waitForTimeout(120));
const scrolls=async(page,where)=>{const y=await page.evaluate(()=>{const s=[...document.querySelectorAll('main>section')].find(x=>!x.hidden);
 if(!s.querySelector('.scroll-probe')){const d=document.createElement('div');d.className='scroll-probe';d.style.height='3000px';s.append(d)}
 scrollTo(0,0);scrollTo(0,1e6);return scrollY});assert.ok(y>500,`${where} scrolls (scrollY ${y})`)};
const side=page=>page.evaluate(()=>{const r=document.querySelector('#app-side').getBoundingClientRect();return {x:r.left,w:Math.round(r.width),h:Math.round(r.height),top:r.top}});
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // A sidebar on the left, the full window high, that stays while the page scrolls; the tabs are rows in it
  let s=await side(page);assert.deepEqual([s.x,Math.round(s.top)+0,s.h],[0,0,900]);assert.ok(s.w>=220&&s.w<=300,'sidebar width '+s.w);
  const navRows=await page.locator('nav button[data-tab]:not([hidden])').evaluateAll(b=>b.map(x=>Math.round(x.getBoundingClientRect().left)));assert.ok(navRows.every(x=>x===navRows[0]),'the tabs stack');
  assert.ok(await page.locator('#open-settings').evaluate(n=>n.getBoundingClientRect().top>innerHeight/2),'Settings sits at the bottom');
  await scrolls(page,'inbox at 1440');assert.ok(Math.abs((await side(page)).top)<1,'the sidebar stays in place');
  // The main area has a title; the old filter row and tiles live in the sidebar instead
  assert.ok(await page.locator('#page-head .page-title').isVisible());assert.ok(!(await page.locator('#suggestion-stats').isVisible()));
  // The cards by section; Done starts folded with its count, opens, and stays open after a reload
  const done=page.locator('[data-side-fold="done"]');assert.equal(await done.getAttribute('aria-expanded'),'false');assert.match(await done.textContent(),/Done\s*12/);
  assert.equal(await page.locator('#side-cards a').count(),12);
  await done.click();assert.equal(await page.locator('#side-cards a').count(),24);
  await page.locator('[data-side-fold="ready"]').click();assert.equal(await page.locator('#side-cards a').count(),20);
  await page.reload();await page.locator('#side-cards a').first().waitFor();assert.equal(await page.locator('#side-cards a').count(),20,'folds are remembered');
  // Long titles fade at the edge and keep the full name as a tooltip
  const a=page.locator('#side-cards a').first();  // the fade is measured on the next frame, once the fonts are in (slower on macOS)
  await page.waitForFunction(()=>document.querySelector('#side-cards a')?.classList.contains('fade'),null,{timeout:5000}).catch(()=>{});assert.match(await a.getAttribute('class')||'',/fade/);assert.match(await a.getAttribute('title'),/does not fit the sidebar/);
  // The filter narrows the list; Work/Personal switches it
  await page.locator('#side-find').fill('number 2');assert.ok((await page.locator('#side-cards a').allTextContents()).every(t=>t.includes('number 2')));
  await page.locator('#side-find').fill('nothing like this');assert.equal(await page.locator('.side-empty').textContent(),'No cards match.');
  await page.locator('#side-find').fill('');
  await page.locator('#side-cards [data-scope="personal"]').click();assert.equal(await page.locator('#side-cards [data-scope="personal"]').getAttribute('aria-pressed'),'true');
  await page.locator('#side-cards [data-scope="all"]').click();
  // A card in the list opens its chat in the main area, from any tab; Suggestions goes back to the overview
  await tab(page,'events');await page.locator('#side-cards a').nth(1).click();await page.locator('#chat-log').waitFor();
  assert.equal(await page.locator('#side-cards a[aria-current="page"]').count(),1);assert.ok(!(await page.locator('.chat-side').isVisible()),'no second list beside the chat');
  assert.ok(!(await page.locator('#page-head').isVisible()));
  await tab(page,'feedback');await page.locator('#side-cards a[aria-current="page"]').click();await page.locator('#chat-log').waitFor({state:'visible'});
  await page.locator('nav [data-tab="inbox"]').click();await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});assert.ok(await page.locator('#page-head').isVisible());
  // Fold to icons: narrow, labels hidden, the list out of the way; remembered after a reload; and back
  await page.locator('#side-toggle').click();s=await side(page);assert.ok(s.w<=72,'folded width '+s.w);
  assert.equal(await page.locator('#side-toggle').getAttribute('aria-expanded'),'false');
  assert.ok(!(await page.locator('nav .nl').first().isVisible()));assert.ok(await page.locator('nav .ni').first().isVisible());assert.ok(!(await page.locator('#side-cards a').first().isVisible()));
  await page.reload();await page.locator('#task-'+cards[0].id).waitFor();assert.ok((await side(page)).w<=72,'the fold is remembered');
  for(const t of TABS){await tab(page,t);await scrolls(page,t+' folded at 1440')}
  // The search icon of a folded sidebar opens it and puts the cursor in the filter
  await page.locator('#side-search').click();assert.ok((await side(page)).w>=220);assert.equal(await page.evaluate(()=>document.activeElement.id),'side-find');
  await context.close()}
 // Storage refused for the sidebar's keys: the page still works and the sidebar still folds (just not remembered)
 // In a Fleet the Fleet tab is a sidebar row like the others, and its page scrolls
 fleetOn=true;{const {context,page}=await open(1440,900);await page.locator('nav [data-tab="fleet"]').waitFor();
  assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('.app-side nav [data-tab]')].filter(b=>!b.hidden&&b.offsetHeight).map(b=>b.dataset.tab)),['inbox','events','people','fleet','feedback','health']);
  await tab(page,'fleet');await scrolls(page,'fleet tab at 1440');assert.equal(await page.locator('#page-head').innerText(),'Fleet');await context.close()}
 fleetOn=false;
 // Wide: the column sits in the middle of the space next to the sidebar, with cards and all clear; the sidebar keeps its padding
 for(const empty of [false,true]){emptyOn=empty;const {context,page}=await open(2000,1000,empty?{wait:'#suggestions .all-clear'}:{});
  const m=await page.evaluate(()=>{const s=document.querySelector('#app-side').getBoundingClientRect(),c=document.querySelector('#suggestions').getBoundingClientRect(),w=document.body.getBoundingClientRect().right;
   return {off:Math.abs((c.left+c.right)/2-(s.right+w)/2),pad:Math.min(...['.app-side .brand','#side-search','.app-side nav button'].map(q=>document.querySelector(q).getBoundingClientRect().left))}});
  assert.ok(m.off<2,`column centred next to the sidebar (${empty?'all clear':'cards'}, off by ${m.off})`);assert.ok(m.pad>=8,`sidebar padding (${empty?'all clear':'cards'}: ${m.pad})`);
  if(empty)assert.equal(await page.locator('#page-head .page-sub').innerText(),'All clear · 3 promises kept track of');
  await context.close()}
 emptyOn=false;
 // Folded, every row has a name and a tooltip to its right: after a moment on hover, at once on keyboard focus, with its count in words; Ctrl+B folds and unfolds
 {const {context,page}=await open(1440,900);await page.keyboard.press('Control+b');await page.waitForFunction(()=>document.documentElement.dataset.side==='collapsed');
  assert.equal(await page.locator('#side-toggle').getAttribute('aria-label'),'Expand sidebar');
  const names=await page.evaluate(()=>[...document.querySelectorAll('.app-side nav [data-tab]')].filter(b=>b.offsetParent).map(b=>b.getAttribute('aria-label')));
  assert.equal(names[0],'Suggestions · 4 ready · 8 to decide');assert.deepEqual(names.slice(1),['Events','Graph','Feedback','Connections']);
  for(const q of ['#open-settings','#side-toggle','#live'])assert.ok(await page.locator(q).getAttribute('aria-label'),q+' has a name');
  const row=page.locator('.app-side nav [data-tab="inbox"]');await row.hover();await page.waitForTimeout(120);assert.equal(await page.locator('#side-tip').isVisible(),false,'not at once on hover');
  await page.locator('#side-tip').waitFor({state:'visible',timeout:1000});assert.equal(await page.locator('#side-tip').textContent(),'Suggestions · 4 ready · 8 to decide');
  const pos=await page.evaluate(()=>{const t=document.querySelector('#side-tip').getBoundingClientRect(),s=document.querySelector('#app-side').getBoundingClientRect(),r=document.querySelector('.app-side nav [data-tab="inbox"]').getBoundingClientRect();return {gap:t.left-s.right,mid:Math.abs((t.top+t.bottom)/2-(r.top+r.bottom)/2)}});
  assert.ok(pos.gap>=4&&pos.gap<=16&&pos.mid<2,JSON.stringify(pos));
  await page.screenshot({path:process.env.SHOT_DIR?process.env.SHOT_DIR+'/tip.png':'/dev/null'}).catch(()=>{});
  await page.mouse.move(900,500);await page.locator('#side-tip').waitFor({state:'hidden'});
  await page.locator('#open-settings').focus();await page.keyboard.press('Shift+Tab');await page.keyboard.press('Tab');
  assert.equal(await page.locator('#side-tip').isVisible(),true,'at once on keyboard focus');assert.equal(await page.locator('#side-tip').textContent(),'Settings');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#side-tip').isVisible(),false);
  await page.locator('body').click({position:{x:900,y:300}});await page.keyboard.press('Control+b');await page.waitForFunction(()=>document.documentElement.dataset.side==='open');
  assert.equal(await page.locator('#side-toggle').getAttribute('aria-label'),'Collapse sidebar');
  await row.hover();await page.waitForTimeout(500);assert.equal(await page.locator('#side-tip').isVisible(),false,'no tooltip with the labels showing');
  await context.close()}
 // Settings in the same column: the lists as wide as the forms, the agent fields in clean rows, no browser button (Browser is in the sidebar)
 {const {context,page}=await open(1440,900);await tab(page,'settings');await page.locator('#agents-settings').waitFor();
  const st=await page.evaluate(()=>{const w=q=>Math.round(document.querySelector(q).getBoundingClientRect().width),r=q=>document.querySelector(q).getBoundingClientRect();
   return {list:Math.round([...document.querySelectorAll('#settings .settings-list')].find(e=>e.offsetWidth).getBoundingClientRect().width),form:w('#agents-settings'),session:w('#agents-session'),device:[r('#agents-device').top,r('#agents-device').width],branch:[r('#agents-branch').top,r('#agents-branch').width],browser:!!document.querySelector('#settings-browser').offsetParent}});
  assert.equal(st.list,st.form,'settings lists are as wide as the forms');assert.ok(st.session>=st.form-2,'the session field is full width');
  assert.equal(st.device[0],st.branch[0],'Runs on and Branch side by side');assert.ok(Math.abs(st.device[1]-st.branch[1])<2);assert.equal(st.browser,false);
  await context.close()}
 // An open chat: its header spans the main area, one line that ends in an ellipsis; the thread stays in the column
 {const {context,page}=await open(2000,1000);await page.locator('.side-cards a').first().click();await page.locator('#chat-log').waitFor();
  const h=await page.evaluate(()=>{const s=document.querySelector('#app-side').getBoundingClientRect(),hd=document.querySelector('.chat-head').getBoundingClientRect(),t=document.querySelector('.chat-title'),cs=getComputedStyle(t),
   box=document.querySelector('.chat-compose').getBoundingClientRect();return {left:hd.left-s.right,right:document.documentElement.clientWidth-hd.right,lines:Math.round(t.getBoundingClientRect().height/parseFloat(cs.lineHeight)),ell:cs.textOverflow,compose:box.width}});
  assert.ok(Math.abs(h.left)<2&&h.right<20,`chat header spans the main area (${h.left}, ${h.right})`);assert.equal(h.lines,1);assert.equal(h.ell,'ellipsis');assert.ok(h.compose<900,`the message box stays in the column (${h.compose})`);
  await context.close()}
 {const {context,page}=await open(1440,900,{noStorage:true});await page.locator('#side-toggle').click();assert.ok((await side(page)).w<=72);await context.close()}
 // Every tab scrolls at each desktop width, and nothing is wider than the window
 for(const [w,h] of [[1024,768],[1920,1080]]){const {context,page}=await open(w,h);
  for(const t of TABS){await tab(page,t);await scrolls(page,t+' at '+w);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),t+' fits at '+w)}
  await context.close()}
 // Graph's rows: each opens its pane, the address says which (back and reload come back to it), the row of the pane on
 // screen is lit, the counts show, and the group folds (remembered); folded to icons each row has its name and count
 {const {context,page}=await open(1440,900);
  await page.waitForFunction(()=>document.querySelector('#count-people').textContent==='214');
  assert.deepEqual(await page.locator('.side-sub a:not([hidden])').evaluateAll(a=>a.map(x=>x.textContent)),['People214','Organisations38','Projects9']);
  const lit=()=>page.evaluate(()=>[...document.querySelectorAll('.side-sub a[aria-current="page"]')].map(a=>a.dataset.graphSub).join());
  const pane=()=>page.evaluate(()=>[...document.querySelectorAll('[data-graph-panel]')].find(p=>!p.hidden)?.dataset.graphPanel);
  await page.locator('.side-sub a[data-graph-sub="orgs"]').click();await page.locator('[data-graph-panel="orgs"]').waitFor();
  assert.equal(await pane(),'orgs');assert.equal(new URL(page.url()).hash,'#graph=orgs');assert.equal(await lit(),'orgs');assert.ok(await page.locator('#page-head').isVisible());
  await page.locator('.side-sub a[data-graph-sub="projects"]').click();await page.waitForFunction(()=>location.hash==='#graph=projects');await page.locator('[data-graph-panel="projects"]').waitFor();assert.equal(await pane(),'projects');assert.equal(await lit(),'projects');
  await page.goBack();await page.waitForFunction(()=>location.hash==='#graph=orgs');await page.locator('[data-graph-panel="orgs"]').waitFor();assert.equal(await pane(),'orgs');assert.equal(await lit(),'orgs');
  await page.reload();await page.locator('[data-graph-panel="orgs"]').waitFor();await page.waitForTimeout(300);assert.equal(await pane(),'orgs');assert.equal(await lit(),'orgs');
  assert.equal(await page.locator('#people-view .sub-tabs').count(),0,'no pills: the rows are the panes');
  assert.equal(await page.locator('#page-head .page-title').textContent(),'Organisations');assert.equal(await page.locator('#page-head .page-sub').textContent(),'38 organisations');
  assert.equal(await page.locator('#page-head .page-crumb').textContent(),'Graph');
  await page.locator('.side-sub a[data-graph-sub="people"]').click();await page.waitForFunction(()=>location.hash==='#graph=people');assert.equal(await lit(),'people');assert.equal(await page.locator('#page-head .page-sub').textContent(),'214 people');
  // Graph itself shows the overview as before, the pane it was on
  await tab(page,'events');assert.equal(new URL(page.url()).hash,'');assert.equal(await lit(),'');
  await page.locator('nav [data-tab="people"]').click();await page.waitForFunction(()=>location.hash==='#graph=people');assert.equal(await lit(),'people');
  await page.locator('#graph-fold').click();assert.ok(!(await page.locator('#graph-sub').isVisible()));assert.equal(await page.locator('#graph-fold').getAttribute('aria-expanded'),'false');
  await page.reload();await page.locator('#task-'+cards[0].id).waitFor({state:'attached'});assert.ok(!(await page.locator('#graph-sub').isVisible()),'the fold is remembered');
  await page.locator('#graph-fold').click();assert.ok(await page.locator('#graph-sub').isVisible());
  await page.locator('#side-toggle').click();assert.equal(await page.locator('.side-sub a[data-graph-sub="people"]').getAttribute('aria-label'),'People · 214');
  assert.ok(await page.locator('.side-sub .sq').first().isVisible());assert.ok(!(await page.locator('.side-sub .nl').first().isVisible()));
  await page.locator('.side-sub a[data-graph-sub="orgs"]').hover();await page.locator('#side-tip').waitFor({state:'visible',timeout:1000});assert.equal(await page.locator('#side-tip').textContent(),'Organisations · 38');
  await page.locator('#side-toggle').click();await context.close()}
 // A phone and a tablet: a slim top bar with the menu button and the page's title; the sidebar is a drawer
 const touch=(page,from,to)=>page.evaluate(([from,to])=>{const el=document.elementFromPoint(from[0],from[1]),t=(x,y)=>new Touch({identifier:1,target:el,clientX:x,clientY:y});
  el.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[t(...from)],changedTouches:[t(...from)]}));
  for(let i=1;i<=6;i++){const x=from[0]+(to[0]-from[0])*i/6,y=from[1]+(to[1]-from[1])*i/6;el.dispatchEvent(new TouchEvent('touchmove',{bubbles:true,touches:[t(x,y)],changedTouches:[t(x,y)]}))}
  el.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[],changedTouches:[t(...to)]}))},[from,to]);
 const isOpen=page=>page.evaluate(()=>document.documentElement.dataset.drawer==='open');
 const settle=page=>page.waitForTimeout(350);
 for(const [w,h] of [[390,844],[360,740],[430,932],[900,1100]]){const {context,page}=await open(w,h);
  assert.ok(await page.locator('#drawer-open').isVisible(),'the menu button at '+w);assert.ok(!(await page.locator('#app-side').isVisible()),'the drawer is closed at '+w);
  // The slim bar: the menu left, a small centred "The Machine", the tagline heading below it
  {const b=await page.evaluate(()=>{const r=e=>document.querySelector(e).getBoundingClientRect();return {bar:r('#topbar'),t:r('#topbar-title'),m:r('#drawer-open'),h:r('#page-head .page-title')}});
   assert.ok(Math.abs(b.t.left+b.t.width/2-(b.bar.left+b.bar.width/2))<2,'the title is centred at '+w);assert.ok(b.m.right<b.t.left&&b.t.bottom<=b.bar.bottom+1,'menu left of the title at '+w);
   assert.ok(b.h.top>=b.bar.bottom,'the tagline heading sits below the bar at '+w);assert.equal(await page.locator('#topbar-title').textContent(),'The Machine');assert.equal(await page.locator('#page-head .page-avatar').isVisible(),false,'one mascot, in the bar')}
  assert.ok(await page.locator('#page-head .page-title').isVisible());assert.ok(!(await page.locator('#suggestion-stats').isVisible()));assert.ok(!(await page.locator('#tagline').isVisible()));
  for(const t of TABS){await tab(page,t);await scrolls(page,t+' at '+w);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),t+' fits at '+w)}
  await tab(page,'inbox');await page.evaluate(()=>scrollTo(0,0));
  // The menu button opens it: nearly the full width, the rest of the page inert, focus on its ✕, the rows and the cards in it, Settings at the bottom
  await page.locator('#drawer-open').click();await settle(page);assert.ok(await isOpen(page));
  const d=await page.evaluate(()=>{const r=document.querySelector('#app-side').getBoundingClientRect();return {x:r.left,w:r.width,h:r.height,inert:document.querySelector('main').inert&&document.querySelector('#topbar').inert,focus:document.activeElement.id,
   settings:document.querySelector('#open-settings').getBoundingClientRect().bottom,wide:document.documentElement.scrollWidth<=innerWidth,label:document.querySelector('#side-toggle').getAttribute('aria-label')}});
  assert.equal(Math.round(d.x),0);assert.ok(d.w>=Math.min(w*.8,360)&&d.w<=w*.9,'drawer width '+d.w);assert.ok(d.inert,'the page behind is inert');assert.equal(d.focus,'side-toggle');assert.ok(d.settings>h-120,'Settings at the bottom');assert.ok(d.wide);
  assert.equal(await page.locator('[data-side-fold="done"]').getAttribute('aria-expanded'),'false');assert.ok(await page.locator('#side-cards a').first().isVisible());
  const rows=await page.locator('.app-side nav button[data-tab]:not([hidden])').evaluateAll(b=>b.map(x=>x.getBoundingClientRect().height));assert.ok(rows.every(x=>x>=44),'rows are a thumb high: '+rows);
  // Esc closes it and gives focus back; so do ✕, the shade and back, and none leaves an entry on the history
  await page.keyboard.press('Escape');await settle(page);assert.ok(!(await isOpen(page)));assert.equal(await page.evaluate(()=>document.activeElement.id),'drawer-open');assert.ok(!(await page.evaluate(()=>document.querySelector('main').inert)));
  const len=await page.evaluate(()=>history.length);
  await page.locator('#drawer-open').click();await settle(page);await page.locator('#side-toggle').click();await settle(page);assert.ok(!(await isOpen(page)),'✕ closes');
  await page.locator('#drawer-open').click();await settle(page);await page.mouse.click(w-30,h/2);await settle(page);assert.ok(!(await isOpen(page)),'the shade closes');
  await page.locator('#drawer-open').click();await settle(page);await page.goBack();await settle(page);assert.ok(!(await isOpen(page)),'back closes');assert.ok(await page.locator('#inbox').isVisible(),'back only closes the drawer');
  assert.equal(await page.evaluate(()=>history.state?.drawer||null),null);
  // A swipe from the left edge opens it, a swipe to the left closes it
  await touch(page,[4,h/2],[w*.7,h/2+10]);await settle(page);assert.ok(await isOpen(page),'an edge swipe opens');
  await touch(page,[w*.6,h/2],[20,h/2]);await settle(page);assert.ok(!(await isOpen(page)),'a swipe to the left closes');
  await touch(page,[w/2,h/2],[w-10,h/2]);await settle(page);assert.ok(!(await isOpen(page)),'a swipe away from the edge does not open');
  // A tab picked in it closes it and shows the tab
  await page.locator('#drawer-open').click();await settle(page);await page.locator('.app-side nav [data-tab="events"]').click();await settle(page);
  assert.ok(!(await isOpen(page)));assert.ok(await page.locator('#events').isVisible());assert.equal(await page.locator('#page-head').innerText(),'Events');assert.equal(await page.evaluate(()=>history.state?.drawer||null),null);
  // A card opens its chat full screen; back from the chat comes back to the page, not to the drawer
  await page.locator('#drawer-open').click();await settle(page);await page.locator('#side-cards a').first().click();await page.locator('#chat-log').waitFor();await settle(page);
  assert.ok(!(await isOpen(page)));assert.ok(!(await page.locator('#topbar').isVisible()),'the top bar steps aside in a chat');assert.ok(await page.locator('.chat-head').isVisible());
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'the chat fits at '+w);
  // All tasks in the chat's ⋯ opens the same drawer
  await page.locator('.chat-menu summary').click();await page.locator('.chat-menu-list [data-chat-drawer]').click();await settle(page);assert.ok(await isOpen(page),'All tasks opens the drawer');
  await page.keyboard.press('Escape');await settle(page);
  await page.goBack();await page.locator('#suggestions .chat-layout').waitFor({state:'detached'});assert.equal(new URL(page.url()).hash,'');
  assert.ok((await page.evaluate(()=>history.length))<=len+2,'the drawer left no stray history entries');
  // Graph's rows in the drawer: one opens its pane and closes the drawer; back goes to where it was
  await page.locator('#drawer-open').click();await settle(page);await page.locator('.side-sub a[data-graph-sub="projects"]').click();await page.locator('[data-graph-panel="projects"]').waitFor();await settle(page);
  assert.ok(!(await isOpen(page)));assert.equal(new URL(page.url()).hash,'#graph=projects');
  await page.locator('#drawer-open').click();await settle(page);assert.equal(await page.locator('.side-sub a[aria-current="page"]').getAttribute('data-graph-sub'),'projects');await page.keyboard.press('Escape');await settle(page);
  await page.goBack();await settle(page);assert.ok(await page.locator('#inbox').isVisible(),'back from a Graph row goes back to Suggestions');
  // Resized to a computer with the drawer open: the sidebar is a column again and nothing stays inert
  if(w===900){await page.locator('#drawer-open').click();await settle(page);await page.setViewportSize({width:1280,height:900});await settle(page);
   assert.ok(!(await isOpen(page)));assert.ok(!(await page.evaluate(()=>document.querySelector('main').inert)));assert.ok(await page.locator('#side-cards').isVisible())}
  await context.close()}
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('desktop layout dashboard ok');
