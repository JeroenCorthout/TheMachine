"""The sandbox's teammates' part of the Shared Graph (shared_graph.py): Priya's and Tomasz's Machines publish their Work
records, timelines and a client thread, sealed for this Machine, once this Machine's admin key reached them. All of it is
invented, like the rest of the fixture; nothing leaves this machine."""
import datetime as dt
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import network_crypto as nc  # noqa: E402
import shared_graph as sg  # noqa: E402


def ago(days, hours=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)).isoformat(timespec='seconds')


def person(key, rid, name, email, role, org, domain, score, days, lines, threads=()):
    return {'r': rid, 't': 'person', 'name': name, 'role': role, 'org': {'name': org, 'd': sg.keyed(key, 'domain:' + domain)},
            'ids': [sg.keyed(key, 'email:' + email)], 'd': [sg.keyed(key, 'domain:' + domain)], 'score': score, 'last': ago(days)[:10],
            'timeline': [{'at': ago(d), 'line': line} for d, line in lines], 'threads': list(threads)}


def project(key, rid, name, board, stages, stage, days, why, next_step, org, domain, score, lines=(), hand=False):
    return {'r': rid, 't': 'project', 'name': name, 'board': board, 'stages': stages, 'stage': stage, 'stage_hand': hand, 'stage_at': ago(days),
            'why': why, 'next': next_step, 'org': {'name': org, 'd': sg.keyed(key, 'domain:' + domain)}, 'people': [], 'score': score,
            'last': ago(days)[:10], 'timeline': [{'at': ago(d), 'line': line} for d, line in lines], 'threads': []}


PARTNERS = ['Contacted', 'Agreed', 'In review', 'Partly live', 'Live', 'Stalled or declined']
PIPELINE = ['Qualified', 'Proposal', 'Negotiation', 'Won', 'Stalled or declined']


PLAN_TAG = secrets.token_hex(16)
PLAN = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj '
        b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 120]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj '
        b'4 0 obj<</Length 52>>stream\nBT /F1 14 Tf 20 60 Td (Rollout plan: Ghent, then Antwerp) Tj ET\nendstream endobj '
        b'5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n')  # made up


