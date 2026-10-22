import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings > Skills > Import: the source picked above Find my skills is the only account it looks in, and the note
// says so. An account that gave nothing says exactly where it looked and why, and opens the way to bring one in by
// hand with a one-line how-to for that site.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const calls=[];let list=null;
const skills={skills:[],waiting:0,targets:[],folder:'skills/the-machine',openclaw:true,import_local:true,hints:[]};
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],connectors:[],skills}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',c=>body+=c);req.on('end',()=>{const {action,args}=JSON.parse(body);calls.push({action,args});
   if(action==='skill_installed'){res.end(JSON.stringify({installed:[],checked_at:null,error:''}));return}
   if(action==='skill_import_find'){list={token:'tok1',items:[],errors:[],finding:{run_id:'r1',status:'running'},browser:{},local:true,
     sources:[{key:'local',state:'done',found:0},{key:'chatgpt',state:'starting',found:0}]};res.end(JSON.stringify(list));return}
   if(action==='skill_import_status'){list={...list,finding:{run_id:'r1',status:'ok'},browser:{chatgpt:'ok'},
     sources:[{key:'local',state:'done',found:0},{key:'chatgpt',state:'done',found:0,where:'https://chatgpt.com/skills?collection=installed',detail:'none you made or uploaded yourself'}]};
     res.end(JSON.stringify(list));return}
   res.end('{}')});return}
 res.statusCode=404;res.end();
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:900}}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.locator('#open-settings').click();await page.locator('#settings-tabs [data-settings-tab="skills"]').click();
 await page.locator('#skill-import-open').click();
 assert.equal(await page.locator('#import-find-note').textContent(),'Looks on this computer and in your Claude account.');
 await page.locator('input[name="import-from"][value="chatgpt"]').check();
 assert.equal(await page.locator('#import-find-note').textContent(),'Looks on this computer and in your ChatGPT account.');
 await page.locator('#import-find').click();
 await page.waitForFunction(()=>document.querySelector('#import-progress')&&!document.querySelector('#import-progress').hidden);
 assert.deepEqual(calls.find(c=>c.action==='skill_import_find').args,{sites:['chatgpt']});
 assert.doesNotMatch(await page.locator('#import-progress').textContent(),/claude/i);
 await page.waitForFunction(()=>/looked in/.test(document.querySelector('#import-status').textContent),null,{timeout:15000});
 const status=await page.locator('#import-status').textContent();
 assert.match(status,/ChatGPT: looked in chatgpt\.com\/skills\?collection=installed, none you made or uploaded yourself\./);
 assert.equal(await page.locator('#import-own').evaluate(d=>d.open),true);
 assert.match(await page.locator('#import-how').textContent(),/In ChatGPT: Skills, Installed, a skill’s ⋯ menu, Download/);
 assert.ok(await page.locator('#import-zip-pick').isVisible());
 assert.deepEqual(errors,[]);
 console.log('skills import source dashboard ok');
}finally{await browser?.close();server.close()}
