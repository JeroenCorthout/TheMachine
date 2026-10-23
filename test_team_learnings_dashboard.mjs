import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Feedback, Share learnings with The Machine team (team_learnings.py): a quiet card; Review shows exactly what would be
// sent, item by item, to edit or remove; an error log only when ticked; Send posts only the kept items as they read;
// Not now and Never ask; the Settings switch. All mocked, nothing leaves the test. SHOTS=dir saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const ask=()=>({id:'tl_1',created_at:'2026-10-23T09:00:00+00:00',error:null,items:[
 {n:1,text:'Drafts for people who don’t know the context yet should introduce it first.',example:'A first email to a new supplier opens with who we are and why we write.',area:'drafts',taken:'Took out 2 names.',log:null},
 {n:2,text:'People often switch a card’s channel to WhatsApp in chat. A button on the card would save a step.',example:'',area:'chat',taken:'',log:null},
 {n:3,text:'Graph updates fail now and then on a busy day, and the card for it shows nothing.',example:'',area:'graph',taken:'',log:'KeyError: missing key "…"\n  at graph.py:120 build (7 times this week)'}]});
let view,posts;
const reset=()=>{view={offer:true,network:true,ask:ask(),last_shared:null};posts=[]};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 const asset={'/brand/avatar.png':'brand/app-icon-192.png','/brand/serif.woff2':'brand/fonts/source-serif-4-latin.woff2','/brand/sans.woff2':'brand/fonts/inter-latin.woff2'}[url.pathname];
 if(asset){res.setHeader('Content-Type',asset.endsWith('png')?'image/png':'font/woff2');res.end(await readFile(new URL('./'+asset,import.meta.url)));return}
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],review_runs:[],voices:[],
  facts:[{id:1,kind:'fact',text:'Quotes go out in EUR, excluding VAT.',created_at:'2026-10-20T09:00:00+00:00',merged:0}],team_learnings:view}));return}
 if(url.pathname==='/api/action'){let body='';for await(const c of req)body+=c;const {action,args}=JSON.parse(body);posts.push({action,args});
  if(action==='learnings_share'){view={...view,ask:null,last_shared:{at:'2026-10-23T10:00:00+00:00',count:args.items.length}};res.end(JSON.stringify({status:'sent',count:args.items.length,team_learnings:view}));return}
  if(action==='learnings_later'){view={...view,ask:null};res.end(JSON.stringify({status:'later',team_learnings:view}));return}
  if(action==='learnings_never'){view={...view,ask:null,offer:false};res.end(JSON.stringify({offer:false,team_learnings:view}));return}
  if(action==='set_learnings_offer'){view={...view,offer:args.on};res.end(JSON.stringify({team_learnings:view}));return}}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port,errors=[],shots=process.env.SHOTS;let browser;
const shot=async(loc,name)=>{if(shots){await loc.page().evaluate(()=>document.fonts.ready);await loc.screenshot({path:`${shots}/${name}.png`})}};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of [1440,390])for(const scheme of ['light','dark']){
  reset();
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#feedback');
  await navClick(page.locator('[data-tab="feedback"]').first());
  const box=page.locator('#share-learnings'),card=box.locator('.share-ask');
  await card.waitFor();
  assert.equal((await card.locator('strong').textContent()),'Share 3 learnings with The Machine team?');
  assert.equal(await box.locator('.share-panel').count(),0,'quiet until Review');
  await shot(box,`ask-${width}-${scheme}`);
  await card.locator('[data-share="open"]').click();
  const items=box.locator('.share-item');
  assert.equal(await items.count(),3);
  assert.equal(await items.nth(0).locator('textarea').first().inputValue(),ask().items[0].text);
  assert.equal(await box.locator('[data-share-log]').count(),1,'a log tick only where there is a log');
  assert.equal(await box.locator('[data-share-log]').isChecked(),false,'off by default');
  assert.equal(await box.locator('#share-named').isChecked(),false);
  assert.equal(await box.locator('[data-share="send"]').textContent(),'Send 3 learnings');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal scroll at '+width);
  await shot(box,`edit-${width}-${scheme}`);
  if(shots&&width===1440&&scheme==='light')await page.screenshot({path:`${shots}/edit-${width}-${scheme}-page.png`});
  if(scheme==='light'){
   // Edit the first, remove the second, tick the log on the third: what goes is what it now reads
   await items.nth(0).locator('textarea').first().fill('Drafts for new contacts should introduce the context first.');
   await items.nth(1).locator('[data-share-remove]').click();
   await box.locator('.share-item.removed',{hasText:'Removed. It won’t be sent.'}).waitFor();
   assert.equal(await box.locator('[data-share="send"]').textContent(),'Send 2 learnings');
   await box.locator('[data-share-log]').check();
   assert.deepEqual(posts,[],'nothing sent before Send');
   await box.locator('[data-share="send"]').click();
   await page.locator('#toast',{hasText:'Sent 2 learnings. Thank you.'}).waitFor({state:'attached'});
   assert.deepEqual(posts,[{action:'learnings_share',args:{id:'tl_1',named:false,items:[
    {n:1,text:'Drafts for new contacts should introduce the context first.',example:ask().items[0].example,log:false},
    {n:3,text:ask().items[2].text,example:'',log:true}]}}]);
   await card.waitFor({state:'detached'});
  }else if(width===1440){
   await box.locator('[data-share="never"]').click();
   await page.locator('#toast',{hasText:'I won’t ask again. Turn it back on in Settings.'}).waitFor({state:'attached'});
   assert.deepEqual(posts.map(p=>p.action),['learnings_never']);
   // Settings shows the switch off; turning it back on asks again another week
   await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="general"]').click();
   const sw=page.locator('#share-offer');await sw.waitFor();
   assert.equal(await sw.getAttribute('aria-checked'),'false');
   await sw.click();await page.locator('#share-offer[aria-checked="true"]').waitFor();
   assert.deepEqual(posts.at(-1),{action:'set_learnings_offer',args:{on:true}});
   await shot(page.locator('#settings-share'),`settings-${width}-${scheme}`);
  }else{
   await box.locator('.share-panel [data-share="later"]').click();
   await page.locator('#toast',{hasText:'Not now. I’ll ask again another week.'}).waitFor({state:'attached'});
   assert.deepEqual(posts.map(p=>p.action),['learnings_later']);
  }
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('team learnings dashboard ok');
}finally{await browser?.close();server.close()}
