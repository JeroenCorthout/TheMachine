import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Feedback, What I've learned: one block per kind with a heading and count, channels show 2 lines with More,
// long blocks fold after 5 with Show all, and Remove on a fact still works. All mocked; LEARNED_SHOTS=dir saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const t=d=>`2026-10-${String(d).padStart(2,'0')}T09:00:00+00:00`;
const voices=[
 {channel:'LinkedIn',text:'English, or Dutch when they write Dutch\nOpen with Hi + first name, no small talk\nTwo short paragraphs at most\nWarm but direct, no exclamation marks\nEnd with a question when a reply is wanted',updated_at:t(22)},
 {channel:'WhatsApp',text:'Dutch with friends and family, English for work\nNo greeting, straight to the point\nlowercase, no full stop at the end\nOne emoji at most',updated_at:t(21)},
 {channel:'Slack',text:'English\nNo greeting in threads\nShort lines, one idea each\nLinks inline, no previews',updated_at:t(20)},
 {channel:'Email',text:'English: Hi or Hey + first name\nRegards, a blank line, then the name\nShort paragraphs\nSay the ask in the first line',updated_at:t(19)},
 {channel:'X DM',text:'English\nNo greeting\nOne or two lines',updated_at:t(18)}];
let facts=[
 {id:21,kind:'fact',text:'Pat leads partnerships at acme.example and prefers a call over long email threads.',created_at:t(22),merged:0},
 {id:20,kind:'fact',text:'Sam handles invoices for acme.example; send billing questions to them, not to Pat.',created_at:t(21),merged:3},
 {id:19,kind:'fact',text:'The team meets on Mondays at 10:00.',created_at:t(20),merged:0},
 {id:18,kind:'fact',text:'Fridays are kept free of meetings.',created_at:t(19),merged:0},
 {id:17,kind:'fact',text:'Quotes go out in EUR, excluding VAT.',created_at:t(18),merged:0},
 {id:16,kind:'fact',text:'Newsletters from acme.example never need a reply.',created_at:t(17),merged:0},
 {id:15,kind:'fact',text:'Trial sign-ups get a welcome note within a day.',created_at:t(16),merged:0},
 {id:14,kind:'fact',text:'A very long fact to check wrapping: when a partner asks for a co-marketing slot, check the shared calendar first and propose two dates in the same week, never more than three weeks out.',created_at:t(15),merged:0},
 {id:8,kind:'draft',text:'For a listing exchange, ask for their listing link before sending ours.',created_at:t(14),merged:0}];
const posts=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 const asset={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'}[url.pathname];
 if(asset){res.setHeader('Content-Type',asset.endsWith('png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+asset,import.meta.url)));return}
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],moved:[{skill_id:'sk_9',name:'Partner listings',notes:5}],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],review_runs:[],voices,facts,condensed:{at:t(20),notes:4}}));return}
 if(url.pathname==='/api/action'){let body='';for await(const c of req)body+=c;const {action,args}=JSON.parse(body);posts.push({action,args});
  if(action==='forget_fact'){facts=facts.filter(f=>f.id!==args.id);res.end(JSON.stringify({ok:true}));return}}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;const shots=process.env.LEARNED_SHOTS;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1440,390])for(const scheme of ['light','dark']){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#feedback');
  await navClick(page.locator('[data-tab="feedback"]').first());
  const learned=page.locator('#facts');await learned.locator('.learn-group').first().waitFor();
  // Separate blocks, each with its own heading, count, line and card.
  assert.deepEqual(await learned.locator('.learn-group').evaluateAll(g=>g.map(x=>x.dataset.group)),['voices','facts','rules','skills']);
  assert.deepEqual(await learned.locator('.learn-head h3').allTextContents(),['How you write 5','Facts 8','How to do things 1','Moved into skills 1']);
  assert.equal(await learned.locator('.learn-group > .list').count(),4);
  assert.match(await learned.locator('.learn-note').textContent(),/^Condensed 4 notes on Tue 20 Oct( 2026)?\.$/);
  assert.equal(await learned.locator('.learn-head h3').first().evaluate(e=>getComputedStyle(e).fontFamily.includes('Serif')),true);
  if(shots){await page.evaluate(()=>document.fonts.ready);await learned.screenshot({path:`${shots}/learned-${width}-${scheme}.png`})}
  const gap=await learned.locator('.learn-group').evaluateAll(g=>g[1].getBoundingClientRect().top-g[0].getBoundingClientRect().bottom);
  assert.ok(gap>=20,'blocks have real space between them: '+gap);
  // A channel has its icon and 2 lines; More shows the rest, the full text stays in the page.
  const li=learned.locator('.voice-row[data-channel="LinkedIn"]');
  assert.equal(await li.locator('.icon svg').count(),1);
  assert.equal(await li.locator('.voice-lines li:visible').count(),2);
  assert.equal(await li.locator('.voice-lines li').count(),5);
  await li.locator('[data-voice-more]').click();
  assert.equal(await learned.locator('.voice-row[data-channel="LinkedIn"] .voice-lines li:visible').count(),5);
  assert.equal(await learned.locator('.voice-row[data-channel="LinkedIn"] [data-voice-more]').textContent(),'Less');
  // Facts fold after 5; Show all 8 shows the rest.
  const fg=learned.locator('[data-group="facts"]');
  assert.equal(await fg.locator('.fact-row').count(),5);
  await fg.locator('[data-learn-all]',{hasText:'Show all 8'}).click();
  assert.equal(await learned.locator('[data-group="facts"] .fact-row').count(),8);
  // Remove sits to the right of the text, never under it.
  const long=learned.locator('.fact-row',{hasText:'A very long fact'});
  const [txt,btn]=await Promise.all([long.locator('.grow').boundingBox(),long.locator('[data-forget]').boundingBox()]);
  assert.ok(btn.x>=txt.x+txt.width-1&&btn.y<txt.y+txt.height,'Remove beside the text');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal scroll at '+width);
  assert.ok(txt.width>=(width<500?200:400),'fact text keeps its width: '+txt.width);
  if(shots)await learned.screenshot({path:`${shots}/learned-${width}-${scheme}-open.png`});
  if(width===390&&scheme==='dark'){
   await learned.locator('[data-forget="19"]').click();
   await page.locator('#toast',{hasText:'Removed.'}).waitFor({state:'attached'});
   assert.deepEqual(posts.at(-1),{action:'forget_fact',args:{id:19}});
   await learned.locator('[data-forget="19"]').waitFor({state:'detached'});
   assert.match(await learned.locator('[data-group="facts"] h3').textContent(),/Facts 7/);
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('learned dashboard ok');
}finally{await browser?.close();server.close()}
