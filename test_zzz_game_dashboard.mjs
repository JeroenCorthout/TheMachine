import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Catch the Zzz with a mouse: a Z catches on pointerdown (no click needed) anywhere in an invisible area of at least
// 44px around it, best follows the score during a round and is kept when the dialog closes early. On a phone the Z
// keeps its own box (the browser's touch targeting already helps a finger), and the rise follows the board height.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const state={suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}};
const server=createServer((req,res)=>{const url=new URL(req.url,'http://fixture.invalid');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 res.setHeader('Content-Type','application/json');if(url.pathname==='/api/status'){res.end(JSON.stringify(state));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
// Start a round and hold the next Z still, so the test aims at a known spot.
const heldZ=async page=>{await page.waitForFunction(()=>document.querySelector('#game-field .zz:not(.got)'));
 return page.evaluate(()=>{const z=document.querySelector('#game-field .zz:not(.got)');z.id='held';z.classList.remove('gold');z.style.animationPlayState='paused';
  const r=z.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,h:r.height}})};
const score=page=>page.evaluate(()=>+document.querySelector('#game-score').textContent);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 {const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.evaluate(()=>{localStorage.setItem('zzz-best','1');startGame()});
  const z=await heldZ(page);
  // The hit area reaches past the glyph's own box, at least 44px across
  const area=await page.evaluate(({x,y})=>{const at=(dx,dy)=>document.elementFromPoint(x+dx,y+dy)?.id==='held';let r=0;while(r<80&&at(r,0)&&at(-r,0)&&at(0,r)&&at(0,-r))r++;return 2*r},z);
  assert.ok(area>=44,`hit area is ${area}px`);assert.ok(area>z.w+10,'hit area is wider than the glyph box');
  assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('#held')).cursor),'pointer');
  // Pointerdown alone counts, just outside the glyph box; no pointerup or click needed
  await page.mouse.move(z.x+z.w/2+8,z.y);await page.mouse.down();
  assert.equal(await score(page),1);assert.ok(await page.evaluate(()=>document.querySelector('#held').classList.contains('got')));
  assert.equal(await page.locator('#game-field .zz-plus').count(),1,'a +1 shows where the Z was caught');
  await page.mouse.up();
  // Best follows the score live once it passes the old best
  const z2=await heldZ(page);await page.mouse.move(z2.x,z2.y);await page.mouse.down();await page.mouse.up();
  assert.equal(await score(page),2);assert.equal(await page.locator('#game-best').textContent(),'2');
  // Far from any Z is a miss
  await page.evaluate(()=>document.querySelectorAll('#game-field .zz').forEach(z=>z.remove()));
  const f=await page.locator('#game-field').boundingBox();await page.mouse.click(f.x+f.width/2,f.y+20);assert.equal(await score(page),2);
  // Closing mid-round keeps the best
  await page.evaluate(()=>document.querySelector('#game-dialog').close());
  await page.waitForFunction(()=>localStorage.getItem('zzz-best')==='2');
  assert.equal(await page.evaluate(()=>document.querySelector('#game-field').style.getPropertyValue('--rise')),'270px');
  await context.close()}
 {const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.evaluate(()=>startGame());
  const z=await heldZ(page);
  // A phone keeps the glyph's own box and no +1
  assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('#held'),'::before').content),'none');
  await page.touchscreen.tap(z.x,z.y);assert.equal(await score(page),1);
  assert.equal(await page.locator('#game-field .zz-plus').count(),0);
  assert.equal(await page.evaluate(()=>document.querySelector('#game-field').style.getPropertyValue('--rise')),'270px');
  await context.close()}
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('zzz game dashboard ok');
