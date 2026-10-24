import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A person's page in the Graph keeps the dashboard's header and tabs, with a breadcrumb back; it has its own link and
// the browser's back button works. Facts about them sit apart from what is open, each with Not about them and Forget.
// Events come by day, each row with its source, who wrote it and the start of the text.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
// The page's clock is today at noon whatever the hour the test runs at, so a run of messages minutes apart a day ago
// never straddles midnight (a run just after midnight split it in two days)
const now=new Date();now.setHours(12,0,0,0);const hour=n=>new Date(now-n*6e4).toISOString(),daysAgo=d=>new Date(now-d*864e5).toISOString();
const long='Can you pick up the kids at five today? I am stuck in a meeting that runs late and the school closes at half past, so it would really help. '.repeat(3);
let known=[{id:5,text:'Ann prefers calls before 10.'},{id:6,text:'Ann is allergic to nuts.'}];
const person=()=>({id:'wa:3247000001',kind:'person',name:'Ann Example',strength:2,closeness:'close',last_at:hour(1),signals:9,ids:['+3247000001'],links:[],
 open:{cards:[{id:'0123456789abcdef',title:'Answer Ann',status:'pending',due:null}],...(known.length?{known}:{})},
 recent:[{id:41,account_id:'chat',kind:'whatsapp.message',occurred_at:hour(1),title:'Ann Example',from:'Ann Example',from_me:false,preview:long},
         {id:40,account_id:'chat',kind:'whatsapp.message',occurred_at:hour(2),title:'Ann Example',from:'You',from_me:true,preview:'ok'},
         {id:39,account_id:'work',kind:'gmail.message',occurred_at:daysAgo(3),title:'School trip',from:'Ann Example',from_me:false,preview:'Here is the form.'},
         {id:38,account_id:'work',kind:'calendar.event',occurred_at:daysAgo(3),title:'Parents evening',preview:'School hall'},
         {id:37,account_id:'help',kind:'intercom.conversation',occurred_at:daysAgo(4),title:'Question about billing',from:'Ann Example',preview:'Hi there'},
         {id:36,account_id:'slack',kind:'slack.message',occurred_at:daysAgo(5),title:'#DM U0ANN: thanks',from:null,from_me:false,dm:true,preview:'thanks'},
         {id:35,account_id:'slack',kind:'slack.message',occurred_at:daysAgo(5),title:'#standup: done',from:'You',from_me:true,dm:false,place:'#standup',preview:'done'},
         {id:34,account_id:'work',kind:'calendar.event',occurred_at:daysAgo(-9),title:'Later dinner',preview:''},
         {id:33,account_id:'work',kind:'calendar.event',occurred_at:daysAgo(-2),title:'Lunch',preview:''},
         // A group conversation: three messages minutes apart are one row; one two hours earlier is its own.
         ...[[50,1,true,'See you at the beach'],[51,1.01,false,'Bring the umbrella'],[52,1.02,false,'And snacks']].map(([id,d,me,t])=>({id,account_id:'chat',kind:'whatsapp.message',occurred_at:daysAgo(d),title:'Family 🌴',from:me?'You':'Ann Example',from_me:me,preview:t,thread:'chat:whatsapp.message:g1'})),
         // One to one with Ann: a run of three a minute apart, and one long message on its own.
         ...[[54,2+2/1440,false,'Are you coming?'],[55,2+1/1440,false,'Dinner is at 7'],[56,2,true,'yes on my way']].map(([id,d,me,t])=>({id,account_id:'chat',kind:'whatsapp.message',occurred_at:daysAgo(d),title:'Ann Example',from:me?'You':'Ann Example',from_me:me,preview:t,thread:'chat:whatsapp.message:ann'})),
         {id:57,account_id:'chat',kind:'whatsapp.message',occurred_at:daysAgo(2.2),title:'Ann Example',from:'Ann Example',from_me:false,preview:long,thread:'chat:whatsapp.message:ann'},
         {id:53,account_id:'chat',kind:'whatsapp.message',occurred_at:daysAgo(1.1),title:'Family 🌴',from:'Ann Example',from_me:false,preview:'Morning',thread:'chat:whatsapp.message:g1'},
         ...Array.from({length:10},(_,i)=>({id:60+i,account_id:'work',kind:'gmail.message',occurred_at:daysAgo(20+i),title:'Invoice '+i,from:'Ann Example',from_me:false,preview:'Attached.'}))].sort((a,b)=>b.occurred_at.localeCompare(a.occurred_at))});
