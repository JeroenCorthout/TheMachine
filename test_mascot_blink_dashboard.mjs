import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The mascot next to "The Machine" blinks now and then: its closed-eyes frame for about 150ms every 4 to 9 seconds,
// sometimes twice. On a computer the sidebar mascot and the one over the cards each keep their own timer and never blink
// together; on a phone the one in the slim top bar blinks (the closed drawer's doesn't). No blinking with reduced motion,
// in a hidden tab, or while the mascot is busy refreshing; the size never changes.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const brand={'/brand/avatar.png':'brand/app-icon-192.png','/brand/avatar-blink.png':'brand/avatar-blink.png'};
const state={suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}};
const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://fixture.invalid');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(brand[url.pathname]){res.setHeader('Content-Type','image/png');res.end(await readFile(new URL('./'+brand[url.pathname],import.meta.url)));return}
 res.setHeader('Content-Type','application/json');
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;const T0=Date.UTC(2026,9,23,9);
const open=async(opts={})=>{const phone=opts.phone;const context=await browser.newContext({viewport:phone?{width:390,height:844}:{width:1440,height:900},isMobile:!!phone,hasTouch:!!phone,reducedMotion:opts.still?'reduce':'no-preference'});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 // The clock is paused from the start: only run() moves time, so a busy computer never stretches a blink or starts one unseen
 await page.clock.install({time:T0});await page.clock.pauseAt(T0+1000);await page.goto(base);await page.locator(phone?'#topbar img':'#page-head .page-avatar').waitFor({state:phone?'visible':'attached'});
 // Log every frame swap with the (fake) time, and the mascot's size at that moment
 await page.evaluate(()=>{window.swaps=[];new MutationObserver(ms=>{for(const m of ms){const r=m.target.getBoundingClientRect();
  const t=m.target;swaps.push({who:t.classList.contains('page-avatar')?'page':t.closest('#topbar')?'top':'side',shut:m.target.getAttribute('src').includes('blink'),at:performance.now(),w:r.width,h:r.height})}})
  .observe(document.body,{attributes:true,attributeFilter:['src'],subtree:true})});
 return {context,page}};
const run=async(page,ms)=>{for(let t=0;t<ms;t+=500)await page.clock.runFor(500);return page.evaluate(()=>swaps.splice(0))};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const {context,page}=await open();
  const swaps=await run(page,60000);
  for(const who of ['side','page']){const mine=swaps.filter(s=>s.who===who);if(mine.length%2)mine.pop();  // one may still be closing at the end
   assert.ok(mine.filter(s=>s.shut).length>=4,`the ${who} mascot blinks a few times a minute (${mine.filter(s=>s.shut).length})`);
   for(let i=0;i<mine.length;i+=2){assert.ok(mine[i].shut&&!mine[i+1].shut,'shut, then open again');
    const closed=mine[i+1].at-mine[i].at;assert.ok(closed>=140&&closed<=170,'eyes closed for about 150ms: '+closed)}
   const sizes=new Set(mine.map(s=>s.w+'x'+s.h));assert.equal(sizes.size,1,'no layout shift: '+[...sizes])}
  let s0=0;for(;s0<1000&&!await page.evaluate(()=>document.querySelector('.page-avatar').getAttribute('src').includes('blink'));s0++)await page.clock.runFor(20);
  assert.ok(s0<1000,'the mascot over the cards blinks');
  // A refresh mid-blink keeps the mascot over the cards (rebuilding it would cut the blink short and leave its timer behind)
  assert.ok(await page.evaluate(()=>{const a=document.querySelector('.page-avatar');pageHead();return document.querySelector('.page-avatar')===a}),'the head is not rebuilt mid-blink');
  await run(page,500);
  // Never two at once: at most one mascot has its eyes closed at any time
  let shut=0;for(const s of swaps){shut+=s.shut?1:-1;assert.ok(shut<=1,'two mascots blinked together')}
  // Busy refreshing: no blink
  await page.evaluate(()=>document.querySelector('.avatar').classList.add('busy'));await run(page,500);
  assert.equal((await run(page,30000)).filter(s=>s.shut&&s.who==='side').length,0,'no blinking while busy refreshing');
  await page.evaluate(()=>document.querySelectorAll('.busy').forEach(e=>e.classList.remove('busy')));
  // A hidden tab: no blinking, and back to blinking once it shows again
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
  assert.equal((await run(page,30000)).filter(s=>s.shut).length,0,'no blinking in a hidden tab');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))});
  assert.ok((await run(page,20000)).some(s=>s.shut),'blinks again once the tab shows');
  await context.close()}
 // A phone: the mascot in the slim top bar blinks; the one in the closed drawer doesn't
 {const {context,page}=await open({phone:true});
  const swaps=await run(page,60000);
  assert.ok(swaps.filter(s=>s.who==='top'&&s.shut).length>=4,'the top bar mascot blinks');
  assert.equal(swaps.filter(s=>s.who==='side').length,0,'not the one in the closed drawer');
  assert.equal(new Set(swaps.filter(s=>s.who==='top').map(s=>s.w+'x'+s.h)).size,1,'no layout shift');
  await context.close()}
 // Reduced motion: never blinks
 {const {context,page}=await open({still:true});
  assert.equal((await run(page,60000)).filter(s=>s.shut).length,0,'no blinking with reduced motion');
  assert.equal(await page.locator('.avatar').getAttribute('src'),'brand/avatar.png');
  await context.close()}
 assert.deepEqual(errors,[]);
 console.log('mascot blink dashboard ok');
}finally{await browser?.close();server.close()}
