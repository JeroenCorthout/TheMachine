import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The top of Fleet and its Handoffs box, at 390px and 1440px, light and dark:
// - "One Fleet item waits for you under Suggestions." sits in a quiet notice card (pageNotice), its text centred with a
//   Show button on the right, with room above the Fleet tabs; plural with two items; the Network off is the same card.
// - Handoffs have their own box, not the "Who knows someone?" one: open ones first, accepted and done ones quieter with
//   three shown and a Show more, the time leading two lines of summary, and the whole row opening its card where it is still there.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const FL='fl_'+'a'.repeat(16),hex=n=>String(n).padStart(16,'c'),at=h=>new Date(Date.now()-h*3600e3).toISOString();
const rules=()=>({relationships:{rule:'ask',details:false},handoff:{rule:'ask',details:false}});
const ho=(id,name,title,status,answer,h,card)=>({id,fleet:FL,member_id:name[0],name,kind:'handoff',context:'handoff:'+hex(card),status,at:at(h),answered_at:answer?at(h-1):null,
 question:`Can you take this on: ${title}? Their summary: “They asked twice already, before Friday please.”\n   Contact: lee@globex.example.`,answer});
const waitingIn=n=>Array.from({length:n},(_,k)=>({id:'q_'+String(k).repeat(16),fleet:FL,member_id:'K',name:'Kim',kind:'handoff',at:at(2+k),
 question:'Can you take this on: Renewal call with Initech? Their summary: “They want a call on Thursday.”'}));
const base={on:true,kinds:{relationships:'who knows someone',handoff:'handoffs'},kind_rules:{relationships:['ask','always','never'],handoff:['ask','always','never']},no_details:[],scoped:[],scopes:{},
 fleets:[{id:FL,kind:'work',label:'Work Fleet',name:'Acme',asks:['relationships','handoff'],
  people:[{member_id:'m',name:'Sam Doe',role:'owner',me:true,email:'sam@acme.example'},{member_id:'P',name:'Pat',role:'member',email:'pat@acme.example'},{member_id:'K',name:'Kim',role:'member',email:'kim@acme.example'}],
  members:[{member_id:'P',name:'Pat',rules:rules()},{member_id:'K',name:'Kim',rules:rules()}],rules:rules(),exceptions:{}}],
 intro_offers:[],free:[],briefs:{},notices:[],log:[],waiting:waitingIn(1),
 asked:[{id:'q1',fleet:FL,member_id:'P',name:'Pat',kind:'relationships',context:null,status:'answered',at:at(30),question:'Who knows someone at globex.example?',answer:'2 people know them, one well'},
  ho('h1','Pat','Send Globex the pricing','asked',null,1,1),
  ho('h2','Kim','Reply to the Umbrella invoice question','answered','declined: “Out until Monday”',5,2),
  ho('h3','Pat','Book the Hooli demo','answered','accepted',20,3),
  ho('h4','Kim','Share the onboarding notes','answered','done',50,4),
  ho('h5','Pat','Follow up on the Stark contract','answered','done',80,5),
  ho('h6','Kim','Send the Q3 numbers','answered','done',120,6),
  ho('h7','Pat','Check the Wayne renewal date','answered','done',200,7)]};
