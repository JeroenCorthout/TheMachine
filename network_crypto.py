"""End-to-end encryption between Machines on The Machine Network (docs/network.md).

Each Machine has two keypairs, made on the Machine and never sent anywhere: X25519 to agree a key, Ed25519 to sign.
Only the public halves go to the Network's directory. A message to a teammate or friend is sealed for their Machine:

- a fresh X25519 key per message; with the receiver's public key it gives a shared secret (forward secret for the
  sender's side);
- HKDF-SHA256 turns that into an encryption key and a MAC key;
- the body is encrypted with HMAC-SHA256 in counter mode as the keystream, then authenticated with HMAC-SHA256
  (encrypt-then-MAC); both from the standard library's OpenSSL-backed hashlib/hmac;
- the sender signs everything with its Ed25519 key, bound to the context the Network routed it in (who to whom, what
  kind, which request), so the Network can't move a sealed message to another conversation or forge one.

The Network sees who talks to whom and when, and the size: never the content. X25519 and Ed25519 come from the
`cryptography` package when it is installed, else from the RFC 7748 and RFC 8032 reference algorithms below (slower,
same results; the tests check both against the RFC test vectors)."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

VERSION = 1
ALG = 'x25519-hkdf-sha256-hmacctr-ed25519'
# A sign-in the control plane sealed for this Machine (fleet.py connects_round): the same sealing, without a sender
# signature, since this Machine fetched it from the control plane itself over its own token. Its own domain, so it is
# never taken for a message from another Machine.
ALG_BOX = 'x25519-hkdf-sha256-hmacctr-box'
BOX_INFO = b'tm-device-connect/1'
MAX_BODY = 64 * 1024

try:  # fast path
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ced, x25519 as _cx
    from cryptography.hazmat.primitives import serialization as _cser
    from cryptography.exceptions import InvalidSignature as _CInvalid
except ImportError:  # pragma: no cover - depends on the host
    _ced = _cx = _cser = None


class SealError(ValueError):
    """A sealed message that can't be opened: wrong key, changed on the way, or not from who it says."""


def b64(data):
    return base64.b64encode(data).decode()


def unb64(text, size=None):
    try:
        data = base64.b64decode(str(text), validate=True)
    except (ValueError, TypeError):
        raise SealError('bad encoding') from None
    if size is not None and len(data) != size:
        raise SealError('bad size')
    return data


# ---------------------------------------------------------------- X25519 (RFC 7748)
P = 2 ** 255 - 19
A24 = 121665


def _x25519_py(k, u):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    scalar = int.from_bytes(k, 'little')
    x1 = int.from_bytes(u, 'little') & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        bit = (scalar >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e = (aa - bb) % P
        c, d = (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, 'little')


def x25519(private, public):
    if _cx:
        try:
            return _cx.X25519PrivateKey.from_private_bytes(private).exchange(_cx.X25519PublicKey.from_public_bytes(public))
        except ValueError:
            raise SealError('bad key') from None
    out = _x25519_py(private, public)
    if out == bytes(32):
        raise SealError('bad key')
    return out


def x25519_public(private):
    if _cx:
        return _cx.X25519PrivateKey.from_private_bytes(private).public_key().public_bytes(_cser.Encoding.Raw, _cser.PublicFormat.Raw)
    return _x25519_py(private, (9).to_bytes(32, 'little'))


# ---------------------------------------------------------------- Ed25519 (RFC 8032, section 6)
Q = P
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, Q - 2, Q) % Q
I = pow(2, (Q - 1) // 4, Q)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % Q
    b = (p[1] + p[0]) * (q[1] + q[0]) % Q
    c = 2 * p[3] * q[3] * D % Q
    d = 2 * p[2] * q[2] % Q
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _mul(s, p):
    out = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            out = _add(out, p)
        p = _add(p, p)
        s >>= 1
    return out


def _equal(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % Q == 0 and (p[1] * q[2] - q[1] * p[2]) % Q == 0


def _recover_x(y, sign):
    if y >= Q:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, Q - 2, Q)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (Q + 3) // 8, Q)
    if (x * x - x2) % Q != 0:
        x = x * I % Q
    if (x * x - x2) % Q != 0:
        return None
    if (x & 1) != sign:
        x = Q - x
    return x


_GY = 4 * pow(5, Q - 2, Q) % Q
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % Q)


def _compress(p):
    zinv = pow(p[2], Q - 2, Q)
    x, y = p[0] * zinv % Q, p[1] * zinv % Q
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % Q)


def _secret_expand(secret):
    h = _sha512(secret)
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public(secret):
    if _ced:
        return _ced.Ed25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(_cser.Encoding.Raw, _cser.PublicFormat.Raw)
    a, _ = _secret_expand(secret)
    return _compress(_mul(a, G))


