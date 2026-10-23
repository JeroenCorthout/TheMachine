"""Synthetic test data for the sandbox: made-up people, companies, signals, cards, promises and a Network, shaped
like a real install (sandbox/profile.json: counts only) but with nothing real in it. Every address is on .example,
every name is invented, and check_fixture.py fails if any real identifier ever slips in.

    python3 sandbox/fixture.py > sandbox/fixture.json      # deterministic: the same file every time

Times are minutes before "now", so a sandbox loaded today looks like today."""
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261020

OWNER = {'name': 'Alex Morgan', 'email': 'alex@lumenlabs.example', 'company': 'Lumen Labs', 'domain': 'lumenlabs.example',
         'personal_email': 'alex.morgan@mail.example', 'username': 'alex-morgan'}
TEAM = [{'member_id': 'm_team_priya', 'name': 'Priya Okonedo', 'email': 'priya@lumenlabs.example', 'role': 'member', 'machine': True},
        {'member_id': 'm_team_tomasz', 'name': 'Tomasz Varga', 'email': 'tomasz@lumenlabs.example', 'role': 'member', 'machine': True},
        {'member_id': 'm_team_ines', 'name': 'Ines Calloway', 'email': 'ines@lumenlabs.example', 'role': 'member', 'machine': False}]
FAMILY = [{'member_id': 'm_fam_robin', 'name': 'Robin Morgan', 'email': 'robin@mail.example', 'role': 'member', 'machine': True},
          {'member_id': 'm_fam_jules', 'name': 'Jules Morgan', 'email': 'jules@mail.example', 'role': 'member', 'machine': True}]
FRIENDS = [{'member_id': 'm_fr_sam', 'name': 'Sam Whitlock', 'username': 'sam-whitlock'},
           {'member_id': 'm_fr_dana', 'name': 'Dana Kerrigan', 'username': 'dana-k'}]
FRIEND_REQUEST_IN = {'name': 'Lee Fontaine', 'username': 'lee-fontaine'}
FRIEND_REQUEST_OUT = 'jordan-pell'

COMPANIES = ['Northwind Analytics', 'Brightpath Logistics', 'Cobalt & Finch', 'Harborline Health', 'Quarry Lane Studios',
             'Tidewater Foods', 'Meridian Robotics', 'Pinecrest Legal', 'Saltmarsh Media', 'Vantage Rail',
             'Orchard Street Bakery', 'Kestrel Energy', 'Glasswing Apps', 'Fernhill Schools', 'Ironbark Outdoor']
FIRST = ['Mara', 'Jonah', 'Elif', 'Bram', 'Noor', 'Teodor', 'Ayla', 'Caspian', 'Wren', 'Idris', 'Sunniva', 'Oskar',
         'Leontine', 'Rafe', 'Zuzana', 'Matteo', 'Hanne', 'Kofi', 'Liesel', 'Anouk', 'Thibault', 'Yara', 'Dorian', 'Mila']
LAST = ['Ashgrove', 'Brightwater', 'Coldwell', 'Dunmore', 'Everly', 'Fairhaven', 'Greystone', 'Hollowell', 'Ingleby',
        'Juniper', 'Kettering', 'Larkspur', 'Merrow', 'Northcott', 'Oakhurst', 'Pemberley', 'Quillon', 'Redfern',
        'Stanhope', 'Thistlewood', 'Underhay', 'Valemont', 'Westerby', 'Yarrowby']
PROJECTS = ['Northwind rollout', 'Pricing page refresh', 'Q4 partner program', 'Harborline pilot', 'Mobile app beta', 'Hiring: support lead']

