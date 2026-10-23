import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The Calendar tab: the next seven days as an agenda (Overdue, Today, Tomorrow, then each day), with meetings from the
// calendar (and their card), cards due or coming back, handoff check-ins and promises with a date. A row opens its card's
// chat and back returns to the Calendar; a meeting without a card opens in its calendar. On a computer a Week grid is one
// click away and remembered. Viewing is read-only: no POST at all. Nothing is wider than the screen at 360, 390 and 900.
// CALENDAR_SHOT=<dir> also writes screenshots at 1440, 900, 390 and 360 in light and dark.
process.env.TZ='Europe/Brussels';  // the same days here as in the page, also on a CI runner in UTC
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'};
const at=(days,h,m=0)=>{const d=new Date();d.setDate(d.getDate()+days);d.setHours(h,m,0,0);return d};
const day=n=>{const d=at(n,12);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const soon=at(0,18);  // a fixed hour, so a late run keeps it on today
const iso=d=>d.toISOString(),now=iso(new Date());
const meetings=[
 {id:101,account_id:'cal',title:'Team standup',start:iso(at(0,0,0)),end:iso(at(0,0,1)),all_day:false,location:'',guests:6,url:'https://calendar.google.com/calendar/event?eid=standup'},
 {id:102,account_id:'cal',title:'Pilot review with Northwind',start:iso(soon),end:iso(new Date(soon.getTime()+45*6e4)),all_day:false,location:'Zoom',guests:3,url:'https://calendar.google.com/calendar/event?eid=pilot'},
 {id:103,account_id:'cal',title:'Quarterly planning',start:iso(at(1,14)),end:iso(at(1,15,30)),all_day:false,location:'Room 2',guests:8,url:'https://calendar.google.com/calendar/event?eid=planning'},
 {id:104,account_id:'cal',title:'Team offsite',start:iso(at(3,0)),end:iso(at(5,0)),all_day:true,location:'Lakeside',guests:0,url:null},
 {id:105,account_id:'cal',title:'Intro call with Example Ltd',start:iso(at(4,11)),end:iso(at(4,11,30)),all_day:false,location:'',guests:1,url:'https://calendar.google.com/calendar/event?eid=intro'},
 {id:106,account_id:'cal',title:'Dentist',start:iso(at(6,8,30)),end:iso(at(6,8,30)),all_day:false,location:'',guests:0,url:null},  // no end time: ends when it starts
 // On a partner's calendar shared with the owner, and they aren't on it: marked as theirs, Friday evening to Sunday evening
 {id:107,account_id:'partner',title:'Weekend away',start:iso(at(2,19)),end:iso(at(4,19)),all_day:false,location:'',guests:0,people:[],shared:'Robin',url:'https://calendar.google.com/calendar/event?eid=away'},
 {id:108,account_id:'cal',title:'Pram from Evelien (cash)',start:iso(at(5,10)),end:iso(at(5,10,30)),all_day:false,location:'Harbour St 2',guests:0,people:[],url:null}];
const card=(id,title,status,extra={})=>({id:id.repeat(16).slice(0,16),title,body:'Made-up card for the Calendar test.',status,scope:'work',created_at:now,event_ids:[],...extra});
const cards=[
 card('a','Prep notes for the pilot review','prepared',{event_ids:'[102]',due:day(0)}),  // as /api/status sends it: JSON text
 card('b','Send the revised quote to Northwind','pending',{due:day(0),awaiting:JSON.stringify({delegated:true,name:'Pat Quinn'})}),
 card('c','Follow up on the unpaid invoice','snoozed',{snoozed_until:iso(at(1,9))}),
 card('d','Venue booking for the offsite','snoozed',{snoozed_until:iso(at(2,9)),handoff_wait:{name:'Robin'}}),
 card('e','Renew the shop domain','pending',{due:day(-1)}),
 card('f','Already done and due today','completed',{due:day(0)}),
 card('9','Due next month','pending',{due:day(40)}),
 card('8','Pack for the weekend','pending',{event_ids:'[107]'})];  // a card can't be a shared calendar's meeting
const promises=[
 {id:1,direction:'mine',who:'Alex Morgan',what:'Send the Q3 deck',due:day(4),state:'open',made_at:now,check_at:now},
 {id:2,direction:'theirs',who:'Sam Lee',what:'Send the signed contract',due:day(2),state:'open',made_at:now,check_at:now},
 {id:4,direction:'theirs',who:'alex morgan',what:'Send the  Q3 deck',due:day(4),state:'open',made_at:now,check_at:now},  // the same plan, seen from both sides
 {id:5,direction:'mine',who:'Evelien Smit',what:'pick up the pram after swimming',due:day(5),state:'open',made_at:now,check_at:now},  // the same thing as that day's meeting
 {id:3,direction:'theirs',who:'Kim',what:'On a card already',due:day(0),state:'card',made_at:now,check_at:now}];
let calls=[],calendarAsks=[],calendarDown=false,failFrom=null,empty=false;
const state=()=>({suggestions:empty?[]:cards,promises:empty?[]:promises,connectors:[{id:'cal',email:'owner@example.com',service:'calendar',status:'ok'}],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]}});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(brand[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+brand[url.pathname],import.meta.url)));return}
 if(req.method!=='GET')calls.push(url.pathname+' '+body);
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/calendar'){calendarAsks.push(Object.fromEntries(url.searchParams));if(calendarDown||failFrom&&new Date(url.searchParams.get('from'))>=failFrom){res.statusCode=500;res.end('{"error":"down"}');return}res.end(JSON.stringify({meetings:empty?[]:meetings}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body||'{}');res.end(JSON.stringify(r.action==='card_chat'?{session:true,status:'ok',active:false,items:[]}:{}));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
const open=async(width,height,scheme='dark')=>{const mobile=width<600;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,locale:'en-GB',timezoneId:'Europe/Brussels',colorScheme:scheme});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(width+': '+e.message));
 await page.goto(base);await page.locator('#task-'+cards[1].id).waitFor();return {context,page}};
