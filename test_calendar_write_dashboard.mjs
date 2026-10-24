import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Changing a calendar from The Machine: New event (with a calendar picker) on the owner's own calendars and shared ones
// they may change, and an editor on an event there without guests. Every change is the owner's tap: Add, Save, or
// Delete twice. A card's calendar change shows exactly what changes, and only Add confirms that revision. Meetings with
// guests and read-only shared calendars open in their calendar as before. Synthetic data, a local fixture server, no
// calendar is touched. Nothing is wider than the screen at 360. SHOT_DIR=<folder> saves screenshots at 1440, 390 and 360, light and dark.
process.env.TZ='Europe/Brussels';
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const at=(days,h,m=0)=>{const d=new Date();d.setDate(d.getDate()+days);d.setHours(h,m,0,0);return d};
const pad=n=>String(n).padStart(2,'0'),day=n=>{const d=at(n,12);return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())};
const iso=d=>d.toISOString(),now=iso(new Date());
const local=d=>{const o=-d.getTimezoneOffset();return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes())+':00'+(o<0?'-':'+')+pad(Math.floor(Math.abs(o)/60))+':'+pad(Math.abs(o)%60)};
const calendars=[{id:'cal',name:'owner@example.com',own:true},{id:'kid',name:'Kid’s calendar',own:false}];
const meetings=[
 {id:201,account_id:'kid',title:'Swimming',start:iso(at(1,10)),end:iso(at(1,11)),all_day:false,location:'Pool',guests:0,people:[],shared:'Kid',url:'https://calendar.google.com/calendar/event?eid=swim',edit:{start:local(at(1,10)),end:local(at(1,11)),all_day:false}},
 {id:202,account_id:'kid',title:'Daycare closed',start:iso(at(2,0)),end:iso(at(3,0)),all_day:true,location:'',guests:0,people:[],shared:'Kid',url:null,edit:{start:day(2),end:day(2),all_day:true}},
 {id:203,account_id:'cal',title:'Quarterly planning',start:iso(at(1,14)),end:iso(at(1,15)),all_day:false,location:'',guests:8,url:'https://calendar.google.com/calendar/event?eid=plan'},
 {id:204,account_id:'partner',title:'Away',start:iso(at(1,8)),end:iso(at(1,9)),all_day:false,location:'',guests:0,people:[],shared:'Robin',url:'https://calendar.google.com/calendar/event?eid=away'}];
const sid='abcdef0123456789',revision='a1b2c3d4e5f6';
const change={kind:'calendar',op:'create',revision,label:'Add to calendar',account_id:'kid',calendar_name:'Kid’s calendar',title:'Doctor check-up',when:'Thu 30 Oct 2026, 09:30 to 10:00',location:'Health centre',start:'2026-10-30T09:30:00+01:00',end:'2026-10-30T10:00:00+01:00'};
const card={id:sid,title:'Book the check-up',body:'The doctor confirmed Thursday 9:30.',status:'prepared',scope:'personal',created_at:now,event_ids:[],next_action:change,next_actions:[change],
 outcome:JSON.stringify({state:'needs_you',needs:'Add it to the calendar'})};
