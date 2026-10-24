import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A browser source that is signed out says so on Connections (counted in its badge) and at the top, with a link to
// sign in again on a hosted install or what to do without one; other errors read as words, not codes.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const ok=new Date().toISOString(),later=new Date(Date.now()+6e5).toISOString();
const linkedin={id:'work-linkedin',service:'linkedin',boundary:'work',display_name:'Work LinkedIn',error:'linkedin_signed_out',error_at:ok,retry_at:later,
 signed_out:true,sign_in_hint:'the browser it reads with is not signed in to LinkedIn; sign in there, then connect again'};
const x={id:'work-x',service:'x',boundary:'work',display_name:'Work X',error:'x_browser_busy',retry_at:later,last_success:ok};
const xli={id:'own-linkedin',service:'linkedin',boundary:'personal',display_name:'Own LinkedIn',error:'linkedin_browser_unreachable',retry_at:later,last_success:ok};
const gateway={id:'openclaw-gateway',service:'gateway',boundary:'owner:system',display_name:'OpenClaw Gateway',last_success:ok};
const crm={id:'crm',service:'salesflare',boundary:'work',display_name:'CRM',error:'salesflare_read_failed',retry_at:later,last_success:ok};
let hosted=true;
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],
   manage_url:hosted?'https://app.example.test/setup':null,
   connectors:[{...linkedin,sign_in_url:hosted?'https://app.example.test/open?to=browser&site=linkedin':null},x,xli,crm,gateway]}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#needs-you li',{hasText:'Signed out of LinkedIn'}).waitFor();
 const top=page.locator('#needs-you a',{hasText:'Sign in again'});
 assert.equal(await top.getAttribute('href'),'https://app.example.test/open?to=browser&site=linkedin');
 assert.equal(await top.getAttribute('target'),'_blank');
 assert.match(await page.locator('#badge-health').textContent(),/4/);
 await page.locator('nav [data-tab="health"]').click();
 const card=page.locator('#connectors article',{hasText:'Work LinkedIn'});
 assert.match(await page.locator('#connectors article',{hasText:'OpenClaw Gateway'}).textContent(),/notices when it has trouble itself/);
 assert.match(await card.locator('.connection-status').textContent(),/Signed out of LinkedIn/);
 assert.equal(await card.locator('.alert a').getAttribute('href'),'https://app.example.test/open?to=browser&site=linkedin');
 // The alerts are in words (the raw code stays in Details, for whoever debugs).
 const text=(await page.locator('#connectors .alert').allTextContents()).join(' ');
 assert.ok(text.includes('The browser was busy with another read.'),text);
 assert.ok(text.includes('Couldn’t read Salesflare CRM.'),text);
 // A crashed or hung browser says so, not a vague failure or a sign-in.
 assert.ok(text.includes('The browser stopped answering; it restarts by itself.'),text);
 assert.ok(!/x_browser_busy|salesflare_read_failed|linkedin_signed_out|browser_unreachable/.test(text),text);
 // Self-hosted: no link, the plain fix instead.
 hosted=false;await page.reload();await page.locator('nav [data-tab="health"]').click();
 const self=page.locator('#connectors article',{hasText:'Work LinkedIn'}).locator('.alert');
 await self.waitFor();
 assert.equal(await self.locator('a').count(),0);
 assert.match(await self.textContent(),/Signed out of LinkedIn\. The browser it reads with is not signed in to LinkedIn/);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({page_errors:errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
