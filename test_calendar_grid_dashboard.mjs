import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The Calendar's real calendar views, next to the agenda (which stays the default). Week on a computer is a time grid:
// hours down the side, Monday to Sunday, each meeting a block as long as it lasts, ones that overlap side by side,
// All day and undated items (due cards, promises) in a strip on top, a line at now on today. On a phone Week is three
// days, swiped through. Month is the calendar month, Monday on or before the 1st to Sunday on or after the last, the
// days around it dimmed: up to three items a day and "+N more" on a computer, dots on a phone where a tap lists the
// day. ‹ › move by the view's span, m picks Month, and the address holds it. Blocks keep their clock time over the
// change at the end of October. Nothing is wider than the screen, and viewing sends nothing.
// CALENDAR_SHOT=<dir> also writes screenshots.
process.env.TZ='Europe/Brussels';
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const at=(days,h,m=0)=>{const d=new Date();d.setDate(d.getDate()+days);d.setHours(h,m,0,0);return d};
const key=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const iso=d=>d.toISOString(),now=iso(new Date());
const mtg=(id,title,a,b,extra={})=>({id,account_id:'cal',title,start:iso(a),end:iso(b),all_day:false,location:'',guests:2,url:'https://calendar.example/e/'+id,...extra});
const meetings=[
 // Today, like a real calendar: half hours and hours on the quarter hour, three at once at 10:30, back to back at 9, a quarter hour at 13:15
 mtg(6,'Standup',at(0,9),at(0,9,30)),mtg(7,'Hiring sync',at(0,9,30),at(0,10)),
 mtg(1,'Design review',at(0,10),at(0,11)),mtg(2,'Pilot call',at(0,10,30),at(0,11)),mtg(8,'Vendor demo',at(0,10,30),at(0,11,15)),
 mtg(3,'Lunch with Kai',at(0,12),at(0,13)),mtg(10,'Quick call',at(0,13,15),at(0,13,30)),mtg(11,'Follow-up',at(0,13,30),at(0,14)),mtg(9,'Quarterly check-in with Harborline',at(0,14,15),at(0,14,45)),
 mtg(4,'Conference',at(1,0),at(3,0),{all_day:true}),mtg(5,'Their dentist',at(1,9),at(1,10),{shared:'Robin',url:''}),
 // The weekend of the clock change: 07:00 on Saturday (summer time) and on Sunday (winter time), before anything else that day
 mtg(20,'Saturday run',new Date('2026-10-24T05:00:00Z'),new Date('2026-10-24T06:00:00Z')),mtg(21,'Sunday walk',new Date('2026-10-25T06:00:00Z'),new Date('2026-10-25T07:00:00Z')),
 // A busy day in October 2026
 ...[8,9,10,11,14].map((h,i)=>mtg(30+i,'Call '+(i+1),new Date(2026,9,14,h),new Date(2026,9,14,h,30)))];
