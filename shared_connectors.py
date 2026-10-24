"""Shared connectors: a Work Fleet's admin connects a source once (Intercom first) and teammates' Machines read it too,
without anyone seeing or pasting the token (docs/network.md, Shared connectors). Mixed into fleet.Fleet.

How it goes:
- An admin's Machine shares a connected Intercom account with one of their Work Fleets. Their owner ticks who gets it;
  every confirmed admin gets it by default. The control plane only learns that the Fleet shares Intercom, from which
  admin, under which grant id, and later who holds it. It never sees the token.
- For each recipient, a confirmed admin's Machine signs a grant (fleet_trust.grant) bound to the recipient's member id
  and key fingerprint at the OK, then seals the token with the grant for that Machine's pinned key (kind 'connector').
  Only admins' Machines send it; members never pass it on.
- A later joiner waits for an admin's OK on that admin's own Machine ("Share Intercom with <name>'s Machine?"); so does a
  recipient whose keys changed. Nothing is sent before.
- The receiving Machine checks the grant (fleet_trust.check_grant), that the sender is the admin who signed it, that the
  control plane still shows that grant id, and that it is not older than one it holds. Then the token goes into a
  private credential file (mode 600) and the account into config.json, marked shared, and the service restarts.
- Stopping, replacing or leaving: when the directory stops showing the share, or shows another grant, or the Fleet is
  gone, the account and its token file go. When someone who held it leaves, the admins' Machines ask their owners to
  replace the token in Intercom; the new token goes to the remaining recipients under a new grant.

Read-only stays read-only: the shared account is the same connector, which never replies, assigns, closes, tags or sends.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets
from pathlib import Path

import fleet_trust as ft
import network_crypto as nc

LOG = logging.getLogger('personal-assistant')
SHAREABLE = {'intercom': {'label': 'Intercom', 'fields': ('workspace_id', 'workspace_name', 'region', 'admin_id'), 'identity': 'workspace_id'}}
RESEND_SECONDS = 6 * 3600  # an unanswered send goes again after this (questions expire on the Network after 3 days)
MAX_PROMPTS = 50
TOKEN = re.compile(r'[A-Za-z0-9_=:.+/-]{8,512}')


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _age(stamp):
    try:
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return 1e12


def _short(fleet_id):
    return re.sub(r'[^a-z0-9]', '', str(fleet_id).lower())[-12:] or 'fleet'


class SharedConnectors:
    # Set by the app: save_config(change) applies change(config) to config.json and the running config, then restarts
    # the service when change returns True; connector_profile(service, account, token) reads the token's own profile
    # once (Intercom: GET /me) and fills the account's identity fields, raising ValueError when it doesn't work.
    save_config = None
    connector_profile = None

    # -- state
    def _private(self):
        return getattr(self.relay, 'private', None)

    def _my_fp(self):
        public = getattr(self.relay, 'public', None)
        checked = nc.check_keys(public) if public else None
        return checked['fingerprint'] if checked else None

    def _pins_key(self, fleet):
        return 'trust:' + fleet

    def trust(self, group):
        """(trusted admins {member: key}, unconfirmed admin ids) for one directory entry, keeping the pins."""
        trusted, unconfirmed, pins = ft.admins(group, self.meta(self._pins_key(group['id'])))
        self.set_meta(self._pins_key(group['id']), pins)
        return trusted, unconfirmed

    def _work_groups(self):
        return [g for g in (self.directory().get('fleets') or []) if g.get('kind') == 'work' and g.get('id')]

    def _group(self, fleet):
        return next((g for g in (self.directory().get('fleets') or []) if g.get('id') == fleet), None)

    def _me_in(self, group):
        return next((m for m in group.get('members', []) if m.get('me')), None)

    def _share_of(self, group, service):
        return next((s for s in group.get('shared') or [] if isinstance(s, dict) and s.get('service') == service), None)

    def _held(self, fleet, service):
        """This Machine's account for a Fleet's shared connector (its own that it shares, or one it was given)."""
        return next((a for a in self.config.get('accounts', []) if a.get('service') == service
                     and isinstance(a.get('shared'), dict) and a['shared'].get('fleet') == fleet), None)

    def _token(self, account):
        path = (self.config.get('credentials') or {}).get(account.get('credential') or '')
        try:
            value = Path(path).expanduser().read_text().strip() if path else ''
        except OSError:
            return None
        if account.get('credential_variable'):
            found = dict(line.split('=', 1) for line in value.splitlines() if '=' in line)
            value = found.get(account['credential_variable'], '').strip().strip('"').strip("'")
        return value if TOKEN.fullmatch(value or '') else None

    def _state(self, fleet, service):
        return self.meta(f'shared:{fleet}:{service}', {}) or {}

    def _set_state(self, fleet, service, value):
        self.set_meta(f'shared:{fleet}:{service}', value)

    def _prompts(self):
        return self.meta('shared_prompts', []) or []

    def _prompt(self, kind, group, service, member, **extra):
        if kind in ('admin', 'successor', 'member') and [group['id'], member.get('member_id'), (ft.keys_of(member) or {}).get('fp')] in (self.meta('trust_declined', []) or []):
            return
        prompts = self._prompts()
        key = (kind, group['id'], service, member.get('member_id'))
        if any((p['kind'], p['fleet'], p['service'], p['member']) == key for p in prompts):
            return
        prompts.append({'id': secrets.token_hex(8), 'kind': kind, 'fleet': group['id'], 'fleet_name': str(group.get('name') or '')[:80],
                        'service': service, 'member': member.get('member_id'), 'name': str(member.get('label') or member.get('name') or '')[:80],
                        'email': str(member.get('account_email') or '')[:200], 'fp': (ft.keys_of(member) or {}).get('fp'), 'at': _now(), **extra})
        self.set_meta('shared_prompts', prompts[-MAX_PROMPTS:])

    def _drop_prompts(self, test):
        self.set_meta('shared_prompts', [p for p in self._prompts() if not test(p)])

    # -- the owner's controls (dashboard only)
    def share(self, account_id, fleet, members=()):
        """Shares this Machine's connected account with one Work Fleet: every confirmed admin, and the members ticked."""
        account = next((a for a in self.config.get('accounts', []) if a.get('id') == account_id), None)
        if not account or account.get('service') not in SHAREABLE:
            raise ValueError('Only a connected Intercom account can be shared')
        if isinstance(account.get('shared'), dict) and not account['shared'].get('origin'):
            raise ValueError('This account is shared with you; only an admin of that Fleet can share it')
        group = self._group(fleet)
        me = group and self._me_in(group)
        if not group or group.get('kind') != 'work' or not me:
            raise ValueError('Share with one of your Work Fleets')
        trusted, _ = self.trust(group)
        if me['member_id'] not in trusted:
            raise ValueError('Only a confirmed admin of the Fleet can share a connector')
        service = account['service']
        if self._held(fleet, service) and self._held(fleet, service) is not account:
            raise ValueError(f'{group.get("name") or "That Fleet"} already shares {SHAREABLE[service]["label"]}')
        identity = account.get(SHAREABLE[service]['identity'])
        token = self._token(account)
        if not identity or not token:
            raise ValueError('Connect it first, so its workspace is known')
        chosen = {str(m) for m in members or ()}
        approved = {}
        for m in group.get('members', []):
            keys = ft.keys_of(m)
            if m.get('me') or not keys:
                continue
            if m['member_id'] in chosen or m['member_id'] in trusted:
                approved[m['member_id']] = {'fp': keys['fp'], 'at': _now(), 'name': str(m.get('label') or m.get('name') or '')[:80]}
        grant_id = secrets.token_hex(16)
        stamp = _now()
        self.relay.call('POST', '/shared', {'op': 'set', 'fleet': fleet, 'service': service, 'grant': grant_id,
                                            'workspace': str(identity), 'name': str(account.get('workspace_name') or identity)[:100]}, timeout=30)
        shared = {'fleet': fleet, 'fleet_name': str(group.get('name') or '')[:80], 'service': service, 'grant': grant_id, 'at': stamp,
                  'by': me['member_id'], 'origin': True}
        self._save(lambda config: self._mark(config, account_id, shared))
        declined = [m['member_id'] for m in group.get('members', []) if not m.get('me') and m['member_id'] not in approved]
        self._set_state(fleet, service, {'approved': approved, 'declined': declined, 'sent': {}, 'holders': {}})
        self._drop_prompts(lambda p: p['fleet'] == fleet and p['service'] == service)
        self.directory(refresh=True)
        self.shared_round()
        return {'ok': True, 'shared_with': len(approved)}

    def _mark(self, config, account_id, shared):
        for a in config.get('accounts', []):
            if a.get('id') == account_id:
                if shared:
                    a['shared'] = shared
                else:
                    a.pop('shared', None)
        return False

    def _save(self, change):
        """Applies change(config) to the running config and, through the app, to config.json."""
        restart = change(self.config)
        if self.save_config:
            self.save_config(change, restart)

    def share_stop(self, fleet, service):
        """Stops sharing: the control plane clears the flag, and every holder's Machine drops the token on its next round."""
        group = self._group(fleet)
        me = group and self._me_in(group)
        if not group or not me or me['member_id'] not in self.trust(group)[0]:
            raise ValueError('Only a confirmed admin of the Fleet can stop sharing')
        self.relay.call('POST', '/shared', {'op': 'off', 'fleet': fleet, 'service': service}, timeout=30)
        held = self._held(fleet, service)
        if held and held['shared'].get('origin'):
            self._save(lambda config: self._mark(config, held['id'], None))
        self._set_state(fleet, service, {})
        self._drop_prompts(lambda p: p['fleet'] == fleet and p['service'] == service)
        self.directory(refresh=True)
        return {'ok': True}

    def share_ok(self, prompt_id, choice):
        """The owner on a prompt: yes or no. Share with someone, confirm an admin or the next owner, or a key change."""
        prompt = next((p for p in self._prompts() if p['id'] == prompt_id), None)
        if not prompt:
            raise ValueError('That was answered already')
        if choice not in ('yes', 'no'):
            raise ValueError('Choose yes or no')
        group = self._group(prompt['fleet'])
        me = group and self._me_in(group)
        member = group and next((m for m in group.get('members', []) if m.get('member_id') == prompt['member']), None)
        self._drop_prompts(lambda p: p['id'] == prompt_id)
        if choice == 'no' or not member or not me:
            if prompt['kind'] in ('recipient', 'rekey'):
                state = self._state(prompt['fleet'], prompt['service'])
                state.setdefault('declined', []).append(prompt['member'])
                self._set_state(prompt['fleet'], prompt['service'], state)
            elif prompt['kind'] in ('admin', 'successor', 'member'):  # not asked again for these keys
                self.set_meta('trust_declined', (self.meta('trust_declined', []) or [])[-200:] + [[prompt['fleet'], prompt['member'], prompt.get('fp')]])
            elif prompt['kind'] == 'optout':  # not asked again for this ask; sharing stays on (shared_graph.py)
                self.set_meta('optout_declined', (self.meta('optout_declined', []) or [])[-200:] + [[prompt['fleet'], prompt['member'], prompt.get('asked')]])
            return {'ok': True, 'done': False}
        keys = ft.keys_of(member)
        if not keys or keys['fp'] != prompt.get('fp'):
            raise ValueError('Their Machine has new keys since this was asked; it asks again')
        trusted, _ = self.trust(group)
        if me['member_id'] not in trusted:
            raise ValueError('Only a confirmed admin can do that')
        if prompt['kind'] == 'optout':  # they asked to stop sharing their Work with the team (shared_graph.py)
            return self.optout_ok(prompt, group, me, member, keys)
        if prompt['kind'] in ('admin', 'successor', 'member'):  # member: their Machine gets the team's Graph (shared_graph.py)
            item = ft.sign(self._private(), ft.statement(prompt['kind'], group['id'], member['member_id'], keys['sign'], me['member_id'],
                                                         at=_now() if prompt['kind'] == 'member' else None))
            self.relay.call('POST', '/trust', {'fleet': group['id'], 'item': item}, timeout=30)
            self.directory(refresh=True)
            if prompt['kind'] == 'member':  # their Machine gets the graph key and what this one shares, now
                try:
                    self.graph_round(force=True)
                except (ValueError, OSError, KeyError, TypeError) as error:
                    LOG.info('shared graph after a member OK error=%s', type(error).__name__)
            return {'ok': True, 'done': True}
        state = self._state(prompt['fleet'], prompt['service'])
        state.setdefault('approved', {})[member['member_id']] = {'fp': keys['fp'], 'at': _now(), 'name': prompt['name']}
        state['declined'] = [x for x in state.get('declined', []) if x != member['member_id']]
        state.setdefault('sent', {}).pop(member['member_id'], None)
        self._set_state(prompt['fleet'], prompt['service'], state)
        self.directory(refresh=True)
        self.shared_round()
        return {'ok': True, 'done': True}

    def share_replace(self, fleet, service, token):
        """A new token for a Fleet's shared connector (someone who held it left, say): checked against the same
        workspace, saved privately, and sent under a new grant to everyone still approved."""
        token = str(token or '').strip()
        if not TOKEN.fullmatch(token):
            raise ValueError('Paste the new access token')
        group = self._group(fleet)
        me = group and self._me_in(group)
        held = self._held(fleet, service)
        if not group or not me or me['member_id'] not in self.trust(group)[0]:
            raise ValueError('Only a confirmed admin of the Fleet can replace the token')
        if not held:
            raise ValueError('This Machine does not hold that connector')
        if not self.connector_profile:
            raise ValueError('This Machine cannot check a token')
        probe = {k: held.get(k) for k in ('id', 'service', 'transport', 'region', *SHAREABLE[service]['fields'])}
        self.connector_profile(service, probe, token)  # the same workspace, or refused (account_identity_mismatch)
        path = (self.config.get('credentials') or {}).get(held.get('credential') or '')
        if not path:
            raise ValueError('That account has no credential file')
        _write_private(Path(path).expanduser(), token + '\n')
        grant_id, stamp = secrets.token_hex(16), _now()
        identity = held.get(SHAREABLE[service]['identity'])
        self.relay.call('POST', '/shared', {'op': 'set', 'fleet': fleet, 'service': service, 'grant': grant_id, 'workspace': str(identity),
                                            'name': str(held.get('workspace_name') or identity)[:100]}, timeout=30)
        shared = {**held['shared'], 'grant': grant_id, 'at': stamp, 'by': me['member_id']}
        self._save(lambda config: (self._mark(config, held['id'], shared), True)[1])  # the service rereads the token
        state = self._state(fleet, service)
        state['sent'] = {}
        self._set_state(fleet, service, state)
        self._drop_prompts(lambda p: p['fleet'] == fleet and p['service'] == service and p['kind'] == 'replace')
        self.directory(refresh=True)
        self.shared_round()
        return {'ok': True}

    # -- every round
    def shared_round(self):
        """After each directory: publish the owner's root, ask about unconfirmed admins, send to approved recipients
        who don't hold the current grant yet, notice who left, and drop shared accounts that are no longer shared."""
        if not self._private():
            return
        groups = self._work_groups()
        self._drop_unshared(groups)
        self._ack_drops()
        for group in groups:
            me = self._me_in(group)
            if not me:
                continue
            self._root(group, me)
            trusted, unconfirmed = self.trust(group)
            if me['member_id'] not in trusted:
                continue
            members = {m['member_id']: m for m in group.get('members', []) if m.get('member_id')}
            for mid in unconfirmed:
                if mid != me['member_id']:
                    self._prompt('admin', group, '', members[mid])
            self._successor(group, me, trusted)
            for service in SHAREABLE:
                try:
                    self._send_round(group, me, trusted, service)
                except Exception as error:  # noqa: BLE001 - away, or a Network from before: the next round tries again
                    LOG.warning('shared connector round fleet=%s error=%s', group['id'], type(error).__name__)

    def _root(self, group, me):
        if me.get('role') != 'owner':
            return
        mine = self.relay.public
        if any(i.get('s', {}).get('t') == 'root' and i['s'].get('member') == me['member_id'] and i['s'].get('key') == mine['sign']
               for i in group.get('trust') or [] if isinstance(i, dict)):
            return
        item = ft.sign(self._private(), ft.statement('root', group['id'], me['member_id'], mine['sign'], me['member_id']))
        try:
            self.relay.call('POST', '/trust', {'fleet': group['id'], 'item': item}, timeout=30)
        except Exception as error:  # noqa: BLE001 - away, or a Network from before: the next round tries again
            LOG.info('fleet root not published error=%s', type(error).__name__)

    def _successor(self, group, me, trusted):
        """The owner names who takes over if they delete their account: the control plane's pick (the longest serving
        admin, else member), once their owner says OK, unless that one is a confirmed admin already."""
        if me.get('role') != 'owner':
            return
        others = [m for m in group.get('members', []) if not m.get('me')]
        heir = next((m for m in others if m.get('role') == 'admin'), others[0] if others else None)
        keys = heir and ft.keys_of(heir)
        if not keys or heir['member_id'] in trusted:
            return
        if any(i.get('s', {}).get('t') == 'successor' and i['s'].get('member') == heir['member_id'] and i['s'].get('key') == keys['sign']
               and i['s'].get('by') == me['member_id'] for i in group.get('trust') or [] if isinstance(i, dict)):
            return
        self._prompt('successor', group, '', heir)

    def _send_round(self, group, me, trusted, service):
        share = self._share_of(group, service)
        held = self._held(group['id'], service)
        if not share or not held or held['shared'].get('grant') != share.get('grant'):
            return
        token = self._token(held)
        if not token:
            return
        state = self._state(group['id'], service)
        approved, sent = state.setdefault('approved', {}), state.setdefault('sent', {})
        declined = set(state.get('declined') or [])
        holders = state.setdefault('holders', {})
        members = {m['member_id']: m for m in group.get('members', []) if m.get('member_id')}
        now_held = set(share.get('held') or [])
        # Who had it and left: their owner should replace the token in Intercom
        for mid in now_held | {share.get('by')}:  # the one who shared it holds it too
            if mid in members and mid != me['member_id']:
                holders[mid] = str(members[mid].get('label') or members[mid].get('name') or '')[:80]
        for mid, name in list(holders.items()):
            if mid not in members:
                self._prompt('replace', group, service, {'member_id': mid, 'name': name})
                holders.pop(mid)
                approved.pop(mid, None)
        # Approvals other confirmed admins signed (their grants on the control plane) count for the key they covered. Kept
        # here once checked, so they still count after that admin leaves; one who left can't add any.
        kept = state.setdefault('vouched', {})
        for item in share.get('grants') or []:
            s = item.get('s') if isinstance(item, dict) else None
            if (isinstance(s, dict) and s.get('t') == 'grant' and s.get('fleet') == group['id'] and s.get('service') == service
                    and s.get('by') in trusted and ft.verified(item, trusted[s['by']]) and isinstance(s.get('to'), str)
                    and s.get('to_fp') not in kept.get(s['to'], [])):
                kept.setdefault(s['to'], []).append(s.get('to_fp'))
        vouched = {mid: set(fps) for mid, fps in kept.items() if mid in members}
        for mid, m in members.items():
            if m.get('me'):
                continue
            keys = ft.keys_of(m)
            if not keys:
                continue
            ok = mid in trusted or approved.get(mid, {}).get('fp') == keys['fp'] or keys['fp'] in vouched.get(mid, ())
            if not ok:
                if mid in approved or mid in vouched:
                    self._prompt('rekey', group, service, m)  # their Machine has new keys: nothing goes until the owner says OK again
                elif mid not in declined:
                    self._prompt('recipient', group, service, m)
                continue
            self._drop_prompts(lambda p: p['fleet'] == group['id'] and p['service'] == service and p['member'] == mid
                               and p['kind'] in ('recipient', 'rekey'))  # another admin said OK
            if mid in now_held:
                continue
            last = sent.get(mid) or {}
            if last.get('grant') == share['grant'] and last.get('fp') == keys['fp'] and _age(last.get('at')) < RESEND_SECONDS:
                continue
            self._send(group, me, service, held, token, share, m, keys)
            sent[mid] = {'grant': share['grant'], 'fp': keys['fp'], 'at': _now()}
        self._set_state(group['id'], service, state)

    def _send(self, group, me, service, held, token, share, member, keys):
        item = ft.grant(self._private(), group['id'], service, held.get(SHAREABLE[service]['identity']), token, share['grant'],
                        member['member_id'], keys['fp'], me['member_id'], _now())
        self.relay.call('POST', '/shared', {'op': 'grant', 'fleet': group['id'], 'service': service, 'item': item}, timeout=30)
        question = {'op': 'share', 'grant': item, 'token': token, 'fleet_name': str(group.get('name') or '')[:80],
                    'account': {k: held.get(k) for k in SHAREABLE[service]['fields'] if held.get(k) is not None}}
        # Straight to the relay, sealed for this one Machine: never through ask(), which keeps questions in fleet_out
        self.relay.call('POST', '/ask', {'fleet': group['id'], 'kind': 'connector', 'question': question, 'to': [member['member_id']],
                                         'request_id': secrets.token_hex(16)})
        LOG.info('shared connector sent fleet=%s service=%s to=%s', group['id'], service, member['member_id'])

    def _drop_unshared(self, groups):
        by_id = {g['id']: g for g in groups}
        gone = []
        for a in self.config.get('accounts', []):
            shared = a.get('shared')
            if not isinstance(shared, dict) or shared.get('origin'):
                continue
            group = by_id.get(shared.get('fleet'))
            share = group and self._share_of(group, a.get('service'))
            if not share or share.get('grant') != shared.get('grant'):
                gone.append(a)
        if gone:
            self.drop_shared([a['id'] for a in gone])
            # Told to the control plane (op dropped), so the Fleet's admins see "Removed, confirmed" after a stop
            acks = self.meta('shared_drop_acks', []) or []
            acks += [{'fleet': a['shared']['fleet'], 'service': a.get('service'), 'grant': a['shared'].get('grant'), 'at': _now()}
                     for a in gone if a['shared'].get('fleet') in by_id and a['shared'].get('grant')]
            self.set_meta('shared_drop_acks', acks[-20:])
        for a in self.config.get('accounts', []):  # an origin account of a Fleet that stopped sharing is plain again
            shared = a.get('shared')
            if isinstance(shared, dict) and shared.get('origin'):
                group = by_id.get(shared.get('fleet'))
                share = group and self._share_of(group, a.get('service'))
                if group and not share:
                    self._save(lambda config, aid=a['id']: self._mark(config, aid, None))

    def _ack_drops(self):
        """Says which shared tokens this Machine deleted; one that doesn't reach the control plane goes again next
        round, for a week at most."""
        left = []
        for ack in self.meta('shared_drop_acks', []) or []:
            if _age(ack.get('at')) > 7 * 86400:
                continue
            try:
                self.relay.call('POST', '/shared', {'op': 'dropped', 'fleet': ack['fleet'], 'service': ack['service'], 'grant': ack['grant']}, timeout=30)
            except Exception as error:  # noqa: BLE001 - away, or a Network from before: next round
                if isinstance(error, OSError) or type(error).__name__ == 'NetworkUnavailable':
                    left.append(ack)  # a refusal (a Network from before, not in the Fleet any more) is not tried again
                LOG.warning('shared drop not confirmed fleet=%s error=%s', ack.get('fleet'), type(error).__name__)
        self.set_meta('shared_drop_acks', left)

    def drop_received(self):
        """The shared accounts this Machine was sent (not one it shares itself), all of them: no Fleet to hold them for."""
        ids = [a['id'] for a in self.config.get('accounts', []) if isinstance(a.get('shared'), dict) and not a['shared'].get('origin')]
        if ids:
            self.drop_shared(ids)
        return ids

    def drop_shared(self, ids):
        """Removes shared accounts and their token files from this Machine."""
        ids = set(ids)
        files = [(self.config.get('credentials') or {}).get(a.get('credential')) for a in self.config.get('accounts', []) if a.get('id') in ids]

        def change(config):
            names = {a.get('credential') for a in config.get('accounts', []) if a.get('id') in ids}
            config['accounts'] = [a for a in config.get('accounts', []) if a.get('id') not in ids]
            still = {a.get('credential') for a in config['accounts']}
            for name in names - still:
                (config.get('credentials') or {}).pop(name, None)
            return True
        self._save(change)
        for path in files:
            if path:
                Path(path).expanduser().unlink(missing_ok=True)
        LOG.info('shared connectors dropped count=%s', len(ids))

    # -- receiving
    def take_connector(self, ask):
        """A shared connector from a Fleet admin's Machine: kept only when everything checks out (module docstring)."""
        status, answer = 'declined', None
        try:
            answer = self._accept(ask)
            status = 'answered'
        except ft.GrantError as error:
            LOG.warning('shared connector refused member=%s reason=%s', ask.get('from'), error)
        self.relay.call('POST', '/answer', {'id': ask['id'], 'status': status, **({'answer': answer} if answer else {})})
        return status

    def _accept(self, ask):
        question = ask.get('question')
        if not isinstance(question, dict) or question.get('op') != 'share':
            raise ft.GrantError('not sealed or not a share')  # anything unsealed arrives here empty
        group = self._group(ask.get('fleet_id') or ask.get('fleet')) or (self.directory(refresh=True) and self._group(ask.get('fleet_id') or ask.get('fleet')))
        me = group and self._me_in(group)
        if not group or group.get('kind') != 'work' or not me:
            raise ft.GrantError('not a Work Fleet of this Machine')
        item, token = question.get('grant'), question.get('token')
        service = (item or {}).get('s', {}).get('service') if isinstance(item, dict) and isinstance(item.get('s'), dict) else None
        if service not in SHAREABLE or not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ft.GrantError('not a connector that can be shared')
        trusted, _ = self.trust(group)
        if ask.get('from') not in trusted:
            raise ft.GrantError('the sender is not a confirmed admin')
        hints = question.get('account') if isinstance(question.get('account'), dict) else {}
        s = ft.check_grant(item, token, group['id'], service, me['member_id'], self._my_fp(), trusted)
        if s['by'] != ask.get('from'):
            raise ft.GrantError('the grant is not the sender\'s')
        if hints.get(SHAREABLE[service]['identity']) not in (None, s['workspace']):
            raise ft.GrantError('another workspace')
        share = self._share_of(group, service)
        if not share or share.get('grant') != s['grant']:
            raise ft.GrantError('that grant was replaced or revoked')
        held = self._held(group['id'], service)
        if held and held['shared'].get('at', '') > s['at']:
            raise ft.GrantError('older than the grant this Machine holds')
        if held and held['shared'].get('origin'):
            return {'ok': True, 'grant': s['grant']}  # this Machine shares it itself
        self._keep(group, service, s, token, hints, held)
        try:
            self.relay.call('POST', '/shared', {'op': 'held', 'fleet': group['id'], 'service': service, 'grant': s['grant']}, timeout=30)
        except Exception as error:  # noqa: BLE001 - only the Fleet page's "who holds it" waits for it
            LOG.info('shared connector held not told error=%s', type(error).__name__)
        return {'ok': True, 'grant': s['grant']}

    def _keep(self, group, service, s, token, hints, held):
        short = _short(group['id'])
        name = f'shared-{service}-{short}'
        base = Path(self.config['database']).expanduser().parent if self.config.get('database') else Path.home()
        path = Path((self.config.get('credentials') or {}).get(name) or base / f'{name}.env').expanduser()
        _write_private(path, token + '\n')
        fleet_name = str(group.get('name') or '')[:80]
        account = {'id': held['id'] if held else f'{service}-{short}', 'service': service, 'transport': 'api',
                   'boundary': 'work:' + (fleet_name or 'Fleet'), 'credential': name, 'poll_seconds': 600,
                   **{k: str(hints[k])[:100] for k in SHAREABLE[service]['fields'] if isinstance(hints.get(k), (str, int))},
                   SHAREABLE[service]['identity']: s['workspace'],
                   'shared': {'fleet': group['id'], 'fleet_name': fleet_name, 'service': service, 'grant': s['grant'], 'at': s['at'], 'by': s['by']}}

        def change(config):
            config.setdefault('credentials', {})[name] = str(path)
            taken = {a.get('id') for a in config.get('accounts', [])}
            if not held and account['id'] in taken:
                account['id'] = f'{service}-{short}-{secrets.token_hex(2)}'
            config['accounts'] = [a for a in config.get('accounts', []) if a.get('id') != account['id']] + [account]
            return True
        self._save(change)
        LOG.info('shared connector kept fleet=%s service=%s', group['id'], service)

    def connector_answered(self, item):
        """An answer to a shared connector send: nothing to keep, the control plane hears who holds it from them."""
        return item.get('kind') == 'connector'

    # -- the Fleet tab
    def shared_view(self):
        prompts = [{k: p.get(k) for k in ('id', 'kind', 'fleet', 'fleet_name', 'service', 'name', 'email', 'fp', 'at')} for p in self._prompts()]
        shares = []
        for group in self._work_groups():
            me = self._me_in(group)
            if not me:
                continue
            trusted, unconfirmed = ft.admins(group, self.meta(self._pins_key(group['id'])))[:2]
            names = {m['member_id']: str(m.get('label') or m.get('name') or '') for m in group.get('members', [])}
            for s in group.get('shared') or []:
                if not isinstance(s, dict) or s.get('service') not in SHAREABLE:
                    continue
                held = self._held(group['id'], s['service'])
                shares.append({'fleet': group['id'], 'fleet_name': group.get('name'), 'service': s['service'], 'label': SHAREABLE[s['service']]['label'],
                               'workspace': s.get('name'), 'by': names.get(s.get('by'), ''), 'held_by': [names[m] for m in s.get('held') or [] if m in names],
                               'mine': bool(held and held['shared'].get('origin')), 'have': bool(held and held['shared'].get('grant') == s.get('grant')),
                               'admin': me['member_id'] in trusted})
            if unconfirmed and me['member_id'] in trusted:
                shares.append({'fleet': group['id'], 'fleet_name': group.get('name'), 'unconfirmed': [names.get(m, '') for m in unconfirmed]})
        candidates = []
        for group in self._work_groups():
            me = self._me_in(group)
            trusted = ft.admins(group, self.meta(self._pins_key(group['id'])))[0] if me else {}
            if me and me['member_id'] in trusted:
                candidates.append({'fleet': group['id'], 'name': group.get('name'),
                                   'members': [{'member_id': m['member_id'], 'name': str(m.get('label') or m.get('name') or ''),
                                                'email': m.get('account_email') or '', 'role': m.get('role'), 'keys': bool(ft.keys_of(m)),
                                                'confirmed': m['member_id'] in trusted}
                                               for m in group.get('members', []) if not m.get('me')]})
        own = [{'id': a['id'], 'service': a['service'], 'label': SHAREABLE[a['service']]['label'], 'workspace': a.get('workspace_name') or ''}
               for a in self.config.get('accounts', []) if a.get('service') in SHAREABLE and not a.get('shared') and a.get(SHAREABLE[a['service']]['identity'])]
        try:  # the Shared Graph: whether this Machine's Work is shared, asked off or off, per Work Fleet (shared_graph.py)
            graph = self.sharing() if hasattr(self, 'sharing') else []
        except (ValueError, KeyError, TypeError, OSError) as error:
            LOG.info('shared graph view error=%s', type(error).__name__)
            graph = []
        return {'prompts': prompts, 'shares': shares, 'can_share': candidates, 'accounts': own, 'graph': graph}


def _write_private(path, text):
    import onboarding
    onboarding.write_private(path, text)
