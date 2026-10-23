import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The Calendar moves like a calendar. The agenda runs on: later days load as it nears the end, earlier ones load
// above when you scroll up at the top (or tap Earlier days), and what is on screen doesn't move when they come in.
// Today, ‹ › and the week's range stay on top. Week is the calendar week, Monday to Sunday, and ‹ › move a whole
// week. t, ← → (k j), a and w do the same from the keyboard. The address holds the view and the week, so back and
// forward work. A week that can't load leaves a Try again. The ranges hold over the clock change at the end of
// October. Nothing is wider than the screen at 390 and 1440, and viewing sends nothing.
// CALENDAR_SHOT=<dir> also writes screenshots.
process.env.TZ='Europe/Brussels';
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const at=(days,h,m=0)=>{const d=new Date();d.setDate(d.getDate()+days);d.setHours(h,m,0,0);return d};
const key=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const monday=n=>{const d=at(0,0);d.setDate(d.getDate()-(d.getDay()+6)%7+7*n);return d};
const iso=d=>d.toISOString(),now=iso(new Date());
// A made-up meeting on most days, five weeks back to ten weeks on, so every week has something to load
const meetings=[];for(let n=-35;n<=70;n+=1)if(n%3)meetings.push({id:1000+n+35,account_id:'cal',title:`Sync ${n<0?'before':'after'} ${Math.abs(n)}`,start:iso(at(n,10)),end:iso(at(n,10,30)),all_day:false,location:'',guests:2,url:'https://calendar.example/e/'+n});
const cards=[{id:'a'.repeat(16),title:'Send the revised quote',body:'Made-up card.',status:'pending',scope:'work',created_at:now,event_ids:[],due:key(at(0,12))}];
let calls=[],asks=[],failFrom=null;const errors=[];
const state=()=>({suggestions:cards,promises:[],connectors:[{id:'cal',service:'calendar',status:'ok'}],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(brand[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[url.pathname],import.meta.url)));return}
 if(req.method!=='GET')calls.push(url.pathname+' '+body);
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/calendar'){const a=new Date(url.searchParams.get('from')),b=new Date(url.searchParams.get('to'));asks.push([a,b]);
  if(failFrom&&a>=failFrom){res.statusCode=500;res.end('{"error":"down"}');return}
  res.end(JSON.stringify({meetings:meetings.filter(m=>new Date(m.start)<b&&new Date(m.end)>a)}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
let browser;
const open=async(width,height,scheme='dark',hash='')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',timezoneId:'Europe/Brussels',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base+hash);return {context,page}};
const toCal=async page=>{await page.locator('#task-'+cards[0].id).waitFor();await page.evaluate(()=>showTab('calendar'));await page.locator('#calendar-view [data-day]').first().waitFor();await page.waitForFunction(()=>document.querySelectorAll('#calendar-view .cal-item').length>2)};
const view=page=>page.evaluate(()=>({hash:location.hash,range:document.querySelector('.cal-range')?.textContent,now:document.querySelector('.cal-today')?.classList.contains('now'),days:[...document.querySelectorAll('#calendar-view [data-day]')].map(e=>e.dataset.day),week:!!document.querySelector('.cal-tg')}));
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const headBottom=page=>page.evaluate(()=>document.querySelector('#page-head').getBoundingClientRect().bottom);
const topOf=(page,k)=>page.evaluate(k=>document.querySelector(`#calendar-view [data-day="${k}"]`)?.getBoundingClientRect().top,k);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 // Calendar weeks and their labels, over a year's end and the clock change on Sunday 25 October 2026
 {const {context,page}=await open(1440,900);await page.locator('#task-'+cards[0].id).waitFor();
  const r=await page.evaluate(()=>({sun:localDay(calMonday(new Date(2026,9,25,12))),mon:localDay(calMonday(new Date(2026,9,26,0,30))),
   next:[localDay(calAdd(new Date(2026,9,19),7)),calAdd(new Date(2026,9,19),7).getHours()],days:(()=>{const out=[];for(let d=new Date(2026,9,19);d<calAdd(new Date(2026,9,19),7);d=calAdd(d,1))out.push(localDay(d));return out})(),
   spans:[calSpan(new Date(2026,9,26)),calSpan(new Date(2026,10,2)),calSpan(new Date(2026,11,28)),calSpan(new Date(2026,8,28))]}));
  assert.equal(r.sun,'2026-10-19','a Sunday belongs to the week that started on Monday');assert.equal(r.mon,'2026-10-26');
  assert.deepEqual(r.next,['2026-10-26',0],'a week on from Monday 19 October is Monday 26 October, at midnight, over the clock change');
  assert.deepEqual(r.days,['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-24','2026-10-25'],'seven days, Sunday 25 once');
  assert.deepEqual(r.spans,['26 Oct – 1 Nov 2026','2 – 8 Nov 2026','28 Dec 2026 – 3 Jan 2027','28 Sept – 4 Oct 2026']);
  // A shared link to that week opens it in Week: one ask, Monday midnight in summer time to Monday midnight in winter time
  asks=[];await page.evaluate(()=>{location.hash='#calendar?view=week&date=2026-10-21'});await page.locator('.cal-tg').waitFor();
  const v=await view(page);assert.equal(v.range,'19 – 25 Oct 2026');assert.deepEqual(v.days,r.days);
  assert.ok(asks.some(([a,b])=>a.toISOString()==='2026-10-18T22:00:00.000Z'&&b.toISOString()==='2026-10-25T23:00:00.000Z'),JSON.stringify(asks));
  await context.close()}
 {const {context,page}=await open(1440,900);await toCal(page);
  // Opens on this week: Today is quiet, the agenda starts today, and the address says so
  let v=await view(page);const today=key(at(0,0)),thisMon=key(monday(0)),nextMon=key(monday(1)),lastMon=key(monday(-1));
  assert.equal(v.now,true);assert.equal(v.days[0],today);assert.equal(v.hash,'#calendar?view=agenda&date='+thisMon);
  // › brings next Monday under the header; Today is no longer quiet; back returns
  await page.locator('[data-cal-go="1"]').click();await page.waitForFunction(k=>location.hash.endsWith(k),nextMon);
  v=await view(page);assert.equal(v.now,false);assert.ok(Math.abs(await topOf(page,nextMon)-await headBottom(page))<16,'next Monday at the top');
  await page.goBack();await page.waitForFunction(k=>location.hash.endsWith(k),thisMon);
  await page.locator('[data-cal-go="0"]').click();await page.waitForFunction(()=>document.querySelector('.cal-today').classList.contains('now'));
  // Scrolling down loads the following weeks, a calendar week per ask
  const had=(await view(page)).days.length;asks=[];for(let i=0;i<12&&(await view(page)).days.length<had+14;i++){await page.mouse.wheel(0,2500);await page.waitForTimeout(250)}
  v=await view(page);assert.ok(v.days.length>=had+14,'later weeks came in: '+v.days.length);assert.ok(asks.length&&asks.every(([a,b])=>a.getDay()===1&&b.getDay()===1));
  assert.ok(await page.locator('.cal-wk').count(),'each new week is named');
  // ... and the header follows the week on screen, in the address too
  await page.waitForFunction(k=>!document.querySelector('.cal-range').textContent.startsWith(k),String(at(0,0).getDate()));
  // Scrolling up at the top loads the earlier days above, and the day on screen stays where it was
  await page.locator('[data-cal-go="0"]').click();await page.waitForTimeout(300);
  const first=(await view(page)).days[0],seen=await page.evaluate(()=>{scrollTo(0,0);return calAnchor(document.querySelector('#calendar-view'))});
  // (a wheel turn that can't scroll; a real one would also move the page up once the days are in)
  await page.evaluate(()=>dispatchEvent(new WheelEvent('wheel',{deltaY:-400})));await page.waitForFunction(k=>document.querySelector('#calendar-view [data-day]').dataset.day<k,first);await page.waitForTimeout(300);
  const after=await topOf(page,seen.day);assert.ok(Math.abs(after-seen.top)<2,`no jump: ${seen.day} at ${seen.top}, then ${after}`);
  // The tap does the same
  const tap=await page.evaluate(()=>{scrollTo(0,0);return [document.querySelector('#calendar-view [data-day]').dataset.day,calAnchor(document.querySelector('#calendar-view'))]});
  await page.locator('[data-cal-more="up"] button').click({timeout:5000}).catch(()=>{});await page.waitForFunction(k=>document.querySelector('#calendar-view [data-day]').dataset.day<k,tap[0]);
  v=await view(page);assert.ok(v.days[0]<=lastMon||v.days[0]===thisMon,'back to a Monday: '+v.days[0]);
  assert.ok(await page.locator('.cal-item.past',{hasText:'Sync before'}).count(),'past meetings step back');
  if(process.env.CALENDAR_SHOT)await page.screenshot({path:`${process.env.CALENDAR_SHOT}/scroll-1440-dark-earlier.png`});
  // Keys: w Week, → next week, k back, t this week, a Agenda
  // Week opens on the week in view: back at this week first
  await page.keyboard.press('t');await page.waitForFunction(k=>location.hash.endsWith(k),thisMon);
  await page.keyboard.press('w');await page.locator('.cal-tg').waitFor();v=await view(page);assert.equal(v.hash,'#calendar?view=week&date='+thisMon);assert.equal(v.days[0],thisMon);
  await page.keyboard.press('ArrowRight');await page.waitForFunction(k=>location.hash.endsWith(k),nextMon);v=await view(page);assert.equal(v.days[0],nextMon);assert.equal(v.days.length,7);
  assert.equal(await page.locator('.cal-tg-day.today').count(),0,'no today in next week');
  if(process.env.CALENDAR_SHOT)for(const scheme of ['dark','light']){await page.emulateMedia({colorScheme:scheme});await page.waitForTimeout(300);await page.screenshot({path:`${process.env.CALENDAR_SHOT}/week-1440-${scheme}-next.png`})}
  await page.keyboard.press('k');await page.keyboard.press('k');await page.waitForFunction(k=>location.hash.endsWith(k),lastMon);
  await page.keyboard.press('t');await page.waitForFunction(k=>location.hash.endsWith(k),thisMon);assert.equal(await page.locator('.cal-tg-day.today').count(),1);
  await fits(page,'week 1440');
  await page.goBack();await page.waitForFunction(k=>location.hash.endsWith(k),lastMon);assert.equal((await view(page)).range,(await page.evaluate(k=>calSpan(calOf(k)),lastMon)));
  await page.keyboard.press('a');await page.waitForFunction(()=>!document.querySelector('.cal-tg'));
  // Typing in a field is never a shortcut
  await page.evaluate(()=>{const i=document.createElement('input');i.id='typing';document.querySelector('#calendar').prepend(i)});await page.locator('#typing').press('w');assert.equal((await view(page)).week,false);
  await context.close()}
 // A week that can't load: no blank days, a Try again that works once it's back
 {const {context,page}=await open(390,844);failFrom=monday(2);await toCal(page);
  for(let i=0;i<10&&!(await page.locator('[data-cal-more="down"] button',{hasText:'Try again'}).count());i++){await page.mouse.wheel(0,2500);await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await page.waitForTimeout(250)}
  assert.equal(await page.locator('[data-cal-more="down"] button').textContent(),'Couldn’t load. Try again');
  const last=(await view(page)).days.at(-1);assert.ok(last<key(monday(2)),'only the weeks that loaded: '+last);
  failFrom=null;await page.locator('[data-cal-more="down"] button').click();await page.waitForFunction(k=>[...document.querySelectorAll('#calendar-view [data-day]')].some(e=>e.dataset.day>=k),key(monday(2)));
  await fits(page,'agenda 390');
  // On a phone the header stays under the top bar while the days scroll, with Today and ‹ › at hand
  await page.evaluate(()=>scrollTo(0,1500));await page.waitForTimeout(200);const bar=await page.evaluate(()=>[document.querySelector('#topbar').getBoundingClientRect().bottom,document.querySelector('#page-head').getBoundingClientRect().top]);
  assert.ok(Math.abs(bar[0]-bar[1])<2,'the header sits under the top bar: '+bar);assert.ok(await page.locator('[data-cal-go="0"]').isVisible());
  if(process.env.CALENDAR_SHOT)for(const scheme of ['dark','light']){await page.emulateMedia({colorScheme:scheme});await page.waitForTimeout(300);await page.screenshot({path:`${process.env.CALENDAR_SHOT}/scroll-390-${scheme}.png`})}
  await context.close()}
 assert.deepEqual(calls,[],'viewing sends nothing');
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log(JSON.stringify({asks:asks.length,posts:calls.length,page_errors:errors}));
