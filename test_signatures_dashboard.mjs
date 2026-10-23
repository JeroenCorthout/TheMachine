import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings, Signatures: one card per mailbox with a rich editor in a frame that runs nothing. With nothing kept yet,
// the mailbox's own signature is imported on load; Save shows only after a change; "Use mailbox signature" goes back
// to the imported one; the HTML toggle edits the source. All mocked.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const GMAIL='<div><b>Owner Name</b></div><div>Example Inc</div>';
let signatures=[{account_id:'work-outlook',email:'owner@example.com',service:'outlook',html:'<b>Owner Name</b>',source:'detected',saveable:true},
 {account_id:'work-gmail',email:'owner@example.org',service:'gmail',html:'',source:'',saveable:true}];
const posts=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},connectors:[],review_runs:[],signatures}));return}
 if(url.pathname==='/api/action'){let body='';for await(const c of req)body+=c;const {action,args}=JSON.parse(body);posts.push({action,args});
  if(action==='detect_signature'){const gmail=args.account_id==='work-gmail',html=gmail?GMAIL:'<p>Owner Name<br>Example Inc</p>';
   signatures=signatures.map(s=>s.account_id===args.account_id&&s.source!=='owner'?{...s,html,source:gmail?'imported':'detected'}:s);
   res.end(JSON.stringify({html,found:true,source:gmail?'imported':'detected',signatures}));return}
  if(action==='set_signature'){signatures=signatures.map(s=>s.account_id!==args.account_id?s:args.use_mailbox?{...s,html:GMAIL,source:'imported'}:{...s,html:args.html,source:'owner'});res.end(JSON.stringify(signatures));return}}
 res.statusCode=404;res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const scheme of ['light','dark']){
  const context=await browser.newContext({viewport:{width:390,height:844},colorScheme:scheme}),page=await context.newPage();
  const remote=[];
  await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.hostname==='127.0.0.1')return route.continue();remote.push(u.href);return route.abort()});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept(d.message().startsWith('Link')?'https://example.org':'javascript:alert(1)'));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="writing"]').click();
  const forms=page.locator('#signatures .sig-form');await forms.first().waitFor();
  assert.equal(await forms.count(),2);
  const outlook=forms.nth(0),gmail=forms.nth(1);
  assert.match(await outlook.locator('.sig-head').textContent(),/Outlook · Imported/);
  // Nothing kept for Gmail: its own signature is imported on load, without a click.
  await gmail.locator('.sig-head',{hasText:'Gmail · Imported'}).waitFor();
  assert.match(await gmail.locator('.sig-note').textContent(),/Imported from Gmail · Re-import/);
  const paper=gmail.locator('iframe.sig-paper');
  assert.equal(await paper.getAttribute('sandbox'),'allow-same-origin');
  const body=page.frameLocator('#signatures .sig-form >> nth=1 >> iframe.sig-paper').locator('body');
  await body.locator('b',{hasText:'Owner Name'}).waitFor();
  assert.equal(await gmail.locator('.sig-save').isHidden(),true);  // no changes, no Save
  // Typing in the editor shows Save; a script typed into the HTML runs nothing and nothing remote loads but images.
  await body.click();await page.keyboard.press('End');await page.keyboard.type(' Ltd');
  await gmail.locator('.sig-save').waitFor();
  await gmail.locator('[data-sig-tool="html"]').click();
  const src=gmail.locator('.sig-source');assert.match(await src.inputValue(),/Example Inc Ltd/);
  await src.fill('<b>Typed</b><img src="https://tracker.invalid/p.png" onerror="parent.hacked=1"><script>parent.hacked=1</script>');
  await gmail.locator('[data-sig-tool="html"]').click();
  await body.locator('b',{hasText:'Typed'}).waitFor();
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>window.hacked),undefined);
  assert.ok(remote.every(u=>u.startsWith('https://tracker.invalid/')),remote.join());
  // Bold and a link from the toolbar; a javascript: link is refused.
  await body.click();await page.keyboard.press('Control+A');
  await gmail.locator('[data-sig-tool="link"]').click();
  assert.match(await page.frameLocator('#signatures .sig-form >> nth=1 >> iframe.sig-paper').locator('body').innerHTML(),/href="https:\/\/example.org"/);
  await gmail.locator('.sig-save .primary').click();
  await page.locator('#notice',{hasText:'Signature saved.'}).waitFor({state:'attached'});
  const saved=posts.filter(p=>p.action==='set_signature').at(-1);
  assert.equal(saved.args.account_id,'work-gmail');assert.match(saved.args.html,/Typed/);assert.match(saved.args.html,/example\.org/);
  // Custom now; "Use mailbox signature" goes back to the imported one.
  const g2=page.locator('#signatures .sig-form[data-account="work-gmail"]');
  await g2.locator('.sig-head',{hasText:'Custom'}).waitFor();
  await g2.locator('[data-sig-mailbox]').click();
  await page.locator('#signatures .sig-form[data-account="work-gmail"] .sig-head',{hasText:'Imported'}).waitFor();
  assert.deepEqual(posts.filter(p=>p.action==='set_signature').at(-1).args,{account_id:'work-gmail',use_mailbox:true});
  // Re-import for Outlook reads the sent mail again.
  await page.locator('#signatures .sig-form[data-account="work-outlook"] [data-sig-import]').click();
  for(let i=0;i<100&&!posts.some(p=>p.action==='detect_signature'&&p.args.account_id==='work-outlook');i++)await page.waitForTimeout(20);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await context.close();
  signatures=signatures.map(s=>s.account_id==='work-gmail'?{...s,html:'',source:''}:s);
 }
 assert.deepEqual(errors,[]);
 console.log('signatures dashboard ok');
}finally{await browser?.close();server.close()}
