import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const sid='1234567890abcdef', revision='1234567890ab';
let kind='finish', finishStatus='needs_input', reply=null;
const receipt={name:'payment-receipt.pdf',size:21213,sha256:'c'.repeat(64),type:'application/pdf'};
const fullDiff='diff --git a/graph.py b/graph.py\n'+('context line\n'.repeat(6000))+'LAST REVIEWED CHANGE\n';
const action=()=>reply?{kind:'finish',state:{status:'needs_input'},label:'Confirm receipt reply',revision,instructions:'Open the conversation, type the message, press Send.',preview:'Reply in the support inbox with the payment receipt.',url:'https://inbox.example.invalid/conversation/7',to_name:'Sam Example',message:'Hi Sam,\n\nHere is the payment receipt.\n\nAlex',...(reply==='file'?{attachments:[receipt]}:{})}:
  kind==='finish'?{kind,state:{status:finishStatus},label:'Submit expense claim',revision,instructions:'Submit E42 for EUR 42.50 only.',preview:'Expense E42 · EUR 42.50 · 16 Oct 2026.',url:'https://expenses.example.invalid/claim/E42'}:
  {kind,label:'Merge fix',revision,summary:'Handle missing keys in the graph.',stat:'graph.py | 2 +-',diff:fullDiff.slice(0,60000)+'\n… preview shortened; download the complete diff.',diff_clipped:true,base:'a'.repeat(40),head:'b'.repeat(40),files:['graph.py'],plugin:false};
const state=()=>({suggestions:[{id:sid,title:'Offline review fixture',body:'Ready to review.',status:'prepared',event_ids:[1],next_action:action(),next_actions:[action()],outcome:{kind:'needs_you',needs:'Confirm'},scope:'work',created_at:new Date().toISOString()}],connectors:[],suggestion_counts:{prepared:1},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]});
const calls=[], errors=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/merge-diff'){assert.equal(url.searchParams.get('id'),sid);assert.equal(url.searchParams.get('revision'),revision);res.setHeader('Content-Type','text/plain');res.end(fullDiff);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(state()));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;calls.push(JSON.parse(body));res.end('{"status":"queued"}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const widths=[];
 for(kind of ['finish','merge']){
  await page.evaluate(()=>refresh());
  await page.evaluate(([id,revision])=>openSend(id,revision),[sid,revision]);
  await page.locator('#send-dialog').waitFor({state:'visible'});
  assert.ok((await page.locator('#send-preview').textContent()).includes(kind==='finish'?'EUR 42.50':'preview shortened'));
  assert.equal(await page.locator('#send-confirm').textContent(),kind==='finish'?'Confirm':'Merge now');
  if(kind==='merge'){const link=page.locator('#send-preview .full-diff');assert.equal(await link.textContent(),'Download complete diff');const url=await link.getAttribute('href');assert.equal(await page.evaluate(async u=>(await fetch(u)).text(),url),fullDiff);}
  for(const width of [320,390,768]){
   await page.setViewportSize({width,height:844});
   const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth,dialog:document.querySelector('#send-dialog').getBoundingClientRect().width}));
   assert.ok(sizes.page<=sizes.viewport,kind+' page overflow '+width);assert.ok(sizes.dialog<=sizes.viewport,kind+' dialog overflow '+width);
   widths.push({kind,width,...sizes});
  }
  await page.locator('#send-cancel').click();
 }
 await page.evaluate(([id,revision])=>openSend(id,revision),[sid,revision]);await page.locator('#send-confirm').click();
 for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
 assert.equal(calls.length,1);assert.equal(calls[0].action,'confirm_action');assert.equal(calls[0].args.revision,revision);
 // A step that posts a reply lists the files it uploads (each opens from the dialog), or says plainly it attaches none.
 for(reply of ['file','none']){
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>refresh());
  await page.evaluate(([id,revision])=>openSend(id,revision),[sid,revision]);await page.locator('#send-dialog').waitFor({state:'visible'});
  const head=await page.locator('#send-head').textContent(),fine=await page.locator('#send-fine').textContent();
  if(reply==='file'){
   const link=page.locator('#send-files a.attachment');assert.equal(await link.count(),1);assert.match(await link.textContent(),/payment-receipt\.pdf.*21 KB/);
   assert.equal(await link.getAttribute('href'),`api/draft-attachment?id=${sid}&sha=${'c'.repeat(64)}`);
   assert.ok(!head.includes('None'));assert.match(fine,/attaches this file, submits it/);
  }else{
   assert.ok(await page.locator('#send-files').isHidden());assert.match(head,/FilesNone/);assert.match(fine,/attaches no file, submits it/);
  }
  // Confirm, Edit and Cancel read at the same size and weight.
  assert.equal(new Set(await page.$$eval('#send-dialog .send-act button',bs=>bs.map(b=>getComputedStyle(b).fontSize+' '+getComputedStyle(b).fontWeight))).size,1);
  if(process.env.SHOTS)await page.screenshot({path:`${process.env.SHOTS}/finish-reply-${reply}.png`});
  await page.locator('#send-cancel').click();
 }
 reply=null;
 kind='finish';finishStatus='done';await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+sid+' [data-smart]').count(),0);
 finishStatus='needs_input';await page.evaluate(()=>refresh());assert.equal(await page.locator('#task-'+sid+' [data-smart]').count(),1);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({dialogs:widths,confirmation_requests:calls.length,full_diff:true,finish_buttons:true,page_errors:errors},null,2));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r))}
