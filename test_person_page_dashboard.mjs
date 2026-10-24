import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// A person's page reads at a glance: photo, name, title · organisation, place and one plain line on how you're in touch;
// then Contact (labelled rows in the normal font, tap to call or mail, Copy), About you (what they are to you, Work or
// Personal, Family or Fleet) and Related (People, Projects) as their own sections, and "Joined from" as a quiet
// disclosure. Edit in the header opens one form (Cancel and Save where Edit was) with fields as long as what goes in
// them; Save sends only what changed. At 390 and 1440, light and dark. SHOT_DIR=… saves screenshots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=n=>new Date(Date.now()+n*864e5).toISOString();
const FACE=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#c9b8a6"/><circle cx="32" cy="25" r="12" fill="#7a5c48"/><path d="M10 64c2-14 11-21 22-21s20 7 22 21z" fill="#3d5a80"/></svg>`;
const fresh=()=>({id:'email:mara.ellison@eu.northwind.example',kind:'person',name:'Mara Ellison',strength:4.2,closeness:'close',last_at:day(-1),signals:180,
 ids:['+32470000071','mara.ellison@eu.northwind.example','mara.ellison@mail.example','Slack'],usual_gap_days:1,quiet:false,next_meeting:day(3),
 role:'partner',role_by:'owner',org:'org:eu.northwind.example',org_name:'Northwind',org_by:'email',side:'personal',side_by:null,edited:[],
 phones:[{value:'+32470000071',by:'messages'}],emails:[{value:'mara.ellison@eu.northwind.example',by:'messages'},{value:'mara.ellison@mail.example',by:'messages'}],
 profile:{title:'Head of the Northwind Technical and Digital Academy',company:'Northwind',photo:'api/person-photo?p=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  linkedin_url:'https://www.linkedin.com/in/mara-ellison-example',city:'Antwerpen',country:'BE',x:'mara_example',timezone:'Europe/Brussels',
  sources:{x:{from:'X',at:'2026-10-20',own:true}}},bio:{text:'Learning, alloys and long walks.',from:'X',at:'2026-10-20'},
 fleet:{machine:true,in:[{kind:'family',label:'Family',fleet:'f1',fleet_name:'',member_id:'m1'}],name:null},
 joined:[{a:'email:mara.ellison@eu.northwind.example',b:'email:mara.ellison@mail.example',a_shown:'mara.ellison@eu.northwind.example',b_shown:'mara.ellison@mail.example',why:'the same name in one message',by:'message'},
         {a:'email:mara.ellison@mail.example',b:'wa:32470000071',a_shown:'mara.ellison@mail.example',b_shown:'+32470000071',why:'the same name on another channel',by:'name'}],
 links:[{id:'email:noor@mail.example',kind:'person',name:'Noor Ellison Vale'},{id:'email:desk@dynamo.example',kind:'person',name:'Dynamo front desk'},
  {id:'project:1',kind:'project',name:'Alert sharing'},{id:'project:2',kind:'project',name:'Winter party'},{id:'project:3',kind:'project',name:'Machine alpha testing'},
  {id:'project:4',kind:'project',name:'Home internet'},{id:'project:5',kind:'project',name:'Pickup for Noor'}],
 recent:[],around:[],open:{}});
