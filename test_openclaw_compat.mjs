// Load the real installed SDK, without a Gateway or a Telegram send. Stub-only tests
// cannot detect packaging changes, missing modules, or renamed minified exports.
import {FIXTURE,ROOT} from './test-env.mjs';
import assert from 'node:assert/strict';
import {gatewayRuntime,OPENCLAW} from './openclaw-root.mjs';
import {telegramInternals} from './openclaw-plugin/change-prompt.ts';
import {execFileSync} from 'node:child_process';
const telegram=await telegramInternals();
for(const name of ['resolveTelegramAccount','sendMessageTelegram','getOrCreateAccountThrottler',
                  'createTelegramClientFetch','asTelegramClientFetch','resolveTelegramTransport','makeProxyFetch','Bot'])
  assert.equal(typeof telegram[name],'function',name);
assert.equal(typeof (await gatewayRuntime()).callGatewayFromCli,'function');
assert.equal(typeof (await import(OPENCLAW+'/dist/plugin-sdk/runtime-config-snapshot.js')).getRuntimeConfig,'function');
for(const file of ['runtime-config-snapshot','system-event-runtime','plugin-entry'])
  assert.ok(Object.keys(await import(OPENCLAW+'/dist/plugin-sdk/'+file+'.js')).length);
// Exercise real capability admission too, always in a separate disposable OpenClaw home.
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.startsWith('OPENCLAW_')));
Object.assign(env,{HOME:FIXTURE,OPENCLAW_STATE_DIR:FIXTURE+'/state',OPENCLAW_CONFIG_PATH:FIXTURE+'/openclaw.json'});
delete env.XDG_CONFIG_HOME; delete env.XDG_DATA_HOME;
execFileSync(process.execPath,[OPENCLAW+'/openclaw.mjs','plugins','install','--link','--force',
  '--accept-capabilities',ROOT+'/openclaw-plugin'],{env,timeout:120000,stdio:'pipe'});
// Card chat reads a card's session with the pinned official Gateway client (package-lock.json), never private files:
// the wire version and the chat.history contract it relies on, against the installed OpenClaw's own docs.
import {readFileSync} from 'node:fs';
const lock=JSON.parse(readFileSync(ROOT+'/package-lock.json','utf8')).packages;
for(const name of ['@openclaw/gateway-client','@openclaw/gateway-protocol'])
  assert.equal(JSON.parse(readFileSync(ROOT+'/node_modules/'+name+'/package.json','utf8')).version,lock['node_modules/'+name].version,name);
const {GatewayClient}=await import('@openclaw/gateway-client');
const {PROTOCOL_VERSION}=await import('@openclaw/gateway-protocol/version');
assert.equal(typeof GatewayClient,'function');
assert.match(readFileSync(OPENCLAW+'/docs/gateway/clients.md','utf8'),new RegExp('current wire version is `'+PROTOCOL_VERSION+'`'));
const schema=JSON.parse(readFileSync(ROOT+'/node_modules/@openclaw/gateway-protocol/protocol.schema.json','utf8'));
assert.equal(schema.methods['chat.history'].scope,'operator.read');
for(const key of ['sessionKey','cursor','limit','maxChars'])assert.ok(schema.definitions.ChatHistoryParams.properties[key],key);
for(const key of ['kind','messages','deltaCursor','inFlightRun'])assert.ok(schema.definitions.ChatHistoryDeltaResult.properties[key],key);
// The helper refuses anything but a card's session, and says only "unavailable" without a Gateway.
const helper=(request,extra={})=>JSON.parse(execFileSync(process.execPath,[ROOT+'/chat-history-run.mjs'],{input:JSON.stringify(request),env:{...env,...extra},timeout:30000,stdio:['pipe','pipe','ignore']}).toString()||'{}');
// The helper asks for read access only (and defines it: a missing name would only show as unavailable).
assert.match(readFileSync(ROOT+'/chat-history-run.mjs','utf8'),/^const SCOPES = \['operator\.read'\];$/m);
assert.throws(()=>helper({session_key:'agent:main:main',limit:10},{ASSISTANT_STATE_DIR:FIXTURE+'/chat'}));
assert.throws(()=>helper({session_key:'agent:main:assistant-review-preparation-'+'a'.repeat(32),limit:10},{ASSISTANT_STATE_DIR:'chat'}));
assert.deepEqual(helper({session_key:'agent:main:assistant-review-preparation-'+'a'.repeat(32),limit:10},{ASSISTANT_STATE_DIR:FIXTURE+'/chat',OPENCLAW_CONFIG_PATH:FIXTURE+'/none.json',OPENCLAW_GATEWAY_PORT:'9'}),{error:'unavailable'});
console.log('Pinned SDK and Telegram transport loaded; real plugin installation passed in a disposable home. No Gateway or send.');