ACCOUNTS = [  # the owner's sources, as in a config; ids say what they are, never whose
    {'id': 'work-gmail', 'service': 'gmail', 'transport': 'gog', 'email': OWNER['email'], 'client': 'default', 'boundary': 'work:Lumen Labs'},
    {'id': 'work-calendar', 'service': 'calendar', 'transport': 'gog', 'email': OWNER['email'], 'client': 'default', 'calendar_id': 'primary', 'boundary': 'work:Lumen Labs'},
    {'id': 'work-slack', 'service': 'slack', 'transport': 'maton', 'email': OWNER['email'], 'display_name': 'Lumen Labs Slack', 'boundary': 'work:Lumen Labs'},
    {'id': 'work-intercom', 'service': 'intercom', 'transport': 'api', 'email': OWNER['email'], 'display_name': 'Intercom', 'boundary': 'work:Lumen Labs', 'poll_seconds': 600},
    {'id': 'work-crm', 'service': 'salesflare', 'transport': 'api', 'email': OWNER['email'], 'display_name': 'CRM', 'boundary': 'work:Lumen Labs', 'poll_seconds': 900},
    {'id': 'work-stripe', 'service': 'stripe', 'transport': 'api', 'email': OWNER['email'], 'display_name': 'Stripe', 'boundary': 'work:Lumen Labs', 'poll_seconds': 900},
    {'id': 'work-github', 'service': 'github', 'transport': 'api', 'email': OWNER['email'], 'display_name': 'GitHub', 'boundary': 'work:Lumen Labs', 'poll_seconds': 900},
    {'id': 'work-linkedin', 'service': 'linkedin', 'transport': 'browser', 'email': OWNER['email'], 'display_name': 'LinkedIn', 'boundary': 'work:Lumen Labs', 'poll_seconds': 1800},
    {'id': 'work-x', 'service': 'x', 'transport': 'browser', 'email': OWNER['email'], 'display_name': '@lumenlabs on X', 'boundary': 'work:Lumen Labs', 'poll_seconds': 1800},
    {'id': 'home-gmail', 'service': 'gmail', 'transport': 'gog', 'email': OWNER['personal_email'], 'client': 'default', 'boundary': 'personal'},
    {'id': 'home-calendar', 'service': 'calendar', 'transport': 'gog', 'email': OWNER['personal_email'], 'client': 'default', 'calendar_id': 'primary', 'boundary': 'personal'},
    {'id': 'home-whatsapp', 'service': 'whatsapp', 'transport': 'wacli', 'email': '', 'display_name': 'WhatsApp', 'boundary': 'personal'},
    {'id': 'home-phone', 'service': 'android', 'transport': 'openclaw-feed', 'email': '', 'display_name': 'Phone notifications', 'boundary': 'personal', 'node_id': 'sandbox-phone'},
]
KIND_ACCOUNT = {  # profile key -> (account, event kind)
    'gmail gmail.message': ('work-gmail', 'gmail.message'), 'android android.notification': ('home-phone', 'android.notification'),
    'whatsapp whatsapp.message': ('home-whatsapp', 'whatsapp.message'), 'slack slack.message': ('work-slack', 'slack.message'),
    'crm salesflare.email': ('work-crm', 'salesflare.email'), 'intercom intercom.conversation': ('work-intercom', 'intercom.conversation'),
    'linkedin linkedin.message': ('work-linkedin', 'linkedin.message'), 'calendar calendar.event': ('work-calendar', 'calendar.event'),
    'x x.mention': ('work-x', 'x.mention'), 'stripe stripe.event': ('work-stripe', 'stripe.event'),
    'crm salesflare.meeting': ('work-crm', 'salesflare.meeting'), 'github github.notification': ('work-github', 'github.notification'),
}

EMAIL_TOPICS = [
    ('Pricing for {n} seats', 'Hi {owner},\n\nWe are comparing three tools for our sales team of {n}. Could you send pricing for annual billing, and tell me whether onboarding is included? We would like to decide before the end of the month.\n\nThanks,\n{first}'),
    ('Re: Demo next week', 'Hi {owner},\n\nThursday at 14:00 works for us. I will bring {other} from operations. Could you show how the reminders work for shared inboxes?\n\nBest,\n{first}'),
    ('Invoice {inv} overdue', 'Hello,\n\nOur records show invoice {inv} is 14 days overdue. Could you let us know when we can expect payment, or whether something is wrong with it?\n\nKind regards,\n{first}\nAccounts, {company}'),
    ('Partnership idea', 'Hi {owner},\n\nI run partnerships at {company}. Our customers keep asking for a CRM integration and yours came up three times this month. Open to a 20 minute call to explore?\n\n{first}'),
    ('Contract redlines', 'Hi {owner},\n\nAttached are our legal team\'s redlines. Two points matter most to us: the liability cap in section 9 and the data retention period. The rest is cosmetic.\n\nCheers,\n{first}'),
    ('Quick question about the API', 'Hey {owner},\n\nIs there a rate limit on the contacts endpoint? Our sync job hits a 429 around 3,000 requests. Logs: https://status.{domain}/incidents/2026/10/very-long-path-to-check-wrapping-of-unbroken-links-in-cards-and-previews\n\n{first}'),
    ('Intro: {first} <> {owner}', 'Hi both,\n\nAs promised, connecting you two. {first} leads growth at {company} and is looking at tooling for their account managers. I will let you take it from here.\n\nBest,\n{other}'),
    ('Following up on our call', 'Hi {owner},\n\nThanks for the time today. As discussed: a pilot for 10 users starting the 3rd, success criteria by Friday, and a check-in after two weeks. Did I miss anything?\n\n{first}'),
]
SLACK = [('#sales', 'Can someone take the {company} renewal call tomorrow? I am out.'), ('#support', 'Heads up: {company} reports duplicate reminders since this morning.'),
         ('#product', 'Draft spec for bulk actions is in the doc, comments welcome before Thursday.'), ('#general', 'Lunch order closes at 11:30 today.'),
         ('Direct message', '{owner}, did you see the email from {first} at {company}? They want an answer today.'),
         ('#sales', 'Won {company} 🎉 47 seats, annual.'), ('#support', 'Who knows the history with {company}? Their admin is asking about an old discount.')]
