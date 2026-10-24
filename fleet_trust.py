"""Who this Machine trusts as a Fleet's admins, and the grants they sign for a shared connector (docs/network.md).

The control plane says who is in a Fleet and who is an admin. For a shared connector that isn't enough: a compromised
control plane could promote someone, or publish a key it holds, and get a token sealed for it. So admins are vouched for
by Machine keys, in a chain that starts at the Fleet owner's key:

- root: the owner's Machine signs {t: root, fleet, member, key} with its own key. The first root this Machine sees for a
  Fleet, from the owner the directory shows, is pinned.
- admin: an admin this Machine trusts signs {t: admin, fleet, member, key, by} after their owner's OK.
- successor: the owner's Machine signs {t: successor, ...} after their owner's OK, for the member who takes the Fleet
  over if they delete their account. It only counts once that member is the owner and the signer is gone.
- member: a trusted admin signs {t: member, fleet, member, key, by} after their owner's OK, so that member's Machine
  gets the team's Graph (shared_graph.py). Trusted admins are members too.
- optout: a trusted admin signs {t: optout, fleet, member, key, by, asked, at} after their owner's OK, when a member
  asked (at `asked`) to stop sharing their Work with the team. Their Machine stops only on such a statement from an admin
  other than themselves, for their key and for that very ask. What they shared before stays with the Fleet.

An admin is trusted when they are an owner or admin in the directory now, with the very key a trusted admin vouched
for (or the pinned root key). The trusted set is pinned between rounds. Being demoted or leaving drops someone at once;
a key change drops them until a trusted admin vouches for the new key. The control plane can only shrink the set.

A grant {t: grant, fleet, service, workspace, hash, grant, to, to_fp, by, at} is signed by a trusted admin for one
recipient: their member id and the fingerprint of their keys when the admin said OK. hash is sha256(grant id, token),
so the token itself is never in it. Statements are signed over b'tm-fleet-trust/1\\n' + canonical JSON."""
from __future__ import annotations

import functools
import hashlib
import json
import re

import network_crypto as nc

TAG = b'tm-fleet-trust/1\n'
ADMIN_ROLES = ('owner', 'admin')
KINDS = ('root', 'admin', 'successor', 'member', 'grant', 'optout')
GRANT_ID = re.compile(r'[a-f0-9]{32}')


class GrantError(ValueError):
    """Why a shared connector was refused: a short reason for the log, never a secret."""


def canon(statement):
    return json.dumps(statement, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sign(private, statement):
    """A statement signed with this Machine's Ed25519 key: {'s': statement, 'sig': base64}."""
    if statement.get('t') not in KINDS:
        raise ValueError('unknown statement')
    return {'s': statement, 'sig': nc.b64(nc.ed25519_sign(nc.unb64(private['sign'], 32), TAG + canon(statement)))}


def verified(item, sign_key):
    """Whether item's signature checks out with sign_key (base64)."""
    try:
        if not isinstance(item, dict) or not isinstance(item.get('s'), dict) or not isinstance(sign_key, str):
            return False
        return _verified(sign_key, canon(item['s']), item.get('sig'))
    except (nc.SealError, TypeError, ValueError):
        return False


@functools.lru_cache(maxsize=4096)
def _verified(sign_key, statement, sig):
    """Each Fleet round checks the same signed statements again: check each one once."""
    return nc.ed25519_verify(nc.unb64(sign_key, 32), TAG + statement, nc.unb64(sig, 64))


def keys_of(member):
    """A directory member's checked keys: {'sign': base64, 'fp': fingerprint}, or None."""
    checked = nc.check_keys(member.get('keys')) if isinstance(member, dict) and member.get('keys') else None
    return {'sign': member['keys']['sign'], 'fp': checked['fingerprint']} if checked else None


def token_hash(grant_id, token):
    return hashlib.sha256(b'tm-shared-connector/1\n' + str(grant_id).encode() + b'\n' + str(token).encode()).hexdigest()


def admins(group, pins=None):
    """The admins of one directory Fleet entry this Machine trusts. pins: what the last round kept
    ({'root': {member, key}, 'admins': {member: key}}). Returns (trusted {member: sign key}, unconfirmed [member ids],
    new pins to keep)."""
    pins = pins or {}
    fleet = group.get('id')
    members = {m.get('member_id'): m for m in group.get('members', []) if m.get('member_id')}
    current = {mid: k['sign'] for mid, m in members.items() if (k := keys_of(m))}
    roles = {mid: m.get('role') for mid, m in members.items()}
    admin_now = {mid for mid, role in roles.items() if role in ADMIN_ROLES}
    items = [i for i in group.get('trust') or [] if isinstance(i, dict) and isinstance(i.get('s'), dict) and i['s'].get('fleet') == fleet]
    root = pins.get('root')
    if not root:
        owner = next((mid for mid, role in roles.items() if role == 'owner'), None)
        for item in items:
            s = item['s']
            if s.get('t') == 'root' and owner and s.get('member') == owner and s.get('key') == current.get(owner) and verified(item, s.get('key')):
                root = {'member': owner, 'key': s['key']}
                break
    before = dict(pins.get('admins') or {})
    if root:
        before.setdefault(root['member'], root['key'])
    trusted = {mid: key for mid, key in before.items() if mid in admin_now and current.get(mid) == key}
    # The owner's chosen successor: once they are the owner and the one who signed it is gone (an account deleted)
    for item in items:
        s = item['s']
        if (s.get('t') == 'successor' and s.get('by') in before and s.get('by') not in members and roles.get(s.get('member')) == 'owner'
                and s.get('key') == current.get(s.get('member')) and verified(item, before[s['by']])):
            trusted[s['member']] = s['key']
    grew = True
    while grew:
        grew = False
        for item in items:
            s = item['s']
            mid = s.get('member')
            if (s.get('t') == 'admin' and mid not in trusted and s.get('by') in trusted and mid in admin_now
                    and s.get('key') == current.get(mid) and verified(item, trusted[s['by']])):
                trusted[mid] = s['key']
                grew = True
    unconfirmed = sorted(admin_now - set(trusted))
    return trusted, unconfirmed, {'root': root, 'admins': trusted} if root else {'admins': trusted}


def members(group, trusted, pins=None):
    """The members of one directory Fleet entry this Machine trusts with the team's Graph: the trusted admins, and
    members a trusted admin vouched for ('member'), each in the directory now with the very key vouched for. pins: the
    members kept last round ({member: key}), so someone vouched for by an admin who left since stays confirmed.
    Returns (confirmed {member: sign key}, waiting [member ids with keys, not confirmed], new pins)."""
    fleet = group.get('id')
    current = {m.get('member_id'): k['sign'] for m in group.get('members', []) if m.get('member_id') and (k := keys_of(m))}
    found = {mid: key for mid, key in (pins or {}).items() if current.get(mid) == key}
    found.update({mid: key for mid, key in trusted.items() if current.get(mid) == key})
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'member' and s.get('fleet') == fleet and s.get('by') in trusted
                and s.get('member') in current and s.get('key') == current[s['member']] and verified(item, trusted[s['by']])):
            found[s['member']] = s['key']
    waiting = sorted(mid for mid in current if mid not in found)
    return found, waiting, dict(found)