const cards=[1,3].map(n=>({id:hex(n),title:'Card '+n,body:'A made-up card.',status:'prepared',created_at:at(3),scope:'work',event_ids:[]}));
let shown=base;
const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify({suggestions:cards,connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:shown.waiting.length}}));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify(shown));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const box=(page,sel)=>page.locator(sel).first().evaluate(e=>{const r=e.getBoundingClientRect();return{top:r.top,bottom:r.bottom,left:r.left,right:r.right,mid:(r.top+r.bottom)/2}});
try{
 for(const [scheme,width] of [['light',390],['dark',390],['light',1440],['dark',1440]]){
  const page=await (await browser.newContext({viewport:{width,height:900},colorScheme:scheme})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const open=async f=>{shown=f;await page.goto('http://127.0.0.1:'+server.address().port);await navClick(page.locator('nav [data-tab="fleet"]'));await page.locator('.fleet-tabs').waitFor()};
  await open(base);
  const at_=`${scheme} ${width}px`;
  // The notice: one card, before the tabs, text and Show inside it, nothing past the screen's edge.
  const notice=page.locator('#fleet-view>.notice-card');
  assert.equal(await notice.count(),1,at_);
  assert.equal(await notice.locator('span').innerText(),'One Fleet item waits for you under Suggestions.');
  assert.equal(await page.locator('#fleet-view p.note').count(),0,'no bare notice line');
  const [card,text,show,tabs,title]=await Promise.all(['#fleet-view>.notice-card','#fleet-view>.notice-card>span','#fleet-view>.notice-card .head-link','.fleet-tabs','#page-head'].map(s=>box(page,s)));
  assert.equal(await notice.locator('.head-link').innerText(),'Show');
  assert.ok(card.right<=width+.5&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${at_}: the notice scrolls sideways`);
  assert.ok(show.right<=card.right-8&&show.left>=card.left&&show.top>=card.top&&show.bottom<=card.bottom,`${at_}: Show is inside the card`);
  assert.ok(card.right-show.right<24,`${at_}: Show sits at the right of the card`);
  assert.ok(text.right<=show.left||text.bottom<=show.top+1,`${at_}: the text comes first`);
  if(width===1440)assert.ok(Math.abs(text.mid-show.mid)<=1.5,`${at_}: text centred with Show (${text.mid} vs ${show.mid})`);
  const gap=tabs.top-card.bottom;assert.ok(gap>=12&&gap<=32,`${at_}: card to tabs ${gap}px`);
  assert.ok(card.top-title.bottom>=16,`${at_}: room under the title`);
  assert.ok(await notice.evaluate(e=>{const s=getComputedStyle(e);return parseFloat(s.borderTopLeftRadius)>=10&&s.borderTopStyle==='solid'}),`${at_}: a rounded, bordered card`);
  // Handoffs: their own box; open ones first, then the quiet ones, three shown.
  const who=page.locator('.fleet-box',{hasText:'Who knows someone?'});
  assert.equal(await who.getByText('Can you take this on').count(),0,'no handoffs under Who knows someone');
  assert.equal(await who.getByText('Who knows someone at globex.example?').count(),1);
  const hos=page.locator('.fleet-handoffs');
  assert.equal(await hos.locator('h3').innerText(),'Handoffs');
  assert.deepEqual(await hos.locator('.fleet-ho:not([hidden]) .fleet-ho-top strong').allInnerTexts(),['From Kim','To Pat','To Kim','To Pat','To Kim','To Pat']);
  assert.deepEqual(await hos.locator('.fleet-ho:not([hidden]) .chip').allInnerTexts(),['Waits for you','Waiting for an answer','Declined','Accepted','Done','Done']);
  assert.equal(await hos.locator('.fleet-ho.quiet').count(),5);
  assert.equal(await hos.locator('.fleet-ho[hidden]').count(),2);
  assert.equal(await hos.locator('.fleet-ho.quiet:not([hidden])').count(),3,'at most three quiet rows before Show more');
  // The same layout on every row: the time leads the summary (never under the name), at most two lines of it.
  const rows=await hos.locator('.fleet-ho:not([hidden])').evaluateAll(li=>li.map(r=>{const t=r.querySelector('.fleet-ho-sum time'),s=r.querySelector('.fleet-ho-sum');
   return{inSum:!!t&&Math.abs(t.getBoundingClientRect().top-s.getBoundingClientRect().top)<=2,top:!r.querySelector('.fleet-ho-top time'),lines:Math.round(s.getBoundingClientRect().height/parseFloat(getComputedStyle(s).lineHeight))}}));
  for(const r of rows){assert.ok(r.inSum&&r.top,`${at_}: the time is on the summary line`);assert.ok(r.lines<=2,`${at_}: two lines of summary at most (${r.lines})`)}
  // No Open button: a row whose card is still there is a tap row with a chevron (Kim's under Suggestions, the cards
  // handed to Pat); the others are plain, not focusable.
  assert.equal(await hos.locator('a,button:not([data-ho-more])').count(),0);
  assert.deepEqual(await hos.locator('.fleet-ho:not([hidden])').evaluateAll(li=>li.map(r=>r.classList.contains('tap')?r.dataset.hoGo:'')),['#fleet=q_'+'0'.repeat(16),'#card='+hex(1),'','#card='+hex(3),'','']);
  assert.equal(await hos.locator('.fleet-ho.tap').first().evaluate(e=>getComputedStyle(e,'::after').content),'"›"');
  assert.equal(await hos.locator('.fleet-ho:not(.tap)').first().evaluate(e=>getComputedStyle(e,'::after').content),'none');
  assert.equal(await hos.locator('.fleet-ho:not(.tap)[tabindex]').count(),0);
  assert.match(await hos.locator('.fleet-ho-sum').nth(2).innerText(),/Out until Monday/);
  await hos.locator('[data-ho-more]').click();
  assert.equal(await hos.locator('.fleet-ho[hidden]').count(),0);
  assert.ok(await page.evaluate(()=>[...document.querySelectorAll('.fleet-handoffs *')].every(e=>e.getBoundingClientRect().right<=innerWidth+.5)),`${at_}: Handoffs scroll sideways`);
  // Tapping a row opens its card under Suggestions; Enter does too.
  if(width===390)await hos.locator(`[data-ho-go="#card=${hex(1)}"] .fleet-ho-sum`).click();
  else{await hos.locator(`[data-ho-go="#card=${hex(1)}"]`).focus();await page.keyboard.press('Enter')}
  await page.locator(`#task-${hex(1)}`).waitFor();
  // Two items: plural. The Network off: the same card, without a button.
  await open({...base,waiting:waitingIn(2)});
  assert.equal(await page.locator('#fleet-view>.notice-card span').innerText(),'2 Fleet items wait for you under Suggestions.');
  await page.locator('#fleet-view>.notice-card .head-link').click();
  await page.locator('#inbox:not([hidden])').waitFor();
  await open({...base,off:true});
  assert.equal(await page.locator('#fleet-view>.notice-card').first().innerText(),'The Network is off for now. Nothing is asked or answered.');
  assert.equal(await page.locator('#fleet-view>.notice-card').first().locator('button').count(),0);
  assert.deepEqual(errors,[]);
  await page.context().close();
 }
 console.log('fleet notice dashboard: ok');
}finally{await browser.close();server.close()}