let calls=[],answer={status:'done',calendar:'Kid’s calendar'},writable=true;
const state=()=>({suggestions:[card],promises:[],connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/calendar'){res.end(JSON.stringify({meetings:writable?meetings:meetings.map(({edit,...m})=>m),calendars:writable?calendars:[]}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body||'{}');calls.push(r);res.end(JSON.stringify(r.action==='calendar_write'?answer:r.action==='card_chat'?{session:true,status:'ok',active:false,items:[]}:{status:'queued'}));return}
 res.statusCode=url.pathname.startsWith('/api/')?200:404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/',shots=process.env.SHOT_DIR;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
const writes=()=>calls.filter(c=>c.action==='calendar_write');
const open=async(width,height,scheme='dark')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',timezoneId:'Europe/Brussels',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));
 await page.goto(base);await page.locator('#task-'+sid).waitFor();return {context,page}};
const showCal=async page=>{await page.evaluate(()=>showTab('calendar'));await page.locator('#calendar-view .cal-item').first().waitFor();await page.waitForFunction(()=>!document.querySelector('#calendar-view').textContent.includes('Loading'))};
const wait=async(page,f)=>{for(let i=0;i<150&&!f();i++)await page.waitForTimeout(20)};
const fits=page=>page.evaluate(()=>[...document.querySelectorAll('#cal-dialog *')].filter(e=>e.getBoundingClientRect().right>innerWidth+.5).length+(document.documentElement.scrollWidth>innerWidth?1:0)).then(n=>assert.equal(n,0,'the form fits'));
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1440,900],[390,844],[360,780]])for(const scheme of ['dark','light']){const {context,page}=await open(width,height,scheme);calls=[];
  // The card: exactly what changes, on which calendar; Add opens the review and only its Add confirms this revision
  const task=page.locator('#task-'+sid);
  assert.equal(await task.locator('.draft.cal-change .dm').textContent(),'Add to Kid’s calendar');
  assert.equal(await task.locator('.draft.cal-change .db').textContent(),'Doctor check-up\nThu 30 Oct 2026, 09:30 to 10:00\nWhere: Health centre');
  if(shots)await task.screenshot({path:`${shots}/calwrite-card-${width}-${scheme}.png`});
  await task.locator(`[data-smart="${revision}"]`).click();await page.locator('#send-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#send-title').textContent(),'Add to Kid’s calendar?');assert.equal(await page.locator('#send-confirm').textContent(),'Add');
  const head=await page.locator('#send-head').textContent();assert.ok(head.includes('Doctor check-up')&&head.includes('09:30 to 10:00')&&head.includes('Health centre'),head);
  assert.equal(await page.locator('#send-fine').textContent(),'Nobody is invited or gets an update.');
  assert.ok(await page.locator('#send-edit').isHidden());assert.equal(calls.filter(c=>c.action==='confirm_action').length,0);
  if(shots)await page.locator('#send-dialog').screenshot({path:`${shots}/calwrite-review-${width}-${scheme}.png`});
  await page.locator('#send-confirm').click();await wait(page,()=>calls.some(c=>c.action==='confirm_action'));
  const confirm=calls.find(c=>c.action==='confirm_action');assert.deepEqual([confirm.args.suggestion_id,confirm.args.revision],[sid,revision]);
  assert.equal(writes().length,0,'a card change runs through its approval, not the form');
  // The Calendar: New event with a picker of the calendars that can be changed
  await showCal(page);calls=[];
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.querySelector('[data-cal-new]').getBoundingClientRect().right<=innerWidth),'the Calendar fits at '+width);
  await page.locator('[data-cal-new]').click();await page.locator('#cal-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#cal-dlg-title').textContent(),'New event');
  assert.deepEqual(await page.locator('#cal-f-cal option').allTextContents(),['owner@example.com','Kid’s calendar']);
  await page.locator('#cal-f-cal').selectOption('kid');
  await page.locator('#cal-dialog [type="submit"]').click();assert.equal(await page.locator('#cal-f-error').textContent(),'Give it a title.');assert.equal(writes().length,0);
  await page.locator('#cal-f-title').fill('Pick-up from daycare');await page.locator('#cal-f-date').fill(day(3));
  await page.locator('#cal-f-start').fill('16:00');await page.locator('#cal-f-end').fill('16:30');await page.locator('#cal-f-where').fill('Daycare');
  await fits(page);
  if(shots)await page.locator('#cal-dialog').screenshot({path:`${shots}/calwrite-new-${width}-${scheme}.png`});
  await page.locator('#cal-dialog [type="submit"]').click();await wait(page,()=>writes().length);await page.locator('#cal-dialog').waitFor({state:'hidden'});
  const made=writes()[0].args;assert.match(made.request_id,/^[a-f0-9-]{36}$/);
  assert.deepEqual({...made,request_id:0},{request_id:0,op:'create',calendar:'kid',title:'Pick-up from daycare',location:'Daycare',notes:'',all_day:false,start:local(at(3,16)),end:local(at(3,16,30))});
  assert.match(await page.locator('#toast').textContent(),/^Added to Kid’s calendar\.$/);
  // Next time the picker starts on the same calendar
  await page.locator('[data-cal-new]').click();assert.equal(await page.locator('#cal-f-cal').inputValue(),'kid');await page.locator('#cal-dialog [data-cal-close]').first().click();
  // An event there without guests opens the editor; Save sends only what changed
  calls=[];const swim=page.locator('#calendar-view [data-cal-edit="201"]').first();assert.match(await swim.textContent(),/Swimming/);
  await swim.click();await page.locator('#cal-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#cal-dlg-title').textContent(),'Change event');assert.ok(await page.locator('#cal-f-cal').isDisabled());
  assert.equal(await page.locator('#cal-f-title').inputValue(),'Swimming');assert.equal(await page.locator('#cal-f-start').inputValue(),'10:00');
  await page.locator('#cal-dialog [type="submit"]').click();await page.locator('#cal-dialog').waitFor({state:'hidden'});assert.equal(writes().length,0,'nothing changed: nothing sent');
  await swim.click();await page.locator('#cal-f-title').fill('Swimming lesson');
  if(shots)await page.locator('#cal-dialog').screenshot({path:`${shots}/calwrite-edit-${width}-${scheme}.png`});
  await page.locator('#cal-dialog [type="submit"]').click();await wait(page,()=>writes().length);
  assert.deepEqual({...writes()[0].args,request_id:0},{request_id:0,op:'update',event:201,title:'Swimming lesson'});
  // Delete takes two taps; a refusal stays in the form
  calls=[];answer={status:'failed',error:'Kid’s calendar is shared with you to view only. Nothing was changed.'};
  await page.locator('#cal-dialog').waitFor({state:'hidden'});await swim.click();
  await page.locator('#cal-f-delete').click();assert.equal(await page.locator('#cal-f-delete').textContent(),'Delete it');assert.equal(writes().length,0);
  if(shots)await page.locator('#cal-dialog').screenshot({path:`${shots}/calwrite-delete-${width}-${scheme}.png`});
  await page.locator('#cal-f-delete').click();await wait(page,()=>writes().length);
  assert.deepEqual({...writes()[0].args,request_id:0},{request_id:0,op:'delete',event:201});
  await page.waitForFunction(()=>document.querySelector('#cal-f-error').textContent.includes('view only'));assert.ok(await page.locator('#cal-dialog').isVisible());
  answer={status:'done',calendar:'Kid’s calendar'};await page.locator('#cal-dialog [data-cal-close]').first().click();
  // An all-day event keeps its last day
  await page.locator('#calendar-view [data-cal-edit="202"]').first().click();assert.ok(await page.locator('#cal-f-allday').isChecked());
  assert.deepEqual([await page.locator('#cal-f-date').inputValue(),await page.locator('#cal-f-until').inputValue()],[day(2),day(2)]);await page.locator('#cal-dialog [data-cal-close]').first().click();
  // Meetings with guests and read-only shared calendars: no editor, they open in their calendar
  for(const t of ['Quarterly planning','Away']){const row=page.locator('#calendar-view .cal-item',{hasText:t}).first();assert.equal(await row.getAttribute('data-cal-edit'),null,t);assert.equal(await row.getAttribute('target'),'_blank',t)}
  if(shots){await page.waitForTimeout(300);await page.screenshot({path:`${shots}/calwrite-calendar-${width}-${scheme}.png`})}
  await context.close()}
 // Nothing to change: no New event and no editor
 {writable=false;const {context,page}=await open(1440,900);await showCal(page);assert.equal(await page.locator('[data-cal-new]').count(),0);assert.equal(await page.locator('[data-cal-edit]').count(),0);await context.close();writable=true}
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,page_errors:errors}));
}finally{await browser?.close();server.close()}