def optout(fleet, member, key, by, asked, at):
    return {'t': 'optout', 'fleet': fleet, 'member': member, 'key': key, 'by': by, 'asked': str(asked), 'at': str(at)}


def optouts(group, trusted):
    """{member: the newest optout statement} a trusted admin other than that member signed, for their current key."""
    fleet = group.get('id')
    current = {m.get('member_id'): k['sign'] for m in group.get('members', []) if m.get('member_id') and (k := keys_of(m))}
    found = {}
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'optout' and s.get('fleet') == fleet and s.get('by') in trusted and s.get('by') != s.get('member')
                and s.get('member') in current and s.get('key') == current[s['member']] and isinstance(s.get('asked'), str)
                and verified(item, trusted[s['by']])):
            if s['member'] not in found or str(s.get('at')) > str(found[s['member']].get('at')):
                found[s['member']] = s
    return found


def statement(kind, fleet, member, key, by):
    return {'t': kind, 'fleet': fleet, 'member': member, 'key': key, 'by': by}


def grant(private, fleet, service, workspace, token, grant_id, to, to_fp, by, at):
    """A trusted admin's signed OK for one recipient to hold this token."""
    if not GRANT_ID.fullmatch(str(grant_id)):
        raise ValueError('grant id')
    return sign(private, {'t': 'grant', 'fleet': fleet, 'service': service, 'workspace': str(workspace), 'hash': token_hash(grant_id, token),
                          'grant': grant_id, 'to': to, 'to_fp': to_fp, 'by': by, 'at': at})


def check_grant(item, token, fleet, service, me, my_fp, trusted, workspace=None):
    """The grant that came with a shared token, checked: signed by an admin this Machine trusts now, for this Fleet,
    connector and (when known) workspace, for this Machine's member id and keys, and for exactly this token. Returns the
    statement, or raises GrantError."""
    if not isinstance(item, dict) or not isinstance(item.get('s'), dict) or item['s'].get('t') != 'grant':
        raise GrantError('no grant')
    s = item['s']
    if s.get('by') not in trusted or not verified(item, trusted[s['by']]):
        raise GrantError('not signed by a confirmed admin')
    if s.get('fleet') != fleet or s.get('service') != service or (workspace is not None and s.get('workspace') != workspace):
        raise GrantError('another Fleet or connector')
    if s.get('to') != me or s.get('to_fp') != my_fp:
        raise GrantError('for another recipient')
    if not GRANT_ID.fullmatch(str(s.get('grant'))) or s.get('hash') != token_hash(s['grant'], token):
        raise GrantError('token does not match the grant')
    return s
