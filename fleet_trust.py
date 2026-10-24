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
- remove: a trusted admin signs {t: remove, fleet, member, by, at} when that person left the Fleet or an admin took them
  out of sharing. It counts against every member statement for them from before `at`, whatever their key, so a control
  plane that keeps them in the directory can't keep them in the team's Graph. Machines keep the newest one they saw. A
  later member statement (with its own `at`) lets them back in, again only by a trusted admin.
- pause: a trusted admin signs {t: pause, fleet, member, key, by, on, at} about themselves (member = by): sharing the
  team's Graph is paused for the whole Fleet (on) or not any more. The newest one from a trusted admin counts.

Admission is the one trust decision. A trusted admin's Machine vouches for a person once ('member'). When that person's
Machine changes keys later (a new device), the admin's Machine vouches for the new key by itself after a hold
(shared_graph.REKEY_HOLD), unless its owner says it isn't theirs; every admin sees it during the hold. The control plane
can never add a person: only a confirmed admin's Machine signs one in.

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
KINDS = ('root', 'admin', 'successor', 'member', 'grant', 'optout', 'pause', 'remove', 'policy')
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


def members(group, trusted, pins=None, gone=None):
    """The members of one directory Fleet entry this Machine trusts with the team's Graph: the trusted admins, and
    members a trusted admin vouched for ('member'), each in the directory now with the very key vouched for. pins: the
    members kept last round ({member: key}), so someone vouched for by an admin who left since stays confirmed.
    gone: {member: at} from removed(): someone removed is out unless an admin vouched for them again after that.
    Returns (confirmed {member: sign key}, waiting [member ids with keys, not confirmed], new pins)."""
    fleet = group.get('id')
    gone = gone or {}
    current = {m.get('member_id'): k['sign'] for m in group.get('members', []) if m.get('member_id') and (k := keys_of(m))}
    found = {mid: key for mid, key in (pins or {}).items() if current.get(mid) == key and mid not in gone}
    found.update({mid: key for mid, key in trusted.items() if current.get(mid) == key})
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'member' and s.get('fleet') == fleet and s.get('by') in trusted
                and s.get('member') in current and s.get('key') == current[s['member']] and verified(item, trusted[s['by']])
                and (s['member'] not in gone or str(s.get('at') or '') >= gone[s['member']])):
            found[s['member']] = s['key']
    waiting = sorted(mid for mid in current if mid not in found)
    return found, waiting, dict(found)


def remove(fleet, member, by, at):
    """A trusted admin takes someone out of the team's Graph: they left, or the admin said so."""
    return {'t': 'remove', 'fleet': fleet, 'member': member, 'by': by, 'at': str(at)}


def removed(group, trusted, seen=None):
    """{member: at of the newest remove} a trusted admin signed for this Fleet, merged with seen (what this Machine kept
    from before), so a control plane can't take one back by leaving it out. An admin never removes themselves this way."""
    fleet = group.get('id')
    found = {m: str(a) for m, a in (seen or {}).items() if isinstance(m, str)}
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'remove' and s.get('fleet') == fleet and s.get('by') in trusted and s.get('member') != s.get('by')
                and isinstance(s.get('member'), str) and isinstance(s.get('at'), str) and verified(item, trusted[s['by']])):
            if s['at'] > found.get(s['member'], ''):
                found[s['member']] = s['at']
    return found


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


def pause(fleet, member, key, on, at, purged=None):
    """An admin turns the team's Graph off (on: paused) or back on for the whole Fleet; signed about themselves. purged:
    when an admin last removed everything shared (Off with Remove shared data). Every later statement carries it on, so
    turning sharing back on can't hide a removal from a Machine that was away."""
    s = {'t': 'pause', 'fleet': fleet, 'member': member, 'key': key, 'by': member, 'on': bool(on), 'at': str(at)}
    if purged:
        s['purged'] = str(purged)
    return s


def paused(group, trusted, seen=None):
    """The newest pause statement a trusted admin signed (with the key they have now) for this Fleet, or seen (the newest
    one this Machine kept from before) when that is newer: a control plane can't put an old one back. Returns the
    statement or None. Sharing is paused while it says on."""
    fleet = group.get('id')
    best = seen if isinstance(seen, dict) and seen.get('fleet') == fleet else None
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'pause' and s.get('fleet') == fleet and s.get('by') in trusted and s.get('member') == s.get('by')
                and s.get('key') == trusted[s['by']] and isinstance(s.get('on'), bool) and isinstance(s.get('at'), str)
                and verified(item, trusted[s['by']])):
            if not best or s['at'] > str(best.get('at') or ''):
                best = s
    return best


REQUIRABLE = ('mail', 'calendar')  # what an admin may require: the members' Work email and Work calendar


def policy(fleet, member, key, require, at):
    """An admin sets which Work connections every member shares with the team's Graph (require: some of REQUIRABLE);
    signed about themselves. The newest from any admin wins."""
    return {'t': 'policy', 'fleet': fleet, 'member': member, 'key': key, 'by': member,
            'require': [r for r in REQUIRABLE if r in (require or [])], 'at': str(at)}


def policy_of(group, trusted, seen=None):
    """The newest policy statement a trusted admin signed (with the key they have now) for this Fleet, or seen (kept from
    before) when that is newer: a control plane can't put an old one back or make one up. None when there is none."""
    fleet = group.get('id')
    best = seen if isinstance(seen, dict) and seen.get('fleet') == fleet and seen.get('t') == 'policy' else None
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'policy' and s.get('fleet') == fleet and s.get('by') in trusted and s.get('member') == s.get('by')
                and s.get('key') == trusted[s['by']] and isinstance(s.get('require'), list) and set(s['require']) <= set(REQUIRABLE)
                and isinstance(s.get('at'), str) and verified(item, trusted[s['by']])):
            if not best or s['at'] > str(best.get('at') or ''):
                best = s
    return best

def purged(group, trusted, seen=None):
    """When an admin last removed everything the Fleet shared: the newest `purged` of any pause statement a trusted
    admin signed, or seen (kept from before) when that is newer. None when nobody ever did."""
    fleet, best = group.get('id'), str(seen or '')
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'pause' and s.get('fleet') == fleet and s.get('by') in trusted and s.get('member') == s.get('by')
                and s.get('key') == trusted[s['by']] and isinstance(s.get('purged'), str) and s['purged'] <= str(s.get('at') or '')
                and verified(item, trusted[s['by']])):
            best = max(best, s['purged'])
    return best or None


def vouched_before(group, trusted, member, gone=None):
    """Whether a trusted admin vouched for this person ('member', checked) with a key other than the one they have now:
    the person was admitted, and their Machine has new keys since. Not after they were removed (gone), unless vouched
    for again later."""
    fleet = group.get('id')
    since = (gone or {}).get(member, '')
    now_key = next((k['sign'] for m in group.get('members', []) if m.get('member_id') == member and (k := keys_of(m))), None)
    for item in group.get('trust') or []:
        s = item.get('s') if isinstance(item, dict) else None
        if (isinstance(s, dict) and s.get('t') == 'member' and s.get('fleet') == fleet and s.get('member') == member and s.get('by') in trusted
                and s.get('key') != now_key and str(s.get('at') or '') >= since and (not since or s.get('at')) and verified(item, trusted[s['by']])):
            return True
    return False


def statement(kind, fleet, member, key, by, at=None):
    """root, admin, successor or member. A member statement carries when it was signed (at), so it can outlast a remove."""
    return {'t': kind, 'fleet': fleet, 'member': member, 'key': key, 'by': by, **({'at': str(at)} if at else {})}


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
