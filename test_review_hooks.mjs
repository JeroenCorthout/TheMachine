import {H, W} from './test-env.mjs';
import assert from 'node:assert/strict';
import {registerReviewRecovery, processingOnlyReviewSummary, currentReviewRun} from './openclaw-plugin/review-recovery.ts';
const hooks={},calls=[];
let review=null;
registerReviewRecovery({on(name,fn){hooks[name]=fn}},async(action,args)=>{
 calls.push({action,args});return action==='review_context'?{review}:{recorded:true,active:true};
});
const main={agentId:'main',sessionKey:'agent:main:main',runId:'heartbeat-run',trigger:'heartbeat',messageProvider:'telegram',senderId:'1000001',chatId:'1000001'};
// Reviews run in isolated sessions started by the service; heartbeats no longer review.
const ctx={...main,sessionKey:'agent:main:assistant-review-signals-'+'a'.repeat(32),runId:'trusted-run',trigger:undefined};
assert.equal(await hooks.before_prompt_build({},main),undefined);
assert.equal(await hooks.before_prompt_build({}, {...ctx,chatId:'shared-chat'}),undefined);
assert.equal(calls.length,0);
assert.ok((await hooks.before_prompt_build({},ctx)).prependContext.includes('analysis_page'));
const reviewContext=(await hooks.before_prompt_build({},ctx)).prependContext;
assert.ok(reviewContext.includes('## About the owner (USER.md)'));assert.ok(reviewContext.includes('## Assistant instructions'));
// Review turns may only read.
assert.equal((await hooks.before_tool_call({toolName:'browser',params:{}},ctx)).block,true);
assert.equal((await hooks.before_tool_call({toolName:'gateway_exec',params:{}},ctx)).block,true);
assert.equal((await hooks.before_tool_call({toolName:'exec',params:{}},ctx)).block,true);  // embedded runtime shell
assert.equal((await hooks.before_tool_call({toolName:'write',params:{}},ctx)).block,true);
assert.notEqual((await hooks.before_tool_call({toolName:'read',params:{}},ctx))?.block,true);
// Code Mode: namespaced names and the script runner (seen live on October 11).
assert.notEqual((await hooks.before_tool_call({toolName:'openclawassistant_signals',params:{action:'status',args:{}}},ctx))?.block,true);
assert.notEqual((await hooks.before_tool_call({toolName:'openclaw__memory_search',params:{}},ctx))?.block,true);
assert.equal((await hooks.before_tool_call({toolName:'openclawbrowser',params:{}},ctx)).block,true);
assert.notEqual((await hooks.before_tool_call({toolName:'exec',toolKind:'code_mode_exec',params:{}},ctx))?.block,true);
assert.equal(await hooks.before_tool_call({toolName:'openclawassistant_signals',params:{action:'begin_analysis',args:{}}},ctx),undefined);  // no rewrite under Code Mode names
assert.notEqual((await hooks.before_tool_call({toolName:'memory_search',params:{}},ctx))?.block,true);
assert.equal(await hooks.before_tool_call({toolName:'browser',params:{}},main),undefined);  // main session unaffected
// Profile drafts (assistant profile) read the same way and get only the owner's USER.md, not review instructions.
const profileCtx={...ctx,sessionKey:'agent:main:assistant-review-profile-'+'b'.repeat(32),runId:'profile-run'};
const profileContext=(await hooks.before_prompt_build({},profileCtx)).prependContext;
assert.ok(profileContext.includes('## About the owner (USER.md)'));assert.ok(!profileContext.includes('analysis_page'));
assert.equal((await hooks.before_tool_call({toolName:'write',params:{}},profileCtx)).block,true);
assert.equal((await hooks.before_tool_call({toolName:'message',params:{}},profileCtx)).block,true);
assert.notEqual((await hooks.before_tool_call({toolName:'assistant_signals',params:{action:'profile_evidence',args:{}}},profileCtx))?.block,true);
// Find my skills: the browser (to read the owner's accounts) and assistant_signals (its report); no shell or writes,
// and no review instructions.
const skillsCtx={...ctx,sessionKey:'agent:main:assistant-review-skills-'+'c'.repeat(32),runId:'skills-run'};
assert.equal(await hooks.before_prompt_build({},skillsCtx),undefined);
assert.notEqual((await hooks.before_tool_call({toolName:'browser',params:{action:'open'}},skillsCtx))?.block,true);
assert.notEqual((await hooks.before_tool_call({toolName:'assistant_signals',params:{action:'skills_found',args:{}}},skillsCtx))?.block,true);
for(const toolName of ['exec','write','message'])assert.equal((await hooks.before_tool_call({toolName,params:{command:'ls'}},skillsCtx)).block,true);
assert.ok((await hooks.before_prompt_build({},ctx)).prependContext.includes('exactly NO_REPLY'));
// Codex refuses a tool call whose parameters a hook rewrote, so the hook never returns params;
// the tool binds ownership from the run the prompt hook recorded for this session.
assert.equal(await hooks.before_tool_call({toolName:'assistant_signals',params:{action:'begin_analysis',args:{account_ids:['work'],_review_run:'spoof'}}},ctx),undefined);
assert.equal(currentReviewRun(ctx.sessionKey),'trusted-run');
review={id:'review-id',owner_run:'trusted-run',last_error:'ai_connection_reset'};
assert.ok((await hooks.before_prompt_build({},ctx)).prependContext.includes('review-id'));
await hooks.model_call_ended({outcome:'error',failureKind:'connection_reset'},ctx);
assert.equal(calls.at(-1).args.reason,'ai_connection_reset');
let guard=await hooks.before_agent_finalize({},ctx);
assert.equal(guard.action,'revise');assert.equal(guard.retry.maxAttempts,2);
assert.ok(guard.retry.instruction.includes('exactly NO_REPLY'));
await hooks.agent_end({success:true},ctx);
assert.equal(calls.at(-1).args.terminal,true);assert.equal(calls.at(-1).args.reason,'review_not_finished');
review=null;assert.equal(await hooks.before_agent_finalize({},ctx),undefined);
const before=calls.length;await hooks.model_call_ended({outcome:'error'}, main);assert.equal(calls.length,before);
console.log('Review hooks: private scope, host-bound run, retry hint, redacted error and bounded finish guard passed.');

