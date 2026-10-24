import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick,scopeClick} from './nav-click.mjs';
// Playwright comes with OpenClaw; PLAYWRIGHT_CORE and CHROMIUM_PATH override the defaults.
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');
import {forceReplyPayload} from './openclaw-plugin/change-prompt.ts';
// The Send preview's header rows as 'From: …' lines.
// A card's rarer buttons wait under More: open it first when the button isn't in the row.
const tap=async(card,name)=>{const b=card.getByRole('button',{name,exact:true});if(!await b.isVisible())await card.locator('.more-actions>summary').click();await b.click()};
const sendHead=page=>page.evaluate(()=>[...document.querySelectorAll('#send-head>div')].map(d=>d.children[0].textContent+': '+d.children[1].textContent).join('\n'));
const payload=forceReplyPayload('sendMessage',{chat_id:'1000001',text:'Change'},'123','1000001');
assert.equal(payload.reply_markup.force_reply,true);assert.equal(payload.reply_parameters.message_id,123);
assert.throws(()=>forceReplyPayload('sendMessage',{chat_id:'another'},'123','1000001'));
const html=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const approvalHTML=execFileSync('python3',['-c',"import assistant; print(assistant.approval_document({'title':'Review change','detail':'<script>bad()</script>\\n'+('long command '*200)+'LAST STATEMENT'}))"],{cwd:new URL('.',import.meta.url),encoding:'utf8'});
const ids=['aaaaaaaaaaaaaaaa','bbbbbbbbbbbbbbbb','cccccccccccccccc','dddddddddddddddd'];
let suggestions=ids.map((id,i)=>({id,status:['pending','accepted','prepared','snoozed'][i],title:['Decision task','Blocked task','Ready task','Later task'][i],body:i===2?'Actual short result. memory/assistant/261011-result.md':'A useful suggestion.',created_at:new Date().toISOString(),preparation_status:i===1?'blocked':i===2?'done':null,preparation_reason:i===1?'Could not connect':null,result_files:i===2?[{name:'261011-result.md',path:'memory/assistant/261011-result.md'}]:[],original_urls:i===2?['https://example.com/invoice']:[],snoozed_until:i===3?new Date(Date.now()+86400000).toISOString():null}));
suggestions[0].created_at=new Date(Date.now()-59*60000).toISOString();
suggestions[0].deliver_after=new Date(Date.now()+86400000).toISOString();
let reviewRuns=[],reviewHealth={};
let modelUsage=null;let progress={status:'running',started_at:null,steps:[{title:'Read the thread',at:''},{title:'Collect retention settings',at:''}]};const posts=[];let state=()=>({suggestions,connectors:[{id:'work',display_name:'alex@lumenlabs.example',email:'work@example.com',boundary:'work',service:'gmail',last_success:new Date().toISOString(),failures_24h:32,last_failure:{at:new Date().toISOString(),error:'timeout'},state:'{}'},{id:'crm',display_name:'CRM',email:'work@example.com',boundary:'work',service:'salesflare',last_success:new Date().toISOString(),error:'network:TimeoutError',state:'{}'}],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:reviewHealth,review_runs:reviewRuns,model_usage:modelUsage});
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;res.setHeader('Content-Type','application/json');if(req.url.startsWith('/brand/')){const path=req.url.endsWith('avatar.png')?'brand/app-icon-192.png':req.url.endsWith('sleepy.webp')?'brand/app-icon-192.png':null;try{res.setHeader('Content-Type',path?.endsWith('.png')?'image/png':'image/webp');res.end(await readFile(new URL('./'+path,import.meta.url)));return}catch{res.statusCode=404;res.end();return}}if(req.url==='/approval'){res.setHeader('Content-Type','text/html');res.end(approvalHTML);return}if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(html);return}if(req.url==='/api/status'){res.end(JSON.stringify(state()));return}if(req.url.startsWith('/api/progress')){res.end(JSON.stringify({...progress,browser:suggestions[1].preparation_browser||null}));return}if(req.url.startsWith('/api/email')){const ev=new URL(req.url,'http://x').searchParams.get('event');res.end(JSON.stringify(ev==='7'?{html:'<!doctype html><html style="background-color:#fff"><head><style>html{background-color:#fff !important}</style></head><body id=admin_reply><p id=hello>Hello <b>HTML</b> <a href="https://example.com/x">link</a></p><table id=box bgcolor="#ffffff"><tr><td id=dark style="color:#333333">Dark text</td><td id=blue style="color:#1a0dab">Blue link text</td></tr></table><img id=logo src="cid:logo1" width=20 height=20><script>parent.pwned=1</script><img src=x onerror="parent.pwned=1"><form action="https://evil.example"><input name=q></form></body></html>',images:{logo1:'2'}}:{html:''}));return}
if(req.url.startsWith('/api/attachment')){res.setHeader('Content-Type','image/png');res.end(await readFile(new URL('./brand/app-icon-badge-96.png',import.meta.url)).catch(()=>Buffer.alloc(0)));return}
if(req.url.startsWith('/api/originals')){const id=new URL(req.url,'http://x').searchParams.get('id');const mail=(from,body,at)=>({kind:'email',from,to:'owner@example.com',cc:'',title:'Re: Board meeting',occurred_at:at,body,clipped:false,url:'https://mail.google.com/mail/u/?authuser=owner%40example.com#all/abc',event_id:from.startsWith('"Ann')?7:8,attachments:from.startsWith('"Ann')?[{part:'1',name:'Board <deck>.pdf',mime:'application/pdf',size:1029993}]:[]});res.end(JSON.stringify({originals:id===ids[0]?[mail('"Ann Example" <ann@example.com>','Hi,\r\n\r\nCan we move it? See https://example.com/agenda.\r\n<script>window.pwned=1</script><img src=x onerror="window.pwned=1">\n'+'More context line\n'.repeat(30)+'LAST LINE','2026-10-11T18:19:34Z'),mail('Bob <bob@example.com>','Earlier note','2026-10-09T09:00:00Z')]:id===ids[2]?[mail('Cara <cara@example.com>','Original question','2026-10-10T09:00:00Z')]:[]}));return}if(req.url.startsWith('/api/result')){res.end(JSON.stringify({name:'261011-result.md',text:'## A real result\n\nFull draft with **detail**.\n<script>unsafe()</script>\n\n## Attachments\n- credit <note>.pdf\n\nAll checked.'}));return}if(req.url==='/api/action'){const x=JSON.parse(body);posts.push(x.args);if(x.action==='revise_draft'){res.end(JSON.stringify({status:'revised',revision:'fedcba654321'}));return}if(x.action==='browser_login_open'){res.end(JSON.stringify({state:'opened',site:'Drone Portal',kind:'eid',view:null}));return}if(x.action==='browser_login_pages'){res.end(JSON.stringify({pages:[]}));return}const s=suggestions.find(s=>s.id===x.args.suggestion_id);if(x.args.choice==='complete'){s.closed_from=s.status;s.status='completed';s.undo_until=new Date(Date.now()+300000).toISOString()}else if(x.args.choice==='undo')s.status=s.closed_from;else if(x.args.choice==='other'){s.status='instructions';s.preparation_status='queued';s.latest_feedback={choice:'other',feedback:x.args.feedback,created_at:new Date().toISOString()}}else if(x.args.choice==='retry')s.preparation_status='queued';res.end('{}');return}res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
const context=await browser.newContext({viewport:{width:390,height:844},colorScheme:'dark'});const page=await context.newPage();let errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{window.copied=null;Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copied=text}})});
await page.goto('http://127.0.0.1:'+server.address().port+'/approval');
assert.ok((await page.locator('pre').textContent()).endsWith('LAST STATEMENT'));
assert.equal(await page.locator('script').count(),0);
for(const width of [320,390]){await page.setViewportSize({width,height:650});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
await page.screenshot({path:'/tmp/261012-approval-mobile.png',fullPage:true});
await page.setViewportSize({width:390,height:844});
await page.goto('http://127.0.0.1:'+server.address().port);await page.getByText('Needs your decision',{exact:true}).last().waitFor();
// Connected-source cards must fit a narrow phone after the design-system change.
await navClick(page.locator('nav [data-tab="health"]'));
await page.locator('.health article:not([hidden])').first().waitFor();
for(const width of [320,390]){
 await page.setViewportSize({width,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Connections overflows at '+width);
}
await page.setViewportSize({width:390,height:844});
await navClick(page.locator('nav [data-tab="inbox"]'));

{suggestions[0].source_line='Source: email from Ann Example · Sun 11 Oct';suggestions[2].source_line='Source: WhatsApp from Cara · Sat 10 Oct';await page.evaluate(()=>refresh());
const card=page.locator('#task-'+ids[0]);assert.equal(await card.locator('.original-body,.email-frame').count(),0);
await card.getByRole('button',{name:'Read email',exact:true}).click();const dialog=page.locator('#email-dialog');await dialog.waitFor({state:'visible'});
await dialog.getByText('Ann Example',{exact:true}).waitFor();
{const link=dialog.locator('a.attachment');assert.equal(await link.count(),1);assert.equal(await link.getAttribute('href'),'api/attachment?id='+ids[0]+'&event=7&part=1');assert.match(await link.textContent(),/Board <deck>\.pdf1006 KB/);}
const frame=page.frameLocator('#email-dialog iframe.email-frame');await frame.locator('#hello').waitFor();
// HTML emails render dark: light backgrounds go dark, dark text goes light, hue kept.
{await page.locator('#email-dialog iframe.email-frame').evaluate(f=>new Promise(r=>{const t=()=>f.style.visibility?setTimeout(t,50):r();t()}));
const light=c=>{const [r,g,b]=c.match(/[\d.]+/g).map(Number);return (Math.max(r,g,b)+Math.min(r,g,b))/510};
const css=(sel,prop)=>frame.locator(sel).evaluate((el,p)=>getComputedStyle(el)[p],prop);
assert.ok(light(await css('html','backgroundColor'))<.3,'The email document background must turn dark, including original root inline styles.');
assert.ok(light(await css('#box','backgroundColor'))<.3);assert.ok(light(await css('body','color'))>.7);
assert.ok(light(await css('#dark','color'))>.7);const blue=(await css('#blue','color')).match(/[\d.]+/g).map(Number);assert.ok(light(await css('#blue','color'))>.7&&blue[2]>blue[0])}
assert.equal(await frame.locator('#hello b').textContent(),'HTML');assert.equal(await frame.locator('a').first().getAttribute('target'),null);
assert.match(await frame.locator('#logo').getAttribute('src'),/api\/attachment\?id=aaaaaaaaaaaaaaaa&event=7&part=2$/);
await page.waitForTimeout(300);assert.equal(await page.evaluate(()=>window.pwned),undefined);
assert.equal(await dialog.locator('iframe').getAttribute('sandbox'),'allow-same-origin allow-popups allow-popups-to-escape-sandbox');
assert.ok(parseInt(await dialog.locator('iframe').evaluate(f=>f.style.height))>=80);
assert.equal(await dialog.getByRole('link',{name:'Open in Gmail ↗'}).first().getAttribute('href'),'https://mail.google.com/mail/u/?authuser=owner%40example.com#all/abc');
await dialog.getByText('1 related message').waitFor();await dialog.locator('details.email > summary').first().click();await dialog.getByText('Earlier note').waitFor();
await page.screenshot({path:'/tmp/261012-email-dialog.png'});
await dialog.getByRole('button',{name:'Close email'}).click();await dialog.waitFor({state:'hidden'});
{suggestions[0].scope='work';suggestions[1].scope='work';suggestions[2].scope='personal';await page.evaluate(()=>refresh());
const filter=page.locator('.scope-filter');await scopeClick(page,'Personal');
assert.equal(await page.locator('#task-'+ids[0]).count(),0);await page.locator('#task-'+ids[2]+' .chip.scope').getByText('Personal',{exact:true}).waitFor();
await scopeClick(page,'Work');assert.equal(await page.locator('#task-'+ids[2]).count(),0);
await page.locator('#task-'+ids[0]+' .chip.scope').getByText('Work',{exact:true}).waitFor();
assert.equal(await page.evaluate(()=>localStorage.getItem('scope-filter')),'work');
await scopeClick(page,'All');await page.locator('#task-'+ids[2]).waitFor();await page.screenshot({path:'/tmp/261012-scope-filter.png'});}
{// Notification links reveal the destination without changing saved device preferences.
const filter=()=>page.locator('.scope-filter');
await scopeClick(page,'Personal');
await page.evaluate(id=>location.hash='#card='+id,ids[0]);
await page.locator('#task-'+ids[0]).waitFor({state:'visible'});
await page.waitForFunction(()=>location.hash==='');
assert.equal(await page.evaluate(()=>localStorage.getItem('scope-filter')),'personal');
await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+ids[0]).isVisible(),true);
// An explicit filter selection ends the temporary reveal.
await scopeClick(page,'Personal');
assert.equal(await page.locator('#task-'+ids[0]).count(),0);
await scopeClick(page,'All');
await page.locator('#section-ready').click();
assert.equal(await page.locator('#task-'+ids[2]).count(),0);
await navClick(page.locator('nav [data-tab="events"]'));
await page.evaluate(id=>location.hash='#card='+id,ids[2]);
await page.locator('#task-'+ids[2]).waitFor({state:'visible'});
await page.waitForFunction(()=>location.hash==='');
assert.equal(await page.locator('#inbox').evaluate(el=>el.hidden),false);
assert.equal(await page.locator('nav [data-tab="inbox"]').getAttribute('aria-current'),'page');
assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('folded-sections'))),['ready']);
await page.evaluate(id=>location.hash='#card='+id,ids[3]);
await page.locator('#task-'+ids[3]).waitFor({state:'visible'});
assert.equal(await page.locator('#later-list').getAttribute('open'),'');
// An old closed task is in Done with the rest (folded away), and its link opens Done on it.
const saved={...suggestions[0]};Object.assign(suggestions[0],{status:'completed',created_at:'2026-01-01T09:00:00Z'});
await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+ids[0]).isVisible(),false);
await page.evaluate(id=>location.hash='#card='+id,ids[0]);
await page.locator('#task-'+ids[0]).waitFor({state:'visible'});
assert.equal(await page.locator('#finished-list').getAttribute('open'),'');
await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+ids[0]).isVisible(),true);
Object.assign(suggestions[0],saved);
// Do not throw away a link when its destination has not arrived in status yet.
const missing='eeeeeeeeeeeeeeee';await page.evaluate(id=>location.hash='#card='+id,missing);
await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>location.hash),'#card='+missing);
suggestions.push({...saved,id:missing});await page.evaluate(()=>refresh());
await page.locator('#task-'+missing).waitFor({state:'visible'});assert.equal(await page.evaluate(()=>location.hash),'');
suggestions.pop();await page.evaluate(()=>refresh());
// Restore the fixture's normal preferences, not a live owner decision.
await scopeClick(page,'All');
await page.locator('#later-list > summary').click();
await page.locator('#finished-list > summary').click();
await page.evaluate(()=>localStorage.setItem('folded-sections','[]'));
}
{// Plain-language blocker with folded details, live preparing clock and steps, deadlines, recipient picking.
const day=n=>{const d=new Date();d.setDate(d.getDate()+n);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
Object.assign(suggestions[1],{preparation_reason:'I couldn’t open the the CRM inbox. Log in once and tap Try again.',preparation_details:'gog gmail get abc --readonly: 404 Not Found'});
suggestions[0].due=day(-1);suggestions[2].due=day(1);
Object.assign(suggestions[2],{next_action:{kind:'email',label:'Send',revision:'abcdef123456',from:'owner@example.com',to:[],to_options:['will@vendor.example','ann@example.com'],cc:[],bcc:[],subject:'Pick',body:'Draft body'}});
await page.evaluate(()=>refresh());
const blocked=page.locator('#task-'+ids[1]);await blocked.getByText('Log in once and tap Try again').waitFor();
assert.equal(await blocked.locator('details.tech pre').isVisible(),false);await blocked.locator('details.tech > summary').click();
await blocked.locator('details.tech pre').getByText('404 Not Found').waitFor();
await blocked.getByRole('button',{name:'Try again',exact:true}).waitFor();suggestions[1].preparation_reason='Apple needs your passkey in the open tab, then tap Continue.';await page.evaluate(()=>refresh());await blocked.getByRole('button',{name:'Continue',exact:true}).waitFor();suggestions[1].preparation_reason='I couldn’t open the the CRM inbox. Log in once and tap Try again.';await page.evaluate(()=>refresh());await blocked.getByText('Log in once and tap Try again').waitFor();
await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+ids[1]+' details.tech').getAttribute('open'),'');  // Stays open across refreshes.
await page.locator('#task-'+ids[0]+' .chip').getByText('Overdue',{exact:true}).waitFor();await page.locator('#task-'+ids[2]+' .chip').getByText('Due tomorrow',{exact:true}).waitFor();
await page.screenshot({path:'/tmp/261012-blocked-details-due.png',fullPage:true});
Object.assign(suggestions[1],{preparation_status:'running',preparation_started_at:new Date(Date.now()-75000).toISOString(),preparation_retried:true});await page.evaluate(()=>refresh());
const running=page.locator('#task-'+ids[1]);assert.match(await running.locator('.elapsed').textContent(),/^1:1\d$/);
await running.locator('.steps li').getByText('Collect retention settings').waitFor({timeout:8000});await running.getByText('second try',{exact:false}).waitFor();
// Waiting on the browser: without a viewer the tab comes to the front here, with one the link opens it.
suggestions[1].preparation_browser={since:new Date(Date.now()-60000).toISOString(),page:'https://www.linkedin.com/feed/',link:null};await page.evaluate(()=>refresh());
await running.getByRole('button',{name:'Open the browser'}).click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('browser'));assert.deepEqual(posts.at(-1),{suggestion_id:ids[1]});  // the task's own page, opened again when its tab is gone
suggestions[1].preparation_browser.link='https://manage.example/open?to=browser';await page.evaluate(()=>refresh());
assert.equal(await running.getByRole('link',{name:'Open the browser'}).getAttribute('href'),'https://manage.example/open?to=browser');
// Waiting on a sign-in: Sign in opens the dialog for this task, so signing in there carries it on.
Object.assign(suggestions[1].preparation_browser,{sign_in:true,link:null});await page.evaluate(()=>refresh());
const signin=running.getByRole('button',{name:'Sign in',exact:true});assert.equal(await signin.getAttribute('data-task'),ids[1]);assert.equal(await signin.getAttribute('data-page'),'https://www.linkedin.com/feed/');
// An itsme or eID sign-in: the dialog opens the task's sign-in itself, shows the browser in place, and closes once the task carries on.
{const base=state;state=()=>({...base(),browser_link:'/browser/vnc.html?frame=window'});await page.evaluate(()=>refresh());progress.status='blocked';
await signin.click();const sd=page.locator('#signin-dialog');await sd.waitFor({state:'visible'});await page.locator('#signin-status-text').getByText('Drone Portal signs in with itsme or eID. Finish it below.',{exact:false}).waitFor();
assert.ok(posts.some(p=>p.suggestion_id===ids[1]&&Object.keys(p).length===1));assert.equal(await page.locator('#signin-frame').getAttribute('src'),'/browser/vnc.html?frame=window');
assert.ok(await page.locator('#signin-fine').isHidden());assert.equal(await page.locator('#signin-error').textContent(),'');assert.ok(await sd.getByRole('button',{name:'Continue'}).isVisible());assert.ok(await page.locator('#signin-go').isHidden());
await page.screenshot({path:'/tmp/261020-signin-eid.png'});progress.status='running';await sd.waitFor({state:'hidden',timeout:9000});await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('carrying on'));
state=base;await page.evaluate(()=>refresh());}
await running.getByRole('button',{name:'Open the browser'}).click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('browser'));assert.deepEqual(posts.at(-1),{suggestion_id:ids[1]});delete suggestions[1].preparation_browser;
const first=await running.locator('.elapsed').textContent();await page.waitForTimeout(2100);assert.notEqual(await running.locator('.elapsed').textContent(),first);
await page.screenshot({path:'/tmp/261012-preparing-progress.png'});
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Review and send',exact:true}).click();const send=page.locator('#send-dialog');await send.waitFor({state:'visible'});
assert.match(await sendHead(page),/^From: owner@example.com\nTo: will@vendor.example\n/);
await send.getByLabel('will@vendor.example').uncheck();assert.ok(await page.locator('#send-confirm').isDisabled());assert.match(await sendHead(page),/To: \(choose above\)/);
await send.getByLabel('ann@example.com').check();assert.match(await sendHead(page),/To: ann@example.com\nSubject: Pick/);
await page.screenshot({path:'/tmp/261012-send-recipients.png'});
await page.locator('#send-confirm').click();await send.waitFor({state:'hidden'});
const sent=posts.at(-1);assert.equal(sent.revision,'abcdef123456');assert.deepEqual(sent.recipients,['ann@example.com']);
Object.assign(suggestions[1],{preparation_status:'blocked',preparation_started_at:null,preparation_retried:false,preparation_details:null,preparation_reason:'Could not connect'});
delete suggestions[0].due;delete suggestions[2].due;delete suggestions[2].next_action;await page.evaluate(()=>refresh());}
{// Coming back later is folded by default and stays open once opened.
const later=page.locator('#later-list');assert.equal(await later.getAttribute('open'),null);assert.equal(await page.locator('#task-'+ids[3]).isVisible(),false);
// Section heads fold their section: Later opens, Ready folds (and is remembered), tapping again restores.
await later.locator(':scope>summary').click();await page.locator('#task-'+ids[3]).waitFor();
await later.locator(':scope>summary').click();await page.locator('#task-'+ids[3]).waitFor({state:'hidden'});
await page.locator('#section-ready').click();assert.equal(await page.locator('#task-'+ids[2]).count(),0);assert.equal(await page.locator('#section-ready').getAttribute('aria-expanded'),'false');
assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('folded-sections'))),['ready']);
await page.locator('#section-ready').click();assert.equal(await page.locator('#task-'+ids[2]).isVisible(),true);
await later.locator(':scope>summary').getByText('Coming back later').waitFor();await later.locator(':scope>summary').click();await page.locator('#task-'+ids[3]).waitFor();
await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+ids[3]).isVisible(),true);
await later.locator(':scope>summary').click();await page.locator('#task-'+ids[3]).waitFor({state:'hidden'});await page.waitForTimeout(300);await page.screenshot({path:'/tmp/261012-later-folded.png'});}
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Read message',exact:true}).waitFor();
suggestions[2].source_line=undefined;suggestions[2].event_ids='[3]';suggestions[2].original_urls=['https://mail.google.com/mail/u/?authuser=x#all/1'];await page.evaluate(()=>refresh());
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Read email',exact:true}).waitFor();
suggestions[0].source_line=undefined;suggestions[2].source_line=undefined;await page.evaluate(()=>refresh());}
// The longest relative time, set right here: 59 minutes at the start of the run turns into a short "1 hour ago" on a slow run
suggestions[0].created_at=new Date(Date.now()-45*60000).toISOString();await page.evaluate(()=>refresh());await page.locator('#task-'+ids[0]+' .meta time',{hasText:'45 minutes ago'}).waitFor();
for(const width of [320,360,390,768]){
await page.setViewportSize({width,height:844});await page.locator('#task-'+ids[0]+' .meta time').waitFor();  // laid out at the new width
const layout=await page.locator('#task-'+ids[0]+' .meta').evaluate(el=>{
 const clock=el.querySelector('time'),box=clock.getBoundingClientRect(),hidden=[];
 for(let e=el;e;e=e.parentElement)if(getComputedStyle(e).display==='none')hidden.push(e.tagName+'#'+e.id);
 return {height:el.getBoundingClientRect().height,clockWidth:box.width,clockHeight:box.height,overflow:document.documentElement.scrollWidth>innerWidth,hidden};
});
assert.ok(layout.height<110,`Scheduled card header too tall at ${width}px: ${JSON.stringify(layout)}`);
assert.ok(layout.clockWidth>75&&layout.clockHeight<32,`Timestamp crushed at ${width}px: ${JSON.stringify(layout)}`);
assert.equal(layout.overflow,false,`Overflow at ${width}px: `+JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width})).slice(0,12))));
}
await page.setViewportSize({width:390,height:844});
await page.screenshot({path:'/tmp/261011-assistant-scheduled-card.png',fullPage:true});
assert.equal(await page.locator('#suggestions textarea').count(),0);
await tap(page.locator('#task-'+ids[0]),'Change');await page.locator('#feedback-'+ids[0]).fill('Please make this shorter');await page.getByRole('button',{name:'Save changes',exact:true}).click();await page.locator('#feedback-'+ids[0]).waitFor({state:'detached'});assert.equal(posts.filter(p=>'feedback' in p).at(-1).feedback,'Please make this shorter');
// A Change stays on the card; the chat only opens when asked
assert.equal(await page.locator('.chat-head').count(),0);await page.locator('#task-'+ids[0]).waitFor();
// Success notices clear after a few seconds; errors stay.
await page.evaluate(()=>notice('Saved.'));assert.equal(await page.locator('#notice').textContent(),'Saved.');await page.waitForFunction(()=>$('#notice').textContent==='',null,{timeout:7000});
await page.evaluate(()=>notice('Broke.',true));await page.waitForTimeout(5000);assert.equal(await page.locator('#notice').textContent(),'Broke.');await page.evaluate(()=>notice(''));
// Ctrl+Enter in the change box saves like the Save changes button; plain Enter adds a new line.
{const card=page.locator('#task-'+ids[0]);await tap(card,'Change');const box=card.locator('#feedback-'+ids[0]);
await box.fill('First line');await box.press('Enter');await box.pressSequentially('Second line');assert.equal(await box.inputValue(),'First line\nSecond line');
const said=()=>posts.filter(p=>'feedback' in p),before=said().length;await box.press('Control+Enter');await box.waitFor({state:'detached'});assert.equal(said().length,before+1);assert.equal(said().at(-1).feedback,'First line\nSecond line');
assert.equal(await page.locator('.chat-head').count(),0);await page.locator('#task-'+ids[0]).waitFor();}
{const menu=page.locator('#task-'+ids[1]+' .more-actions');await menu.locator('summary').click();await page.evaluate(()=>refresh());assert.equal(await menu.getAttribute('open'),'','a refresh keeps an opened More menu open');await menu.locator('summary').click();}
{const card=page.locator('#task-'+ids[1]);const before=posts.length;await tap(card,'Not needed');assert.equal(posts.length,before);
await card.getByText('Why isn’t this needed?').waitFor();await card.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await card.getByText('Why isn’t this needed?').count(),0);
// Ctrl+Enter in any filled field clicks its editor's primary button: here Confirm not needed. Empty fields do nothing.
await tap(card,'Not needed');const note=card.locator('#dismiss-'+ids[1]);await note.press('Control+Enter');assert.equal(posts.length,before);await note.fill('Handled by Sam');await note.press('Control+Enter');
await page.waitForFunction(()=>document.querySelector('#toast').textContent.startsWith('Marked not needed'));assert.equal(posts.at(-1).choice,'reject');assert.equal(posts.at(-1).feedback,'Handled by Sam');suggestions[1].status='accepted';await page.evaluate(()=>refresh());}
await page.locator('#task-'+ids[1]).getByRole('button',{name:'Try again',exact:true}).click();await page.locator('#task-'+ids[1]).getByText('I’ll start shortly.',{exact:true}).waitFor();assert.equal(posts.at(-1).choice,'retry');
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Read result',exact:true}).click();await page.getByText('Full draft with',{exact:false}).waitFor();assert.equal(await page.locator('#result-content script').count(),0);await page.locator('#result-copy').click();assert.ok((await page.evaluate(()=>window.copied)).includes('Full draft with'));await page.getByRole('button',{name:'Close result'}).click();
suggestions[2].next_action={kind:'email',revision:'abcdef123456',from:'owner@example.com',to:['recipient@example.com'],cc:['cc@example.com'],bcc:[],subject:'Exact subject',body:'Exact email draft <script>not executable</script>',attachments:[{name:'credit <note>.pdf',sha256:'ab'.repeat(32),size:8754,type:'application/pdf'}]};
await page.evaluate(()=>refresh());await page.locator('#task-'+ids[2]).getByRole('button',{name:'Review and send',exact:true}).click();
await page.locator('#send-dialog').waitFor({state:'visible'});assert.match(await sendHead(page),/From: owner@example.com/);assert.match(await page.locator('#send-preview').textContent(),/Exact email draft/);assert.equal(await page.locator('#send-preview script').count(),0);
{const link=page.locator('#send-files a.attachment');assert.equal(await link.count(),1);assert.equal(await link.getAttribute('href'),'api/draft-attachment?id='+suggestions[2].id+'&sha='+'ab'.repeat(32));assert.match(await link.textContent(),/credit <note>\.pdf9 KB/);}
const beforeApproval=posts.length;await page.locator('#send-cancel').click();assert.equal(posts.length,beforeApproval);
// The same draft files open from Read result and Read email too.
{const href='api/draft-attachment?id='+ids[2]+'&sha='+'ab'.repeat(32);
await page.evaluate(id=>openResult(id),ids[2]);const r=page.locator('#result-files .draft-files a.attachment');await r.waitFor();assert.equal(await r.getAttribute('href'),href);{const text=await page.locator('#result-content').textContent();assert.match(text,/All checked\./);assert.doesNotMatch(text,/Attachments|credit <note>/)}await page.getByRole('button',{name:'Close result'}).click();
await page.evaluate(id=>openEmail(id),ids[2]);const e=page.locator('#email-content .draft-files a.attachment');await e.waitFor();assert.equal(await e.getAttribute('href'),href);await page.getByText('Original question').first().waitFor();await page.getByRole('button',{name:'Close email'}).click();}
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Review and send',exact:true}).click();await page.locator('#send-confirm').click();await page.locator('#send-dialog').waitFor({state:'hidden'});assert.equal(posts.at(-1).revision,'abcdef123456');assert.equal(posts.at(-1).suggestion_id,ids[2]);
// Edit the draft yourself: Send saves the edit first, then sends that exact new revision.
{await page.locator('#task-'+ids[2]).getByRole('button',{name:'Review and send',exact:true}).click();await page.locator('#send-edit').click();
assert.equal(await page.locator('#send-body').inputValue(),suggestions[2].next_action.body);assert.doesNotMatch(await sendHead(page),/Subject:/);assert.ok(await page.locator('#send-preview').isHidden());
await page.locator('#send-body').fill('My own words.');await page.locator('#send-confirm').click();await page.locator('#send-dialog').waitFor({state:'hidden'});
assert.deepEqual([posts.at(-2).body,posts.at(-2).revision,posts.at(-2).subject],['My own words.','abcdef123456',suggestions[2].next_action.subject]);assert.equal(posts.at(-1).revision,'fedcba654321');
await page.locator('#task-'+ids[2]).getByRole('button',{name:'Review and send',exact:true}).click();assert.equal(await page.locator('#send-editor').isHidden(),true);await page.locator('#send-cancel').click();}
suggestions[2].next_actions=[{...suggestions[2].next_action,label:'Send reply',state:{status:'blocked',error:'Gmail is connected read-only. Nothing was sent.'}},
 {kind:'prepare',revision:'fedcba654321',label:'Investigate further',instructions:'Check the field mapping'},
 {kind:'open',revision:'111111111111',label:'Open the Zap',url:'https://example.com/zap'}];
