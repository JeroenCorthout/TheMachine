import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A card a teammate already handled (their own Machine closed theirs on the same email): it says Done, Mark done leads,
// and it offers no handoff to them. Drafts show links as the recipient gets them: a real link in an email, words (URL)
// in a Fleet handoff. No network beyond this fake server.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const URL_='https://dashboard.vendor.example/settings/tax?x=1&y=2',LINK='[the tax settings]('+URL_+')';
const handoff=id=>({kind:'handoff',label:'Hand off to Kim',revision:'h0000000000'+id,to_email:'kim@acme.example',to_name:'Kim',body:'Can you check '+LINK+' with the accountant?'});
const email={kind:'email',label:'Send reply',revision:'e00000000001',account_id:'work',from:'owner@acme.example',to:['sam@client.example'],subject:'Tax',body:'See '+LINK+'.'};
const base={status:'prepared',event_ids:[1],scope:'work',created_at:new Date().toISOString(),body:'Prepared a handoff to Kim. Nothing sent.'};
const cards=[{...base,id:'c1',title:'Have Kim check the tax alert',next_actions:[handoff(1)],
  handled_hint:JSON.stringify({reason:'Kim already handled this',event_ids:[],fleet:true,at:new Date().toISOString()})},
 {...base,id:'c2',title:'Have Kim check another alert',next_actions:[handoff(2)]},
 {...base,id:'c3',title:'Reply to Sam',next_actions:[email]}];
const state={suggestions:cards,connectors:[],suggestion_counts:{prepared:3},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],fleet:{on:true,waiting:0}};
const server=createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');
 if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(req.url==='/api/status'){res.end(JSON.stringify(state));return}
 if(req.url.startsWith('/api/fleet')){res.end(JSON.stringify({on:true,fleets:[],waiting:[],asked:[],intro_offers:[],splits:[],log:[],notices:[]}));return}
 res.statusCode=404;res.end('{}')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const page=await (await browser.newContext({viewport:{width:390,height:900}})).newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const done=page.locator('#task-c1');await done.waitFor();
 assert.equal(await done.locator('.note.handled').innerText(),'Done: Kim already handled this');
 assert.equal(await done.locator('[data-fleet-handoff]').count(),0);  // nothing to hand on
 assert.equal(await done.locator('.actions>button.primary').first().innerText(),'Mark done');
 const open=page.locator('#task-c2');
 assert.equal(await open.locator('[data-fleet-handoff]').first().innerText(),'Review handoff to Kim');  // not handled: as before
 const plain=await open.locator('.draft .db').innerText();
 assert.equal(plain,'Can you check the tax settings ('+URL_+') with the accountant?');  // what Kim gets: no raw Markdown
 const mail=page.locator('#task-c3 .draft .db');
 assert.equal(await mail.innerText(),'See the tax settings.');
 assert.equal(await mail.locator('a').getAttribute('href'),URL_);  // the send makes it a real link too
 assert.deepEqual(errors,[]);
 console.log('handled elsewhere dashboard: ok');
}finally{await browser.close();server.close()}
