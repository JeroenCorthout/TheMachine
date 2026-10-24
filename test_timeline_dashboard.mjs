import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The timeline on a person's page: every source in one list, a chip row to filter by source, an Intercom conversation
// with its state and link, a Zoom summary, cards, promises and project moves among them; Show more loads the next page.
// A person with nothing yet still has the section.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date();now.setHours(12,0,0,0);const daysAgo=d=>new Date(now-d*864e5).toISOString();
const mails=Array.from({length:50},(_,i)=>({id:100+i,type:'event',account_id:'work',kind:'gmail.message',source:'email',occurred_at:daysAgo(10+i),title:'Update '+i,from:'Ivy Park',from_me:false,preview:'Here it is.'}));
const first=[
 {id:1,type:'event',account_id:'help',kind:'intercom.conversation',source:'intercom',occurred_at:daysAgo(1),title:'Export fails',from:'Ivy Park',preview:'The CSV export stops at 100 rows',state:'open',source_url:'https://app.intercom.com/a/inbox/w/conversation/1'},
 {id:2,type:'event',account_id:'help',kind:'intercom.conversation',source:'intercom',occurred_at:daysAgo(2),title:'Invoice copy',from:'Ivy Park',preview:'Could you send it again',state:'closed',source_url:'https://app.intercom.com/a/inbox/w/conversation/2'},
 {id:3,type:'event',account_id:'zoom',kind:'zoom.summary',source:'meetings',occurred_at:daysAgo(3),title:'Pilot call',from:null,preview:'We agreed on a pilot.',source_url:'https://zoom.us/doc/1'},
 {id:'c1',type:'card',source:'cards',at:daysAgo(4),title:'Send Ivy the pilot plan',done:false},
 {id:9,type:'promise',source:'cards',at:daysAgo(4.5),what:'Send the pilot plan',who:'Sam',mine:true,status:'open'},
 {id:'move:4',type:'move',source:'projects',at:daysAgo(5),project:'Globex pilot',project_id:'project:7',stage:'Proposal'},
 ...mails.slice(0,34)];
const sources={email:50,meetings:1,intercom:2,cards:2,projects:1};
const ivy=()=>({id:'email:ivy@globex.example',kind:'person',name:'Ivy Park',strength:2,closeness:'close',last_at:daysAgo(1),signals:9,ids:['ivy@globex.example'],links:[],open:{},
 recent:first,timeline:{next:'40',sources,total:56}});
const lou=()=>({id:'manual:lou',kind:'person',name:'Lou Marsh',strength:0,closeness:'faint',signals:0,ids:[],links:[],open:{},recent:[],timeline:{next:null,sources:{},total:0}});
const calls=[],errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph/timeline'){const q=Object.fromEntries(url.searchParams);calls.push(q);
  const items=q.source==='intercom'?first.filter(i=>i.source==='intercom'):q.cursor==='40'?mails.slice(34):first;
  res.end(JSON.stringify({items,next:!q.source&&!q.cursor?'40':null,sources,total:56}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');
  res.end(JSON.stringify(node==='manual:lou'?lou():node?ivy():{projects:[],people:[ivy(),lou()],quiet:[],new:[],orgs:[]}));return}
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
 await page.clock.install({time:now});await page.goto(base+'#graph/'+encodeURIComponent('email:ivy@globex.example'));
 await page.locator('.graph-timeline').waitFor();
 assert.equal(await page.locator('.graph-timeline .section-head h2').textContent(),'Timeline');
 assert.equal(await page.locator('.graph-timeline .section-head .count').textContent(),'56');
 assert.deepEqual(await page.locator('.graph-timeline [data-tl-source]').allTextContents(),['All','Email','Meetings','Intercom','Cards','Projects']);
 // Intercom: subject, Open or Closed, a link to the conversation
 const open=page.locator('.graph-event[data-event="1"]');
 assert.equal(await open.locator('h4').textContent(),'Export fails');
 assert.equal(await open.locator('.ge-state').textContent(),'Open');
 assert.equal(await open.locator('a.ge-link').getAttribute('href'),'https://app.intercom.com/a/inbox/w/conversation/1');assert.equal(await open.locator('a.ge-link').textContent(),'Open in Intercom');
 assert.equal(await page.locator('.graph-event[data-event="2"] .ge-state').textContent(),'Closed');
 assert.equal(await page.locator('.graph-event[data-event="3"] a.ge-link').textContent(),'Summary');
 assert.equal(await page.locator('.graph-event[data-tl="card"] a').getAttribute('href'),'#card=c1');
 assert.equal(await page.locator('.graph-event[data-tl="promise"] h4').textContent(),'You promised');
 assert.equal(await page.locator('.graph-event[data-tl="move"] .ge-preview').textContent(),'Moved to Proposal');
 assert.equal(await page.locator('.graph-event:visible').count(),15);
 // No horizontal scroll at phone width
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 // Show more: the rows already here and, when few are left, the next page from the server
 await page.locator('[data-ge-more]').click();
 await page.waitForFunction(()=>document.querySelectorAll('.graph-event').length===56);
 assert.equal(calls.at(-1).cursor,'40');
 assert.equal(await page.locator('.graph-event:visible').count(),45);
 await page.locator('[data-ge-more]').click();
 assert.equal(await page.locator('.graph-event:visible').count(),56);
 assert.equal(await page.locator('[data-ge-more]').count(),0);
 // A source chip: only that source, the counts stay
 await page.locator('.graph-timeline [data-tl-source="intercom"]').click();
 await page.waitForFunction(()=>document.querySelectorAll('.graph-event').length===2);
 assert.equal(calls.at(-1).source,'intercom');
 assert.equal(await page.locator('.graph-timeline [data-tl-source="intercom"]').getAttribute('aria-selected'),'true');
 assert.equal(await page.locator('.graph-timeline .section-head .count').textContent(),'2');
 assert.equal(await page.locator('[data-ge-more]').count(),0);
 await page.locator('.graph-timeline [data-tl-source=""]').click();
 await page.waitForFunction(()=>document.querySelectorAll('.graph-event').length===40);
 // Someone with nothing yet still has a timeline
 await page.goto(base+'#graph/manual%3Alou');
 await page.locator('.graph-timeline .ge-empty').waitFor();
 assert.equal(await page.locator('.graph-timeline .ge-empty').textContent(),'Nothing here yet.');
 assert.deepEqual(errors,[]);
 console.log('timeline dashboard ok');
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
