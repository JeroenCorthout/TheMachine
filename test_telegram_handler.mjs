import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const fixtureDir=await mkdtemp(join(tmpdir(),'assistant-handler-'));
// Exercise the bridge against the installed Telegram decoder, independently
// of our encoder. Generated chunk names are discovered only in this test.
import {OPENCLAW} from './openclaw-root.mjs';
const coreDist=join(OPENCLAW,'dist');
const callbackChunk=(await readdir(coreDist)).find(name=>/^native-command-callback-data-.*\.mjs$/.test(name));
assert.ok(callbackChunk,'installed Telegram callback codec is available');
const {a:decodeCallback}=await import(join(coreDist,callbackChunk));
let source=await readFile(new URL('./openclaw-plugin/index.ts',import.meta.url),'utf8');
source=source.replace("import { buildCallback } from './telegram-callback.ts';",`import { buildCallback } from ${JSON.stringify(new URL('./openclaw-plugin/telegram-callback.ts',import.meta.url).pathname)};`);
const start=source.indexOf('async function settings()'),end=source.indexOf('export default definePluginEntry');
source=source.slice(0,start)+'async function settings(){return globalThis.fixtureConfig;}\nfunction ownerChat(){return String(globalThis.fixtureConfig.telegram_owner);}\nasync function call(action,args={}){return globalThis.fixtureCall(action,args); }\n'+source.slice(end);
for(const line of ["import { readFile } from 'node:fs/promises';","import { homedir } from 'node:os';","import { join } from 'node:path';"])source=source.replace(line,'');
source=source.replace("import { sendChangePrompt } from './change-prompt.ts';",'const sendChangePrompt=async()=>"456";');
source=source.replace("import { registerAndroidFeed } from './android-feed.ts';",'const registerAndroidFeed=()=>{};');
source=source.replace("import { registerHeartbeatFailureGate } from './heartbeat-failure-gate.ts';",'const registerHeartbeatFailureGate=()=>{};');
source=source.replace("import { registerSecretRedaction } from './secret-redaction.ts';",'const registerSecretRedaction=()=>{};');
source=source.replace("import { registerReviewRecovery, currentReviewRun } from './review-recovery.ts';",'const registerReviewRecovery=()=>{};const currentReviewRun=()=>undefined;');
source=source.replace("import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';",'const definePluginEntry=v=>v;').replace('export default definePluginEntry','globalThis.fixturePlugin=definePluginEntry');
const fixturePath=join(fixtureDir,'handler.ts');await writeFile(fixturePath,source);
const id='aaaaaaaaaaaaaaaa';let handler, beforeDispatch, toolFactory, choices=[], edits=[], replies=[];
globalThis.fixtureConfig={telegram_owner:'1000001',dashboard_host:'machine.example.ts.net',dashboard_path:'/assistant/'};
let item={id,status:'snoozed'};
globalThis.fixtureCall=async(action,args)=>{if(action==='refresh_card')return {queued:true};if(action==='status')return {suggestions:[item],deliveries:[{suggestion_id:id,message_id:'123',previous_message_ids:'["old"]'}]};choices.push(args);if(action==='decide')item.status=args.choice==='complete'?'completed':args.choice==='reject'?'rejected':args.choice==='snooze'?'snoozed':'accepted';return {status:item.status,snoozed_until:'2026-10-10T07:00:00Z'};};
await import(fixturePath);
globalThis.fixturePlugin.register({runtime:{config:{current:()=>({})}},registerTool(factory){toolFactory=factory;},registerInteractiveHandler(h){handler=h.handler},on(name,h){if(name==='before_dispatch')beforeDispatch=h},registerCommand(){}});
const ctx=(choice,message='123')=>({auth:{isAuthorizedSender:true},isGroup:false,accountId:'default',senderId:'1000001',callback:{data:`assistant:${id}:${choice}`,chatId:'1000001',messageId:message},callbackId:'unique',respond:{editButtons:async x=>edits.push(x),reply:async x=>replies.push(x),editMessage:async x=>edits.push(x),clearButtons:async()=>{}}});
await handler(ctx('snooze'));assert.equal(choices.length,0);let buttons=edits.pop().buttons.flat();assert.equal(buttons.length,6);assert.ok(buttons.some(b=>b.url?.startsWith('https://machine.example.ts.net/assistant/#snooze-custom=')));assert.ok(buttons.filter(b=>b.callback_data).every(b=>Buffer.byteLength(b.callback_data)<=64&&decodeCallback(b.callback_data)));
await handler(ctx('back'));assert.deepEqual(edits.pop().buttons.flat().map(b=>b.text),['Unsnooze','Change time','Not needed']);
await handler(ctx('snooze_tomorrow','old'));assert.equal(choices.pop().snooze_option,'tomorrow');
item.status='prepared';const repliesBeforeDone=replies.length;await handler(ctx('complete'));assert.equal(choices.pop().choice,'complete');assert.deepEqual(edits.pop().buttons,[]);assert.equal(replies.length,repliesBeforeDone);
item.status='completed';await handler(ctx('accept'));assert.equal(choices.length,0);assert.equal(replies.length,repliesBeforeDone);
item.status='pending';await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Do it');
item.status='completed';item.undo_until=new Date(Date.now()+300000).toISOString();await handler(ctx('undo'));assert.equal(choices.pop().choice,'undo');
item.status='accepted';item.preparation_status='blocked';await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Try again');item.preparation_reason='Sign in to Stripe in the open tab, then tap Continue.';await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Continue');delete item.preparation_reason;delete item.preparation_status;delete item.undo_until;
item.status='accepted';item.preparation_browser={link:'https://app.example.com/open?to=browser'};await handler(ctx('back'));{const b=edits.pop().buttons.flat();assert.equal(b[0].text,'Open the browser');assert.equal(b[0].url,'https://app.example.com/open?to=browser');}
item.preparation_browser={link:'http://127.0.0.1:6080/vnc.html'};await handler(ctx('back'));assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Open the browser'));
item.status='pending';item.preparation_browser={link:'https://app.example.com/open?to=browser'};await handler(ctx('back'));assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Open the browser'));delete item.preparation_browser;
const unauthorized=ctx('accept');unauthorized.senderId='someone-else';await handler(unauthorized);assert.equal(choices.length,0);
item.status='pending';const baseline=replies.length;await handler(ctx('accept'));choices.pop();await handler(ctx('reject'));choices.pop();item.status='pending';await handler(ctx('snooze_2h'));choices.pop();assert.equal(replies.length,baseline);
item.status='pending';await handler(ctx('other'));const prompt=choices.pop();assert.equal(prompt.card_message_id,'123');assert.equal(prompt.managed_prompt,true);assert.equal(choices.length,0);assert.equal(replies.length,baseline);
{const saved=globalThis.fixtureCall;globalThis.fixtureCall=async(action,args)=>action==='request_feedback'?{awaiting_feedback:true,duplicate:true,earlier_prompt:'uncertain'}:saved(action,args);
item.status='pending';const before=replies.length;await handler(ctx('other'));await handler(ctx('other'));assert.equal(replies.length,before);globalThis.fixtureCall=saved;}
item.status='prepared';item.next_action={kind:'email',revision:'abcdef123456',from:'owner@example.com',to:['recipient@example.com'],cc:[],bcc:[],subject:'Verified subject',body:'Exact draft'};
await handler(ctx('back'));buttons=edits.pop().buttons.flat();assert.equal(buttons[0].text,'Send');assert.ok(buttons.every(b=>!b.callback_data||Buffer.byteLength(b.callback_data)<=64));
const beforeSend=choices.length;await handler(ctx('send_abcdef123456'));const review=edits.pop();assert.match(review.text,/From: owner@example.com/);assert.match(review.text,/Exact draft/);assert.equal(choices.length,beforeSend);
assert.deepEqual(review.buttons.flat().map(b=>b.text),['Send now','Edit','Cancel']);assert.match(review.buttons.flat()[1].url,/#send=[a-f0-9]{16}&revision=abcdef123456&edit=1$/);
await handler(ctx('confirm_abcdef123456'));assert.equal(choices.pop().revision,'abcdef123456');edits.pop();
{const before=replies.length;await handler(ctx('confirm_000000000000'));assert.equal(choices.length,beforeSend);edits.pop();
 assert.equal(replies.length,before+1);assert.match(replies.pop().text,/nothing was sent/);}  // a Send on an older card says so
// The card shows this draft in full (card_send): its redraw offers Send that confirms, and Edit.
item.card_send='abcdef123456';await handler(ctx('back'));{const b=edits.pop().buttons.flat();assert.deepEqual(b.slice(0,2).map(x=>x.text),['Send','Edit']);
 assert.match(JSON.stringify(decodeCallback(b[0].callback_data)),/confirm_abcdef123456/);
 assert.match(b[1].url,/^https:\/\/machine\.example\.ts\.net\/assistant\/#send=a{16}&revision=abcdef123456&edit=1$/);}
{const before=choices.length;await handler(ctx('confirm_abcdef123456'));assert.equal(choices.length,before+1);assert.equal(choices.pop().revision,'abcdef123456');edits.pop();}
delete item.card_send;delete item.next_action;
item.next_action={kind:'email',revision:'abcdef123456',from:'owner@example.com',to:[],to_options:['will@vendor.example','ann@example.com'],cc:[],bcc:[],subject:'Pick',body:'Draft'};
{await handler(ctx('send_abcdef123456'));const pick=edits.pop();assert.match(pick.text,/Who should get this email/);const b=pick.buttons.flat();
 assert.deepEqual(b.map(x=>x.text),['Send to will@vendor.example','Send to ann@example.com','Choose several','Edit','Cancel']);assert.equal(b.find(x=>x.text==='Edit').url.endsWith('&edit=1'),true);assert.ok(b.every(x=>!x.callback_data||Buffer.byteLength(x.callback_data)<=64));
 const before=choices.length;await handler(ctx('confirm_abcdef123456'));assert.equal(choices.length,before);edits.pop();  // An old plain Send now shows the choice again.
 await handler(ctx('confirm_abcdef123456_5'));assert.equal(choices.length,before);edits.pop();
 await handler(ctx('confirm_abcdef123456_1'));assert.deepEqual(choices.pop().recipients,['ann@example.com']);edits.pop();}
delete item.next_action;
item.next_actions=[{kind:'email',label:'Send reply',revision:'abcdef123456',from:'owner@example.com',to:['recipient@example.com'],subject:'Exact reply',body:'Approved text'},
 {kind:'prepare',label:'Investigate further',revision:'fedcba654321',instructions:'Check the exact affected mapping locally'},
 {kind:'open',label:'Open the Zap',revision:'111111111111',url:'https://example.com/zap'}];
await handler(ctx('back'));const menu=edits.pop().buttons;assert.ok(menu.every(row=>row.length<=3));assert.deepEqual(menu.flat().slice(0,2).map(b=>b.text),['Send reply','Open the Zap']);
const beforeWrongKind=choices.length;await handler(ctx('send_fedcba654321'));await handler(ctx('next_abcdef123456'));assert.equal(choices.length,beforeWrongKind);
item.next_actions=item.next_actions.slice(1);
await handler(ctx('next_fedcba654321'));assert.equal(choices.pop().revision,'fedcba654321');assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Investigate further'));
{const saved=globalThis.fixtureCall;globalThis.fixtureCall=async(action,args)=>{if(action==='confirm_action')throw Object.assign(new Error('Newer result'),{status:400});return saved(action,args)};
 await handler(ctx('confirm_abcdef123456'));globalThis.fixtureCall=saved;}
delete item.next_actions;
item.next_action={kind:'email_demo',label:'Send',revision:'abcdef123456',from:'the-machine@example.invalid',to:['alex@example.invalid'],subject:'Demo reply',body:'Thanks for the proposal.'};
await handler(ctx('send_abcdef123456'));const demoPreview=edits.pop();assert.match(demoPreview.text,/nothing will be sent/);assert.deepEqual(demoPreview.buttons.flat().map(b=>b.text),['Back']);
const demoChoices=choices.length;await handler(ctx('confirm_abcdef123456'));assert.equal(choices.length,demoChoices);delete item.next_action;
item.status='prepared';item.next_action={kind:'linkedin',label:'Send reply',revision:'abcdef123456',account_id:'li',conversation_id:'urn:li:msg_conversation:(x,2-A)',to_name:'Ada Lovelace',body:'Friday works.'};
await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Send reply');
{const before=choices.length;await handler(ctx('send_abcdef123456'));const r=edits.pop();assert.match(r.text,/Send this LinkedIn message\?\nTo: Ada Lovelace\n\nFriday works\./);
 assert.deepEqual(r.buttons.flat().map(b=>b.text),['Send now','Edit','Cancel']);assert.equal(choices.length,before);
 await handler(ctx('confirm_abcdef123456'));assert.equal(choices.pop().revision,'abcdef123456');edits.pop();}
// Handing it to a colleague on Slack: the Send shares a row with Mark done, and the preview says the card waits.
item.status='prepared';item.next_action={kind:'slack',label:'Send to Sam',revision:'abcdef123456',account_id:'slack',to_email:'sam@example.com',to_name:'Sam',body:'Could you check the invoice?',delegate:true,wait_days:3};
await handler(ctx('back'));assert.deepEqual(edits.pop().buttons.map(row=>row.map(b=>b.text)),[['Send to Sam','Mark done','Snooze'],['Change','Not needed']]);
{const before=choices.length;await handler(ctx('send_abcdef123456'));const r=edits.pop();
 assert.equal(r.text,'Send this Slack message?\nTo: Sam (sam@example.com)\n\nCould you check the invoice?\n\nAfter sending, this card waits 3 days for Sam’s reply; it closes when they answer.');
 assert.deepEqual(r.buttons.flat().map(b=>b.text).filter(t=>t!=='Edit'),['Send now','Cancel']);assert.equal(choices.length,before);
 await handler(ctx('confirm_abcdef123456'));assert.equal(choices.pop().revision,'abcdef123456');edits.pop();}
// X: a reply shows the account and the post it answers; a DM the conversation.
item.status='prepared';item.next_action={kind:'x',label:'Reply on X',revision:'abcdef123456',account_id:'x-acme',handle:'acme',reply_to:'1978',to_screen_name:'ada',to_name:'Ada',to_text:'Does it sync?',body:'Yes, it does.'};
await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Reply on X');
{const before=choices.length;await handler(ctx('send_abcdef123456'));const r=edits.pop();
 assert.equal(r.text,'Reply on X as @acme?\nTo: @ada (Ada)\n“Does it sync?”\n\nYes, it does.');
 assert.deepEqual(r.buttons.flat().map(b=>b.text).filter(t=>t!=='Edit'),['Send now','Cancel']);assert.equal(choices.length,before);
 await handler(ctx('confirm_abcdef123456'));assert.equal(choices.pop().revision,'abcdef123456');edits.pop();}
item.status='prepared';item.next_action={kind:'x',label:'Send',revision:'abcdef123456',account_id:'x-acme',handle:'acme',conversation_id:'42-7',to_name:'Ada',body:'Hi Ada'};
{await handler(ctx('send_abcdef123456'));assert.equal(edits.pop().text,'Send this X message?\nAs @acme to Ada\n\nHi Ada');}
// A merged fix to The Machine: Undo merge for a day, through the owner's tap only.
item.status='prepared';item.next_action={kind:'merge',label:'Merge fix',revision:'abcdef123456',summary:'Fix',stat:'1 file',state:{status:'sent',message_id:'merge:abc',undo_until:new Date(Date.now()+864e5).toISOString()}};
await handler(ctx('back'));{const labels=edits.pop().buttons.flat().map(b=>b.text);assert.ok(labels.includes('Undo merge'));assert.ok(!labels.includes('Merge fix'));}
await handler(ctx('unmerge'));{const c=choices.pop();assert.equal(c.suggestion_id,id);assert.match(c.approval_id,/^telegram:/);}
item.next_action.state.undo_until=new Date(Date.now()-1000).toISOString();await handler(ctx('back'));assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Undo merge'));
// The last step of a prepared task: the review shows what goes out; Confirm runs only that step.
item.status='prepared';item.next_action={kind:'finish',label:'Submit claim',revision:'abcdef123456',instructions:'Press Submit on the filled-in claim.',preview:'42.50 EUR, Travel, 16 Oct',url:'https://expenses.example.com/claims/7'};
await handler(ctx('back'));assert.equal(edits.pop().buttons.flat()[0].text,'Submit claim');
{const before=choices.length;await handler(ctx('next_abcdef123456'));assert.equal(choices.length,before);
 await handler(ctx('send_abcdef123456'));const r=edits.pop();
 assert.equal(r.text,'Submit claim?\n\n42.50 EUR, Travel, 16 Oct\n\nhttps://expenses.example.com/claims/7\n\nConfirm does only this last step, checks that it went through and reports back.');
 assert.deepEqual(r.buttons.map(row=>row.map(b=>b.text)),[['Confirm','Edit','Cancel'],['Open page']]);assert.equal(choices.length,before);
 assert.match(r.buttons[0][1].url,/#send=[a-f0-9]{16}&revision=abcdef123456&edit=1$/);
 await handler(ctx('confirm_abcdef123456'));assert.equal(choices.pop().revision,'abcdef123456');
 assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Submit claim'));}
// A step that posts a message shows its exact text and who gets it; Edit opens it in the dashboard editor.
{const step=item.next_action;item.next_action={...step,state:undefined,label:'Submit inbox reply',preview:'Reply in conversation 7.',to_name:'Sam Example',message:'Hi Sam,\n\nFixed now.'};
 await handler(ctx('back'));edits.pop();await handler(ctx('send_abcdef123456'));const r=edits.pop();
 assert.equal(r.text,'Submit inbox reply?\nTo: Sam Example\nReply in conversation 7.\n\nHi Sam,\n\nFixed now.\n\nFiles: none\n\nhttps://expenses.example.com/claims/7\n\nConfirm types exactly this text on the page, attaches no file, submits it, checks that it went through and reports back.');
 assert.deepEqual(r.buttons[0].map(b=>b.text),['Confirm','Edit','Cancel']);
 // With a file it uploads, the review names it and its size, and Confirm says it attaches it.
 item.next_action={...item.next_action,attachments:[{name:'receipt.pdf',size:21213,sha256:'a'.repeat(64),type:'application/pdf'}]};
 await handler(ctx('back'));edits.pop();await handler(ctx('send_abcdef123456'));const f=edits.pop();
 assert.match(f.text,/\n\nAttachments: receipt\.pdf \(21 KB\)\n/);assert.match(f.text,/attaches the files above, submits it/);item.next_action=step;}
item.next_action.state={status:'done'};
{const before=choices.length;await handler(ctx('back'));assert.ok(!edits.pop().buttons.flat().some(b=>b.text==='Submit claim'));
 await handler(ctx('confirm_abcdef123456'));assert.equal(choices.length,before);edits.pop();}
item.next_action.state={status:'needs_input'};
await handler(ctx('back'));assert.ok(edits.pop().buttons.flat().some(b=>b.text==='Submit claim'));
item.status='pending';await handler(ctx('back'));assert.deepEqual(edits.pop().buttons.map(row=>row.map(b=>b.text)),[['Do it','Mark done','Snooze'],['Change','Not needed']]);
delete item.next_action;
item.status='pending';await handler(ctx('reject'));assert.equal(choices.length,0);assert.deepEqual(edits.pop().buttons.flat().map(b=>b.text),['Not relevant','Wrong facts','Other reason…','No reason','Cancel']);
await handler(ctx('dismiss_handled'));{const d=choices.pop();assert.equal(d.choice,'reject');assert.equal(d.feedback,'Already handled');}
item.status='pending';await handler(ctx('dismiss_none'));{const d=choices.pop();assert.equal(d.choice,'reject');assert.equal(d.feedback,'');}
item.status='pending';await handler(ctx('dismiss_other'));{const d=choices.pop();assert.equal(d.kind,'dismiss');assert.equal(d.managed_prompt,true);}
item.status='pending';item.snooze_choices=[['2h','2 hours'],['tomorrow','Monday · 9:00'],['1w','1 week · 9:00']];await handler(ctx('snooze'));assert.deepEqual(edits.pop().buttons.flat().map(b=>b.text),['2 hours','Monday · 9:00','1 week · 9:00','Choose date','Cancel']);delete item.snooze_choices;
const countBeforeStale=choices.length;
item.status='instructions';await handler(ctx('undo'));assert.equal(choices.length,countBeforeStale);assert.equal(edits.pop().buttons.flat().at(-1).text,'Not needed');
item.status='completed';item.undo_until=new Date(Date.now()-1000).toISOString();await handler(ctx('undo'));assert.equal(choices.length,countBeforeStale);assert.deepEqual(edits.pop().buttons,[]);
item.status='pending';await handler(ctx('retry'));assert.equal(choices.length,countBeforeStale);
// Mark done works on a new card too: it was already handled without Do it.
await handler(ctx('complete'));assert.equal(choices.pop().choice,'complete');assert.equal(choices.length,countBeforeStale);
const savedCall=globalThis.fixtureCall;globalThis.fixtureCall=async(action,args)=>{if(action==='decide')throw Object.assign(new Error('A newer decision replaced this action'),{status:400});return savedCall(action,args)};
item.status='completed';item.undo_until=new Date(Date.now()+300000).toISOString();await handler(ctx('undo'));assert.equal(choices.length,countBeforeStale);assert.equal(edits.pop().buttons.flat()[0].text,'Undo');
globalThis.fixtureCall=async(action,args)=>{if(action==='decide')throw Object.assign(new Error('Server unavailable'),{status:503});return savedCall(action,args)};
await assert.rejects(handler(ctx('undo')),/Server unavailable/);
globalThis.fixtureCall=async(action,args)=>{assert.equal(action,'feedback_reply');assert.equal(args.timestamp,1791614021000);assert.equal(args.reply_to_id,'2982');return {handled:true};};
const handled=await beforeDispatch({content:'I fixed it already',timestamp:1791614021000},{channelId:'telegram',accountId:'default',senderId:'1000001',conversationId:'1000001',sessionKey:'agent:main:main',messageId:'2984',replyToId:'2982'});assert.deepEqual(handled,{handled:true});
// Both profile reads and saves use the host's run, never a model-provided one.
{
 const key='agent:main:assistant-review-profile-aaaaaaaaaaaaaaaa';
 const tool=toolFactory({agentId:'main',sessionKey:key});
 globalThis.fixtureCall=async(action,args)=>{assert.equal(args._profile_session,key);return {ok:true};};
 for(const action of ['profile_evidence','save_profile'])await tool.execute('test',{action,args:{_profile_session:'forged',page:2}});
 const mainTool=toolFactory({agentId:'main',sessionKey:'agent:main:main'});
 globalThis.fixtureCall=async(action,args)=>{assert.equal(args._profile_session,undefined);return {ok:true};};
 await mainTool.execute('test',{action:'profile_evidence',args:{_profile_session:key}});
}
// The Find my skills report: bound to its own skills session (sent as an upload); refused from any other session.
{
 const key='agent:main:assistant-review-skills-'+'d'.repeat(32);
 globalThis.fixtureCall=async(action,args)=>{assert.equal(action,'skills_found');assert.equal(args._skills_session,key);return {added:1};};
 await toolFactory({agentId:'main',sessionKey:key}).execute('test',{action:'skills_found',args:{_skills_session:'forged',found:[]}});
 await assert.rejects(toolFactory({agentId:'main',sessionKey:'agent:main:assistant-review-profile-'+'d'.repeat(32)}).execute('test',{action:'skills_found',args:{found:[]}}),/Only the Find my skills turn/);
}
// A Fleet question: the tap goes to the service with the tapped message id; the service's message replaces the old one.
{const saved=globalThis.fixtureCall,calls=[];const q='q_'+'c'.repeat(16);
 globalThis.fixtureCall=async(action,args)=>{calls.push([action,args]);if(args.message_id!=='321'){throw Object.assign(new Error('That Telegram message is not the one sent for this question'),{status:400});}
  return {status:'answered',message:{text:"Pat's Machine asks: Who knows someone at acme.example?\n   → Answered once",buttons:[[{text:'Open in Suggestions',url:'https://machine.example.ts.net/assistant/#fleet='+q}]]}};};
 const fleetCtx=(data,message='321')=>({...ctx('accept',message),callback:{data,chatId:'1000001',messageId:message}});
 const before=edits.length;await handler(fleetCtx(`assistant:fleet:${q}:once`));
 assert.deepEqual(calls.pop(),['fleet_decide',{id:q,choice:'once',source:'telegram',message_id:'321',decision_id:'telegram:unique'}]);
 const edited=edits.pop();assert.equal(edits.length,before);assert.match(edited.text,/Answered once/);assert.equal(edited.buttons[0][0].url.endsWith('#fleet='+q),true);
 await handler(fleetCtx(`assistant:fleet:${q}:never`));await handler(fleetCtx(`assistant:fleet:q_x:once`));assert.equal(calls.length,0);  // never is for the dashboard
 await handler(fleetCtx(`assistant:fleet:${q}:always`,'999'));assert.equal(calls.pop()[1].message_id,'999');assert.equal(edits.length,before);  // refused: buttons cleared, nothing decided
 const stranger=fleetCtx(`assistant:fleet:${q}:once`);stranger.senderId='someone-else';await handler(stranger);assert.equal(calls.length,0);
 globalThis.fixtureCall=saved;}
// A member asks the Fleet for a server: Confirm relays the tap with its message id; a stale tap says why; only the owner's chat.
{const saved=globalThis.fixtureCall,calls=[];const n='fn_'+'d'.repeat(16);let status='confirmed';
 globalThis.fixtureCall=async(action,args)=>{calls.push([action,args]);if(args.message_id!=='654'){throw Object.assign(new Error('That Telegram message is not the one sent for this request'),{status:400});}
  return {status,reason:status==='stale'?'Already settled: Confirmed in Telegram: their server is starting. Nothing more was done.':'',
   message:{text:'Ann wants a Standard server in Acme: €30/month, new total €60/month.\n\n→ Confirmed in Telegram: their server is starting.',buttons:[[{text:'Open',url:'https://machine.example.ts.net/assistant/#fleet='+n}]]}};};
 const tap=(data,message='654')=>({...ctx('accept',message),callback:{data,chatId:'1000001',messageId:message}});
 const before=edits.length,said=replies.length;await handler(tap(`assistant:fleetn:${n}:confirm`));
 assert.deepEqual(calls.pop(),['fleet_notice_decide',{id:n,choice:'confirm',source:'telegram',message_id:'654',decision_id:'telegram:unique'}]);
 assert.match(edits.pop().text,/Confirmed in Telegram/);assert.equal(edits.length,before);assert.equal(replies.length,said);
 status='stale';await handler(tap(`assistant:fleetn:${n}:confirm`));calls.pop();assert.match(edits.pop().text,/Confirmed in Telegram/);assert.equal(replies.length,said);  // the message itself shows it
 {const respond=tap('x').respond;const t=tap(`assistant:fleetn:${n}:confirm`);t.respond={...respond,editMessage:async()=>{throw new Error('Bad Request: message is not modified');}};
  await handler(t);calls.pop();assert.match(replies.pop().text,/^Already settled/);}  // unchanged message: the reason comes as a reply
 {const t=tap(`assistant:fleetn:${n}:confirm`);let cleared=0;t.respond={...t.respond,clearButtons:async()=>{cleared++;}};
  globalThis.fixtureCall=async(action,args)=>{calls.push([action,args]);throw Object.assign(new Error('The operation was aborted'),{name:'AbortError'});};
  await handler(t);calls.pop();assert.match(replies.pop().text,/did not answer just now, so nothing was done/);assert.equal(cleared,0);  // service away: buttons stay
  globalThis.fixtureCall=async(action,args)=>{calls.push([action,args]);throw Object.assign(new Error('That request is no longer here, so nothing was done.'),{status:400});};
  await handler(tap(`assistant:fleetn:${n}:confirm`));calls.pop();assert.match(replies.pop().text,/no longer here/);
  globalThis.fixtureCall=async(action,args)=>{calls.push([action,args]);if(args.message_id!=='654'){throw Object.assign(new Error('That Telegram message is not the one sent for this request'),{status:400});}
   return {status,reason:'',message:{text:'x',buttons:[]}};};}
 await handler(tap(`assistant:fleetn:${n}:decline`,'999'));assert.equal(calls.pop()[1].message_id,'999');assert.equal(edits.length,before);
 assert.match(replies.pop().text,/nothing was done/);  // not the message sent for it: buttons cleared, nothing decided
 const heard=replies.length;await handler(tap(`assistant:fleetn:${n}:approve`));await handler(tap(`assistant:fleetn:fn_x:confirm`));assert.equal(calls.length,0);
 assert.equal(replies.length,heard+2);assert.match(replies.pop().text,/That button did nothing/);replies.pop();  // an unknown button says so
 const quiet=replies.length;
 const stranger=tap(`assistant:fleetn:${n}:confirm`);stranger.senderId='someone-else';await handler(stranger);
 const group=tap(`assistant:fleetn:${n}:confirm`);group.callback.chatId='-100200';await handler(group);assert.equal(calls.length,0);assert.equal(replies.length,quiet);  // not the owner's chat: silence
 globalThis.fixtureCall=saved;}
console.log('Telegram handler: snooze menu, typed callback sizes, old recorded cards, completion and ownership passed.');

await rm(fixtureDir,{recursive:true,force:true});
