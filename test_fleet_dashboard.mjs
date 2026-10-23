import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Fleet tab: who is who per member, and one rule per kind of question for the whole Fleet: Ask me first | Always | Never
// as pills, how much they hear, Only about…, a short example, and exceptions for someone in particular as small pills.
// Never scrolls sideways (390px and 1000px, light and dark).
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16);
const fresh=()=>({relationships:{rule:'ask',details:false},meeting:{rule:'always',details:true},handling:{rule:'ask',details:false},brief:{rule:'never',details:false}});
const members=[{member_id:'p',name:'Sam Doe (sam.doe@mail.example)'},{member_id:'q',name:'Alexandra Longname-Whitfield'},{member_id:'l',name:'Robin Vale'}];
const view={on:true,kinds:{relationships:'who knows someone',meeting:'meeting context',handling:'who handles shared emails',brief:'brief facts'},ask_meetings:false,
 kind_rules:{relationships:['ask','always','never'],meeting:['ask','always','never'],handling:['ask','always','never'],brief:['ask','always','never']},
 no_details:['handling','brief'],scoped:['relationships','meeting','handling'],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['relationships','meeting'],
  people:[{member_id:'m',name:'Sam Doe',role:'owner',me:true,answers_with:'Self-hosted: Desk',email:'sam@acme.example'},
   {member_id:'p',name:'Sam Doe',role:'member',me:false,answers_with:'Hosted: sam.example.test',email:'sam.doe@mail.example'},
   {member_id:'k',name:'Kim Lee',role:'member',answers_with:'Self-hosted: Laptop',email:'kim@acme.example'},{member_id:'k2',name:'Kim Lee',role:'member',answers_with:'Hosted: kim.example.test',email:'kim@acme.example'}],
  members:members.map(m=>({...m,rules:fresh()})),rules:fresh(),exceptions:{}}],
 waiting:[],asked:[],log:[],notices:[]};
const reset=()=>{const w=view.fleets[0];w.rules=fresh();w.exceptions={relationships:[{member_id:'l',name:'Robin Vale',rule:'always',details:false,own_scope:false}]};
 view.scopes={[FL]:{'*':{relationships:{only:['acme.com'],never:['initech'],work_only:true}}}}};
const calls=[];
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}}));return}
 if(req.url==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);const a=call.args,w=view.fleets[0];
  const list=w.exceptions[a.kind]=(w.exceptions[a.kind]||[]).filter(e=>e.member_id!==a.member_id||!a.clear);
  if(a.member_id==='*'){Object.assign(w.rules[a.kind],a.rule?{rule:a.rule}:{},a.details!==undefined?{details:a.details}:{})}
  else if(!a.clear){const e=list.find(x=>x.member_id===a.member_id);if(e)e.rule=a.rule;else list.push({member_id:a.member_id,name:members.find(m=>m.member_id===a.member_id).name,rule:a.rule,details:w.rules[a.kind].details,own_scope:false})}
  const scope=a.scope?{only:String(a.scope.only).split(',').map(x=>x.trim().toLowerCase()).filter(Boolean),never:String(a.scope.never).split(',').map(x=>x.trim().toLowerCase()).filter(Boolean),work_only:a.scope.work_only}:(view.scopes[FL]['*'][a.kind]||{only:[],never:[],work_only:true});
  if(a.scope)view.scopes[FL]['*'][a.kind]=scope;
  res.end(JSON.stringify({...(a.member_id==='*'||a.clear?w.rules[a.kind]:{rule:a.rule,details:false}),scope}));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(view));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const shots=process.env.FLEET_SHOTS;