INTERCOM = ['How do I export all contacts with their custom fields?', 'We were charged twice this month, can you check?',
            'Is there a way to bulk assign accounts to a teammate?', 'The Outlook add-in shows a blank panel after the update.',
            'Can we get an extension of our trial for two more weeks?', 'Does your API support webhooks for deal stage changes?']
WHATSAPP = [('Morgan family', 'Who picks up the kids on Wednesday? I have a dentist appointment at 4.'), ('Morgan family', 'Groceries: oat milk, apples, bread, birthday candles 🎂'),
            ('Sam Whitlock', 'Tennis Saturday at 10? Court 3 is free.'), ('Dana Kerrigan', 'Sending you the photos from the weekend tonight'),
            ('Football parents', 'Match moved to 9:30, bring the orange shirts'), ('Robin Morgan', 'Can you book the car service before Friday?')]
ANDROID = [('Bank', 'Card payment of €42.10 at Orchard Street Bakery'), ('Parcel', 'Your parcel arrives today between 13:00 and 15:00'),
           ('Calendar', 'Pilot check-in in 15 minutes'), ('Train', 'Your 08:12 train is 6 minutes late'), ('Weather', 'Rain expected from 16:00')]


# Boards (Graph › Projects, Board): projects of one kind in stages, each moved by a message. Made up like the rest.
# Per project: name, domain, contact, the messages (days ago, from the owner?, what it says) and its moves
# (stage, why, the message that shows it). The last move is where it stands; next is the owner's next step.
D = 24 * 60
BOARDS = [
    {'name': 'Partner listings', 'stages': [{'name': 'Contacted', 'hint': 'Asked or applied, no yes yet'}, {'name': 'Agreed', 'hint': 'They said yes'},
                                            {'name': 'In review', 'hint': 'Copy or listing with one side'}, {'name': 'Partly live', 'hint': 'Some of it is up'},
                                            {'name': 'Live', 'hint': 'Everything agreed is up'}],
     'projects': [
         ('Paperkite', 'paperkite.example', 'Ottilie Brandt', [(1, False, 'They want to join our integrations page and offer a link back')],
          [('Contacted', 'Ottilie asked to join our integrations page', 0)], 'Decide if they fit the page'),
         ('Bramblebox', 'bramblebox.example', 'Hugo Penrose', [(52, True, 'Asked for a listing on their marketplace'), (38, True, 'Nudged about the listing'), (21, True, 'Nudged again')],
          [('Contacted', 'asked for a listing on their marketplace', 0)], 'Find another contact or drop it'),
         ('Quillfeather', 'quillfeather.example', 'Esme Varley', [(30, True, 'Asked for an exchange of reviews'), (3, False, 'Still interested, will apply through the partner form this week')],
          [('Contacted', 'asked for an exchange of reviews', 0)], 'Approve their partner application when it comes'),
         ('Otterly Mail', 'otterlymail.example', 'Rowan Achebe', [(46, True, 'Proposed a listing swap'), (31, True, 'Followed up on the swap')],
          [('Contacted', 'proposed a listing swap', 0)], 'Follow up after Fri, or drop it'),
         ('Fernwave', 'fernwave.example', 'Ilse Moreau', [(44, True, 'Asked to be listed in their app directory'), (29, True, 'Nudged twice')],
          [('Contacted', 'asked to be listed in their app directory', 0)], 'Find another contact or drop it'),
         ('Copperleaf Apps', 'copperleafapps.example', 'Teo Lindqvist', [(6, True, 'Sent our listing request')],
          [('Contacted', 'sent our listing request', 0)], 'Wait for their answer'),
         ('Puffin Metrics', 'puffinmetrics.example', 'Nadia Ferro', [(12, True, 'Asked for a co-marketing listing'), (11, False, 'Thanks, passing this to our partner lead')],
          [('Contacted', 'asked for a co-marketing listing', 0)], 'Wait for their partner lead'),
         ('Tinker Lane', 'tinkerlane.example', 'Pia Okafor', [(16, True, 'Asked about a directory entry')],
          [('Contacted', 'asked about a directory entry', 0)], 'Nudge Pia once'),
         ('Hazelbrook Software', 'hazelbrook.example', 'Felix Amari', [(9, True, 'Proposed an editorial exchange'), (1, False, 'Yes, let us do both entries')],
          [('Contacted', 'proposed an editorial exchange', 0), ('Agreed', 'Felix said yes to both entries', 1)], 'Write both entries'),
         ('Driftwood Docs', 'driftwooddocs.example', 'Saskia Holm', [(14, True, 'Asked for a listing'), (4, False, 'Happy to list you, send your copy')],
          [('Contacted', 'asked for a listing', 0), ('Agreed', 'Saskia asked for our copy', 1)], 'Send them our copy'),
         ('Pebblepath', 'pebblepath.example', 'Arlo Svensson', [(20, True, 'Sent the exchange proposal'), (8, False, 'Agreed, here is our draft'), (0, False, 'Left comments on your draft too')],
          [('Contacted', 'sent the exchange proposal', 0), ('Agreed', 'Arlo agreed and sent their draft', 1), ('In review', 'Arlo commented on our draft', 2)], 'Wait for their edits, then publish'),
         ('Larkfield Labs', 'larkfieldlabs.example', 'Mina Castell', [(18, True, 'Asked for a listing'), (7, False, 'Yes, send copy'), (2, True, 'Sent copy for both sides')],
          [('Contacted', 'asked for a listing', 0), ('Agreed', 'Mina said yes', 1), ('In review', 'sent copy for both, no comments yet', 2)], 'Get their comments, then publish both'),
         ('Willowtick', 'willowtick.example', 'Jasper Nkemelu', [(25, True, 'Asked for a listing'), (12, False, 'Our page is live, yours next'), (9, True, 'Thanks, ours follows')],
          [('Contacted', 'asked for a listing', 0), ('Partly live', 'their entry for us is live, ours not yet', 1)], 'Publish our entry for them'),
         ('Saffron Sync', 'saffronsync.example', 'Lotte Ahrens', [(33, True, 'Proposed a swap'), (15, False, 'Prices fixed, the FAQ still has the old plan')],
          [('Contacted', 'proposed a swap', 0), ('Partly live', 'prices fixed, the FAQ still wrong', 1)], 'Nudge Lotte about the FAQ'),
         ('Moorhen Data', 'moorhendata.example', 'Cyrus Bell', [(40, True, 'Asked for a listing'), (8, False, 'All three fixed, have a look')],
          [('Contacted', 'asked for a listing', 0), ('Partly live', 'Cyrus fixed two of the three', 1)], 'Nothing'),
         ('Juniper Ledger', 'juniperledger.example', 'Freya Lund', [(60, True, 'Asked for a listing'), (18, False, 'Listing approved and updated')],
          [('Contacted', 'asked for a listing', 0), ('Live', 'listing updated and approved', 1)], 'Suggest another swap'),
         ('Nimbus & Nook', 'nimbusandnook.example', 'Eliot Grange', [(22, True, 'Asked for a listing'), (2, False, 'Sorry, we only list paying partners')],
          [('Contacted', 'asked for a listing', 0), ('Stalled or declined', 'they only list paying partners', 1)], 'Nothing unless they come back'),
         ('Lanternfish', 'lanternfish.example', 'Beatrix Oyelaran', [(10, True, 'Asked for a listing'), (9, False, 'Address not found')],
          [('Contacted', 'asked for a listing', 0), ('Stalled or declined', 'the only address bounced', 1)], 'Find a working contact or drop it'),
     ]},
    {'name': 'Customer onboarding', 'stages': [{'name': 'Kickoff', 'hint': 'Signed, first call planned'}, {'name': 'Setting up', 'hint': 'Import and settings'},
                                               {'name': 'Training', 'hint': 'Teams learning it'}, {'name': 'Live', 'hint': 'Using it every day'}],
     'projects': [
         ('Thornbury Freight onboarding', 'thornburyfreight.example', 'Maeve Dalton', [(3, False, 'Signed, can we plan the kickoff?')],
          [('Kickoff', 'Maeve signed and asked for a kickoff', 0)], 'Plan the kickoff call'),
         ('Gullwing Clinics onboarding', 'gullwingclinics.example', 'Ravi Castellan', [(9, False, 'Kickoff done, import file attached'), (2, True, 'Import is running')],
          [('Kickoff', 'signed', 0), ('Setting up', 'Ravi sent the import file', 0)], 'Check the import tomorrow'),
         ('Stonefly Studio trial', 'stoneflystudio.example', 'Noemi Hart', [(14, False, 'Started the trial'), (5, False, 'Booked training for the team')],
          [('Kickoff', 'started the trial', 0), ('Training', 'Noemi booked training for the team', 1)], 'Run the training on Tue'),
         ('Brackenridge Homes rollout', 'brackenridge.example', 'Owen Mbeki', [(40, False, 'Signed'), (6, False, 'Everyone is on it now')],
          [('Kickoff', 'signed', 0), ('Live', 'Owen says everyone uses it', 1)], 'Ask for a review'),
     ]},
    {'name': 'Support escalations', 'stages': [{'name': 'Open'}, {'name': 'Waiting on us'}, {'name': 'Waiting on them'}, {'name': 'Solved'}],
     'projects': [
         ('Kittiwake export bug', 'kittiwake.example', 'Signe Paulsen', [(2, False, 'The export drops custom fields')],
          [('Open', 'Signe reported missing custom fields', 0)], 'Reproduce it'),
         ('Halyard sync delay', 'halyard.example', 'Desmond Oduya', [(5, False, 'Sync takes hours'), (4, True, 'Asked for their logs')],
          [('Open', 'Desmond reported slow sync', 0), ('Waiting on them', 'asked for their logs', 1)], 'Wait for the logs'),
     ]},
]


