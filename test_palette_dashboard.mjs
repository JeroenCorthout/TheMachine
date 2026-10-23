import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The command palette and the keyboard shortcuts (made-up data). ⌘K on a Mac and Ctrl+K elsewhere open and close it;
// / opens it too; fuzzy match over one action registry, plus cards and the Graph's own search (api/graph?q=, stale
// answers dropped); arrows, Enter runs, Esc puts focus back; recent commands first; no key fires while typing or
// composing; g-sequences; the ? sheet; J/K and E/S/D on a card; and send-type actions only open the review.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString();
const id=n=>(n+'').padStart(2,'0').repeat(8);
const base0=()=>[
 {id:id(1),title:'Send Sam the signed contract',body:'Sam asked on Monday.',status:'pending',scope:'work',created_at:now,event_ids:[1]},
 {id:id(2),title:'Reply to Pat about the offsite',body:'Draft ready.',status:'prepared',scope:'work',created_at:now,event_ids:[2],
  next_actions:[{kind:'email',label:'Send reply',revision:'ab12cd34ef56',to:['pat@acme.example'],subject:'Offsite',body:'Hi Pat, Thursday works.'}]},
 {id:id(3),title:'Book the dentist',body:'',status:'pending',scope:'personal',created_at:now,event_ids:[3]}];
let cards,actions,graphQueries;
const reset=()=>{cards=base0();actions=[];graphQueries=[]};reset();
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const NODES=[{id:'email:pat@acme.example',kind:'person',name:'Pat Doe',ids:['pat@acme.example']},{id:'org:acme.example',kind:'org',name:'Acme',domain:'acme.example'},{id:'project:offsite',kind:'project',name:'Acme offsite'}];
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/graph'){const q=url.searchParams.get('q');if(q==null){res.end(JSON.stringify({counts:{}}));return}graphQueries.push(q);
  // "ac" answers late: its answer must not replace the newer "acme" one
  if(q==='ac')await new Promise(r=>setTimeout(r,700));
  res.end(JSON.stringify({found:NODES.filter(n=>n.name.toLowerCase().includes(q.toLowerCase())||(q==='ac'&&n.kind==='person'))}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);if(r.action!=='card_chat')actions.push(r);
  if(r.action==='decide'){const c=cards.find(x=>x.id===r.args.suggestion_id);if(r.args.choice==='complete')c.status='completed';res.end(JSON.stringify({status:c.status}));return}
  res.end(JSON.stringify(r.action==='card_chat'?{session:true,status:'ok',active:false,cursor:'k1',items:[]}:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height,platform='Linux x86_64')=>{const mobile=width<600;reset();
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-US',timezoneId:'Europe/Brussels'});
 await context.addInitScript(p=>Object.defineProperty(Navigator.prototype,'platform',{get:()=>p}),platform);
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));
 await page.goto(base);await page.locator('#task-'+id(1)).waitFor();return {context,page}};