const prepCtx={...ctx,sessionKey:'agent:main:assistant-review-preparation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'};
const previousCount=calls.length;const prep=await hooks.before_prompt_build({},prepCtx);
assert.ok(prep.prependContext.startsWith('## About the owner (USER.md)'));assert.ok(prep.prependContext.includes('## Preparation rules')&&prep.prependContext.includes('then tap Continue'));assert.ok(!prep.prependContext.includes('analysis_page'));
assert.equal(calls.length,previousCount);  // a preparation is not a review
// Preparations read untrusted email. Research, drafting and other changes run;
// only credentials and self-edits stay blocked (loosened at his request October 12 and 14).
const prepOutcome=async(toolName,params,extra={})=>{
 const r=await hooks.before_tool_call({toolName,params,...extra},prepCtx);
 return r?.block?'block':r?.requireApproval?'ask':'run';
};
const expect=async(outcome,cases)=>{for(const [tool,params] of cases)assert.equal(await prepOutcome(tool,params),outcome,tool+' '+JSON.stringify(params));};
await expect('run',[['assistant_signals',{action:'prepare'}],['openclawweb_fetch',{url:'https://example.com'}],['memory_search',{}],
  ['browser',{action:'snapshot'}],['exec',{command:"gog gmail messages search 'from:a {b c}' --account x --readonly --json"}],
  ['bash',{command:"/usr/bin/bash -c 'assistant show 42476e61036c4665 --json'"}],['exec',{command:['rg','-n','Acme','memory']}],
  ['exec',{command:"curl -q -fsSL --max-time 15 https://api.example.com/openapi.json | jq '.paths | keys'"}],
  ['exec',{command:'rg -n Zap memory 2>/dev/null | head -20'}],
  ['exec',{command:'python3 integrations/browser-lease.py claim --owner prep --minutes 30'}],
  ['write',{path:'memory/assistant/261011-brief.md',content:'x'}],
  ['apply_patch',{input:'*** Begin Patch\n*** Add File: '+W+'/memory/assistant/261011-x.md\n+x\n*** End Patch'}]]);
assert.equal(await prepOutcome('exec',{},{toolKind:'code_mode_exec'}),'run');  // the runner; its calls are checked one by one
// No ask tier any more (October 12): approvals can't reach a background run, so everything not blocked runs.
await expect('run',[['sessions_spawn',{}],['browser',{action:'act',request:{kind:'click'}}],['browser',{}],
  ['exec',{command:'python3 assistant.py call'}],['exec',{command:'npm test'}],['exec',{command:'cat $(ls)'}],
  ['exec',{command:'rg --pre=sh x memory'}],['exec',{command:'curl -X POST https://example.com'}],
  ['exec',{command:'curl http://127.0.0.1:8787/api/state'}],['edit',{path:''+H+'/.bashrc'}],
  ['write',{path:'memory/assistant/../../AGENTS.md',content:'x'}],
  ['apply_patch',{input:'*** Begin Patch\n*** Add File: memory/assistant/a.md\n*** Update File: AGENTS.md\n*** End Patch'}]]);
assert.equal((await hooks.before_tool_call({toolName:'exec',params:{command:'npm test'}},prepCtx))?.requireApproval,undefined);
// Loose on purpose: what the owner asks or confirms runs, including sending,
// deleting, restarts and Google/GitHub writes. Only credentials and self-edits stay blocked.
await expect('run',[['exec',{command:'ls; rm -rf memory/tmp'}],['exec',{command:'gog gmail labels modify x'}],
  ['exec',{command:'git push'}],['exec',{command:'systemctl --user restart x'}],['exec',{command:'openclaw gateway restart'}],
  ['exec',{command:'gh pr create'}],['exec',{command:'sudo ls'}],['message',{action:'send'}],['sessions_send',{}],
  ['exec',{command:'gog gmail send --to a@example.com --subject x --body y'}],['exec',{command:'gog gmail drafts send d1'}],
  ['exec',{command:'gog gmail get 1 --readonly=true'}],['exec',{command:'gog calendar events list --readonly | jq .'}]]);