const showCal=async page=>{await page.evaluate(()=>showTab('calendar'));await page.locator('#calendar-view .cal-item,#calendar-view .cal-chip').first().waitFor();await page.waitForFunction(()=>!document.querySelector('#calendar-view').textContent.includes('Loading'))};
const blocks=page=>page.locator('#calendar-view .cal-block').evaluateAll(b=>b.map(x=>({day:x.querySelector('.cal-day span').textContent,rows:[...x.querySelectorAll('.cal-item')].map(r=>r.querySelector('.cal-time').textContent+' | '+r.querySelector('.cal-title').textContent+' | '+(r.querySelector('.cal-sub')?.textContent||''))})));
const fits=(page,where)=>page.evaluate(()=>({page:document.documentElement.scrollWidth,vw:innerWidth,wide:[...document.querySelectorAll('#calendar-view *')].filter(e=>e.getBoundingClientRect().right>innerWidth+.5).map(e=>e.className).slice(0,3)})).then(s=>{assert.ok(s.page<=s.vw&&!s.wide.length,`${where}: wider than the screen ${JSON.stringify(s)}`)});
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open(1440,900);
  // A sidebar row under Suggestions
  const rows=await page.locator('nav button[data-tab]').evaluateAll(b=>b.map(x=>x.dataset.tab));assert.equal(rows[1],'calendar');
  await page.locator('nav [data-tab="calendar"]').click();await showCal(page);
  assert.equal(await page.locator('#page-head .page-title').textContent(),'Calendar');
  assert.match(await page.locator('#page-head .cal-range').textContent(),/^\d{1,2}( \w{3})? – \d{1,2} \w{3} \d{4}$/);
  // Meetings come a calendar week per ask, Monday midnight to Monday midnight with time zones, this week and the next
  const monday=n=>{const d=at(0,0);d.setDate(d.getDate()-(d.getDay()+6)%7+7*n);return d.getTime()};
  for(const ask of calendarAsks){const a=new Date(ask.from),b=new Date(ask.to);assert.equal(a.getHours()+a.getMinutes()+b.getHours()+b.getMinutes(),0);assert.equal(a.getDay(),1);assert.equal(b.getDay(),1)}
  assert.ok(calendarAsks.some(a=>new Date(a.from).getTime()===monday(0))&&calendarAsks.some(a=>new Date(a.from).getTime()===monday(1)));
  const b=(await blocks(page)).slice(0,8),names=b.map(x=>x.day);
  assert.deepEqual(names.slice(0,3),['Overdue','Today','Tomorrow']);assert.equal(names.length,8);
  assert.deepEqual(b[0].rows,[await page.evaluate(d=>calShort(d),day(-1))+' | Renew the shop domain | ']);assert.match(b[0].rows[0],/^\w{3} \d{1,2} \|/);
  const today=b[1].rows.join('\n');
  // The prepped meeting carries its card, which is not listed again as due; done cards and promises on a card stay out
  assert.match(today,/Pilot review with Northwind \| Prep ready · 18:00 to 18:45 · Zoom · 3 guests/);assert.ok(!today.includes('Prep notes'),'its card is the meeting');
  assert.match(today,/Due \| Send the revised quote to Northwind \| Waiting on Pat Quinn$/m);assert.ok(!today.includes('Already done')&&!today.includes('On a card already'));
  assert.ok(b[1].rows[0].startsWith('Due'),'dates without a time come first');
  const tomorrow=b[2].rows.join('\n');assert.match(tomorrow,/09:00 \| Follow up on the unpaid invoice \| Comes back/);assert.match(tomorrow,/14:00 \| Quarterly planning \| 14:00 to 15:30 · Room 2 · 8 guests/);
  assert.ok(tomorrow.indexOf('09:00')<tomorrow.indexOf('14:00'),'by time');
  assert.match(b[3].rows.join('\n'),/09:00 \| Check in with Robin \| If no answer · Venue booking for the offsite/);
  assert.match(b[3].rows.join('\n'),/Due \| Send the signed contract \| Sam Lee promised/);
  assert.match(b[4].rows.join('\n'),/All day \| Team offsite/);assert.match(b[5].rows.join('\n'),/All day \| Team offsite/);assert.ok(!b[6].rows.join().includes('Team offsite'),'an all-day end is exclusive');
  assert.match(b[5].rows.join('\n'),/Due \| Send the Q3 deck \| With Alex Morgan/);assert.equal(b[5].rows.filter(r=>/Q3 deck/i.test(r)).length,1,'one row per promise');
  // Someone else's trip on a shared calendar: on each day it covers, All day with its dates, marked with their name
  for(const k of [3,4,5]){const r=b[k].rows.find(r=>r.includes('Weekend away'));assert.ok(r,'day '+k);assert.match(r,/^All day \| Robin’s Weekend away \| \w{3} \d{1,2} to \w{3} \d{1,2}$/)}
  assert.ok(!b[6].rows.join().includes('Weekend away'));assert.ok(await page.locator('.cal-item.k-theirs',{hasText:'Weekend away'}).count());
  assert.ok(!b.flatMap(x=>x.rows).some(r=>r.includes('Pack for the weekend')&&!r.startsWith('Due'))&&!(await page.locator('a.cal-item[href="#chat='+cards[7].id+'"]',{hasText:'Weekend away'}).count()),'no card on a shared meeting');
  // A promise about a meeting that day with the same person rides on the meeting: one row, at its time
  assert.deepEqual(b[6].rows.filter(r=>/pram/i.test(r)),['10:00 | Pram from Evelien (cash) | 10:00 to 10:30 · Harbour St 2']);
  assert.equal(await page.locator('.cal-item',{hasText:'Pram from Evelien'}).locator('.cal-said').textContent(),'Promised to Evelien Smit: pick up the pram after swimming');
  assert.equal(await page.locator('.cal-item',{hasText:'Dentist'}).locator('.cal-sub').count(),0);assert.match(b[7].rows.join('\n'),/^08:30 \| Dentist \| $/m,'no end time: only the start');
  assert.ok(!b.some(x=>x.rows.join().includes('Due next month')));
  // No event line starts with "You"
  assert.ok(!b.flatMap(x=>x.rows).some(r=>r.split(' | ').some(p=>/^You\b/.test(p))));
  // The meeting's card chip; the standup at 00:15 is over and steps back; a meeting without a card opens its calendar
  assert.equal(await page.locator('#calendar-view a.cal-item[href="#chat='+cards[0].id+'"] .chip').textContent(),'Prep ready');
  assert.ok(await page.locator('.cal-item.past .cal-title',{hasText:'Team standup'}).count());
  const ext=page.locator('#calendar-view a.cal-item',{hasText:'Quarterly planning'});assert.equal(await ext.getAttribute('target'),'_blank');assert.match(await ext.getAttribute('href'),/^https:\/\/calendar\.google\.com\//);
  assert.equal(await page.locator('#calendar-view div.cal-item',{hasText:'Dentist'}).count(),1,'no card and no link: not a link');
  assert.equal(await page.locator('#badge-calendar').textContent(),String(b[1].rows.length));
  // A row opens the card's chat; back returns to the Calendar
  await page.locator('#calendar-view a.cal-item',{hasText:'Send the revised quote'}).click();
  await page.waitForFunction(id=>location.hash==='#chat='+id&&!document.querySelector('#inbox').hidden,cards[1].id);
  await page.goBack();await page.waitForFunction(()=>!document.querySelector('#calendar').hidden);
  // A promise opens its own page, never Suggestions
  await page.locator('#calendar-view a.cal-item',{hasText:'Send the Q3 deck'}).click();await page.waitForFunction(()=>/^#promise\/\d+$/.test(location.hash)&&!document.querySelector('#people').hidden&&document.querySelector('#inbox').hidden);
  await page.goBack();await page.waitForFunction(()=>!document.querySelector('#calendar').hidden&&document.querySelector('#people').hidden);
  // The Week grid: the calendar week, Monday to Sunday, today marked; remembered after a reload; Agenda again
  await page.evaluate(()=>showTab('calendar'));await page.locator('[data-cal-go="0"]').click();await page.locator('[data-cal-mode="week"]').click();  // Week opens on the week in view
  assert.equal(await page.locator('.cal-tg .cal-tg-day').count(),7);assert.match(await page.locator('.cal-tg-day.today').textContent(),/^Today\d+$/);
  assert.match(await page.locator('.cal-tg-day').first().textContent(),/^(Mon|Today)\d+$/);
  const wd=(new Date().getDay()+6)%7;assert.equal(await page.locator('.cal-tg-strip .cal-chip',{hasText:'Team offsite'}).count(),[3,4].filter(n=>wd+n<=6).length);
  assert.equal(await page.locator('#calendar-view .cal-block .cal-day span').first().textContent(),'Overdue');
  const w=await page.locator('main').evaluate(m=>m.getBoundingClientRect().width);assert.ok(w>1000,'the week gets a wider main area '+w);await fits(page,'week 1440');
  if(process.env.CALENDAR_SHOT){for(const scheme of ['dark','light']){await page.emulateMedia({colorScheme:scheme});await page.waitForTimeout(500);await page.screenshot({path:`${process.env.CALENDAR_SHOT}/calendar-1440-${scheme}-week.png`,fullPage:true})}await page.emulateMedia({colorScheme:'dark'})}
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#calendar').hidden);await showCal(page);assert.equal(await page.locator('.cal-tg').count(),1,'Week is remembered');
  await page.locator('[data-cal-mode="agenda"]').click();assert.equal(await page.locator('.cal-tg').count(),0);
  if(process.env.CALENDAR_SHOT)for(const scheme of ['dark','light']){await page.emulateMedia({colorScheme:scheme});await page.waitForTimeout(500);await page.screenshot({path:`${process.env.CALENDAR_SHOT}/calendar-1440-${scheme}.png`,fullPage:true})}
  // The link: #calendar opens it
  await page.evaluate(()=>showTab('inbox'));await page.evaluate(()=>{location.hash='#calendar'});await page.waitForFunction(()=>!document.querySelector('#calendar').hidden);
  await context.close()}
 // Below 1024px: the row in the drawer, the agenda by default, and it fits
 for(const [width,height] of [[900,1000],[390,844],[360,780]]){for(const scheme of ['dark','light']){const {context,page}=await open(width,height,scheme);
  await page.evaluate(()=>drawerOpen());const row=page.locator('nav [data-tab="calendar"]');await row.waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#app-side').getAnimations().length);
  if(process.env.CALENDAR_SHOT&&width<900)await page.screenshot({path:`${process.env.CALENDAR_SHOT}/calendar-${width}-${scheme}-drawer.png`});
  await row.click();await showCal(page);assert.equal(await page.evaluate(()=>document.documentElement.dataset.drawer||''),'','picking it closes the drawer');
  assert.equal(await page.locator('.cal-mode [aria-pressed="true"]').textContent(),'Agenda','the agenda by default at '+width);
  assert.equal(await page.locator('.cal-tg').count(),0);await fits(page,'agenda '+width);
  if(width<=390){const t=await page.locator('.cal-item .cal-title').first().evaluate(e=>getComputedStyle(e).textOverflow);assert.equal(t,'ellipsis')}
  // On a phone the meeting's card says so in its line
  if(width<=560)assert.match(await page.locator('a.cal-item',{hasText:'Pilot review'}).locator('.cal-sub').textContent(),/^Prep ready · /);
  if(process.env.CALENDAR_SHOT){await page.waitForTimeout(400);await page.screenshot({path:`${process.env.CALENDAR_SHOT}/calendar-${width}-${scheme}.png`,fullPage:true})}
  await context.close()}}
 // Meetings that can't load: the cards stay, with one quiet line; a week with nothing: the clear state
 {calendarDown=true;const {context,page}=await open(390,844);await page.evaluate(()=>showTab('calendar'));await page.locator('.cal-note').waitFor();
  assert.equal(await page.locator('.cal-note').textContent(),'Meetings couldn’t load. Cards and promises are here.');assert.ok(await page.locator('.cal-item',{hasText:'Send the revised quote'}).count());
  calendarDown=false;empty=true;await page.evaluate(()=>{calMeetings=null;return refresh()});await page.evaluate(()=>calendarView(true));await page.locator('.cal-clear').waitFor();
  assert.match(await page.locator('.cal-clear').textContent(),/A clear week/);
  if(process.env.CALENDAR_SHOT)await page.screenshot({path:`${process.env.CALENDAR_SHOT}/calendar-390-dark-empty.png`});
  empty=false;await context.close()}
 // Read-only: viewing, switching views and opening rows sent nothing but the chat's own read
 assert.deepEqual(calls.filter(c=>!c.includes('"card_chat"')),[]);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({calendar_requests:calendarAsks.length,posts:calls.length,page_errors:errors}));
}finally{await browser?.close();server.close()}