let person=fresh();const calls=[];
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){res.end(JSON.stringify(url.searchParams.get('node')?person:{projects:[],people:[person],quiet:[],new:[],orgs:[]}));return}
 if(url.pathname==='/api/person-photo'){res.setHeader('Content-Type','image/svg+xml');res.end(FACE);return}
 if(url.pathname==='/api/action'){let body='';for await(const b of req)body+=b;const call=JSON.parse(body);calls.push(call);
  if(call.action==='graph_person_edit'){const f=call.args.fields;person={...person,name:f.name??person.name,role:f.role??person.role,side:f.side??person.side,
   profile:{...person.profile,...(f.title!==undefined?{title:f.title}:{})},phones:f.phones?f.phones.map(value=>({value,by:'owner'})):person.phones,
   emails:f.emails?f.emails.map(value=>({value,by:'owner'})):person.emails}}
  res.end('{}');return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 if(process.env.SHOT_DIR)await mkdir(process.env.SHOT_DIR,{recursive:true});
 for(const [width,height,scheme] of [[390,844,'dark'],[390,844,'light'],[1440,900,'dark'],[1440,900,'light']]){
  person=fresh();calls.length=0;
  const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,deviceScaleFactor:width<500?2:1,timezoneId:'America/New_York'}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/#graph/'+encodeURIComponent(person.id));
  const card=page.locator('#people-view .person-page');await card.waitFor();
  await page.waitForFunction(()=>document.querySelector('.person-page .person-pic img')?.complete);
  const tag=`${width} ${scheme}`;
  // Header: title · organisation, the place, and one plain line.
  assert.equal((await card.locator('.person-job').textContent()).trim(),'Head of the Northwind Technical and Digital Academy · Northwind',tag);
  assert.equal(await card.locator('.person-job button.node[data-node="org:eu.northwind.example"]').count(),1);
  assert.match((await card.locator('.person-place').textContent()).trim(),/^Antwerpen, Belgium · \d\d:\d\d their time$/);  // 24h, as their clock differs from the owner's
  assert.equal(await page.evaluate(()=>localTime('America/New_York')),'','no "their time" when it is the owner\'s too');
  assert.equal(await card.locator('.person-bio').textContent(),'Learning, alloys and long walks.');
  assert.match(await card.locator('.person-rel').textContent(),/^Talk daily · last \w+, \w+ \d+ · next meeting \w+, \w+ \d+$/);
  // Sections with headings, in order; no scattered Edit links.
  assert.deepEqual(await card.locator('.detail-section>h4').allTextContents(),['Contact','About you','People','Projects']);
  assert.equal(await card.locator('[data-role-edit],[data-person-org-edit],.role-form,.person-org-form,.name-form').count(),0);
  assert.deepEqual(await card.locator('.person-contact .kv .k').allTextContents(),['Phone','Email','Email','LinkedIn','X','Also on']);
  assert.equal(await card.locator('.person-contact a[href="tel:+32470000071"]').textContent(),'+32 470 00 00 71');  // grouped to read; tap and Copy use the number
  assert.equal(await card.locator('.person-contact [data-copy-command="+32470000071"]').count(),1);
  assert.equal(await card.locator('.person-contact a[href="mailto:mara.ellison@eu.northwind.example"]').count(),1);
  assert.equal(await card.locator('.person-contact [data-copy-command="mara.ellison@eu.northwind.example"]').count(),1);
  const fonts=await card.locator('.person-contact .kv .v').evaluateAll(els=>els.map(e=>getComputedStyle(e).fontFamily));
  assert.ok(fonts.every(f=>!/mono/i.test(f)),'contact rows use the normal font: '+fonts);
  // Full text: nothing cut off, nothing wider than the page.
  for(const v of await card.locator('.kv .v').all())assert.ok(await v.evaluate(e=>e.scrollWidth<=e.clientWidth+1),'a row is cut off at '+tag);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll at '+tag);
  const about=await card.locator('.person-about').textContent();
  assert.ok(about.includes('Partner')&&about.includes('Personal contact')&&about.includes('Family · Has a Machine'),about);
  assert.equal(await card.locator('.person-about [data-fleet-member="m1"]').count(),1);
  assert.equal(await card.locator('details.joined summary').textContent(),'Joined from 4 identities');
  assert.equal(await card.locator('details.joined').evaluate(d=>d.open),false);
  assert.equal(await card.locator('.person-menu [data-person-delete]').count(),1);  // Delete stays in the ⋯ menu
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/person-${width}-${scheme}.png`,fullPage:true});
  // Edit: one form, Cancel and Save in the header where Edit was.
  await card.locator('[data-org-edit]').click();
  const form=card.locator('.person-form');await form.waitFor();
  assert.ok(await card.locator('[data-org-save]').isVisible()&&await card.locator('[data-org-cancel]').isVisible()&&!(await card.locator('[data-org-edit]').isVisible()));
  assert.ok(!(await card.locator('.person-view').isVisible()),'the read view steps aside while editing');
  assert.deepEqual(await form.locator('legend').allTextContents(),['Profile','Contact','About you']);
  assert.equal(await form.locator('[name=org]+small').textContent(),'From their work email');
  for(const i of await form.locator('input:not([type=radio])').all()){const h=await i.evaluate(e=>e.getBoundingClientRect().height);assert.ok(h>=36&&h<=46,'compact fields at '+tag+': '+h)}
  const w=async sel=>form.locator(sel).first().evaluate(e=>e.getBoundingClientRect().width);
  if(width>=1000){assert.ok(await w('.w-phone .pf-item input')<=260,'a phone field fits a phone number');assert.ok(await w('[name=title]')<=560,'no page-wide fields')}
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no sideways scroll while editing at '+tag);
  if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/person-edit-${width}-${scheme}.png`,fullPage:true});
  // Cancel changes nothing.
  await form.locator('[name=title]').fill('Something else');await card.locator('[data-org-cancel]').click();
  await card.locator('.person-view').waitFor();assert.equal(calls.length,0);
  assert.ok((await card.locator('.person-job').textContent()).startsWith('Head of the Northwind'));
  // A bad address says why under its field, and nothing is sent.
  await card.locator('[data-org-edit]').click();
  await form.locator('[data-pf-add="emails"]').click();await form.locator('.w-email .pf-item input').last().fill('not an address');
  await card.locator('[data-org-save]').click();
  assert.equal(await form.locator('#pe-err-emails').textContent(),'not an address is not an email address');assert.equal(calls.length,0);
  await form.locator('.w-email .pf-item').last().locator('[data-pf-remove]').click();
  // Save: only what changed.
  await form.locator('[name=title]').fill('Academy lead');
  await form.locator('[data-pf-add="phones"]').click();await form.locator('.w-phone .pf-item input').last().fill('+32 9 000 00 72');
  await form.locator('.w-email .pf-item').nth(1).locator('[data-pf-remove]').click();
  await form.locator('.pf-seg label',{hasText:'Work'}).click();
  await form.locator('[name=role]').fill('wife');
  await card.locator('[data-org-save]').click();
  for(let i=0;i<100&&!calls.length;i++)await page.waitForTimeout(20);
  assert.deepEqual(calls[0],{action:'graph_person_edit',args:{node_id:'email:mara.ellison@eu.northwind.example',fields:{title:'Academy lead',role:'wife',
   phones:['+32470000071','+32 9 000 00 72'],emails:['mara.ellison@eu.northwind.example'],side:'work'}}});
  await page.locator('#toast',{hasText:'Saved.'}).waitFor();
  await card.locator('.person-job',{hasText:'Academy lead'}).waitFor();
  assert.ok((await card.locator('.person-about').textContent()).includes('Work contact'));
  assert.deepEqual(await card.locator('.person-contact .kv .k').allTextContents(),['Phone','Phone','Email','LinkedIn','X','Also on']);
  assert.deepEqual(errors,[]);await context.close();
 }
 console.log('person page dashboard ok');
}finally{await browser?.close();server.close()}