await page.evaluate(()=>refresh());const smartCard=page.locator('#task-'+ids[2]);await smartCard.getByRole('button',{name:'Review and send reply',exact:true}).waitFor();
assert.ok(await smartCard.getByRole('link',{name:'Open the Zap',exact:false}).isVisible());assert.ok(await smartCard.getByText('Send reply: Gmail is connected read-only. Nothing was sent.',{exact:true}).isVisible());
assert.equal(await smartCard.getByRole('button',{name:'Investigate further',exact:true}).count(),0);
suggestions[2].next_actions=suggestions[2].next_actions.slice(1);await page.evaluate(()=>refresh());
await smartCard.getByRole('button',{name:'Investigate further',exact:true}).click();assert.equal(posts.at(-1).revision,'fedcba654321');assert.equal(posts.at(-1).suggestion_id,ids[2]);
suggestions[2].next_actions=[{kind:'email_demo',label:'Send',revision:'abcdef123456',from:'the-machine@example.invalid',to:['alex@example.invalid'],subject:'Demo reply',body:'Thanks!'}];await page.evaluate(()=>refresh());
await smartCard.getByRole('button',{name:'Review and send',exact:true}).click();await page.getByText('Demo only. Nothing will be sent.',{exact:true}).waitFor();assert.equal(await page.locator('#send-confirm').isVisible(),false);
const demoPosts=posts.length;await page.locator('#send-cancel').click();assert.equal(posts.length,demoPosts);
assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
await tap(page.locator('#task-'+ids[2]),'Mark done');await page.locator('#toast [data-undo]').click();await page.locator('#task-'+ids[2]).getByRole('button',{name:'Read result',exact:true}).waitFor();assert.equal(posts.at(-1).choice,'undo');
await navClick(page.locator('nav [data-tab="health"]'));assert.equal(await page.locator('#connectors .state-dot.t-mint').count(),1);assert.equal(await page.locator('#connectors .state-dot.t-amber').count(),1);assert.equal(await page.getByText('32 failed syncs',{exact:false}).isVisible(),false);
assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));assert.deepEqual(errors,[]);