const calls=[],errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');if(node&&node!=='wa:3247000001'){res.statusCode=404;res.end('{"error":"Not in the graph"}');return}
  res.end(JSON.stringify(node?person():{projects:[],people:[person()],quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/originals'){const ids=(url.searchParams.get('events')||'').split(',').map(Number);calls.push({originals:ids});
  const at=id=>person().recent.find(r=>r.id===id).occurred_at;
  res.end(JSON.stringify({originals:ids.map((id,i)=>({kind:'whatsapp',from:[50,56].includes(id)?'You':'Ann Example',occurred_at:at(id),body:'message '+id,event_id:id,url:i===ids.length-1&&id===56?'https://wa.example/chat':null}))}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_fact_unlink'||call.action==='forget_fact')known=known.filter(f=>f.id!==(call.args.fact_id??call.args.id));res.end('{}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 const dialogs=[];page.on('dialog',async d=>{dialogs.push(d.message());await(dialogs.length===1?d.dismiss():d.accept())});
 await page.clock.install({time:now});await page.goto(base);
 await navClick(page.locator('nav [data-tab="people"]'));
 await page.locator('#people-view article[data-node="wa:3247000001"] button.node').click();
 await page.locator('.graph-crumbs').waitFor();
 // The page's title stays (the pane, under a quiet Graph), Graph current (in the drawer on a phone); the breadcrumb says where this is.
 assert.ok(await page.locator('#page-head .page-title',{hasText:'People'}).isVisible()&&await page.locator('#page-head .page-crumb',{hasText:'Graph'}).isVisible()&&await page.locator('nav [data-tab="people"].active').count()===1);
 assert.equal(await page.evaluate(()=>scrollY),0);
 assert.equal((await page.locator('.graph-crumbs').textContent()).replace(/\s+/g,' '),'Graph›People›Ann Example');
 assert.equal(await page.locator('.graph-back').count(),0);
 assert.ok(!await page.locator('.people-search').isVisible());
 assert.equal(new URL(page.url()).hash,'#graph/wa%3A3247000001');
 // Open between you: the card only. The facts are under Known about Ann.
 assert.ok(!(await page.locator('.graph-open').textContent()).includes('Ann prefers'));
 assert.equal(await page.locator('.graph-known-head h2').textContent(),'Known about Ann');
 assert.equal(await page.locator('.graph-known li').count(),2);
 // Events: by day, who wrote it, a preview cut with a fade, the source's icon; tapping opens the event.
 const days=await page.locator('.ge-day').allTextContents();
 // Coming up first, soonest on top; then today and back.
 assert.deepEqual((await page.locator('.graph-event h4').allTextContents()).slice(0,2),['Lunch','Later dinner']);
 assert.equal(days[2],'Today');assert.equal(days[3],'Yesterday');
 // One row for the run of three, the time from the first to the last, the last two lines with who said them.
 const run=page.locator('.graph-event[data-events="52,51,50"]');
 assert.equal(await page.locator('.graph-event[data-events]').count(),2);
 assert.equal(await run.locator('h4').textContent(),'Family 🌴');
 assert.match(await run.locator('.ge-meta').textContent(),/^3 messages · \d\d:\d\d to \d\d:\d\d$/);
 assert.deepEqual(await run.locator('.ge-lines li').allTextContents(),['Ann: Bring the umbrella','You: See you at the beach']);
 const alone=page.locator('.graph-event[data-event="53"]:not([data-events])');  // on its own: the same look, one message
 assert.equal(await alone.locator('h4').textContent(),'Family 🌴');assert.equal(await alone.locator('.ge-preview').textContent(),'Ann: Morning');assert.match(await alone.locator('.ge-meta').textContent(),/^\d\d:\d\d$/);
 // With the page's own person no title: the page already says who. Just how many, when, and the last lines.
 const ann=page.locator('.graph-event[data-events="54,55,56"]');
 assert.equal(await ann.locator('h4').count(),0);assert.match(await ann.locator('.ge-meta').textContent(),/^3 messages · \d\d:\d\d to \d\d:\d\d$/);
 assert.deepEqual(await ann.locator('.ge-lines li').allTextContents(),['Ann: Dinner is at 7','You: yes on my way']);
 assert.equal(await ann.getAttribute('aria-label'),'WhatsApp with Ann, 3 messages');
 const annLong=page.locator('.graph-event[data-event="57"]');
 assert.equal(await annLong.locator('h4').count(),0);assert.equal(await annLong.locator('.ge-preview.fade').count(),1);
 // The count is rows; past fifteen the rest waits behind Show more.
 assert.equal(await page.locator('.graph-events ~ .section-head .count, .section-head:has(+ .graph-events) .count').textContent(),'23');
 assert.equal(await page.locator('.graph-event:visible').count(),15);
 await page.locator('[data-ge-more]').click();
 assert.equal(await page.locator('.graph-event:visible').count(),23);assert.equal(await page.locator('[data-ge-more]').count(),0);
 // Tapping the run opens the conversation, in order.
 // A group's transcript: the date once, each turn its sender and time once, yours on the right.
 await run.click();
 await page.locator('#email-dialog .convo-say').nth(2).waitFor();
 assert.deepEqual(calls.pop(),{originals:[52,51,50]});
 assert.equal(await page.locator('#email-title').textContent(),'Family 🌴');
 assert.equal(await page.locator('#email-dialog .convo-day').count(),1);
 assert.equal(await page.locator('#email-dialog .convo-turn').count(),3);
 assert.match(await page.locator('#email-dialog .convo-turn').first().locator('.convo-meta').textContent(),/^Ann Example · \d\d:\d\d$/);
 assert.match(await page.locator('#email-dialog .convo-turn.me .convo-meta').textContent(),/^\d\d:\d\d$/);
 assert.equal(await page.locator('#email-dialog .convo-source').count(),0);
 await page.keyboard.press('Escape');
 // One to one: messages a minute apart from the same sender are one turn, no names; the source link at the end.
 await ann.click();
 await page.locator('#email-dialog .convo-say').nth(2).waitFor();
 assert.deepEqual(calls.pop(),{originals:[54,55,56]});
 assert.equal(await page.locator('#email-title').textContent(),'WhatsApp with Ann');
 assert.equal(await page.locator('#email-dialog .convo-turn').count(),2);
 assert.deepEqual(await page.locator('#email-dialog .convo-turn').first().locator('.convo-say').allTextContents(),['message 54','message 55']);
 assert.match(await page.locator('#email-dialog .convo-turn').first().locator('.convo-meta').textContent(),/^\d\d:\d\d$/);
 assert.equal(await page.locator('#email-dialog .convo-source').getAttribute('href'),'https://wa.example/chat');
 assert.equal(await page.locator('#email-dialog .convo-source').textContent(),'Open in WhatsApp');
 await page.keyboard.press('Escape');
 assert.ok((await page.locator('.graph-event h4').allTextContents()).includes('#standup'));  // a channel by its name, a direct message by who wrote it
 assert.equal(await page.locator('.graph-event[data-event="36"] h4').textContent(),'Ann Example');
 assert.equal(await page.locator('.graph-event[data-event="35"] .ge-preview').textContent(),'You: done');
 const first=page.locator('.graph-event[data-event="41"]');
 assert.equal(await first.locator('h4').textContent(),'Ann Example');
 assert.equal(await page.locator('.graph-event[data-event="40"] h4').textContent(),'You');
 assert.ok(await first.locator('.ge-preview.fade').count()===1&&await page.locator('.graph-event[data-event="40"] .ge-preview.fade').count()===0);
 assert.equal(await page.locator('.graph-event[data-event="39"] h4').textContent(),'School trip');
 assert.ok((await page.locator('.graph-event[data-event="39"] .ge-preview').textContent()).startsWith('Ann Example: '));
 assert.equal(await page.locator('.graph-event[aria-label^="Calendar"]').count(),3);
 assert.equal(await page.locator('.graph-event[aria-label^="Intercom"] .icon svg path').count(),1);
 const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
 assert.ok(sizes.page<=sizes.viewport,'overflow at 390');
 // Not about Ann takes only this link away.
 await page.locator('.graph-known li').first().locator('summary').click();
 assert.equal(await page.locator('[data-fact-unlink="5"]').textContent(),'Not about AnnOnly takes it off this page');
 await page.locator('[data-fact-unlink="5"]').click();
 for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[0],{action:'graph_fact_unlink',args:{fact_id:5,node_id:'wa:3247000001'}});
 await page.locator('.graph-known li').first().filter({hasText:'allergic'}).waitFor();
 // Forget asks first, plainly, and says it goes everywhere; cancel keeps it.
 await page.locator('.graph-known li summary').click();
 await page.locator('[data-fact-forget="6"]').click();
 assert.match(dialogs[0],/Forget this everywhere\?/);assert.match(dialogs[0],/from everything your Machine has learned/);assert.match(dialogs[0],/Not about Ann/);
 assert.equal(calls.length,1);
 await page.locator('[data-fact-forget="6"]').click();
 for(let i=0;i<100&&calls.length<2;i++)await page.waitForTimeout(20);
 assert.deepEqual(calls[1],{action:'forget_fact',args:{id:6}});
 await page.waitForFunction(()=>!document.querySelector('.graph-known'));
 // The breadcrumb goes back to the list; the browser's back button returns to Ann, forward to the list again.
 await page.locator('.graph-crumbs [data-crumb="people"]').click();
 await page.locator('[data-graph-panel="people"]:not([hidden])').waitFor();
 assert.ok(await page.locator('.people-search').isVisible());
 await page.goBack();
 await page.locator('.graph-crumbs').waitFor();
 await page.goBack();
 await page.locator('[data-graph-panel="people"]:not([hidden])').waitFor();
 // Another tab drops the link; a deep link opens the page straight away, with the tabs.
 await navClick(page.locator('nav [data-tab="events"]'));
 assert.equal(new URL(page.url()).hash,'');
 const fresh=await context.newPage();fresh.on('pageerror',e=>errors.push(e.message));
 await fresh.goto(base+'#graph/wa%3A3247000001');
 await fresh.locator('.graph-crumbs').waitFor();
 assert.ok(await fresh.locator('nav [data-tab="people"].active').count()===1&&await fresh.locator('#page-head .page-title',{hasText:'Graph'}).isVisible());
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({days,dialogs:dialogs.length,actions:calls.length,page_errors:errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