const sent=name=>actions.filter(a=>a.action===name);
const pal=page=>page.locator('#palette');
const isOpen=page=>page.evaluate(()=>!!document.querySelector('#palette')?.open);
const labels=page=>page.locator('#pal-list .pal-item .pal-label').allTextContents();
const groups=page=>page.locator('#pal-list .pal-group').allTextContents();
const tab=page=>page.evaluate(()=>[...document.querySelectorAll('main>section')].find(s=>!s.hidden)?.id);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 // Fuzzy match: start of the label first, then a word, then letters in order; nothing for letters out of order
 {const {context,page}=await open(1440,900);
  const s=await page.evaluate(()=>[palScore('set','Settings'),palScore('ings','Settings'),palScore('stg','Settings'),palScore('gts','Settings'),palScore('light','Switch to light mode'),palScore('sw li','Switch to light mode'),palScore('','x')]);
  assert.ok(s[0]>s[1]&&s[1]>s[2]&&s[2]>0,'prefix > inside > letters: '+s);assert.equal(s[3],-1);assert.ok(s[4]>=70&&s[5]>0);assert.equal(s[6],1);
  // Board and List on Projects are listed once there is a board
  assert.deepEqual(await page.evaluate(()=>{const ids=()=>actsFor(kbCtx()).map(a=>a.id).filter(x=>x.startsWith('go-projects-'));const a=ids();graphBoards=[{id:1,name:'Hiring',projects:[]}];const b=ids();graphBoards=[];return [a,b]}),[[],['go-projects-board','go-projects-list']]);
  await context.close()}
 // Linux and Windows: Ctrl+K. ⌘ does nothing there. Labels say Ctrl K.
 {const {context,page}=await open(1440,900);
  assert.equal(await page.locator('#pal-open kbd').textContent(),'Ctrl K');
  await page.locator('#side-find').focus();await page.keyboard.press('Meta+k');assert.ok(!(await isOpen(page)),'⌘K is not Linux’s key');
  await page.keyboard.press('Control+k');await pal(page).waitFor();assert.equal(await page.evaluate(()=>document.activeElement.id),'pal-q');
  assert.deepEqual((await groups(page)).slice(0,2),['Actions','Go to']);
  // focus stays in the palette (Tab), Esc closes and focus goes back where it was
  await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'pal-q');
  await page.keyboard.press('Escape');assert.ok(!(await isOpen(page)));assert.equal(await page.evaluate(()=>document.activeElement.id),'side-find');
  // Ctrl+K toggles it closed too
  await page.keyboard.press('Control+k');await pal(page).waitFor();await page.keyboard.press('Control+k');assert.ok(!(await isOpen(page)));
  // / opens it, but not in a field: there it types a /
  await page.locator('#side-find').fill('');await page.locator('#side-find').press('/');assert.ok(!(await isOpen(page)));assert.equal(await page.locator('#side-find').inputValue(),'/');
  await page.locator('#side-find').fill('');await page.locator('body').click({position:{x:700,y:5}});await page.keyboard.press('/');await pal(page).waitFor();
  // Type, arrows, Enter runs: "conn" → Connections; Esc puts focus back
  await page.keyboard.type('conn');assert.equal((await labels(page))[0],'Connections');
  await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowUp');assert.equal(await page.locator('#pal-list [aria-selected="true"] .pal-label').textContent(),'Connections');
  assert.match(await page.locator('#pal-list [aria-selected="true"] .pal-keys').textContent(),/GthenN/);
  await page.keyboard.press('Enter');assert.ok(!(await isOpen(page)));assert.equal(await tab(page),'health');
  // Recent commands come first, remembered in this browser
  await page.keyboard.press('Control+k');await pal(page).waitFor();assert.equal((await groups(page))[0],'Recent');assert.equal((await labels(page))[0],'Connections');
  assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('palette-recent'))),['go-connections']);
  // "new" → New card; it opens the composer and nothing is saved
  await page.keyboard.type('new');assert.equal((await labels(page))[0],'New card');await page.keyboard.press('Enter');await page.locator('#new-card').waitFor();
  await page.keyboard.press('Escape');await page.locator('#new-card').waitFor({state:'hidden'});
  // Cards from the list, People / Organisations / Projects from the Graph's search; the late "ac" answer is ignored
  await page.keyboard.press('Control+k');await page.keyboard.type('ac');await page.waitForTimeout(250);await page.keyboard.type('me');
  await page.locator('#pal-list .pal-group',{hasText:'Organisations'}).waitFor();await page.waitForTimeout(900);
  assert.deepEqual(await groups(page),['Organisations','Projects'],'no People from the stale answer');
  assert.ok(graphQueries.includes('acme'));
  await page.locator('#pal-q').fill('contract');await page.locator('#pal-list .pal-group',{hasText:'Cards'}).waitFor();
  assert.ok((await labels(page)).includes('Send Sam the signed contract'));
  await page.locator('#pal-q').fill('pat');await page.locator('#pal-list .pal-group',{hasText:'People'}).waitFor();
  await page.locator('#pal-list .pal-item',{hasText:'Pat Doe'}).click();assert.ok(!(await isOpen(page)));
  await page.waitForFunction(()=>location.hash==='#graph/'+encodeURIComponent('email:pat@acme.example'));assert.equal(await tab(page),'people');
  // IME: Enter while composing doesn't run
  await page.keyboard.press('Control+k');await page.keyboard.type('settings');
  await page.locator('#pal-q').evaluate(i=>i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true})));assert.ok(await isOpen(page));
  await page.keyboard.press('Escape');
  // g-sequences; nothing fires in a field, a textarea or contenteditable, or while composing
  await page.locator('body').click({position:{x:700,y:5}});
  for(const [k,want] of [['c','calendar'],['e','events'],['b','feedback'],['n','health'],['s','inbox']]){await page.keyboard.press('g');await page.keyboard.press(k);assert.equal(await tab(page),want,'g '+k)}
  await page.keyboard.press('g');await page.keyboard.press(',');assert.equal(await tab(page),'settings');
  await page.keyboard.press('g');await page.keyboard.press('p');assert.equal(await tab(page),'people');
  await page.keyboard.press('g');await page.keyboard.press('f');assert.equal(await tab(page),'people','no Fleet: g f does nothing');
  await page.keyboard.press('g');await page.keyboard.press('s');
  await page.locator('#side-find').focus();await page.keyboard.type('gc');assert.equal(await tab(page),'inbox');assert.equal(await page.locator('#side-find').inputValue(),'gc');
  await page.locator('#side-find').fill('');
  await page.evaluate(()=>{const d=document.createElement('div');d.id='ce';d.contentEditable='true';document.body.append(d)});await page.locator('#ce').focus();
  for(const k of ['c','?','/','g','e','j','d'])await page.keyboard.press(k);
  assert.ok(!(await isOpen(page))&&!(await page.locator('#new-card').isVisible())&&!(await page.locator('#keys-sheet').count()),'nothing fired in contenteditable');
  await page.evaluate(()=>document.querySelector('#ce').remove());
  await page.evaluate(()=>document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'c',isComposing:true,bubbles:true})));assert.ok(!(await page.locator('#new-card').isVisible()),'not while composing');
  // The ? sheet: grouped, platform-aware
  await page.locator('body').click({position:{x:700,y:5}});await page.keyboard.press('?');const sheet=page.locator('#keys-sheet');await sheet.waitFor();
  assert.deepEqual(await sheet.locator('h3').allTextContents(),['General','Go to','Lists and cards']);
  const st=await sheet.textContent();assert.match(st,/Search and commandsCtrlKor\//);assert.match(st,/SettingsGthen,/);assert.match(st,/New cardC/);assert.match(st,/SnoozeS/);assert.ok(!st.includes('⌘'));
  await page.keyboard.press('Escape');await sheet.waitFor({state:'hidden'});
  // Hints: tooltips on the sidebar, a quiet key in the card's More menu
  assert.equal(await page.locator('nav [data-tab="calendar"]').getAttribute('title'),'Calendar (G then C)');
  assert.equal(await page.locator('#open-settings').getAttribute('aria-keyshortcuts'),'G Comma');
  assert.equal(await page.locator(`#task-${id(1)} .more-menu [data-rename]`).evaluate(b=>getComputedStyle(b,'::after').content),'"E"');
  assert.equal(await page.locator(`#task-${id(1)} .more-menu [data-rename]`).textContent(),'Rename','the key is not part of its name');
  // J/K through the cards; S opens the snooze picker for the picked one, E renames, D marks it done
  const order=await page.evaluate(()=>[...document.querySelectorAll('#suggestions article.card')].map(a=>a.id));
  await page.keyboard.press('j');assert.equal(await page.evaluate(()=>document.activeElement.id),order[0]);
  await page.keyboard.press('j');assert.equal(await page.evaluate(()=>document.activeElement.id),order[1]);
  await page.keyboard.press('k');assert.equal(await page.evaluate(()=>document.activeElement.id),order[0]);
  assert.ok(await page.locator('#'+order[0]).evaluate(a=>a.classList.contains('kb-on')));
  await page.locator('#task-'+id(1)).focus();await page.keyboard.press('s');await page.locator('#snooze-dialog').waitFor();await page.keyboard.press('Escape');await page.locator('#snooze-dialog').waitFor({state:'hidden'});
  assert.equal(sent('decide').length,0,'S only opens the picker');
  await page.locator('#task-'+id(1)).focus();await page.keyboard.press('e');await page.locator('.rename-box input').waitFor();await page.keyboard.press('Escape');
  await page.locator('#task-'+id(1)).focus();await page.keyboard.press('d');await page.waitForFunction(()=>true);
  for(let i=0;i<40&&!sent('decide').length;i++)await page.waitForTimeout(50);
  assert.deepEqual([sent('decide')[0].args.suggestion_id,sent('decide')[0].args.choice],[id(1),'complete']);
  // Send-type actions: no key, and from the palette only the Send dialog opens; nothing is confirmed or sent
  await page.locator('#task-'+id(2)).waitFor();await page.locator('body').click({position:{x:700,y:5}});
  for(let i=0;i<4&&await page.evaluate(()=>document.activeElement.id)!=='task-'+'02'.repeat(8);i++)await page.keyboard.press('j');
  assert.deepEqual(await page.evaluate(()=>ACTIONS.filter(a=>a.review&&a.keys).map(a=>a.id)),[],'no review action has a key');
  const before=actions.length;await page.keyboard.press('Control+k');await page.keyboard.type('review');
  assert.equal((await labels(page))[0],'Review the email…');await page.keyboard.press('Enter');await page.locator('#send-dialog').waitFor();
  assert.equal(actions.length,before,'opening the review posts nothing');assert.match(await page.locator('#send-preview').textContent(),/Thursday works/);
  await page.locator('#send-cancel').click();assert.equal(sent('confirm_action').length,0);
  // In a chat: R writes in the chat's box; J goes to the next card
  await page.locator(`#side-cards a[href="#chat=${id(3)}"]`).click();await page.locator('#chat-log').waitFor();await page.locator('body').click({position:{x:700,y:5}});
  await page.keyboard.press('r');assert.equal(await page.evaluate(()=>document.activeElement.id),'chat-input');
  await page.keyboard.type('s');assert.ok(!(await page.locator('#snooze-dialog').isVisible()),'typing s in the chat box types');
  await context.close()}
 // macOS: ⌘K; Ctrl+K is not it; labels say ⌘K
 {const {context,page}=await open(1440,900,'MacIntel');
  assert.equal(await page.locator('#pal-open kbd').textContent(),'⌘K');
  await page.locator('body').click({position:{x:700,y:5}});await page.keyboard.press('Control+k');assert.ok(!(await isOpen(page)));
  await page.keyboard.press('Meta+k');await pal(page).waitFor();await page.keyboard.press('Meta+k');assert.ok(!(await isOpen(page)));
  const why=await page.evaluate(()=>[document.querySelector('dialog[open]')?.id,document.activeElement?.tagName+'#'+document.activeElement?.id]);
  await page.keyboard.press('?');await page.locator('#keys-sheet').waitFor({timeout:3000}).catch(()=>{throw Error('no sheet: '+JSON.stringify(why))});assert.match(await page.locator('#keys-sheet').textContent(),/Search and commands⌘K/);
  await context.close()}
 // Phone: the menu has Search and commands; the palette fits 390
 {const {context,page}=await open(390,844);
  await page.locator('#drawer-open').tap();await page.locator('#side-cmd').waitFor();await page.waitForTimeout(400);await page.locator('#side-cmd').tap();await pal(page).waitFor();
  assert.notEqual(await page.evaluate(()=>document.documentElement.dataset.drawer),'open','the drawer closes');
  const b=await pal(page).boundingBox();assert.ok(b.x>=0&&b.x+b.width<=390.5,'fits');
  await page.locator('#pal-q').fill('cal');assert.equal((await labels(page))[0],'Calendar');await page.locator('#pal-list .pal-item').first().tap();assert.equal(await tab(page),'calendar');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await context.close()}
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('palette dashboard ok');
