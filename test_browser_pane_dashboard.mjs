import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// On a computer (from 1024px) Browser shows the Machine's browser in the main area beside the sidebar, with Open in
// new tab and close. The Browser row is the active one; another row, back and close leave it, and the viewer stays
// loaded meanwhile. On a phone, or without a viewer on the dashboard's own address, it stays a new tab.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
let embed='/browser/';const loads=[];
const state=()=>({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]},
 browser_link:'https://cp.example/open?to=browser',browser_embed:embed});
const server=createServer(async(req,res)=>{for await(const _ of req);const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/browser/'){loads.push(url.pathname);res.setHeader('Content-Type','text/html');res.end('<body style="background:#123"><p id="viewer">viewer</p></body>');return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);const row=page.locator('#open-browser');await row.waitFor();
 await row.click();
 const pane=page.locator('#browser-pane');await pane.waitFor();
 assert.equal(await page.locator('#bp-frame').getAttribute('src'),'/browser/');
 await page.frameLocator('#bp-frame').locator('#viewer').waitFor();
 assert.equal(await row.getAttribute('aria-current'),'page');
 assert.equal(await page.locator('nav button.active').count(),0);
 assert.equal(await page.locator('#inbox').isHidden(),true);
 assert.equal(await page.locator('#bp-tab').getAttribute('href'),'https://cp.example/open?to=browser');
 assert.equal(await page.locator('#bp-tab').getAttribute('target'),'_blank');
 assert.ok(page.url().endsWith('#browser'));
 const box=await page.locator('#bp-frame').boundingBox(),side=await page.locator('#app-side').boundingBox();
 assert.ok(box.x>=side.x+side.width-1&&box.height>800,'fills the main area beside the sidebar');
 // Another row leaves it; the viewer is not loaded again when it comes back
 await page.locator('nav [data-tab="events"]').click();
 assert.equal(await pane.isHidden(),true);assert.equal(await page.locator('#events').isHidden(),false);
 assert.equal(await row.getAttribute('aria-current'),'false');assert.ok(!page.url().includes('#browser'));
 await row.click();await pane.waitFor();assert.deepEqual(loads,['/browser/']);
 // Back returns to the view before
 await page.goBack();await pane.waitFor({state:'hidden'});assert.equal(await page.locator('#events').isHidden(),false);
 // Close too
 await row.click();await pane.waitFor();await page.locator('#bp-close').click();await pane.waitFor({state:'hidden'});
 assert.equal(await page.locator('#events').isHidden(),false);assert.equal(await row.getAttribute('aria-current'),'false');
 // Leaving through another row spends no Back: the one before it leaves the dashboard
 const hist=await context.newPage();hist.on('pageerror',e=>errors.push(e.message));await hist.goto('about:blank');await hist.goto(base);await hist.locator('#open-browser').click();
 await hist.locator('#browser-pane').waitFor();await hist.locator('nav [data-tab="events"]').click();await hist.waitForFunction(()=>!location.hash);
 await hist.goBack();assert.equal(hist.url(),'about:blank');
 // Narrower than a computer while it is open: the view before comes back
 await hist.goto(base);await hist.locator('#open-browser').click();await hist.locator('#browser-pane').waitFor();
 await hist.setViewportSize({width:800,height:900});await hist.locator('#inbox').waitFor();  // not only hidden by the narrow layout
 assert.equal(await hist.locator('#browser-pane').getAttribute('hidden'),'');await hist.close();
 // A link straight to #browser opens it
 const deep=await context.newPage();deep.on('pageerror',e=>errors.push(e.message));await deep.goto(base+'/#browser');await deep.locator('#browser-pane').waitFor();await deep.close();
 // A phone keeps the new tab
 const phone=await browser.newContext({viewport:{width:390,height:800}});await phone.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const p2=await phone.newPage();p2.on('pageerror',e=>errors.push(e.message));await p2.goto(base);await p2.locator('#open-settings').waitFor();
 await p2.evaluate(()=>document.querySelector('#open-browser').click());await new Promise(r=>setTimeout(r,300));
 assert.equal(await p2.locator('#browser-pane').isHidden(),true);await phone.close();
 // No viewer on the dashboard's own address: a new tab, also on a computer
 embed=null;await page.reload();await row.waitFor();
 const [tab]=await Promise.all([context.waitForEvent('page'),row.click()]);await tab.close();
 assert.equal(await pane.isHidden(),true);
 await context.close();
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('browser pane dashboard ok');