BY_HAND = {'Moorhen Data': ('Live', 1)}  # the owner dragged it there, a day ago


def board_data():
    """Boards, their projects, the messages behind each move and one open card on a project waiting for a reply."""
    events, boards, cards = [], [], []
    for b in BOARDS:
        board = {'name': b['name'], 'stages': b['stages'], 'projects': []}
        for name, domain, contact, msgs, moves, step in b['projects']:
            first, key = contact.split()[0], slug(name)
            address = f'{first.lower()}@{domain}'
            keys = []
            for i, (days_ago, mine, text) in enumerate(msgs):
                k, thread = f'board:{key}:{i}', f'tb{key}'
                minutes = days_ago * D + 90 + i * 7
                body = (f'Hi {first},\n\n{text}.\n\nBest,\nAlex' if mine else f'Hi Alex,\n\n{text}.\n\n{first}')
                events.append({'key': k, 'account_id': 'work-gmail', 'kind': 'gmail.message', 'minutes_ago': minutes, 'title': name.split(' ')[0] + ' and Lumen Labs',
                               'body': body, 'metadata': {'from': f"{OWNER['name']} <{OWNER['email']}>" if mine else f'{contact} <{address}>',
                                                          'to': address if mine else OWNER['email'], 'labels': ['SENT'] if mine else ['INBOX'],
                                                          'thread_id': thread, 'message-id': f'<{thread}.{i}@{domain}>',
                                                          'source_url': f'https://mail.example/#inbox/{thread}'}})
                keys.append(k)
            board['projects'].append({'name': name, 'org': domain, 'org_name': name.split(' onboarding')[0].split(' trial')[0].split(' rollout')[0].split(' export')[0].split(' sync')[0],
                                      'events': keys, 'moves': [{'stage': st, 'reason': why, 'event': keys[i]} for st, why, i in moves], 'next': step,
                                      **({'by_hand': {'stage': BY_HAND[name][0], 'days_ago': BY_HAND[name][1]}} if name in BY_HAND else {})})
        boards.append(board)
    cards.append({'key': 'board-bramblebox', 'title': 'Follow up with Hugo at Bramblebox: no reply in 52 days', 'body': 'You asked for a listing and nudged twice; nothing back.',
                  'status': 'pending', 'events': ['board:bramblebox:2'], 'accounts': ['work-gmail'], 'minutes_ago': 300, 'source': 'email to Hugo Penrose',
                  'project': 'Bramblebox', 'urgent': False, 'snooze_hours': None})
    return events, boards, cards


