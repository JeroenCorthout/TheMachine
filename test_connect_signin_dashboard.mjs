import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_CORE||OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Connections, + Add on a self-hosted Machine: Intercom signs in through The Machine's own app once this Machine is
// paired (a one-time start link in a new tab, then its row follows how it goes); not paired, it says pair first.
// Pasting a token stays, folded away. Google, Microsoft and Zoom the same way, with their own app folded away (Use your
// own app). SHOTS=<folder> saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
let paired=true,state='started',calls=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/connect/start/'+'A'.repeat(32)){res.setHeader('Content-Type','text/html');res.end('<title>Intercom</title>consent');return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const {action,args}=JSON.parse(body);calls.push(action);
  if(action==='connect_sources'){res.end(JSON.stringify({saveable:true,manage_url:null,paired,pair_url:'https://app.example.org/account?tab=fleet#devices',sources:[
   {name:'intercom',service:'intercom',label:'Intercom',what:'Customer conversations in your Intercom workspace.',connected:0,broker:false,account_page:'source-intercom',signin:true},
   {name:'gmail',service:'gmail',label:'Gmail',what:'Your email.',connected:0,broker:false,signin:'google'},
   {name:'calendar',service:'calendar',label:'Google Calendar',what:'Your meetings.',connected:0,broker:false,signin:'google'},
   {name:'outlook',service:'outlook',label:'Outlook',what:'Your Outlook email.',connected:0,broker:false,signin:'microsoft'},
   {name:'zoom',service:'zoom',label:'Zoom',what:'Your meetings.',connected:0,broker:false,signin:'zoom'},
   {name:'github',service:'github',label:'GitHub',connected:0,broker:false}]}));return}
  if(action==='connect_signin'){assert.ok(['intercom','gmail'].includes(args.name));res.end(JSON.stringify({url:base+'/connect/start/'+'A'.repeat(32),state:'started'}));return}
  if(action==='connect_signin_state'){res.end(JSON.stringify(state==='done'?{state:'done',name:'Example Support',replies:true}:state==='done-google'?{state:'done',name:'dee@acme.example'}:{state}));return}
  if(action==='connect_start'){res.end(JSON.stringify({session:'s1',state:'running',label:'Intercom',lines:[],question:{id:'q1',kind:'secret',prompt:'Intercom access token'}}));return}
  res.end('{}');return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port;
const shots=process.env.SHOTS;if(shots)await mkdir(shots,{recursive:true});
const errors=[];let browser;
const shot=async(page,name)=>{if(shots)await page.locator('#connect-add').screenshot({path:`${shots}/${name}.png`})};
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const scheme of ['dark','light'])for(const [width,height] of [[1440,900],[390,844],[320,700]]){
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  page.on('pageerror',e=>errors.push(e.message));
  const tag=`${width}-${scheme}`;
  paired=true;state='started';calls=[];
  await page.goto(base+'/#connections=add');
  await page.locator('#connect-add [data-connect-signin="intercom"]').click();
  const go=page.getByRole('button',{name:'Sign in with Intercom'});
  await go.waitFor();
  assert.match(await page.locator('#connect-add').innerText(),/Reads your conversations\. Replies only after you approve them, as yourself\. Never assigns or closes\./);
  assert.equal(await page.locator('#connect-add details.connect-other').getAttribute('open'),null,'pasting a token is folded away');
  assert.ok(!(await page.locator('#connect-add').innerText()).includes('—'),'no em dashes');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  await shot(page,`add-intercom-${tag}`);
  const [tab]=await Promise.all([context.waitForEvent('page'),go.click()]);
  await tab.waitForURL(/\/connect\/start\//);
  assert.equal(await tab.evaluate(()=>window.opener),null,'the sign-in tab has no way back into the dashboard');
  await tab.close();
  await page.getByText('Sign in at Intercom in the tab that opened').waitFor();
  await shot(page,`add-intercom-waiting-${tag}`);
  state='done';
  await page.getByText('Connected to Example Support.').waitFor({timeout:8000});
  assert.match(await page.locator('#connect-add').innerText(),/Replies go as you, after you approve each one\./);
  assert.equal(await page.locator('#connect-add details.connect-other').count(),0);
  await shot(page,`add-intercom-connected-${tag}`);
  // Not paired: pair first, on the account page; pasting a token still works
  paired=false;
  await page.goto('about:blank');await page.goto(base+'/#connections=add');
  await page.locator('#connect-add [data-connect-signin="intercom"]').click();
  const pair=page.getByRole('link',{name:'Pair this Machine first ↗'});
  await pair.waitFor();
  assert.equal(await pair.getAttribute('href'),'https://app.example.org/account?tab=fleet#devices');
  assert.equal(await pair.getAttribute('target'),'_blank');
  assert.equal(await page.getByRole('button',{name:'Sign in with Intercom'}).count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  await shot(page,`add-intercom-not-paired-${tag}`);
  await page.locator('#connect-add details.connect-other summary').click();
  await shot(page,`add-intercom-paste-open-${tag}`);
  await page.getByRole('button',{name:'Paste a token'}).click();
  await page.locator('#connect-answer').waitFor();
  assert.ok(calls.includes('connect_start'));
  // Google: Gmail and Calendar in one sign-in, with its own app folded away
  paired=true;state='started';
  await page.goto('about:blank');await page.goto(base+'/#connections=add');
  await page.locator('#connect-add [data-connect-signin="gmail"]').click();
  const google=page.getByRole('button',{name:'Sign in with Google'});
  await google.waitFor();
  assert.match(await page.locator('#connect-add').innerText(),/Gmail and Google Calendar\. Sends only emails you approved\./);
  assert.equal(await page.locator('#connect-add details.connect-other summary').innerText(),'Use your own app');
  assert.equal(await page.locator('#connect-add details.connect-other').getAttribute('open'),null);
  assert.ok(!(await page.locator('#connect-add').innerText()).includes('—'),'no em dashes');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll');
  await shot(page,`add-google-${tag}`);
  const [gtab]=await Promise.all([context.waitForEvent('page'),google.click()]);
  await gtab.close();
  await page.getByText('Sign in at Google in the tab that opened').waitFor();
  await shot(page,`add-google-waiting-${tag}`);
  state='done-google';
  await page.getByText('Connected to dee@acme.example.').waitFor({timeout:8000});
  assert.doesNotMatch(await page.locator('#connect-add').innerText(),/Replies go as you/);
  await shot(page,`add-google-connected-${tag}`);
  await page.goto('about:blank');await page.goto(base+'/#connections=add');
  await page.locator('#connect-add [data-connect-signin="zoom"]').click();
  await page.getByRole('button',{name:'Sign in with Zoom'}).waitFor();
  assert.match(await page.locator('#connect-add').innerText(),/Reads only\./);
  await page.locator('#connect-add details.connect-other summary').click();
  await shot(page,`add-zoom-own-app-${tag}`);
  await page.goto('about:blank');await page.goto(base+'/#connections=add');
  await shot(page,`add-list-${tag}`);
  await context.close();
 }
 assert.deepEqual(errors,[]);
}finally{await browser?.close();server.close()}
console.log('ok');
