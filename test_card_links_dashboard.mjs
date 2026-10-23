import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Card text shows [words](https://...) as a link on the words and makes bare web addresses clickable (a long one by
// its domain), next to the highlighted **values**: in the body, the Needs you line, the Asked note, a blocker and
// its details, the result and a teammate's answer. Only http(s) and mailto become links; the rest stays inert text.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const now=new Date().toISOString(),doc='https://docs.google.com/document/d/abc123/edit';
const cards=[
 {id:'l1',title:'Agreed to the listing exchange',body:`Updated your [Google Doc](${doc}): removed Sources and kept **3 listings**. See https://example.com/a/very/long/path/to/the/listing/page?ref=card. Ask [Sam](mailto:sam@example.com).`,
  status:'prepared',scope:'work',created_at:now,event_ids:[1],outcome:JSON.stringify({state:'needs_you',needs:'Check the [listing page](https://example.com/listing).'}),
  latest_feedback:{choice:'other',feedback:'Use https://example.org/x instead',created_at:now}},
 {id:'x1',title:'Look at the odd links',body:'Click [x](javascript:alert(1)) or [y](data:text/html,<script>alert(2)</script>) or <img src=x onerror=alert(3)> or [z](https://example.com/q"onmouseover="alert`4`).',
  status:'prepared',scope:'work',created_at:now,event_ids:[2]},
 {id:'b1',title:'Book the venue',body:'Tried to book it.',status:'accepted',scope:'work',created_at:now,event_ids:[3],preparation_status:'blocked',
  preparation_error:'browser_failed',preparation_reason:'The [booking page](https://example.com/book) did not load.',preparation_details:'GET https://example.com/book failed'}];
const state=()=>({suggestions:cards,promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],review_live:{runs:[]},connectors:[],fleet:{on:false,fleets:[]}});
const server=createServer(async(req,res)=>{for await(const _ of req);const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 res.statusCode=url.pathname.startsWith('/brand/')?404:200;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[],dialogs=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>{dialogs.push(d.message());d.dismiss()});
 await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#task-l1').waitFor();
 const anchors=sel=>page.locator(sel+' a:not(.card-link)').evaluateAll(n=>n.map(a=>[a.textContent,a.getAttribute('href'),a.target,a.rel]));
 const ext='noopener noreferrer';
 // Body: the words link, a long bare address shows its domain, mailto works, and the highlight stays
 assert.deepEqual(await anchors('#task-l1 .body'),[['Google Doc',doc,'_blank',ext],['example.com','https://example.com/a/very/long/path/to/the/listing/page?ref=card','_blank',ext],['Sam','mailto:sam@example.com','_blank',ext]]);
 const body=await page.locator('#task-l1 .body').innerText();
 assert.ok(!body.includes('](')&&!body.includes('[Google'),body);
 assert.ok(body.includes('See example.com. Ask Sam.'),body);
 assert.equal(await page.locator('#task-l1 .body strong').textContent(),'3 listings');
 // Needs you line and the Asked note
 assert.deepEqual(await anchors('#task-l1 .status-line'),[['listing page','https://example.com/listing','_blank',ext]]);
 assert.equal(await page.locator('#task-l1 .status-line').innerText(),'Needs you: Check the listing page.');
 assert.deepEqual(await anchors('#task-l1 .saved-feedback blockquote'),[['https://example.org/x','https://example.org/x','_blank',ext]]);
 // A blocker and its details
 assert.deepEqual(await anchors('#task-b1 p.note'),[['booking page','https://example.com/book','_blank',ext]]);
 await page.locator('#task-b1 details.tech summary').click();
 assert.deepEqual(await anchors('#task-b1 details.tech pre'),[['https://example.com/book','https://example.com/book','_blank',ext]]);
 // Unsafe links stay inert text, no HTML gets in, and a quote never breaks out of href
 const odd=page.locator('#task-x1 .body');
 assert.equal(await odd.locator('img,script').count(),0);
 const hrefs=await odd.locator('a').evaluateAll(n=>n.map(a=>[a.getAttribute('href'),[...a.attributes].map(x=>x.name).sort().join(',')]));
 assert.deepEqual(hrefs,[["https://example.com/q\"onmouseover=\"alert`4`","href,rel,target"]]);
 const oddText=await odd.innerText();
 for(const kept of ['[x](javascript:alert(1))','[y](data:text/html,<script>alert(2)</script>)','<img src=x onerror=alert(3)>'])assert.ok(oddText.includes(kept),kept);
 await odd.locator('a').hover();
 // The result dialog renders links too
 await page.locator('#task-l1 [data-result="read"]').click();await page.locator('#result-dialog[open]').waitFor();
 assert.equal(await page.locator('#result-content a[href="'+doc+'"]').textContent(),'Google Doc');
 assert.deepEqual(dialogs,[]);
 await context.close();
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('card links dashboard ok');
