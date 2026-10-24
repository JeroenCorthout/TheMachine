import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A line of How you write learned from a note on a card: the card's chat says "Saved to How you write: Slack" with Edit,
// which opens Settings, Writing with that channel's line in view and ready to type in. What I've learned has Edit on
// each line too. On a phone and a computer, light and dark. All mocked.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),t0=Date.now()-3600000;
const voices=[{channel:'Email',text:'English: Hey + first name\nShort paragraphs',source:'study',updated_at:now},
 {channel:'Slack',text:'No greeting or sign-off\nSentences often start lowercase, names keep their capitals',source:'assistant',updated_at:now},
 {channel:'WhatsApp',text:'Short bursts in Flemish',source:'study',updated_at:now}];
const cards=[{id:'c1c1c1c1c1c1c1c1',title:'Nudge Andrea about the signup',body:'Drafted a short nudge.',status:'prepared',scope:'work',created_at:now,event_ids:[1],
 next_actions:[{kind:'slack',label:'Send to Andrea',body:'did you get a chance to try it?',revision:'r1',to_name:'Andrea',account_id:'work-slack'}]}];
const chat={session:true,status:'ok',active:false,cursor:'k1',items:[
 {kind:'reply',text:'Drafted a nudge.',drafts:[{kind:'slack',to:['andrea'],subject:'',body:'did you get a chance to try it?',reply:false}],at:t0},
 {kind:'you',text:'I only lowercase the start of sentences, not names',at:t0+1000},
 {kind:'event',text:'Saved to How you write: Slack',voice:'Slack',at:t0+2000},
 {kind:'reply',text:'Fixed: names keep their capitals.',drafts:[{kind:'slack',to:['Andrea'],subject:'',body:'did you get a chance to try it, Andrea?',reply:false}],at:t0+3000}]};
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],connectors:[],voices,facts:[]}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body);if(r.action==='card_chat'){res.end(JSON.stringify(chat));return}res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [name,viewport] of [['phone',{width:390,height:844}],['desktop',{width:1280,height:900}]])for(const scheme of ['light','dark']){
  const context=await browser.newContext({viewport,colorScheme:scheme,locale:'en-US'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const shot=async n=>{if(process.env.REVIEW_SHOTS)await page.screenshot({path:`${process.env.REVIEW_SHOTS}/${name}-${scheme}-${n}.png`})};
  await page.goto('http://127.0.0.1:'+server.address().port+'/#chat=c1c1c1c1c1c1c1c1');
  const line=page.locator('#chat-log .chat-event',{hasText:'Saved to How you write'});await line.waitFor();
  assert.match(await line.textContent(),/^Saved to How you write: Slack · Edit/);
  assert.equal(await line.evaluate(n=>n.classList.contains('mine')),false);  // the Machine did it: a line in the middle
  await line.scrollIntoViewIfNeeded();await shot('chat');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await line.getByRole('link',{name:'Edit'}).click();
  const slack=page.locator('#voices .voice-form[data-channel="Slack"] textarea');
  await page.waitForFunction(()=>document.activeElement?.closest?.('.voice-form')?.dataset.channel==='Slack');
  assert.equal(new URL(page.url()).hash,'#settings/writing');
  assert.equal(await page.locator('#settings-tabs [data-settings-tab="writing"]').getAttribute('aria-selected'),'true');
  assert.ok(await slack.isVisible());
  assert.match(await page.locator('#settings-voices .section-head p').textContent(),/Edit it here, or tell any card/);
  await page.waitForTimeout(1700);await shot('settings');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // Back goes to the chat
  await page.goBack();await page.locator('#chat-log .chat-event',{hasText:'Saved to How you write'}).waitFor();
  // What I've learned: Edit on each line opens that line
  await page.goto('http://127.0.0.1:'+server.address().port+'/#feedback');
  await navClick(page.locator('[data-tab="feedback"]'));
  const row=page.locator('#facts .voice-row[data-channel="WhatsApp"]');await row.waitFor();
  await row.scrollIntoViewIfNeeded();await shot('learned');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await row.getByRole('button',{name:'Edit how you write: WhatsApp'}).click();
  await page.waitForFunction(()=>document.activeElement?.closest?.('.voice-form')?.dataset.channel==='WhatsApp');
  // A channel without a line yet opens the new-line form with its name filled in
  await page.evaluate(()=>{const a=document.createElement('a');a.href='#settings/writing';a.dataset.voiceEdit='LinkedIn';a.textContent='x';document.body.append(a);a.click();a.remove()});
  await page.waitForFunction(()=>document.activeElement?.closest?.('.voice-form')?.dataset.new!==undefined);
  assert.equal(await page.locator('#voices .voice-form[data-new] input').inputValue(),'LinkedIn');
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('voice saved dashboard ok');
}finally{await browser?.close();server.close()}
