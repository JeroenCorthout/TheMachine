import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings > Model: the model in use, the plan's windows with when each starts over, the usage limit resets with
// "Use a reset" behind a confirm, and a failed run on a usage limit says when it lifts. All mocked; nothing is sent.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const t=Date.now()/1000|0;
let status={model:'openai/example-model',provider:'openai',chatgpt:true,plan:'pro',limit_reached:true,limit_resets_at:t+2*3600+14*60,
 windows:[{label:'5h',used:100,reset:t+2*3600+14*60+20},{label:'Week',used:40,reset:t+3*86400+3700}],
 resets:{available:2,credits:[{title:'Full reset',expires:t+20*86400}],allowed:false},can_reset:true,usage_page:'https://chatgpt.com/codex/cloud/settings/usage',checked_at:new Date().toISOString()};
const posts=[];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],
   review_runs:[{run_id:'r1',status:'error',error:'rate_limited',error_phase:'provider',started_at:new Date().toISOString(),attempts:1}],
   model_usage:[{provider:'openai',name:'OpenAI',windows:status?.windows||[]}],model_status:status}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',c=>body+=c);req.on('end',()=>{const {action,args}=JSON.parse(body);posts.push({action,args});
   if(action==='model_reset'){status={...status,limit_reached:false,limit_resets_at:null,windows:status.windows.map(w=>({...w,used:0})),resets:{...status.resets,available:1},can_reset:true};
     res.end(JSON.stringify({outcome:'reset',model_status:status}));return}
   res.end(JSON.stringify({model_status:status}))});return}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="model"]').click();
 await page.locator('#model-panel strong').first().waitFor();
 const panel=await page.locator('#model-panel').textContent();
 assert.match(panel,/^Example Model/);  // the name as people say it; the id on hover
 assert.equal(await page.locator('#model-panel strong').first().getAttribute('title'),'openai/example-model');
 assert.match(panel,/Signed in with ChatGPT · plan: pro/);
 assert.match(panel,/5-hour limit: 100% used · resets in 2h 14m/);
 assert.match(panel,/Weekly limit: 40% used · resets in 3d 1h/);
 assert.match(panel,/ChatGPT limit reached, resets at/);
 assert.match(panel,/Resets available: 2/);
 assert.equal(await page.locator('#model-panel progress').count(),2);
 assert.ok(await page.evaluate(()=>document.querySelector('#model-panel').getBoundingClientRect().right<=innerWidth));
 // Cancelling the confirm sends nothing.
 let asked='';page.once('dialog',d=>{asked=d.message();d.dismiss()});
 await page.locator('#model-panel [data-model-reset]').first().click();
 assert.match(asked,/This uses 1 of your 2 resets/);
 assert.equal(posts.filter(p=>p.action==='model_reset').length,0);
 // Accepting it uses one, with a key so a repeat is the same request.
 page.once('dialog',d=>d.accept());
 await page.locator('#model-panel [data-model-reset]').first().click();
 await page.locator('#notice',{hasText:'Usage reset'}).waitFor();
 const reset=posts.find(p=>p.action==='model_reset');assert.match(reset.args.key,/^[0-9a-f-]{36}$/);
 assert.match(await page.locator('#model-panel').textContent(),/Resets available: 1/);
 assert.doesNotMatch(await page.locator('#model-panel').textContent(),/limit reached/);
 // The reading updates by itself (the server checks every few minutes); when, sits in the model row, not under the panel.
 assert.equal(await page.locator('[data-model-refresh]').count(),0);
 assert.match(await page.locator('#model-panel .settings-list').first().textContent(),/Updated /);
 assert.doesNotMatch(await page.locator('#model-panel').textContent(),/Checked /);
 // No reset possible from here: the way to ChatGPT instead.
 status={...status,limit_reached:true,limit_resets_at:t+3600,can_reset:false,resets:null};
 await page.evaluate(()=>refresh());
 await page.locator('#model-panel a',{hasText:'See resets in ChatGPT'}).first().waitFor();
 // A link that looks like a button: no underline, its text centred in it, as tall as a button.
 {const a=await page.locator('#model-panel a',{hasText:'See resets in ChatGPT'}).first().evaluate(el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect(),t=document.createRange();t.selectNodeContents(el);const q=t.getBoundingClientRect();
  return {line:s.textDecorationLine,h:r.height,dx:Math.abs((q.left-r.left)-(r.right-q.right)),dy:Math.abs((q.top-r.top)-(r.bottom-q.bottom))}});
  assert.equal(a.line,'none');assert.ok(a.h>=30&&a.dx<=2&&a.dy<=2,JSON.stringify(a))}
 assert.equal(await page.evaluate(()=>[modelName('acme/lm-2.5-swift'),modelName('example-model'),modelName('')].join('|')),'LM-2.5 Swift|Example Model|');
 assert.equal(await page.locator('#model-panel a').first().getAttribute('href'),'https://chatgpt.com/codex/cloud/settings/usage');
 assert.doesNotMatch(await page.locator('#model-panel').textContent(),/unknown|cannot read/);
 assert.match(await page.locator('#model-panel').textContent(),/Usage limit resetsChatGPT shows them\./);
 status={...status,plan:'team'};await page.evaluate(()=>refresh());
 await page.locator('#model-panel',{hasText:'ChatGPT shows them under your team workspace.'}).waitFor();
 // Where a run failed on the limit, Connections says so and offers the same way out.
 await page.evaluate(()=>{for(const x of document.querySelectorAll('main>section'))x.hidden=x.id!=='health';document.querySelector('#review-run-history').open=true});
 const runs=await page.locator('#review-runs').textContent();
 assert.match(runs,/ChatGPT limit reached, resets at/);
 assert.equal(await page.locator('#review-runs a',{hasText:'See resets in ChatGPT'}).count(),1);
 // A Claude plan through Claude Code says so, and offers no ChatGPT reset.
 status={model:'anthropic/claude-sonnet-9-1',provider:'anthropic',chatgpt:false,claude_plan:true,windows:[],resets:null,can_reset:false,usage_page:null,checked_at:new Date().toISOString()};
 await page.evaluate(()=>refresh());
 await page.evaluate(()=>{for(const x of document.querySelectorAll('main>section'))x.hidden=x.id!=='settings'});
 await page.locator('#model-panel',{hasText:'Uses your Claude plan'}).waitFor();
 assert.doesNotMatch(await page.locator('#model-panel').textContent(),/ChatGPT/);
 // Nothing known: no Model section at all.
 status=null;await page.evaluate(()=>refresh());
 await page.evaluate(()=>{for(const x of document.querySelectorAll('main>section'))x.hidden=x.id!=='settings'});
 assert.equal(await page.locator('#settings-model').isHidden(),true);
 assert.deepEqual(errors,[]);
 console.log('model dashboard ok');
}finally{await browser?.close();server.close()}
