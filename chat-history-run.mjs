// A card's chat: the history of its own preparation session, read through the Gateway's stable WebSocket protocol
// (v4) with the pinned official client (@openclaw/gateway-client, package.json), never OpenClaw's private files or
// plugin-only modules. Read-only: it asks for operator.read and calls chat.history, nothing else.
//
// This helper is its own operator device: an Ed25519 identity kept in the app's data folder. On its first connect it
// uses the Gateway's shared token from openclaw.json once, as the docs' bootstrap; the Gateway pairs a loopback
// device silently, and from then on the device token it issued is used.
import {GatewayClient} from '@openclaw/gateway-client';
import {PROTOCOL_VERSION} from '@openclaw/gateway-protocol/version';
import crypto from 'node:crypto';
import {readFileSync, writeFileSync, renameSync, mkdirSync, unlinkSync} from 'node:fs';
import {resolve, isAbsolute} from 'node:path';
import {homedir} from 'node:os';

const SESSION = /^agent:main:assistant-review-preparation-[a-f0-9]{32}$/;
const SCOPES = ['operator.read'];
// Where this helper keeps its device identity (the app's data folder) and where OpenClaw's config is: set by the
// service in the environment, never taken from the request. Only an absolute, already normal folder is used.
const STATE_DIR = process.env.ASSISTANT_STATE_DIR || '';
const STATE_OK = isAbsolute(STATE_DIR) && resolve(STATE_DIR) === STATE_DIR;
const DEVICE_FILE = `${STATE_DIR}/gateway-device.json`;
const TOKEN_FILE = `${STATE_DIR}/gateway-device-token.json`;
const OPENCLAW_CONFIG = process.env.OPENCLAW_CONFIG_PATH || `${homedir()}/.openclaw/openclaw.json`;

const parsed = text => { try { return JSON.parse(text); } catch { return null; } };
const readDevice = () => { try { return parsed(readFileSync(DEVICE_FILE, 'utf8')); } catch { return null; } };
const readToken = () => { try { return parsed(readFileSync(TOKEN_FILE, 'utf8')); } catch { return null; } };
const readConfig = () => { try { return parsed(readFileSync(OPENCLAW_CONFIG, 'utf8')); } catch { return null; } };
function writePrivate(path, value) {
  const temp = path + '.tmp';
  writeFileSync(temp, JSON.stringify(value), {mode: 0o600});
  renameSync(temp, path);
}
const rawPublicKey = pem => crypto.createPublicKey(pem).export({format: 'der', type: 'spki'}).subarray(-32);

export function deviceIdentity() {
  const saved = readDevice();
  if (saved?.deviceId && saved.publicKeyPem && saved.privateKeyPem) return saved;
  const {publicKey, privateKey} = crypto.generateKeyPairSync('ed25519');
  const publicKeyPem = publicKey.export({format: 'pem', type: 'spki'});
  const identity = {deviceId: crypto.createHash('sha256').update(rawPublicKey(publicKeyPem)).digest('hex'),
    publicKeyPem, privateKeyPem: privateKey.export({format: 'pem', type: 'pkcs8'})};
  mkdirSync(STATE_DIR, {recursive: true, mode: 0o700});
  writePrivate(DEVICE_FILE, identity);
  return identity;
}

function gatewaySettings() {
  const config = readConfig() || {};
  const gateway = config.gateway || {}, auth = gateway.auth || {};
  const port = Number(process.env.OPENCLAW_GATEWAY_PORT || gateway.port || 18789);
  const secret = key => typeof auth[key] === 'string' && auth[key] ? auth[key] : undefined;
  return {url: `ws://127.0.0.1:${port}`, token: process.env.OPENCLAW_GATEWAY_TOKEN || secret('token'),
    password: process.env.OPENCLAW_GATEWAY_PASSWORD || secret('password')};
}

// One connection, one or two chat.history calls. `bootstrap` connects with the shared secret (first pairing, or
// again after the Gateway forgot the device token).
async function history(request, bootstrap) {
  const identity = deviceIdentity(), gateway = gatewaySettings();
  const stored = bootstrap ? null : readToken();
  const hostDeps = {
    loadOrCreateDeviceIdentity: () => identity,
    signDevicePayload: (pem, payload) => crypto.sign(null, Buffer.from(payload, 'utf8'), crypto.createPrivateKey(pem)).toString('base64url'),
    publicKeyRawBase64UrlFromPem: pem => rawPublicKey(pem).toString('base64url'),
    loadDeviceAuthToken: () => stored?.token ? stored : null,
    storeDeviceAuthToken: ({token, scopes}) => writePrivate(TOKEN_FILE, {token, scopes}),
    clearDeviceAuthToken: () => { try { unlinkSync(TOKEN_FILE); } catch {} },
  };
  let connected, failed;
  const ready = new Promise((ok, fail) => { connected = ok; failed = fail; });
  const client = new GatewayClient({
    url: gateway.url, ...(stored?.token ? {} : {token: gateway.token, password: gateway.password}),
    clientName: 'gateway-client', mode: 'ui', clientDisplayName: 'The Machine card chat', role: 'operator', scopes: SCOPES,
    minProtocol: PROTOCOL_VERSION, maxProtocol: PROTOCOL_VERSION, requestTimeoutMs: 15000, hostDeps,
    onHelloOk: hello => connected(hello), onConnectError: error => failed(error),
  });
  // A Gateway busy with a run can take several seconds to greet a new client (7-8 s seen on a busy desktop): wait for it
  // rather than call the chat unreadable while the answer is being written.
  const timer = setTimeout(() => failed(new Error('connect timeout')), 15000);
  client.start();
  try {
    await ready;
    clearTimeout(timer);
    const base = {sessionKey: request.session_key, maxChars: 8000};
    if (request.cursor) {
      const delta = await client.request('chat.history', {...base, cursor: request.cursor});
      if (delta?.kind === 'delta' && !(delta.messages || []).length) return delta;  // nothing new: the caller keeps what it has
    }
    return await client.request('chat.history', {...base, limit: request.limit});
  } finally {
    clearTimeout(timer);
    client.stop();
  }
}

let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
if (!SESSION.test(request.session_key || '') || !STATE_OK ||
    !(Number.isInteger(request.limit) && request.limit > 0 && request.limit <= 500) ||
    (request.cursor != null && (typeof request.cursor !== 'string' || request.cursor.length > 4000))) {
  process.stdout.write(JSON.stringify({error: 'invalid_request'}));
  process.exit(1);
}
let result, reason = 'other';
const why = error => { const m = String(error?.message || ''); return /timeout/i.test(m) ? 'timeout' : /token|pair|auth/i.test(m) ? 'auth' : /ECONNREFUSED|closed|socket/i.test(m) ? 'offline' : 'other'; };
try {
  result = await history(request, false);
} catch (error) {
  reason = why(error);
  // A device token the Gateway no longer knows: pair again once with the shared secret.
  if (/token|pair|auth/i.test(String(error?.message || '')) && !/timeout/i.test(String(error?.message || ''))) {
    try { result = await history(request, true); } catch (again) { reason = why(again); }
  }
}
// Never pass on raw Gateway errors (they can name paths or settings): unavailable and a fixed word for why (timeout,
// auth, offline, other) is all the caller needs. Exit only once all of it is out: a large history is more than one pipe buffer.
process.stdout.write(JSON.stringify(result ?? {error: 'unavailable', reason}), () => process.exit(0));