def ed25519_sign(secret, message):
    if _ced:
        return _ced.Ed25519PrivateKey.from_private_bytes(secret).sign(message)
    a, prefix = _secret_expand(secret)
    public = _compress(_mul(a, G))
    r = int.from_bytes(_sha512(prefix + message), 'little') % L
    rs = _compress(_mul(r, G))
    h = int.from_bytes(_sha512(rs + public + message), 'little') % L
    return rs + int.to_bytes((r + h * a) % L, 32, 'little')


def ed25519_verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    # Small-order keys cannot establish a sender identity, even if a library accepts them.
    point = _decompress(public)
    if point is None or _equal(_mul(8, point), (0, 1, 1, 0)):
        return False
    if _ced:
        try:
            _ced.Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
            return True
        except (_CInvalid, ValueError):
            return False
    a = _decompress(public)
    rp = _decompress(signature[:32])
    if not a or not rp:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= L:
        return False
    h = int.from_bytes(_sha512(signature[:32] + public + message), 'little') % L
    return _equal(_mul(s, G), _add(rp, _mul(h, a)))


# ---------------------------------------------------------------- keys
def new_keys():
    """This Machine's keypairs, to keep private (mode 600) on the Machine."""
    box, sign = secrets.token_bytes(32), secrets.token_bytes(32)
    return {'v': VERSION, 'box': b64(box), 'sign': b64(sign)}


def public_keys(private):
    """What the Network's directory shows of this Machine: both public keys, the box key signed by the sign key."""
    return dict(_public_keys(private['box'], private['sign']))


@functools.lru_cache(maxsize=64)
def _public_keys(box, sign):
    """Ed25519 signatures are deterministic: the same private keys always give the same entry, so make it once."""
    box_pub = x25519_public(unb64(box, 32))
    sign_secret = unb64(sign, 32)
    sign_pub = ed25519_public(sign_secret)
    return tuple({'v': VERSION, 'box': b64(box_pub), 'sign': b64(sign_pub),
                  'sig': b64(ed25519_sign(sign_secret, b'tm-network-keys/1\n' + box_pub))}.items())


def check_keys(public):
    """A directory entry's keys, checked: {'box','sign'} as bytes plus a short fingerprint, or None."""
    if not isinstance(public, dict):
        return None
    try:
        box, sign, sig = unb64(public.get('box'), 32), unb64(public.get('sign'), 32), unb64(public.get('sig'), 64)
    except SealError:
        return None
    if not _keys_signed(box, sign, sig):
        return None
    return {'box': box, 'sign': sign, 'fingerprint': fingerprint(sign, box)}


@functools.lru_cache(maxsize=4096)
def _keys_signed(box, sign, sig):
    """The same directory entry is checked on every directory read and every sealed message: verify each entry once
    (without the `cryptography` package a check takes milliseconds, and a Fleet sync made thousands of them)."""
    return ed25519_verify(sign, b'tm-network-keys/1\n' + box, sig)


def fingerprint(sign, box):
    """A Machine's safety number: the same on both sides, changes when its keys change."""
    digest = hashlib.sha256(b'tm-network-fp/1' + sign + box).hexdigest()[:24]
    return ' '.join(digest[i:i + 4] for i in range(0, 24, 4))


