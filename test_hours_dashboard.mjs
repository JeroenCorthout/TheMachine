import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// Settings (the gear in the header): Work hours show the saved hours, toggle work days and save through
// set_work_hours; the device's theme and notifications sit there too. On a phone it fits the screen.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
let hours={days:[1,2,3,4,5],start:'08:00',end:'23:00',morning:'09:00',timezone:'Europe/Amsterdam',digest_at:'08:30',saveable:true};
const saved=[];
const server=createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[],connectors:[],hours}));return}
 if(url.pathname==='/api/action'){let body='';req.on('data',c=>body+=c);req.on('end',()=>{const {action,args}=JSON.parse(body);if(action!=='set_work_hours'){res.end('{}');return}saved.push({action,args});
   if(!args.days.length){res.statusCode=400;res.end(JSON.stringify({error:'Pick at least one work day'}));return}
   hours={...args,saveable:true};res.end(JSON.stringify(hours))});return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const errors=[];let browser;
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:390,height:844},timezoneId:'America/New_York'}),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 assert.equal(await page.locator('#health #hours').count(),0);  // not on Connections any more
 await navClick(page.locator('#open-settings'));
 await page.locator('#theme-toggle').waitFor();  // General first
 assert.equal(await page.locator('#open-settings').getAttribute('aria-current'),'page');
 assert.equal(await page.locator('#inbox').isHidden(),true);
 assert.equal(await page.locator('#open-settings .lbl').isHidden(),true);  // just the gear on a phone
 assert.equal(await page.locator('#settings #theme-toggle').isVisible(),true);
 assert.equal(await page.locator('#settings-fleet').isHidden(),true);  // no Fleet here
 assert.match(await page.locator('#settings-more').textContent(),/assistant telegram/);
 // self-hosted, not paired: a way onto The Machine Network (Friends, free) or a Fleet, marked Beta
 assert.match(await page.locator('#settings-more').textContent(),/The Machine Network/);
 assert.equal(await page.locator('#settings-more .alpha').textContent(),'Beta');
 assert.equal(await page.locator('#settings-more a').count(),0);  // a button, not an inline link
 assert.equal(await page.locator('#settings-cloud').textContent(),'Join the Network');
 assert.equal(await page.locator('#settings-cloud').getAttribute('href'),'https://app.themachine.computer/start');  // the Network or a Fleet, kept through sign-in
 assert.equal(await page.locator('#settings-cloud').getAttribute('target'),'_blank');
 {const r=await page.evaluate(()=>{const t=document.querySelector('#settings-more').getBoundingClientRect(),b=document.querySelector('#settings-cloud').getBoundingClientRect();return b.top>=t.bottom-1});assert.equal(r,true)}  // below the text on a phone
 // section notes sit under their titles, not off to the right
 const head=await page.locator('#settings .section-head:visible').first().evaluate(e=>{const h=e.querySelector('h2').getBoundingClientRect(),p=e.querySelector('p').getBoundingClientRect();return {below:p.top>=h.bottom-1,left:Math.abs(p.left-h.left)<2}});
 assert.deepEqual(head,{below:true,left:true});
 assert.equal(await page.locator('.site-footer #theme-toggle, .site-footer #push-toggle').count(),0);
 await page.locator('#settings-tabs [data-settings-tab="hours"]').click();await page.locator('#hours').waitFor();
 assert.deepEqual(await page.locator('#hours-days [aria-pressed="true"]').allTextContents(),['Mon','Tue','Wed','Thu','Fri']);
 assert.equal(await page.locator('#hours-start').inputValue(),'08:00');
 assert.equal(await page.locator('#hours-end').inputValue(),'23:00');
 assert.equal(await page.locator('#hours-morning').inputValue(),'09:00');
 assert.equal(await page.locator('#hours-digest').inputValue(),'08:30');
 assert.equal(await page.locator('#hours-zone').inputValue(),'Europe/Amsterdam');
 // Fits a phone: nothing wider than the screen.
 assert.ok(await page.evaluate(()=>document.querySelector('#hours').getBoundingClientRect().right<=innerWidth));
 // This device is elsewhere: one tap takes its zone.
 assert.match(await page.locator('#hours-detect').textContent(),/America\/New_York/);
 await page.locator('#hours-detect').click();
 assert.equal(await page.locator('#hours-zone').inputValue(),'America/New_York');
 await page.locator('#hours-days [data-day="5"]').click();
 await page.locator('#hours-days [data-day="7"]').click();
 await page.locator('#hours-start').fill('09:00');
 await page.locator('#hours-end').fill('17:30');
 await page.locator('#hours-morning').fill('08:15');
 await page.locator('#hours-save').click();
 await page.locator('#notice,#toast',{hasText:'Work hours saved.'}).first().waitFor();
 assert.deepEqual(saved.at(-1),{action:'set_work_hours',args:{days:[1,2,3,4,7],start:'09:00',end:'17:30',morning:'08:15',digest_at:'08:30',timezone:'America/New_York'}});
 // A refresh keeps what was saved.
 await page.evaluate(()=>refresh());
 assert.deepEqual(await page.locator('#hours-days [aria-pressed="true"]').allTextContents(),['Mon','Tue','Wed','Thu','Sun']);
 assert.equal(await page.locator('#hours-detect').isHidden(),true);
 // No work day at all is refused before it is sent.
 const before=saved.length;
 for(const day of [1,2,3,4,7])await page.locator(`#hours-days [data-day="${day}"]`).click();
 await page.locator('#hours-save').click();
 assert.match(await page.locator('#hours-error').textContent(),/at least one work day/);
 assert.equal(saved.length,before);
 // A refresh while editing does not undo the edit.
 await page.evaluate(()=>refresh());
 assert.equal(await page.locator('#hours-days [aria-pressed="true"]').count(),0);
 // Not saveable here (no config file): shown, read-only, with where to change it.
 hours={...hours,saveable:false};
 await page.reload();await navClick(page.locator('#open-settings'));
 assert.equal(await page.locator('#hours-save').isDisabled(),true);
 assert.match(await page.locator('#hours-error').textContent(),/config\.json/);
 // Another tab clears the gear; the gear brings Settings back. Light mode reads well too.
 await navClick(page.locator('nav [data-tab="events"]'));
 assert.equal(await page.locator('#open-settings').getAttribute('aria-current'),'false');
 assert.equal(await page.locator('#settings').isHidden(),true);
 await navClick(page.locator('#open-settings'));await page.locator('#settings-tabs [data-settings-tab="general"]').click();const was=await page.evaluate(()=>document.documentElement.dataset.theme);
 await page.locator('#theme-toggle').click();
 assert.notEqual(await page.evaluate(()=>document.documentElement.dataset.theme),was);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1100,height:900});
 assert.equal(await page.locator('#open-settings .lbl').isVisible(),true);
 assert.deepEqual(errors,[]);
 console.log('hours dashboard ok');
}finally{await browser?.close();server.close()}
