import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Opening the app: until the first real data, a calm outline of the cards (never "All clear", also when the Graph's
// counts come in first); "All clear" only when the Machine says there is nothing. The app's page (sw.js) and the last
// cards, the open card's chat and the Graph's lists stay on the device so the app opens with them at once: encrypted
// (AES-GCM, a non-extractable key in IndexedDB), only for the owner the page was served for, without tokens, and wiped
// on Sign out, when the session is gone and for another owner. A copy shown while the Machine can't be reached says so
// ("Offline · updated 10:42"). The page copy never keeps the CSRF token: a change waits for the status, which has it.
const raw=await readFile(new URL('./dashboard.html',import.meta.url),'utf8'),sw=await readFile(new URL('./sw.js',import.meta.url),'utf8');
const CSRF='c'.repeat(64),S1='a1'.repeat(16),S2='b2'.repeat(16),now=new Date().toISOString();
let scope=S1,mode='ok',cards=null,holdStatus=null;
const card=(n,title)=>({id:String(n).repeat(16),title,body:'Robin asked for the numbers.',status:'pending',scope:'work',created_at:now,event_ids:[n]});
const full=[card(1,'Reply to Robin about the plan'),card(2,'Book the venue')];
const page=()=>raw.replace('__CSRF__',CSRF).replace('__UI_VERSION__','v1').replace('__BASE__','/').replace('__CACHE_SCOPE__',scope);
const status=()=>({suggestions:cards??full,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},
 connectors:[],fleet:{on:false},ui_version:'v1',csrf:CSRF,agents_settings:{session_token:'tok-never-kept',device:'Desk'}});
const hits={page:0,notModified:0,status:0,session:0},posts=[];let graphDown=false;
const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
 if(url.pathname==='/'){const html=page(),etag='"'+html.length+scope+'"';if(req.headers['if-none-match']===etag){hits.notModified++;res.writeHead(304,{ETag:etag});res.end();return}
  hits.page++;res.setHeader('Content-Type','text/html');res.setHeader('ETag',etag);res.end(html);return}
 if(url.pathname==='/sw.js'){res.setHeader('Content-Type','text/javascript');res.end(sw);return}
 if(url.pathname==='/bye'){res.setHeader('Content-Type','text/html');res.end('<p>Signed out</p>');return}
 if(url.pathname==='/_machine/logout'){posts.push('logout');res.writeHead(303,{Location:'/bye'});res.end();return}
 if(url.pathname==='/api/session'){hits.session++;if(mode==='down'){res.statusCode=502;res.end('{}');return}
  if(mode==='gone'){res.writeHead(302,{Location:'http://signin.invalid/open'});res.end();return}
  res.end(JSON.stringify({csrf:CSRF,cache_scope:scope}));return}
 if(url.pathname==='/api/status'){hits.status++;if(holdStatus)await holdStatus;
  if(mode==='down'){res.statusCode=502;res.end('{"error":"down"}');return}
  if(mode==='gone'){res.writeHead(302,{Location:'http://signin.invalid/open'});res.end();return}
  res.end(JSON.stringify(status()));return}
 if(url.pathname==='/api/graph'){if(graphDown){res.statusCode=502;res.end('{}');return}
  res.end(JSON.stringify({counts:{person:2,org:0,project:0},list_counts:{people:2,quiet:0,new:0,orgs:0,warmup:0,projects:0,projects_done:0},whos_who:[],waiting:0}));return}
 if(url.pathname==='/api/graph/list'){if(graphDown){res.statusCode=502;res.end('{}');return}
  res.end(JSON.stringify({items:url.searchParams.get('list')==='people'?[{id:'person:1',kind:'person',name:'Robin Example',strength:2,last_at:now,signals:4,ids:[]}]:[],next:null,total:1}));return}
 if(url.pathname==='/api/action'){const r=JSON.parse(body||'{}');posts.push({action:r.action,csrf:req.headers['x-csrf-token']});
  if(r.action==='card_chat'){if(mode==='down'){res.statusCode=502;res.end('{}');return}res.end(JSON.stringify({session:true,status:'ok',active:false,cursor:'k1',items:[{kind:'reply',text:'Drafted the numbers for Robin.',at:Date.now()}]}));return}
  res.end('{}');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const errors=[];let browser;
// What the device keeps, read the way the page itself can: the raw rows, and the plain text with the page's own key
const kept=p=>p.evaluate(async()=>{const dbs=await indexedDB.databases();if(!dbs.some(d=>d.name==='tm-cache'))return null;
 const db=await new Promise(r=>{const q=indexedDB.open('tm-cache');q.onsuccess=()=>r(q.result)});const get=(s,k)=>new Promise(r=>{const q=db.transaction(s).objectStore(s).get(k);q.onsuccess=()=>r(q.result)});
 const keys=await new Promise(r=>{const q=db.transaction('data').objectStore('data').getAllKeys();q.onsuccess=()=>r(q.result)});const meta=await get('meta','key'),out={scope:meta?.scope,extractable:meta?.key?.extractable,alg:meta?.key?.algorithm?.name,rows:{}};
 for(const k of keys){const row=await get('data',k);const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:row.iv,additionalData:new TextEncoder().encode(meta.scope+'\n'+k)},meta.key,row.ct).then(b=>new TextDecoder().decode(b),()=>null);
  out.rows[k]={rawHasText:new TextDecoder('latin1').decode(row.ct).includes('Robin'),isBuffer:row.ct instanceof ArrayBuffer,plain}}
 db.close();return out});