await expect('block',[['exec',{command:'gog auth add x --readonly'}],['exec',{command:'rg token '+H+''}],
  ['write',{path:W+'/tools/personal-assistant/assistant.py',content:'x'}],
  ['exec',{command:'curl -H "X-Assistant-Owner: k" -d @x http://127.0.0.1:8787/api/action'}],
  ['bash',{command:"/usr/bin/bash -c 'cat "+H+"/.local/share/personal-assistant/api-token'"}],
  ['read',{path:''+H+'/.local/share/personal-assistant/owner-token'}],
  ['write',{path:''+H+'/.local/share/personal-assistant/config.json',content:'x'}]]);
assert.equal((await hooks.before_tool_call({toolName:'read',params:{path:''+H+'/.local/share/personal-assistant/api-token'}},ctx)).block,true);  // reviews too
assert.equal(await hooks.before_tool_call({toolName:'exec',params:{command:'gog gmail send'}},main),undefined);  // main chat keeps its own approvals

const processing='Resumed and finished the interrupted review, then completed one more batch: 78 of 80 signal slots assessed.\nSaved one vendor follow-up suggestion. The incomplete partner signal remains pending. The service’s review-budget gate stopped further claims; this was not a complete inbox review.';
const continuation='Resumed and finished the unfinished review, then committed two more batches. 114 of 120 signal slots assessed; no new suggestion was warranted.\nThe empty newsletter message and incomplete partner text remain pending. This was a bounded review, not a complete inbox or history check.';
const delivery={payload:{text:processing},kind:'final',sessionKey:ctx.sessionKey,runId:ctx.runId};
const destination={channelId:'telegram',accountId:'default',conversationId:'1000001'};
assert.equal(processingOnlyReviewSummary(processing),true);
assert.equal(processingOnlyReviewSummary(continuation),true);
assert.equal((await hooks.reply_payload_sending(delivery,destination)).cancel,true);
assert.equal(await hooks.reply_payload_sending({...delivery,payload:{text:processing+'\nYour meeting starts in 20 minutes.'}},destination),undefined);
assert.equal(processingOnlyReviewSummary('Resumed and finished the interrupted review: your meeting starts now.'),false);
assert.equal(await hooks.reply_payload_sending({...delivery,runId:'another-run'},destination),undefined);
assert.equal(await hooks.reply_payload_sending(delivery,{...destination,conversationId:'shared-chat'}),undefined);
assert.equal(await hooks.reply_payload_sending(delivery,{...destination,accountId:'other-account'}),undefined);
assert.equal(await hooks.reply_payload_sending({...delivery,kind:'tool'},destination),undefined);
assert.equal(await hooks.reply_payload_sending({...delivery,payload:{text:processing,mediaUrl:'/report.pdf'}},destination),undefined);
assert.equal(await hooks.reply_payload_sending({...delivery,payload:{text:'A new suggestion: confirm a time with Peter.'}},destination),undefined);
const internal={...main,runId:'internal-run',trigger:'user',inputProvenance:{kind:'internal_system',sourceTool:'notifications-event'}};
const n=calls.length;
assert.ok((await hooks.before_prompt_build({},internal)).prependContext.includes('exactly NO_REPLY'));
assert.equal(calls.length,n); // A notification is not an extra review trigger.
await hooks.before_tool_call({toolName:'assistant_signals',params:{action:'begin_analysis',args:{}}},internal);
assert.equal((await hooks.reply_payload_sending({...delivery,sessionKey:internal.sessionKey,runId:internal.runId,payload:{text:continuation}},destination)).cancel,true);
review={id:'internal-review',owner_run:internal.runId};
assert.equal((await hooks.before_agent_finalize({},internal)).action,'revise');
await hooks.agent_end({success:true},internal);
assert.equal(calls.at(-1).args.owner_run,internal.runId);assert.equal(calls.at(-1).args.terminal,true);
review=null;
const irrelevantNotification={...internal,runId:'notification-without-review'};
await hooks.before_prompt_build({},irrelevantNotification);
const countBeforeFinish=calls.length;
await hooks.agent_end({success:true},irrelevantNotification);
assert.equal(calls.length,countBeforeFinish);
const human={...main,runId:'human-run',trigger:'user',inputProvenance:{kind:'external_user'}};
assert.equal(await hooks.before_prompt_build({},human),undefined);
await hooks.before_tool_call({toolName:'assistant_signals',params:{action:'finish_analysis',args:{}}},human);
assert.equal(await hooks.reply_payload_sending({...delivery,runId:human.runId},destination),undefined);
assert.equal(await hooks.before_prompt_build({}, {...human,trigger:'heartbeat'}),undefined);
assert.equal(await hooks.reply_payload_sending({...delivery,runId:'unproven-internal'},destination),undefined);
console.log('Quiet reviews: both real leaked summaries canceled; internal continuations, user replies, mixed alerts, media, scope and service-owned suggestions protected.');