def person(rng, used):
    while True:
        name = rng.choice(FIRST) + ' ' + rng.choice(LAST)
        if name not in used:
            used.add(name)
            return name


def slug(text):
    return ''.join(c for c in text.lower().replace(' ', '') if c.isalnum())


def build(profile):
    rng = random.Random(SEED)
    used = set()
    companies = [{'name': c, 'domain': slug(c.replace('&', 'and')) + '.example'} for c in COMPANIES]
    people = []
    for company in companies:
        for _ in range(rng.choice((1, 2, 2, 3))):
            name = person(rng, used)
            first = name.split()[0]
            people.append({'name': name, 'email': f"{first.lower()}@{company['domain']}", 'company': company['name'], 'domain': company['domain']})
    events = []
    days = 7

    def add(account, kind, minutes, title, body, meta, key):
        events.append({'key': key, 'account_id': account, 'kind': kind, 'minutes_ago': minutes, 'title': title, 'body': body, 'metadata': meta})

    for pkey, rate in profile['signals_per_day'].items():
        if pkey not in KIND_ACCOUNT:
            continue
        account, kind = KIND_ACCOUNT[pkey]
        total = max(6, round(rate * days * 0.35))  # a third of the real volume: plenty for layout, quick to load
        total = max(total, 16) if kind == 'calendar.event' else total  # a busy week has meetings
        for i in range(total):
            minutes = int(rng.betavariate(1, 3) * days * 24 * 60) + rng.randint(1, 30)  # busier today than last week
            p = rng.choice(people)
            other = rng.choice(people)['name'].split()[0]
            key = f'{kind}:{i}'
            if kind in ('gmail.message', 'salesflare.email'):
                subject, text = rng.choice(EMAIL_TOPICS)
                fill = dict(n=rng.choice((8, 12, 25, 40, 120)), owner='Alex', first=p['name'].split()[0], other=other, company=p['company'],
                            inv=f'INV-{rng.randint(1000, 9999)}', domain=p['domain'])
                thread = f't{rng.randint(10**11, 10**12)}'
                add(account, kind, minutes, subject.format(**fill), text.format(**fill),
                    {'from': f"{p['name']} <{p['email']}>", 'to': OWNER['email'], 'labels': ['INBOX'] + (['UNREAD'] if minutes < 600 else []),
                     'thread_id': thread, 'message-id': f'<{thread}.{i}@{p["domain"]}>',
                     'source_url': f'https://mail.example/#inbox/{thread}', **({'contacts': [p['email']], 'crm_account': p['company']} if kind == 'salesflare.email' else {})}, key)
            elif kind == 'calendar.event':
                minutes = start = rng.choice((-2880, -1500, -300, -90, -30, 45, 180, 600, 1500, 3000)) + rng.randint(0, 20)  # some still to come
                add(account, kind, minutes, rng.choice(('Demo', 'Pilot check-in', 'Renewal call', 'Intro call', 'Weekly sync')) + f" with {p['company']}",
                    f"Video call. Agenda: goals, questions, next steps.", {'attendees': [OWNER['email'], p['email']], 'organizer': OWNER['email'],
                     'start_minutes_ago': start, 'duration_minutes': rng.choice((30, 45, 60)), 'status': 'confirmed', 'location': 'https://meet.example/abc-defg-hij',
                     'source_url': f'https://calendar.example/event/{i}'}, key)
            elif kind == 'slack.message':
                channel, text = rng.choice(SLACK)
                add(account, kind, minutes, f"{channel}: {TEAM[i % 3]['name']}", text.format(company=p['company'], owner='Alex', first=p['name'].split()[0]),
                    {'channel_name': channel, 'conversation_type': 'im' if channel == 'Direct message' else 'channel', 'user_id': f'U{1000 + i % 3}',
                     'message_ts': f'17610{i:05d}.000100', 'reply_count': rng.choice((0, 0, 1, 4)), 'source_url': f'https://lumenlabs.slack.example/archives/C1/p{i}'}, key)
            elif kind == 'intercom.conversation':
                add(account, kind, minutes, f"{p['name']} ({p['company']})", rng.choice(INTERCOM),
                    {'from': p['email'], 'state': rng.choice(('open', 'open', 'closed')), 'priority': rng.random() < 0.2,
                     'conversation_id': str(9000 + i), 'source_url': f'https://inbox.example/conversation/{9000 + i}'}, key)
            elif kind == 'whatsapp.message':
                chat, text = rng.choice(WHATSAPP)
                add(account, kind, minutes, chat, text, {'chat_name': chat, 'from_me': rng.random() < 0.3, 'group': chat in ('Morgan family', 'Football parents'),
                     'sender_display': chat if chat not in ('Morgan family', 'Football parents') else 'Robin Morgan', 'message_id': f'wa{i}'}, key)
            elif kind == 'android.notification':
                app, text = rng.choice(ANDROID)
                add(account, kind, minutes, app, text, {'app_name': app, 'package_name': 'example.' + app.lower(), 'truncated': False}, key)
            elif kind == 'linkedin.message':
                add(account, kind, minutes, p['name'], f"Hi Alex, saw your post about shared inboxes. We are hiring at {p['company']}, any advice on tooling?",
                    {'from': p['name'], 'headline': f"Head of Sales at {p['company']}", 'conversation_id': f'li{i}', 'source_url': f'https://linkedin.example/messaging/{i}'}, key)
            elif kind == 'x.mention':
                add(account, kind, minutes, f"@{slug(p['name'])[:12]}", f"@lumenlabs does the new reminder feature work with shared inboxes? asking for {p['company']}",
                    {'screen_name': slug(p['name'])[:12], 'followers': rng.randint(40, 9000), 'source_url': f'https://x.example/status/{i}'}, key)
            elif kind == 'stripe.event':
                amount = rng.choice((290, 590, 1180, 4700))
                add(account, kind, minutes, rng.choice(('invoice.paid', 'invoice.payment_failed', 'customer.subscription.updated')),
                    f"{p['company']}: €{amount}.00", {'customer_name': p['company'], 'type': 'invoice', 'source_url': f'https://dashboard.stripe.example/invoices/{i}'}, key)
            elif kind == 'salesflare.meeting':
                add(account, kind, minutes, f"Meeting notes: {p['company']}", 'Discussed pilot scope, pricing tiers and the security questionnaire.',
                    {'contacts': [p['email']], 'crm_account': p['company'], 'meeting_type': 'video', 'source_url': f'https://crm.example/meetings/{i}'}, key)
            elif kind == 'github.notification':
                add(account, kind, minutes, f"PR #{300 + i}: Fix duplicate reminders", 'Review requested.',
                    {'repo': 'lumenlabs/app', 'number': 300 + i, 'type': 'PullRequest', 'reason': 'review_requested', 'source_url': f'https://github.example/lumenlabs/app/pull/{300 + i}'}, key)
    events.sort(key=lambda e: -e['minutes_ago'])

    def pick(kind, n):
        return [e['key'] for e in events if e['kind'] == kind][-n:]

    gmail = pick('gmail.message', 30)
    cards = []
    open_titles = [
        ('Reply to {first} about pricing for {company}', 'They asked for annual pricing for their team and whether onboarding is included. They want to decide this month.', 'email', True),
        ('Prepare for the {company} demo', 'Thursday 14:00. Two attendees; they asked how reminders work for shared inboxes.', 'prepare', False),
        ('Chase the overdue invoice with {company}', 'Their accounts team says an invoice is 14 days overdue. Check what went wrong before replying.', 'email', True),
        ('Answer {first}\'s API rate limit question', 'Their sync job hits 429s around 3,000 requests. A short answer with the documented limits and a workaround.', 'email', True),
        ('Send {first} the pilot recap', 'You promised a written recap of the pilot scope after the call.', 'email', True),
        ('Decide on the {company} partnership call', 'Their partnerships lead wants 20 minutes to explore an integration.', None, False),
        ('Review the contract redlines from {company}', 'Two points matter: the liability cap in section 9 and data retention.', None, False),
        ('Reply in #support about duplicate reminders', 'A teammate flagged duplicate reminders for {company} this morning.', 'slack', True),
        ('Book the car service before Friday', 'Robin asked on WhatsApp.', None, False),
        ('Who picks up the kids on Wednesday?', 'Robin has a dentist appointment at 4.', 'whatsapp', True),
        ('Thank {first} for the intro', 'They connected you with someone at {company}.', 'email', True),
        ('Follow up with {first}: no reply in 6 days', 'You sent a proposal last week and heard nothing since.', 'email', True),
    ]
    for i, (title, body, action, draft) in enumerate(open_titles):
        p = people[(i * 3) % len(people)]
        fill = dict(first=p['name'].split()[0], company=p['company'])
        work = 'Robin' not in body and 'kids' not in title
        status = 'prepared' if draft and i % 3 == 0 else 'snoozed' if i in (6,) else 'pending'
        card = {'key': f'open{i}', 'title': title.format(**fill), 'body': body.format(**fill), 'status': status,
                'events': [gmail[i]] if work else [pick('whatsapp.message', 12)[i % 12]], 'accounts': ['work-gmail'] if work else ['home-whatsapp'],
                'minutes_ago': 20 + i * 47, 'source': ('email from ' + p['name']) if work else 'WhatsApp from Robin Morgan',
                'project': PROJECTS[i % len(PROJECTS)] if work and i % 2 == 0 else None, 'urgent': i in (0, 2), 'snooze_hours': 50 if status == 'snoozed' else None}
        if draft and action == 'email':
            card['next_action'] = {'kind': 'email', 'account_id': 'work-gmail', 'from': OWNER['email'], 'to': [p['email']], 'cc': [], 'bcc': [],
                                   'subject': 'Re: ' + card['title'].split(' about ')[-1], 'label': 'Send to ' + p['name'].split()[0], 'to_name': p['name'],
                                   'body': f"Hi {p['name'].split()[0]},\n\nThanks for your message. Here is what you asked for, in short:\n\n- Annual billing is 20% below monthly\n- Onboarding is included for teams over 10 seats\n- We can start a pilot next week\n\nWould Thursday work for a quick call?\n\nBest,\nAlex",
                                   'revision': f'rev{i:04d}'}
        elif draft and action == 'slack':
            card['next_action'] = {'kind': 'slack', 'account_id': 'work-slack', 'to_name': '#support', 'body': 'Looking into it now; it started with the 08:00 deploy. Update within the hour.',
                                   'label': 'Reply in #support', 'revision': f'rev{i:04d}'}
        elif draft and action == 'whatsapp':
            card['next_action'] = {'kind': 'whatsapp', 'account_id': 'home-whatsapp', 'to_name': 'Robin Morgan', 'chat_id': 'robin', 'body': 'I can do Wednesday, I will leave at 3:30.',
                                   'label': 'Send to Robin', 'revision': f'rev{i:04d}'}
        cards.append(card)
    closed = profile['cards_by_status']
    for i in range(min(40, closed.get('completed', 0) + closed.get('rejected', 0))):
        p = people[(i * 5 + 1) % len(people)]
        status = 'rejected' if i % 4 == 3 else 'completed'
        cards.append({'key': f'closed{i}', 'title': rng.choice(('Reply to {f}', 'Send {f} the deck', 'Confirm the call with {f}', 'Thank {f}')).format(f=p['name']),
                      'body': f"From {p['company']}.", 'status': status, 'events': [gmail[i % len(gmail)]], 'accounts': ['work-gmail'],
                      'minutes_ago': 600 + i * 190, 'source': 'email from ' + p['name'], 'choice': 'complete' if status == 'completed' else 'reject'})
    promises = []
    for i in range(6):
        p = people[(i * 7 + 2) % len(people)]
        promises.append({'direction': 'mine', 'who': p['name'], 'what': rng.choice(('send the pilot recap', 'share the security answers', 'introduce them to our CTO', 'send pricing')),
                         'due_days': rng.choice((-1, 0, 1, 3)), 'status': 'open', 'event': gmail[i]})
        q = people[(i * 7 + 4) % len(people)]
        promises.append({'direction': 'theirs', 'who': q['name'], 'what': rng.choice(('send the signed contract', 'confirm the seat count', 'share their API logs', 'get budget approval')),
                         'due_days': rng.choice((-2, 1, 2, 5)), 'status': 'open' if i < 5 else 'kept', 'event': gmail[-i - 1]})
    network = {
        'owner': {'member_id': 'm_me', 'name': OWNER['name'], 'email': OWNER['email'], 'username': OWNER['username']},
        'work': {'id': 'fl_5a4db0c0ffee0001', 'name': 'Lumen Labs', 'team': TEAM},
        'family': {'id': 'fl_5a4db0c0ffee0002', 'name': 'Morgans', 'team': FAMILY},
        'friends': {'id': 'fr_5a4db0c0ffee0003', 'friends': FRIENDS, 'request_in': FRIEND_REQUEST_IN, 'request_out': FRIEND_REQUEST_OUT},
        'asks': [  # what teammates' Machines ask this one, kept for the owner
            {'id': 'fa_sandbox0001', 'fleet': 'work', 'from': 'm_team_priya', 'kind': 'relationships', 'question': {'about': 'meridianrobotics.example'}},
            {'id': 'fa_sandbox0002', 'fleet': 'work', 'from': 'm_team_tomasz', 'kind': 'anything', 'question': {'text': 'Did anyone talk to Tidewater Foods about their renewal this month?'}},
            {'id': 'fa_sandbox0003', 'fleet': 'work', 'from': 'm_team_priya', 'kind': 'handoff',
             'question': {'title': 'Answer Harborline Health about SSO', 'summary': 'Their IT lead wants SAML before the pilot. I am out Thursday and Friday.', 'contact': people[3]['email']}},
            {'id': 'fa_sandbox0004', 'fleet': 'work', 'from': 'm_team_ines', 'kind': 'intro', 'question': {'about': 'kestrelenergy.example', 'note': 'They are expanding to the Nordics'}},
            {'id': 'fa_sandbox0005', 'fleet': 'family', 'from': 'm_fam_robin', 'kind': 'todo', 'question': {'title': 'Pick up the kids from football', 'due': '2026-10-23', 'note': 'I have the dentist at 4'}},
            {'id': 'fa_sandbox0006', 'fleet': 'family', 'from': 'm_fam_jules', 'kind': 'event', 'question': {'title': 'Sunday lunch at grandma’s', 'start': '2026-10-25T12:30', 'place': 'Grandma’s'}},
        ],
        'notices': [
            {'id': 'fn_5a4db0c0ffee0004', 'kind': 'seat_request', 'fleet': 'work', 'fleet_id': 'fl_5a4db0c0ffee0001', 'fleet_name': 'Lumen Labs', 'status': 'open', 'version': 1,
             'text': 'Ines Calloway wants a Standard server in your Fleet', 'detail': '€30 a month, on your Fleet’s bill.', 'key': 'k1',
             'buttons': [{'choice': 'confirm', 'label': 'Confirm'}, {'choice': 'decline', 'label': 'Decline'}]},
        ],
        'asked': [  # what this Machine asked its teammates, with their answers
            {'kind': 'relationships', 'question': {'about': 'northwindanalytics.example'}, 'answers': {'m_team_priya': {'knows': True, 'people': 2, 'closeness': 'close', 'last_contact': 'last week'},
                                                                                                         'm_team_tomasz': {'knows': False}}},
        ],
    }
    board_events, boards, board_cards = board_data()
    return {'note': 'Synthetic. Every name, company and address is invented; see sandbox/fixture.py.', 'seed': SEED,
            'owner': OWNER, 'accounts': ACCOUNTS, 'companies': companies, 'people': people, 'projects': PROJECTS,
            'events': events + board_events, 'cards': cards + board_cards, 'promises': promises, 'network': network, 'boards': boards}


if __name__ == '__main__':
    print(json.dumps(build(json.loads((HERE / 'profile.json').read_text())), indent=1, ensure_ascii=False))
