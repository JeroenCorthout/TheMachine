import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, How you write: one line per channel. Save shows only after a change; Remove clears a line; a new channel
// is added with its line. All mocked.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const start=[{channel:'All',text:'Natural dates.',source:'assistant',updated_at:'2026-10-20T10:00:00+00:00'},
 {channel:'Email',text:'English: Hi or Hey + first name\nRegards, a blank line, then the name\nShort paragraphs',source:'study',updated_at:'2026-10-20T10:00:00+00:00'}];
let voices=start;
const facts=[{id:9,kind:'fact',text:'Newest fact.',created_at:'2026-10-21T09:00:00+00:00',merged:2},{id:8,kind:'draft',text:'A rule for a kind of task.',created_at:'2026-10-20T09:00:00+00:00',merged:0},
 {id:3,kind:'fact',text:'Older fact.',created_at:'2026-10-10T09:00:00+00:00',merged:0}];
const posts=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[{id:'sk1',title:'Turn 6 notes into a skill: Listing exchanges',body:'When a listing exchange is agreed.',status:'pending',
  created_at:'2026-10-21T09:00:00+00:00',event_ids:'[]',labels:{accept:'Create skill',reject:'Not now'}}],moved:[{skill_id:'sk_9',name:'Partner listings',notes:5}],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],review_runs:[],voices,facts,
  condensed:{at:'2026-10-21T08:00:00+00:00',notes:3}}));return}
 if(url.pathname==='/api/action'){let body='';for await(const c of req)body+=c;const {action,args}=JSON.parse(body);posts.push({action,args});
  if(action==='set_voice'){voices=voices.filter(v=>v.channel!==args.channel);if(args.text)voices.push({channel:args.channel,text:args.text,source:'owner',updated_at:new Date().toISOString()});
   res.end(JSON.stringify({voices}));return}
  if(action==='decide'){res.end(JSON.stringify({status:'completed'}));return}
  if(action==='unmerge_fact'){res.end(JSON.stringify({restored:2}));return}}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const scheme of ['light','dark']){
  voices=start;posts.length=0;
  const context=await browser.newContext({viewport:{width:390,height:844},colorScheme:scheme}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  // A skill-from-notes card says what its buttons do.
  const skillCard=page.locator('#task-sk1');await skillCard.waitFor();
  assert.ok(await skillCard.getByRole('button',{name:'Create skill'}).count());
  assert.ok(await skillCard.locator('button',{hasText:'Not now'}).count());
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="writing"]').click();
  const forms=page.locator('#voices .voice-form:not([data-new])');await forms.first().waitFor();
  assert.equal(await forms.count(),2);
  assert.match(await forms.nth(0).locator('.sig-head').textContent(),/Everywhere/);
  assert.match(await forms.nth(1).locator('.sig-head').textContent(),/Email.*From your messages/);
  const email=forms.nth(1);
  assert.equal(await email.locator('textarea').inputValue(),'English: Hi or Hey + first name\nRegards, a blank line, then the name\nShort paragraphs');
  assert.equal(await email.locator('textarea').getAttribute('maxlength'),'450');
  assert.match(await page.locator('#settings-voices .section-head p').textContent(),/A few lines per channel/);
  assert.equal(await email.locator('.primary').isHidden(),true);  // no change, no Save
  await forms.first().locator('textarea').fill('Unsaved everywhere preference.');
  await email.locator('textarea').fill('Hey name, short.\nRegards, a blank line, then my name.');
  await email.locator('.primary').click();
  await page.locator('#notice',{hasText:'Saved.'}).waitFor({state:'attached'});
  assert.deepEqual(posts.at(-1),{action:'set_voice',args:{channel:'Email',text:'Hey name, short.\nRegards, a blank line, then my name.'}});
  await page.locator('#voices .voice-form[data-channel="Email"] .sig-head',{hasText:'Yours'}).waitFor();
  assert.equal(await page.locator('#voices .voice-form[data-channel="All"] textarea').inputValue(),'Unsaved everywhere preference.');
  assert.equal(await page.locator('#voices .voice-form[data-channel="All"] .primary').isVisible(),true);
  // Add a channel with its line; a missing name is said, not posted.
  const add=page.locator('#voices .voice-form[data-new]');
  await add.locator('textarea').fill('lowercase, no full stop');
  await add.locator('button').click();
  assert.match(await add.locator('[role=status]').textContent(),/Name the channel/);
  await add.locator('input').fill('WhatsApp');await add.locator('button').click();
  await page.locator('#voices .voice-form[data-channel="WhatsApp"]').waitFor();
  // Remove clears a line.
  await page.locator('#voices .voice-form[data-channel="All"] [data-voice-remove]').click();
  await page.locator('#voices .voice-form[data-channel="All"]').waitFor({state:'detached'});
  assert.deepEqual(posts.at(-1),{action:'set_voice',args:{channel:'All',text:''}});
  if(process.env.REVIEW_SHOTS)await page.screenshot({path:process.env.REVIEW_SHOTS+'/'+scheme+'-writing.png',fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // What I've learned: grouped (how you write, facts, how to do things), newest first, merges shown with Undo.
  await page.goto('http://127.0.0.1:'+server.address().port+'/#feedback');
  await navClick(page.locator('[data-tab="feedback"]'));
  const learned=page.locator('#facts');await learned.locator('.learn-head').first().waitFor();
  assert.deepEqual(await learned.locator('.learn-head h3').allTextContents(),['How you write 2','Facts 2','How to do things 1','Moved into skills 1']);
  await learned.locator('[data-unmerge-skill="sk_9"]').click();
  await page.locator('#toast',{hasText:'Notes restored.'}).waitFor({state:'attached'});
  assert.deepEqual(posts.at(-1),{action:'unmerge_fact',args:{skill:'sk_9'}});
  assert.match(await learned.locator('.learn-note').textContent(),/Condensed 3 notes on/);
  assert.equal(await learned.locator('.voice-row[data-channel="Email"] .voice-lines li').count(),2);  // a block keeps its lines
  const rows=await learned.locator('.learn-group .list>div .grow').allTextContents();
  assert.match(rows[rows.indexOf(rows.find(r=>r.startsWith('Newest')))],/Merged from 2 notes/);
  assert.ok(rows.findIndex(r=>r.startsWith('Newest'))<rows.findIndex(r=>r.startsWith('Older')));
  await learned.locator('[data-unmerge-fact="9"]').click();
  await page.locator('#toast',{hasText:'Notes restored.'}).waitFor({state:'attached'});
  assert.deepEqual(posts.at(-1),{action:'unmerge_fact',args:{id:9}});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('voices dashboard ok');
}finally{await browser?.close();server.close()}