def load_or_make(path):
    """Publish a complete key file once; concurrent first use reads the same winner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def read():
        try:
            found = json.loads(path.read_text())
            if not isinstance(found, dict) or found.get('v') != VERSION:
                raise SealError('invalid key file; do not rotate it automatically')
            unb64(found.get('box'), 32)
            unb64(found.get('sign'), 32)
            return found
        except (json.JSONDecodeError, UnicodeError):
            raise SealError('invalid key file; do not rotate it automatically') from None
    try:
        return read()
    except FileNotFoundError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(new_keys(), handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)  # no replacement of a key published by another process
        except FileExistsError:
            pass
        return read()
    finally:
        os.unlink(temporary)


# ---------------------------------------------------------------- sealing
def _hkdf(secret, salt, info, length=64):
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    out, block, counter = b'', b'', 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _stream(key, nonce, data):
    out = bytearray()
    for i in range(0, len(data), 32):
        pad = hmac.new(key, nonce + i.to_bytes(8, 'big'), hashlib.sha256).digest()
        out += bytes(a ^ b for a, b in zip(data[i:i + 32], pad))
    return bytes(out)


def context_bytes(context):
    """The routing a sealed message is bound to: purpose (ask/answer), kind, from and to member, and ref (the sender's
    request id for a question, the question's id for an answer)."""
    keep = {k: str(context.get(k) or '') for k in ('p', 'k', 'f', 't', 'r')}
    return json.dumps(keep, sort_keys=True, separators=(',', ':')).encode()


def _signed(ctx, env):
    return (b'tm-network-msg/1\n' + ctx + b'\n' + unb64(env['eph']) + unb64(env['n']) + unb64(env['ct']) + unb64(env['mac']))


def seal(body, private, to_public, context):
    """`body` (JSON-able) sealed for one Machine: only it can open it, and it can check it came from us."""
    data = json.dumps(body, separators=(',', ':'), sort_keys=True).encode()
    if len(data) > MAX_BODY:
        raise SealError('too big')
    recipient = check_keys(to_public)
    if not recipient:
        raise SealError('their keys do not check out')
    eph = secrets.token_bytes(32)
    eph_pub = x25519_public(eph)
    ctx = context_bytes(context)
    keys = _hkdf(x25519(eph, recipient['box']), eph_pub + recipient['box'], b'tm-network/1' + ctx)
    nonce = secrets.token_bytes(16)
    ct = _stream(keys[:32], nonce, data)
    mac = hmac.new(keys[32:], nonce + ct, hashlib.sha256).digest()
    env = {'v': VERSION, 'alg': ALG, 'eph': b64(eph_pub), 'n': b64(nonce), 'ct': b64(ct), 'mac': b64(mac),
           'to': recipient['fingerprint']}
    env['sig'] = b64(ed25519_sign(unb64(private['sign'], 32), _signed(ctx, env)))
    return env


def is_sealed(value):
    return isinstance(value, dict) and value.get('alg') == ALG and 'ct' in value


def open_sealed(env, private, from_public, context):
    """The body of a sealed message for us, from `from_public`, routed as `context`; SealError otherwise."""
    if not is_sealed(env) or env.get('v') != VERSION:
        raise SealError('not a sealed message')
    for field, size in (('eph', 32), ('n', 16), ('mac', 32), ('sig', 64)):
        unb64(env.get(field), size)
    if len(unb64(env.get('ct'))) > MAX_BODY:
        raise SealError('too big')
    sender = check_keys(from_public)
    if not sender:
        raise SealError('sender keys do not check out')
    ctx = context_bytes(context)
    if not ed25519_verify(sender['sign'], _signed(ctx, env), unb64(env.get('sig'), 64)):
        raise SealError('not signed by the sender for this conversation')
    box = unb64(private['box'], 32)
    mine = x25519_public(box)
    eph_pub = unb64(env['eph'], 32)
    keys = _hkdf(x25519(box, eph_pub), eph_pub + mine, b'tm-network/1' + ctx)
    nonce, ct = unb64(env['n'], 16), unb64(env['ct'])
    if not hmac.compare_digest(hmac.new(keys[32:], nonce + ct, hashlib.sha256).digest(), unb64(env['mac'], 32)):
        raise SealError('changed on the way')
    if len(ct) > MAX_BODY:
        raise SealError('too big')
    try:
        return json.loads(_stream(keys[:32], nonce, ct))
    except ValueError:
        raise SealError('not readable') from None


# ---------------------------------------------------------------- sealed for this Machine by the control plane
def seal_box(body, to_box, context):
    """`body` sealed for the Machine whose X25519 public key is `to_box` (bytes), unsigned: what the control plane does
    for a sign-in (cloud/device_connect.py). Here for tests and symmetry."""
    data = json.dumps(body, separators=(',', ':'), sort_keys=True).encode()
    if len(data) > MAX_BODY:
        raise SealError('too big')
    eph = secrets.token_bytes(32)
    eph_pub = x25519_public(eph)
    keys = _hkdf(x25519(eph, to_box), eph_pub + to_box, BOX_INFO + context_bytes(context))
    nonce = secrets.token_bytes(16)
    ct = _stream(keys[:32], nonce, data)
    return {'v': VERSION, 'alg': ALG_BOX, 'eph': b64(eph_pub), 'n': b64(nonce), 'ct': b64(ct),
            'mac': b64(hmac.new(keys[32:], nonce + ct, hashlib.sha256).digest())}


def open_box(env, private, context):
    """A sign-in sealed for this Machine and bound to `context` ({'p': 'connect', 't': device id, 'r': connect id});
    SealError otherwise. Only for what this Machine fetched from the control plane itself: it carries no signature."""
    if not isinstance(env, dict) or env.get('alg') != ALG_BOX or env.get('v') != VERSION:
        raise SealError('not sealed for this Machine')
    for field, size in (('eph', 32), ('n', 16), ('mac', 32)):
        unb64(env.get(field), size)
    ct = unb64(env.get('ct'))
    if len(ct) > MAX_BODY:
        raise SealError('too big')
    box = unb64(private['box'], 32)
    eph_pub = unb64(env['eph'], 32)
    keys = _hkdf(x25519(box, eph_pub), eph_pub + x25519_public(box), BOX_INFO + context_bytes(context))
    nonce = unb64(env['n'], 16)
    if not hmac.compare_digest(hmac.new(keys[32:], nonce + ct, hashlib.sha256).digest(), unb64(env['mac'], 32)):
        raise SealError('changed on the way, or not for this Machine')
    try:
        return json.loads(_stream(keys[:32], nonce, ct))
    except ValueError:
        raise SealError('not readable') from None