// Runtime errors and committed review outcomes are distinct and fit a phone.
reviewRuns=[{run_id:'r1',status:'error',review_saved:true,checked_count:40,pending_count:0,claimed_count:40,duration_seconds:121,attempts:1,error:'model_idle_timeout',error_phase:'model',started_at:new Date().toISOString()},
{run_id:'r2',status:'error',review_saved:false,checked_count:0,pending_count:3,claimed_count:3,duration_seconds:60,attempts:2,error:null,started_at:new Date().toISOString()},
{run_id:'r3',status:'ok',review_saved:true,checked_count:12,pending_count:0,claimed_count:12,duration_seconds:61,attempts:1,last_error:'connection_error',last_error_phase:'connection',error_at:new Date().toISOString(),started_at:new Date().toISOString()}];
reviewHealth={worker:reviewRuns[0],pending:3,total:55};
await page.evaluate(()=>refresh());
assert.match(await page.locator('#review-summary').textContent(),/^3 waiting · 52 reviewed · last .*\. Next one shortly\.$/);
// The AI sign-in's limits show as bars; a window that has already reset says nothing about now.
modelUsage=[{provider:'openai',name:'OpenAI',windows:[{label:'5h',used:42,reset:Date.now()/1000+3600|0},{label:'168h',used:18,reset:Date.now()/1000+4*86400|0},{label:'5h',used:99,reset:Date.now()/1000-60|0}]}];
await page.evaluate(()=>refresh());
const usage=await page.locator('#model-usage').textContent();
assert.match(usage,/OpenAI 5-hour limit: 42% used, resets /);assert.match(usage,/OpenAI Weekly: 18% used, resets /);assert.doesNotMatch(usage,/99%/);
assert.equal(await page.locator('#model-usage progress').count(),2);
await page.locator('#health-tabs [data-health-pane="reviews"]').click();  // Connections > Reviews
await page.locator('#review-run-history > summary').click();
const runs=page.locator('#review-runs');
assert.match(await runs.textContent(),/40 signal versions saved/);
assert.match(await runs.textContent(),/3 signal versions still waiting/);
assert.match(await runs.textContent(),/original explanation was not retained/);
assert.match(await runs.textContent(),/AI stopped responding/);
assert.match(await runs.textContent(),/temporarily unreachable/);
assert.match(await runs.textContent(),/Duration 2m 1s/);
await page.evaluate(()=>refresh());assert.equal(await page.locator('#review-run-history').getAttribute('open'),'');
for(const width of [320,390]){await page.setViewportSize({width,height:700});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
await page.screenshot({path:'/tmp/261013-review-observability.png',fullPage:true});
await page.setViewportSize({width:390,height:844});
reviewRuns=[];reviewHealth={};await page.evaluate(()=>refresh());
await page.screenshot({path:'/tmp/261011-assistant-connections.png',fullPage:true});await navClick(page.locator('nav [data-tab="inbox"]'));await page.screenshot({path:'/tmp/261011-assistant-inbox.png',fullPage:true});
// Light mode: a sun/moon switch in Settings, remembered per browser; HTML emails keep their own colors in light.
await navClick(page.locator('#open-settings'));
{const theme=()=>page.evaluate(()=>[document.documentElement.dataset.theme,document.documentElement.dataset.themeMode]);
const toggle=page.locator('#theme-toggle');const size=()=>toggle.evaluate(el=>{const b=el.getBoundingClientRect();return b.width+'x'+b.height});
assert.deepEqual(await theme(),['dark','auto']);assert.equal(await toggle.getAttribute('aria-checked'),'false');const before=await size();
// Before any choice it follows the device, live.
await page.emulateMedia({colorScheme:'light'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');assert.deepEqual(await theme(),['light','auto']);
await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
await toggle.scrollIntoViewIfNeeded();await toggle.screenshot({path:'/tmp/261013-toggle-dark.png',scale:'device'});
await toggle.click();assert.deepEqual(await theme(),['light','light']);assert.equal(await toggle.getAttribute('aria-checked'),'true');assert.equal(await size(),before);
assert.match(await page.evaluate(()=>document.cookie),/(^|; )theme=light(;|$)/);  // shared with the hosted app's pages
await page.waitForTimeout(400);await toggle.screenshot({path:'/tmp/261013-toggle-light.png'});
assert.equal(await page.evaluate(()=>getComputedStyle(document.body).color),'rgb(29, 29, 27)');
await page.reload();await page.locator('#settings:not([hidden])').waitFor();await navClick(page.locator('nav [data-tab="inbox"]'));await page.locator('#task-'+ids[0]).waitFor();assert.deepEqual(await theme(),['light','light']);  // a reload stays in Settings (#settings/general)
await page.setViewportSize({width:1100,height:900});await page.screenshot({path:'/tmp/261013-light-desktop.png'});
{await page.setViewportSize({width:1100,height:2600});const f=await page.locator('.site-footer').evaluate(el=>[el.getBoundingClientRect().bottom,innerHeight,el.textContent]);
assert.ok(Math.abs(f[0]-f[1])<2,'footer sits at the bottom of a short page: '+f);assert.match(f[2],/© 20\d\d The Machine\. /);await page.screenshot({path:'/tmp/261013-footer-tall.png'});}
await page.setViewportSize({width:320,height:700});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/261013-light-mobile.png',fullPage:true});
await page.setViewportSize({width:1100,height:900});
await page.evaluate(id=>openEmail(id),ids[0]);const f=page.frameLocator('#email-dialog iframe.email-frame');await f.locator('#box').waitFor();
await page.locator('#email-dialog iframe.email-frame').evaluate(fr=>new Promise(r=>{const t=()=>fr.style.visibility?setTimeout(t,50):r();t()}));
assert.equal(await f.locator('#box').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
assert.equal(await f.locator('html').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
await page.getByRole('button',{name:'Close email'}).click();
await navClick(page.locator('#open-settings'));await toggle.click();assert.deepEqual(await theme(),['dark','dark']);
// Picked in the app meanwhile (the shared cookie): coming back to the tab shows it.
await page.evaluate(()=>{document.cookie='theme=light; Path=/';document.dispatchEvent(new Event('visibilitychange'))});
assert.deepEqual(await theme(),['light','light']);assert.equal(await toggle.getAttribute('aria-checked'),'true');
await toggle.click();assert.deepEqual(await theme(),['dark','dark']);}
assert.match(await page.locator('#updated').textContent(),/\b\d{2}:\d{2}$/);  // 24-hour clock, no AM/PM
// Tapping the logo or the time refreshes and shows a new tagline.
{const tag=()=>page.locator('#tagline').textContent();const first=await tag();await page.locator('img.avatar').click();await page.waitForFunction(t=>document.querySelector('#tagline').textContent!==t,first);
const second=await tag();await page.locator('#live').click();await page.waitForFunction(t=>document.querySelector('#tagline').textContent!==t,second);}
console.log('UX browser: mobile groups, Work/Personal filter, Change feedback, retry, safe full results, copy, Undo, recovered health and no overflow passed.');
}finally{await browser?.close();await new Promise(r=>server.close(r))}