def shared(key):
    """{member id: (records, threads)}: what each teammate's Machine shares."""
    rid = lambda: secrets.token_hex(16)
    rollout = secrets.token_hex(16)
    priya = [
        person(key, rid(), 'Mila Underhay', 'mila@northwindanalytics.example', 'Head of operations', 'Northwind Analytics', 'northwindanalytics.example',
               6.2, 1, [(1, 'Mila confirmed the rollout starts with the Ghent team'), (4, 'Priya sent the training schedule'),
                        (9, 'Mila asked for SSO before the rollout')], [rollout]),
        person(key, rid(), 'Kofi Hollowell', 'kofi@harborlinehealth.example', 'IT lead', 'Harborline Health', 'harborlinehealth.example',
               3.1, 3, [(3, 'Kofi asked for the security questionnaire'), (12, 'Kofi joined the pilot call')]),
        person(key, rid(), 'Rosalind Achterberg', 'rosalind@marigoldtravel.example', 'Founder', 'Marigold Travel', 'marigoldtravel.example',
               2.4, 2, [(2, 'Rosalind wants a demo for her team next week'), (6, 'Rosalind found us through the partner page')]),
        {'r': rid(), 't': 'org', 'name': 'Marigold Travel', 'role': 'Prospect', 'd': sg.keyed(key, 'domain:marigoldtravel.example'), 'score': 2.4,
         'last': ago(2)[:10]},
        project(key, rid(), 'Lanternfish', 'Partner listings', PARTNERS, 'Agreed', 2, 'Lanternfish said yes to a listing swap', 'Send them our listing copy',
                'Lanternfish', 'lanternfish.example', 1.8, [(2, 'Lanternfish said yes to a listing swap')]),
        project(key, rid(), 'Marigold Travel', 'Enterprise pipeline', PIPELINE, 'Qualified', 2, 'Rosalind asked for a demo', 'Book the demo',
                'Marigold Travel', 'marigoldtravel.example', 2.4),
    ]
    priya_threads = {rollout: {'tag': rollout, 'subject': 'Rollout timeline', 'at': ago(1), 'messages': [
        {'at': ago(2), 'from': 'Priya Okonedo <priya@lumenlabs.example>', 'to': 'mila@northwindanalytics.example', 'cc': '',
         'body': 'Hi Mila,\n\nHere is the plan we discussed: training for the Ghent team on Monday, Antwerp the week after. SSO will be on before the first session.\n\nPriya',
         'attachments': [{'name': 'rollout-plan.pdf', 'size': len(PLAN), 'mime': 'application/pdf', 'file': PLAN_TAG},
                         {'name': 'training-recording.mp4', 'size': 48_000_000, 'mime': 'video/mp4', 'too_large': True}]},
        {'at': ago(1), 'from': 'Mila Underhay <mila@northwindanalytics.example>', 'to': 'priya@lumenlabs.example', 'cc': '',
         'body': 'Thanks Priya, that works. Ghent first is confirmed. Could you send the calendar invites?\n\nMila', 'attachments': []}]}}
    private_tag = secrets.token_hex(16)
    tomasz = [
        person(key, rid(), 'Ayla Valemont', 'ayla@kestrelenergy.example', 'Procurement manager', 'Kestrel Energy', 'kestrelenergy.example',
               4.0, 1, [(1, 'Ayla asked for a 3 year price'), (5, 'Tomasz sent the proposal'),
                        (8, 'Private thread with Ayla, ' + dt.datetime.now(dt.timezone.utc).strftime('%b %-d'))]),
        person(key, rid(), 'Mila Underhay', 'mila@northwindanalytics.example', 'Head of operations', 'Northwind Analytics', 'northwindanalytics.example',
               1.2, 6, [(6, 'Mila asked about invoicing in euros')]),
        project(key, rid(), 'Kestrel Energy', 'Enterprise pipeline', PIPELINE, 'Negotiation', 1, 'Ayla asked for a 3 year price', 'Send the 3 year price',
                'Kestrel Energy', 'kestrelenergy.example', 4.0),
        project(key, rid(), 'Northwind rollout', 'Enterprise pipeline', PIPELINE, 'Won', 3, 'Mila signed the order form', 'Plan the training',
                'Northwind Analytics', 'northwindanalytics.example', 1.2),
        project(key, rid(), 'Bramblebox', 'Partner listings', PARTNERS, 'Agreed', 1, 'Bramblebox agreed to list us next month', 'Send the logo and copy',
                'Bramblebox', 'bramblebox.example', 0.9),
    ]
    tomasz_threads = {private_tag: {'tag': private_tag, 'private': True, 'at': ago(8), 'line': 'Private thread with Ayla, ' + (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).strftime('%b %-d')}}
    return {'m_team_priya': (priya, priya_threads), 'm_team_tomasz': (tomasz, tomasz_threads)}


def extras(member):
    """Beyond records and mail: Priya's rollout plan as a file, and Tomasz asking the admins to stop sharing his Work."""
    if member == 'm_team_priya':
        return [('file', {'name': 'rollout-plan.pdf', 'mime': 'application/pdf', 'data': sg.nc.b64(PLAN)}, {'tag': PLAN_TAG})]
    if member == 'm_team_tomasz':
        return [('ask', {'off': True, 'asked': ago(0, 2)}, {'tag': secrets.token_hex(16)})]
    return []


def blobs(network, fleet, me, key):
    """The teammates' blobs, sealed for this Machine with their own Machines' keys: a snapshot and their mail each."""
    out = []
    for member, (records, threads) in shared(key).items():
        private = network.peer(member)
        base = int(time.time() * 1000)
        for n, (kind, body, extra) in enumerate([('snapshot', {'kv': None, 'records': records, 'threads': sorted(threads)}, {})]
                                                + [('mail', t, {'tag': tag}) for tag, t in threads.items()] + extras(member)):
            header = {'v': 1, 'fleet': fleet, 'from': member, 'id': secrets.token_hex(16), 'seq': base + n, 'kind': kind, 'at': sg.now(), 'to': [me], **extra}
            blob, _ = sg.make(private, header, body, {me: network.keys})
            out.append({'header': blob['header'], 'ct': blob['ct'], 'sig': blob['sig'], 'wrap': blob['wraps'][me], 'by': member})
    return out


def open_key(network, blob, me):
    """A graph key this Machine sealed for a simulated teammate, opened as their Machine would."""
    header = blob['header']
    for member, wrap in blob['wraps'].items():
        if member in network.peers:
            got = sg.open_blob({**blob, 'wrap': wrap, 'by': header['from']}, network.peer(member), member, network.keys, network.keys['sign'])
            return got[1].get('key')
    return None