const promises=[{id:7,direction:'mine',who:'Wren Fairhaven',what:'send the pilot recap',due:key(at(1,0)),state:'open',made_at:now,check_at:now}];
const cards=[{id:'a'.repeat(16),title:'Send the revised quote',body:'Made-up card.',status:'pending',scope:'work',created_at:now,event_ids:[],due:key(at(2,0))}];
let calls=[],asks=[];const errors=[];
const state=()=>({suggestions:cards,promises,connectors:[{id:'cal',service:'calendar',status:'ok'}],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(brand[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[url.pathname],import.meta.url)));return}
 if(req.method!=='GET')calls.push(url.pathname+' '+body);
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/calendar'){const a=new Date(url.searchParams.get('from')),b=new Date(url.searchParams.get('to'));asks.push([a,b]);
  res.end(JSON.stringify({meetings:meetings.filter(m=>new Date(m.start)<b&&new Date(m.end)>a)}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
let browser;
const open=async(width,height,scheme='dark',hash='')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',timezoneId:'Europe/Brussels',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));await page.goto(base+hash);await page.locator('#task-'+cards[0].id).waitFor();return {context,page}};
const go=async(page,hash)=>{await page.evaluate(h=>{location.hash=h},hash);await page.waitForFunction(h=>location.hash===h&&!document.querySelector('#calendar').hidden,hash)};
const range=page=>page.locator('.cal-range').textContent();
// Where the time grid opens: the first timed meeting (or 08:00 when earlier) with a little room above, and the now
// line in view later in the day
const opensAt=async(page,where)=>{await page.waitForFunction(()=>{const p=document.querySelector('.cal-tg-scroll');return p&&p.clientHeight&&p.scrollTop>0});
 const s=await page.evaluate(()=>{const p=document.querySelector('.cal-tg-scroll'),tops=[...p.querySelectorAll('.cal-blk')].map(b=>parseFloat(b.style.top)),n=p.querySelector('.cal-now');
  return {top:p.scrollTop,h:p.clientHeight,first:Math.min(8*CAL_HOUR,...tops),now:n?parseFloat(n.style.top):null,hour:CAL_HOUR}});
 const seen=y=>y>=s.top&&y<=s.top+s.h;
 if(s.now!=null&&s.now>s.first)assert.ok(seen(s.now),where+': the now line is in view '+JSON.stringify(s));
 else assert.ok(Math.abs(s.top-(s.first-s.hour/2))<2,where+': opens half an hour before the first meeting '+JSON.stringify(s));
 return s};
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth})).then(s=>assert.ok(s.page<=s.vw,`${where}: wider than the screen ${JSON.stringify(s)}`));
const shot=async(page,name)=>{if(process.env.CALENDAR_SHOT)await page.screenshot({path:`${process.env.CALENDAR_SHOT}/${name}.png`,fullPage:true})};
const swipe=(page,dx)=>page.evaluate(dx=>{const el=document.querySelector('.cal-tg-body'),r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+40;
 const t=(cx)=>new Touch({identifier:1,target:el,clientX:cx,clientY:y});
 el.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[t(x)],changedTouches:[t(x)]}));el.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[],changedTouches:[t(x+dx)]}))},dx);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 // Month boundaries: February 2026 starts on a Sunday, so its grid starts Monday 26 January; October 2026 runs from
 // Monday 28 September to Sunday 1 November, over the clock change
 {const {context,page}=await open(1440,900);
  const r=await page.evaluate(()=>[new Date(2026,1,1),new Date(2026,9,1),new Date(2026,10,1),new Date(2026,5,1)].map(m=>{const [a,b]=calMonthDays(m),last=calAdd(b,-1);return [localDay(a),localDay(last),Math.round((b-a)/864e5),b.getHours()]}));
  assert.deepEqual(r,[['2026-01-26','2026-03-01',35,0],['2026-09-28','2026-11-01',35,0],['2026-10-26','2026-12-06',42,0],['2026-06-01','2026-07-05',35,0]]);
  // A link to February: its grid, the days around it dimmed, the weeks it covers asked once each
  asks=[];await go(page,'#calendar?view=month&date=2026-02-01');await page.locator('.cal-mg').waitFor();
  assert.equal(await range(page),'February 2026');
  const cells=await page.locator('.cal-mday').evaluateAll(c=>c.map(x=>[x.dataset.day,x.classList.contains('out')]));
  assert.equal(cells.length,35);assert.deepEqual(cells[0],['2026-01-26',true]);assert.deepEqual(cells[6],['2026-02-01',false]);assert.deepEqual(cells.at(-1),['2026-03-01',true]);
  assert.equal(cells.filter(c=>c[1]).length,7,'six January days and 1 March step back');
  assert.ok(['2026-01-26','2026-02-02','2026-02-23'].every(k=>asks.some(([a])=>key(a)===k)),'the month\'s weeks are asked, Monday to Monday');
  assert.deepEqual(await page.locator('.cal-mg-head span').allTextContents(),['Mon','Tue','Wed','Thu','Fri','Sat','Sun']);
  // ‹ › move a whole month; a busy day shows three and "+2 more", which opens that day in the agenda
  await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>location.hash==='#calendar?view=month&date=2026-03-01');assert.equal(await range(page),'March 2026');
  await go(page,'#calendar?view=month&date=2026-10-01');await page.locator('.cal-mday[data-day="2026-10-14"] .cal-chip').first().waitFor();
  const busy=page.locator('.cal-mday[data-day="2026-10-14"]');assert.equal(await busy.locator('.cal-chip').count(),3);assert.equal(await busy.locator('.cal-mmore').textContent(),'+2 more');
  assert.equal(await page.locator('.cal-mday').count(),35);assert.equal(await page.locator('.cal-mday.out').count(),4);
  await fits(page,'month 1440');await shot(page,'grid-month-1440');
  await busy.locator('.cal-mmore').click();await page.waitForFunction(()=>location.hash==='#calendar?view=agenda&date=2026-10-12');
  assert.equal(await page.locator('#calendar-view .cal-block[data-day="2026-10-14"] .cal-item').count(),5);
  // The weekend of the clock change: 07:00 sits at 07:00 on both days
  await go(page,'#calendar?view=week&date=2026-10-19');await page.locator('.cal-tg-col[data-col="2026-10-25"] .cal-blk',{hasText:'Sunday walk'}).waitFor();
  await opensAt(page,'week of 19 October');
  // The rule itself: half an hour before the first meeting or 08:00, whichever is earlier; the now line once it is
  // past the screen; never past the end of the day
  const first=await page.evaluate(()=>{const box=(tops,now)=>{const d=document.createElement('div');d.innerHTML=tops.map(h=>`<a class="cal-blk" style="top:${h*CAL_HOUR}px"></a>`).join('')+(now==null?'':`<i class="cal-now" style="top:${now*CAL_HOUR}px"></i>`);return d};
   return [calFirstHour(box([9,14])),calFirstHour(box([6.5,9])),calFirstHour(box([])),calFirstHour(box([9],3)),calFirstHour(box([9],11)),calFirstHour(box([9],20)),calFirstHour(box([9],23.5),8)]});
  assert.deepEqual(first,[7.5,6,7.5,7.5,7.5,16,16]);
  assert.equal(await range(page),'19 – 25 Oct 2026');
  const tops=await page.evaluate(()=>[['2026-10-24','Saturday run'],['2026-10-25','Sunday walk']].map(([k,t])=>{const b=[...document.querySelectorAll(`.cal-tg-col[data-col="${k}"] .cal-blk`)].find(b=>b.textContent.includes(t));return [parseFloat(b.style.top),parseFloat(b.style.height)]}));
  assert.deepEqual(tops,[[476,66],[476,66]],'an hour at 07:00 on both sides of the change');
  // This week: seven columns from Monday, today marked with the now line, overlaps side by side, All day on top
  await page.keyboard.press('t');await page.locator('.cal-tg-col.today').waitFor();
  await opensAt(page,'Today');
  assert.ok(await page.locator('.cal-today.now').count(),'Today is quiet on this week');
  const days=await page.locator('.cal-tg-day').evaluateAll(d=>d.map(x=>x.dataset.day));assert.equal(days.length,7);assert.equal(new Date(days[0]+'T12:00').getDay(),1);
  assert.equal(await page.locator('.cal-tg-col.today .cal-now').count(),1);
  const blocks=await page.evaluate(()=>[...document.querySelectorAll('.cal-tg-col.today .cal-blk')].map(b=>({text:b.textContent,left:b.style.left,width:b.style.width,z:b.style.zIndex,h:b.getBoundingClientRect().height})));
  const find=t=>blocks.find(b=>b.text.includes(t));
  // At most two abreast: the third at 10:30 becomes "+1", which opens the day in the agenda
  const second=find('Vendor demo')||find('Pilot call');
  assert.ok(find('Design review').left!==second.left&&/50%/.test(find('Design review').width)&&/50%/.test(second.width),'two at once share the width '+JSON.stringify(blocks));
  assert.ok(!(find('Vendor demo')&&find('Pilot call')),'a third at once is not squeezed in');assert.equal(await page.locator('.cal-tg-col.today .cal-tg-more').textContent(),'+1');
  // A half hour is two lines tall with its time first, and back to back ones don't touch; a quarter of an hour is
  // as tall, so the one after it sits on top, a little to the right, keeping the width
  assert.ok(find('Standup').h>=32,'two lines at least '+find('Standup').h);assert.match(find('Standup').text,/^09:00 Standup$/);
  assert.equal(find('Hiring sync').z,'1');assert.ok(find('Quick call').h>=32);
  assert.match(find('Follow-up').left,/12px/);assert.equal(find('Follow-up').z,'2');assert.match(find('Follow-up').width,/100%/);
  assert.match(find('Lunch').width,/100%/,'one on its own takes the column');assert.equal(find('Lunch').z,'1');
  const tomorrow=key(at(1,0));
  if(days.includes(tomorrow)){const strip=page.locator('.cal-tg-strip');
   assert.ok(await strip.locator('.cal-chip',{hasText:'Conference'}).count(),'All day goes on top');
   assert.equal(await strip.locator('a.cal-chip',{hasText:'send the pilot recap'}).getAttribute('href'),'#promise/7','a promise opens its own page');
   assert.ok(await page.locator('.cal-blk.k-theirs',{hasText:'Their dentist'}).count(),'a shared calendar\'s meeting is marked as theirs')}
  await fits(page,'week 1440');await shot(page,'grid-week-1440');
  await page.locator('.cal-tg-col.today .cal-tg-more').click();await page.waitForFunction(()=>location.hash.startsWith('#calendar?view=agenda'));
  assert.ok(await page.locator('#calendar-view .cal-block[data-day="'+key(at(0,0))+'"] .cal-item',{hasText:'Vendor demo'}).count()&&await page.locator('#calendar-view .cal-item',{hasText:'Pilot call'}).count(),'the agenda lists all three');
  await page.keyboard.press('w');await page.locator('.cal-tg').waitFor();
  // › and ‹ open each week at its own first meeting again
  await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>!document.querySelector('.cal-today.now'));await opensAt(page,'next week');
  await page.keyboard.press('ArrowLeft');await page.locator('.cal-tg-col.today').waitFor();await opensAt(page,'back to this week');
  // m picks Month, on this month; the view is remembered on this device, and the Calendar opens on the current period
  await page.keyboard.press('m');await page.locator('.cal-mg').waitFor();assert.equal(await range(page),new Date().toLocaleDateString('en-GB',{month:'long',year:'numeric'}));
  await page.keyboard.press('ArrowLeft');await page.waitForFunction(()=>!document.querySelector('.cal-today.now'));
  await page.reload();await page.locator('#task-'+cards[0].id).waitFor({state:'attached'});await page.evaluate(()=>{history.replaceState(null,'',location.pathname);showTab('inbox')});
  await page.locator('nav [data-tab="calendar"]').click();await page.locator('.cal-mg').waitFor();assert.ok(await page.locator('.cal-today.now').count(),'opens on this month');
  // Week remembered, the Calendar opened from another tab: the grid is drawn while hidden, and still opens at the morning
  await page.keyboard.press('w');await page.locator('.cal-tg').waitFor();
  await page.reload();await page.locator('#task-'+cards[0].id).waitFor({state:'attached'});await page.evaluate(()=>{history.replaceState(null,'',location.pathname);showTab('inbox')});
  await page.waitForTimeout(2500);await page.locator('nav [data-tab="calendar"]').click();await page.locator('.cal-tg').waitFor();await opensAt(page,'opened from another tab');
  await page.keyboard.press('a');await page.waitForFunction(()=>!document.querySelector('.cal-mg')&&!document.querySelector('.cal-tg'));
  await context.close()}
 // Phone: Week is three days, swiped through (and ‹ ›); Month shows dots, and a tap lists the day under the grid
 for(const scheme of ['dark','light']){const {context,page}=await open(390,844,scheme);
  await page.evaluate(()=>showTab('calendar'));await page.locator('[data-cal-mode="week"]').click();await page.locator('.cal-tg').waitFor();
  const first=()=>page.locator('.cal-tg-day').first().getAttribute('data-day');
  assert.equal(await page.locator('.cal-tg-day').count(),3);assert.equal(await first(),key(at(0,0)),'three days from today');
  assert.ok(await page.locator('.cal-today').isVisible(),'Today stays in sight');
  await fits(page,'3 days '+scheme);await shot(page,'grid-week-390-'+scheme);
  await swipe(page,-120);await page.waitForFunction(k=>document.querySelector('.cal-tg-day')?.dataset.day===k,key(at(3,0)));assert.ok(!(await page.locator('.cal-today.now').count()));
  await swipe(page,120);await page.waitForFunction(k=>document.querySelector('.cal-tg-day')?.dataset.day===k,key(at(0,0)));
  await page.locator('[data-cal-go="1"]').click();await page.waitForFunction(k=>document.querySelector('.cal-tg-day')?.dataset.day===k,key(at(3,0)));
  await page.locator('[data-cal-mode="month"]').click();await page.locator('.cal-mg.dots').waitFor();
  await go(page,'#calendar?view=month&date=2026-10-01');await page.locator('[data-cal-pick="2026-10-14"] .cal-dots i').first().waitFor();
  assert.equal(await page.locator('[data-cal-pick="2026-10-14"] .cal-dots i').count(),3);
  await page.locator('[data-cal-pick="2026-10-14"]').click();await page.locator('#calendar-view .cal-block[data-day="2026-10-14"]').waitFor();
  assert.equal(await page.locator('#calendar-view .cal-block[data-day="2026-10-14"] .cal-item').count(),5);
  await fits(page,'month 390 '+scheme);await shot(page,'grid-month-390-'+scheme);
  await context.close()}
 assert.deepEqual(calls,[],'viewing sends nothing');assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log(JSON.stringify({asks:asks.length,posts:calls.length,page_errors:errors}));