try{
 for(const [scheme,width] of [['dark',390],['light',390],['dark',1000],['light',1000]]){
  reset();
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-pane-head h2',{hasText:/^Acme$/}).waitFor();
  assert.deepEqual(errors,[]);
  // One view at a time, picked with pills: the Fleet, Sharing (every rule), Activity.
  assert.deepEqual(await page.locator('.fleet-tabs [data-fleet-pane]').allInnerTexts(),['Acme','Sharing','Activity']);
  assert.equal(await page.locator('.fleet-pane:not([hidden])').count(),1);
  const people=await page.locator('.fleet-people li').allInnerTexts();
  assert.match(people[0],/^Sam Doe\s*you\s*owner\nsam@acme\.example · Self-hosted: Desk$/);assert.match(people[1],/Hosted: sam\.example\.test/);
  assert.equal(people[2],'Kim Lee\nkim@acme.example\nSelf-hosted: Laptop\nHosted: kim.example.test');  // one person, two Machines: the name once
  // Each row: the name and its tags on one line, email and Machine muted below, lined up the same.
  const rows=await page.locator('.fleet-people li').evaluateAll(l=>l.map(li=>{const n=li.querySelector('.fp-name').getBoundingClientRect(),m=li.querySelector('.fp-meta').getBoundingClientRect();return [Math.round(m.left-n.left),m.top>=n.bottom-1]}));
  assert.ok(rows.every(r=>r[0]===0&&r[1]),JSON.stringify(rows));
  assert.equal(await page.locator('.fleet-rule').evaluateAll(x=>x.filter(r=>r.checkVisibility()).length),0);
  await page.locator('[data-fleet-pane="sharing"]').click();await page.locator('.fleet-rules-head').waitFor();
  // What the three choices mean, once, short; each kind with its question in plain words; a hint on each choice.
  assert.deepEqual(await page.locator('.fleet-legend dt').allInnerTexts(),['Ask me first','Always','Never','Names']);
  assert.match(await page.locator('.fleet-legend').innerText(),/Ask me first\s+You choose each time/);
  assert.equal(await page.locator('.fleet-rule[data-member="*"][data-kind="relationships"] .fleet-rule-kind small').innerText(),'“Who knows someone at Acme?”');
  assert.equal(await page.locator('.fleet-rule[data-member="*"][data-kind="relationships"] label:has([data-fleet-rule][value="never"])').getAttribute('title'),'They get “no”');
  // One rule per kind for the whole Fleet, title on top and a short line under it.
  const head=page.locator('.fleet-rules-head');
  assert.equal(await head.locator('h3').innerText(),'What your Machine answers');
  assert.match(await head.locator('p').innerText(),/^One rule per kind of question, for everyone in your Fleet\./);
  assert.ok(await head.evaluate(h=>h.querySelector('p').getBoundingClientRect().top>=h.querySelector('h3').getBoundingClientRect().bottom-1));
  const R=k=>page.locator(`.fleet-rule[data-member="*"][data-kind="${k}"]`);
  assert.equal(await page.locator('.fleet-rule').count(),4);
  assert.equal(await page.locator('.fleet-rules select:not([data-fleet-ex-member]), .fleet-rules input[type=checkbox]:not([data-scope])').count(),0);
  for(const k of ['relationships','meeting','handling','brief'])
   assert.deepEqual(await R(k).locator('[data-fleet-rule]').evaluateAll(x=>x.map(i=>i.parentElement.innerText)),['Ask me first','Always','Never'],k);
  assert.deepEqual(await R('meeting').locator('[data-fleet-details]').evaluateAll(x=>x.map(i=>i.parentElement.innerText)),['Without names','Names and promises']);
  assert.equal(await R('brief').locator('.fleet-scope').count(),0);
  assert.equal(await R('relationships').locator('details.fleet-scope').evaluate(d=>d.open),false);
  assert.match(await R('relationships').locator('summary').first().innerText(),/only acme\.com; not initech/);
  // Short examples, for everyone.
  const ex=k=>R(k).locator('.fleet-preview').innerText();
  assert.match(await ex('relationships'),/^Example: Asked first; if you answer, they get: 3 people at acme\.com know them/);
  assert.match(await ex('meeting'),/^Example: They get, without asking you: .*with name and promises\.$/);
  assert.equal(await ex('brief'),'Example: They get “no”.');
  for(const k of ['relationships','meeting','handling','brief'])assert.ok((await ex(k)).length<=140,k+": "+await ex(k));
  // Exceptions: small pills, and a way to add one.
  assert.equal(await R('relationships').locator('.fleet-ex').innerText().then(t=>t.replace('×','').trim()),'Robin Vale: Always');
  calls.length=0;await R('relationships').locator('.fleet-ex button').click();
  await page.waitForFunction(()=>!document.querySelector('.fleet-rule[data-member="*"][data-kind="relationships"] .fleet-ex'));
  assert.deepEqual(calls[0].args,{fleet:FL,member_id:'l',kind:'relationships',clear:true});
  await R('brief').locator('.fleet-ex-add summary').click();
  await R('brief').locator('[data-fleet-ex-member]').selectOption('q');
  assert.deepEqual(await R('brief').locator('[data-fleet-ex-rule]').allInnerTexts(),['Ask me first','Always']);  // not the Fleet's own choice
  await R('brief').locator('[data-fleet-ex-rule="always"]').click();
  await page.waitForFunction(()=>/Alexandra Longname-Whitfield: Always/.test(document.querySelector('.fleet-rule[data-member="*"][data-kind="brief"] .fleet-ex')?.textContent||''));
  assert.deepEqual(calls[1].args,{fleet:FL,member_id:'q',kind:'brief',rule:'always'});
  // The Fleet's own choice saves for everyone.
  await R('handling').locator('[data-fleet-rule][value="always"]').check({force:true});
  await page.waitForFunction(()=>/without asking you/.test(document.querySelector('.fleet-rule[data-member="*"][data-kind="handling"] .fleet-preview').textContent));
  assert.deepEqual(calls[2],{action:'fleet_rule',args:{fleet:FL,member_id:'*',kind:'handling',rule:'always'}});
  await R('relationships').locator('summary').first().click();
  await R('relationships').locator('[data-scope="never"]').fill('Globex, rival.example');
  await R('relationships').locator('[data-scope="never"]').press('Tab');
  await page.waitForFunction(()=>/not globex/i.test(document.querySelector('.fleet-rule[data-member="*"][data-kind="relationships"] .fleet-preview').textContent));
  assert.deepEqual(calls[3].args,{fleet:FL,member_id:'*',kind:'relationships',scope:{only:'acme.com',never:'Globex, rival.example',work_only:true}});
  const layout=await page.evaluate(()=>{const r=document.querySelector('.fleet-rules');
   return {fits:document.documentElement.scrollWidth<=innerWidth,inside:[...r.querySelectorAll('.seg, .fleet-preview, .fleet-scope input, .fleet-ex, .fleet-ex-add')].every(s=>s.getBoundingClientRect().right<=innerWidth+.5),
    stacked:getComputedStyle(r.querySelector('.fleet-rule')).gridTemplateColumns.split(' ').length===1}});
  assert.ok(layout.fits&&layout.inside,`${scheme} ${width}px scrolls sideways`);
  assert.equal(layout.stacked,width<600);
  if(shots){await page.evaluate(()=>window.scrollTo(0,0));await page.locator('#fleet-view').screenshot({path:`${shots}/${scheme}-${width}.png`})}
  await page.context().close();
 }
 console.log('fleet dashboard: ok');
}finally{await browser.close();server.close()}