const shellCopy=p=>p.evaluate(async()=>{const c=await caches.open('tm-shell-1');const r=await c.match(location.origin+'/');return r?await r.text():null});
const until=async(p,fn,arg,label)=>{try{await p.waitForFunction(fn,arg,{timeout:15000})}catch{throw Error('timed out: '+label)}};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1100,height:900}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 // Every text the page shows before its first card, and whether the loading outline was there from the start
 await context.addInitScript(()=>{window.__before=[];window.__outline=null;const look=()=>{const s=document.querySelector('#suggestions');if(window.__outline===null&&s)window.__outline=!!s.querySelector('.skeleton');
  if(s?.querySelector('article[id]'))return;const t=document.querySelector('main')?.innerText||'';if(/All clear/.test(t))window.__before.push(t.slice(0,120))};
  new MutationObserver(look).observe(document,{subtree:true,childList:true,characterData:true,attributes:true})});
 const tab=await context.newPage();tab.on('pageerror',e=>errors.push(e.message));

 // 1. A slow first status, with the Graph's counts in before it (what used to put "All clear" under the tagline)
 let release;
 holdStatus=new Promise(r=>release=r);await tab.goto(base);
 await until(tab,()=>document.querySelector('#count-people')?.textContent,null,'graph counts before the status');
 assert.ok(await tab.locator('#suggestions .skeleton').isVisible(),'the loading outline shows while the first status loads');
 assert.equal(await tab.locator('#suggestions').getAttribute('aria-busy'),'true');
 assert.ok(!/All clear/.test(await tab.locator('main').innerText()));
 release();holdStatus=null;
 await tab.locator('#suggestions article[id]').first().waitFor();
 assert.equal(await tab.evaluate(()=>__outline),true,'the outline is in the page from its first paint');
 assert.deepEqual(await tab.evaluate(()=>__before),[],'never "All clear" before the first data');
 assert.equal(await tab.locator('#suggestions').getAttribute('aria-busy'),null);
 assert.match(await tab.locator('#page-head').innerText(),/2 to decide/);

 // 2. Kept on the device: encrypted, a non-extractable AES-GCM key, for this owner, no tokens
 await until(tab,async()=>(await indexedDB.databases()).some(d=>d.name==='tm-cache'),null,'the device cache');
 let k;for(let i=0;i<50&&!(k=await kept(tab))?.rows.status;i++)await tab.waitForTimeout(100);
 assert.ok(k?.rows.status,'the cards are kept');assert.equal(k.scope,S1);assert.equal(k.extractable,false);assert.equal(k.alg,'AES-GCM');
 assert.equal(k.rows.status.isBuffer,true);assert.equal(k.rows.status.rawHasText,false,'stored encrypted, not as text');
 const plain=JSON.parse(k.rows.status.plain);assert.equal(plain.suggestions.length,2);assert.equal(plain.csrf,undefined,'never the CSRF token');
 assert.equal(plain.agents_settings.session_token,undefined,'never a token');assert.equal(plain.agents_settings.device,'Desk');
 // The service worker keeps the page, never its CSRF token
 await tab.evaluate(()=>navigator.serviceWorker.ready);await tab.reload();await tab.locator('#suggestions article[id]').first().waitFor();
 await until(tab,()=>!!navigator.serviceWorker.controller,null,'the service worker controls the page');
 let shell;for(let i=0;i<50&&!(shell=await shellCopy(tab));i++)await tab.waitForTimeout(100);
 assert.ok(shell,'the page is kept');assert.ok(!shell.includes(CSRF),'the kept page has no CSRF token');assert.ok(shell.includes("const csrf=''"));

 // 3. The Machine can't be reached: the app still opens, from the device, with the cards and when they were read
 mode='down';const pages=hits.page;await tab.reload();
 await tab.locator('#suggestions article[id]',{hasText:'Reply to Robin about the plan'}).waitFor();
 assert.equal(hits.page,pages,'the page came from the device');
 await until(tab,()=>/^Offline · updated \d\d:\d\d$/.test(document.querySelector('#updated').textContent),null,'the offline line');
 assert.ok(await tab.locator('#live.stale').count());
 assert.deepEqual(await tab.evaluate(()=>__before),[]);

 // 4. A change made on the page from the device waits for api/session, then carries the CSRF token
 assert.equal(await tab.evaluate(()=>csrf),'','the page copy has no token of its own');
 const sent=tab.evaluate(()=>request('api/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({action:'ping',args:{}})}));
 await tab.waitForTimeout(300);assert.ok(!posts.some(p=>p.action==='ping'),'nothing is sent without the token');
 mode='ok';await tab.evaluate(()=>refresh());await sent;
 assert.equal(posts.find(p=>p.action==='ping').csrf,CSRF);
 await until(tab,()=>/^Updated/.test(document.querySelector('#updated').textContent),null,'live again');

 // 5. The open card's chat and the Graph's lists open from the device too
 await tab.goto(base+'#chat='+'1'.repeat(16));await tab.locator('#chat-log',{hasText:'Drafted the numbers for Robin.'}).waitFor();
 await tab.goto(base+'#graph');await tab.locator('#people-view',{hasText:'Robin Example'}).waitFor();
 for(let i=0;i<50&&!((k=await kept(tab))?.rows['chat:'+'1'.repeat(16)]&&k.rows['get:api/graph']);i++)await tab.waitForTimeout(100);
 mode='down';graphDown=true;
 await tab.goto(base+'#chat='+'1'.repeat(16));await tab.reload();await tab.locator('#chat-log',{hasText:'Drafted the numbers for Robin.'}).waitFor();
 await tab.goto(base+'#graph');await tab.reload();await tab.locator('#people-view',{hasText:'Robin Example'}).waitFor();
 assert.match(await tab.locator('#updated').textContent(),/^Offline · updated \d\d:\d\d$/);
 mode='ok';graphDown=false;

 // 6. Kept cards with nothing open: still the outline until the Machine itself says "All clear"
 cards=[];await tab.goto(base);await tab.locator('#suggestions .all-clear',{hasText:'All clear'}).waitFor();
 for(let i=0;i<50&&JSON.parse((await kept(tab))?.rows.status?.plain||'{}').suggestions?.length!==0;i++)await tab.waitForTimeout(100);
 mode='down';await tab.reload();await tab.waitForTimeout(1500);
 assert.ok(await tab.locator('#suggestions .skeleton').isVisible(),'a kept "nothing" shows the outline');
 assert.deepEqual(await tab.evaluate(()=>__before),[],'a kept "nothing" is not "All clear"');
 mode='ok';cards=null;await tab.evaluate(()=>refresh());await tab.locator('#suggestions article[id]').first().waitFor();

 // 7. Sign out wipes the device before it goes
 await tab.evaluate(()=>{$('#sign-out').hidden=false;$('#settings').hidden=false});  // Settings' Sign out (shown on a hosted Machine)
 await Promise.all([tab.waitForURL(base+'bye'),tab.click('#sign-out button')]);
 assert.ok(posts.includes('logout'));
 assert.equal(await kept(tab),null,'no data left after Sign out');
 assert.deepEqual(await tab.evaluate(async()=>(await caches.keys()).filter(k=>k.startsWith('tm-'))),[],'no page copy left after Sign out');

 // 8. The session is gone (the sign-in gate redirects the status): wiped, and the gate gets the next load
 await tab.goto(base);await tab.locator('#suggestions article[id]').first().waitFor();
 for(let i=0;i<50&&!(await kept(tab))?.rows.status;i++)await tab.waitForTimeout(100);
 mode='gone';await tab.evaluate(()=>refresh());
 await until(tab,async()=>!(await indexedDB.databases()).some(d=>d.name==='tm-cache'),null,'wiped when the session is gone');
 mode='ok';await tab.goto(base);await tab.locator('#suggestions article[id]').first().waitFor();

 // 9. Another owner: what the device kept for the old one goes
 for(let i=0;i<50&&!(await kept(tab))?.rows.status;i++)await tab.waitForTimeout(100);
 scope=S2;await tab.reload();
 for(let i=0;i<100&&(k=await kept(tab).catch(()=>null))?.scope!==S2;i++)await tab.waitForTimeout(100);
 assert.equal(k?.scope,S2,'the device now keeps for the new owner only');
 assert.ok(!Object.values(k.rows).some(r=>r.plain===null),'nothing of the old owner is left');

 // 10. In a frame (the site's demo) nothing is kept
 await tab.goto(base+'bye');await tab.evaluate(async()=>{for(const k of await caches.keys())await caches.delete(k);await new Promise(r=>{const q=indexedDB.deleteDatabase('tm-cache');q.onsuccess=q.onerror=q.onblocked=r})});
 const framed=await browser.newContext({viewport:{width:900,height:700},serviceWorkers:'block'});const fp=await framed.newPage();
 await fp.goto(base+'bye');await fp.evaluate(u=>{const f=document.createElement('iframe');f.src=u;f.style.cssText='width:800px;height:600px';document.body.append(f)},base);
 await fp.frameLocator('iframe').locator('#suggestions article[id]').first().waitFor();await fp.waitForTimeout(800);
 assert.equal(await fp.evaluate(async()=>(await indexedDB.databases()).some(d=>d.name==='tm-cache')),false,'nothing kept in a frame');
 await framed.close();

 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({page_errors:errors,hits}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
