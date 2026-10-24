import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {OPENCLAW} from './openclaw-root.mjs';
import {navClick} from './nav-click.mjs';
const {chromium}=await import(OPENCLAW+'/node_modules/playwright-core/index.mjs');

// The inline edit field on a project's page (Rename) has the compact style (people and organisations have one Edit form,
// see test_person_page_dashboard.mjs and test_org_page_dashboard.mjs):
// a single-line field as tall as its Save button (about 40 to 44px), normal padding, one thin focus ring, a Save
// button that never wraps, and normal room before the next section. Checked at 360 and 390. SHOT_DIR=… saves shots.
const dashboard=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const day=new Date(Date.now()-864e5).toISOString();
const kai={id:'email:kai@umbrella.example',kind:'person',name:'Kai Novak',strength:2.1,closeness:'regular',last_at:day,signals:6,ids:['kai@umbrella.example'],usual_gap_days:7,
 quiet:false,role:'supplier',role_by:'owner',org:null,org_name:null,org_by:null,profile:null,recent:[],around:[],open:{},links:[{id:'email:sam@umbrella.example',kind:'person',name:'Sam Reed'},{id:'project:p1',kind:'project',name:'Spring launch'}]};
const acme={id:'org:acme.example',kind:'org',name:'Acme',strength:1.4,last_at:day,signals:3,ids:['acme.example'],role:'customer',role_by:'owner',profile:null,links:[{id:'email:kai@umbrella.example',kind:'person',name:'Kai Novak'}],recent:[],around:[],open:{},people:[]};
const launch={id:'project:p1',kind:'project',name:'Spring launch',strength:1.2,last_at:day,signals:4,state:'active',trend:1,links:[{id:'email:kai@umbrella.example',kind:'person',name:'Kai Novak'}],recent:[],around:[],open:{}};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture.invalid');res.setHeader('Content-Type','application/json');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(dashboard);return}
 if(url.pathname==='/api/status'){res.end(JSON.stringify({suggestions:[],promises:[],connectors:[],suggestion_counts:{},decisions:[],analyses:[],deliveries:[],review_health:{},review_runs:[]}));return}
 if(url.pathname==='/api/graph'){const node=url.searchParams.get('node');res.end(JSON.stringify(node?[kai,acme,launch].find(p=>p.id===node):{projects:[],people:[kai],quiet:[],new:[],orgs:[acme]}));return}
 if(url.pathname.startsWith('/brand/')||url.pathname.startsWith('/assets/')){res.statusCode=404;res.end();return}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
const measure=(page,form)=>page.evaluate(sel=>{const f=document.querySelector('#people-view '+sel),i=f.querySelector('input'),b=f.querySelector('button'),ir=i.getBoundingClientRect(),br=b.getBoundingClientRect();
 const after=el=>{for(let s=el.nextElementSibling;s;s=s.nextElementSibling)if(!s.hidden&&s.getBoundingClientRect().height)return s;return el.parentElement&&!el.parentElement.matches('#people-view')?after(el.parentElement):null};
 const next=after(f);
 const c=getComputedStyle(i);
 return {input:Math.round(ir.height),button:Math.round(br.height),buttonOneLine:b.scrollWidth<=b.clientWidth&&br.height<60&&getComputedStyle(b).whiteSpace==='nowrap',
  fontSize:parseFloat(c.fontSize),padTop:parseFloat(c.paddingTop),
  ring:{outline:c.outlineStyle==='none'?0:parseFloat(c.outlineWidth),shadow:c.boxShadow},gap:next?Math.round(next.getBoundingClientRect().top-Math.max(ir.bottom,br.bottom)):null,
  inRow:Math.abs(ir.top-br.top)<2,overflow:document.documentElement.scrollWidth-innerWidth}},form);
try{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 if(process.env.SHOT_DIR)await mkdir(process.env.SHOT_DIR,{recursive:true});
 const report={};
 for(const width of [360,390]){
  const context=await browser.newContext({viewport:{width,height:800},deviceScaleFactor:2}),page=await context.newPage();
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await navClick(page.locator('nav [data-tab="people"]'));
  const check=async(open,form,label)=>{await open();await page.locator('#people-view '+form).waitFor();
   await page.locator('#people-view '+form+' input').focus();await page.waitForTimeout(400);
   const m=await measure(page,form);report[`${label}-${width}`]=m;
   if(process.env.SHOT_DIR)await page.screenshot({path:`${process.env.SHOT_DIR}/${label}-${width}.png`});
   return m};
  const forms=[];
  await page.evaluate(()=>people('project:p1'));
  await page.locator('#people-view [data-name-edit]').waitFor();
  forms.push(await check(()=>page.locator('#people-view [data-name-edit]').click(),'.name-form','project-rename'));
  assert.equal(await page.locator('#people-view [data-role-edit],#people-view .role-form').count(),0);
  for(const [n,m] of forms.entries()){const label=`form ${n} at ${width}: ${JSON.stringify(m)}`;
   assert.ok(m.buttonOneLine,'Save stays on one line, '+label);assert.ok(m.inRow,'the field and Save sit on one row, '+label);
   assert.ok(m.input>=38&&m.input<=46,'the field is compact, '+label);assert.ok(Math.abs(m.input-m.button)<=2,'the field is as tall as Save, '+label);
   assert.ok(m.padTop<=10,'normal padding, '+label);
   const rings=m.ring.shadow==='none'?[]:m.ring.shadow.split(/,(?![^(]*\))/).filter(s=>!/\b0px 0px 0px 0px/.test(s));
   assert.ok(m.ring.outline===0&&rings.length===1&&parseFloat(rings[0].trim().split(/\s+/).at(-1))<=2,'one thin focus ring, '+label);
   assert.ok(m.gap>=10,'room before the next section, '+label);assert.ok(m.overflow<=0,'no page overflow, '+label)}
  assert.deepEqual(errors,[]);await context.close();
 }
 if(process.env.INLINE_REPORT)console.log(JSON.stringify(report));
 console.log('inline edit dashboard ok');
}finally{await browser?.close();server.close()}
