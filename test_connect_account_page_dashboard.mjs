import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections, Add on a hosted Machine: a source with its own sign-in on the account page (Intercom) links there, in a
// new tab; on a Machine without an account page it is set up right here, by token. SHOTS=<folder> saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
let manage=null;
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const {action}=JSON.parse(body);
  if(action==='connect_sources'){res.end(JSON.stringify({saveable:true,manage_url:manage,sources:[
   {name:'intercom',service:'intercom',label:'Intercom',what:'Customer conversations in your Intercom workspace.',connected:0,broker:false,account_page:'source-intercom'},
   {name:'github',service:'github',label:'GitHub',connected:0,broker:false}]}));return}
  res.end('{}');return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const shots=process.env.SHOTS;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const [width,height] of [[1280,900],[320,700]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  manage='https://app.example.org/setup';
  await page.goto(base+'/#connections=add');
  const link=page.locator('#connect-add a.connect-source');
  await link.waitFor();
  assert.equal(await link.getAttribute('href'),'https://app.example.org/setup#source-intercom');
  assert.equal(await link.getAttribute('target'),'_blank');
  assert.match(await link.innerText(),/Intercom ↗/);
  assert.equal(await page.locator('#connect-add [data-connect="intercom"]').count(),0,'no second way here: the page has both');
  assert.equal(await page.locator('#connect-add [data-connect="github"]').count(),1);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  if(shots)await page.locator('#connect-add').screenshot({path:`${shots}/dashboard-add-hosted-${width}.png`});
  // Self-hosted: no account page, so the token is pasted right here
  manage=null;
  await page.goto('about:blank');await page.goto(base+'/#connections=add');
  await page.locator('#connect-add [data-connect="intercom"]').waitFor();
  assert.equal(await page.locator('#connect-add a.connect-source').count(),0);
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('ok');
