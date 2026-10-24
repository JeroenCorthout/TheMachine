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
        'Stanhope', 'Thistlewood', 'Underhay', 'Valemont', 'Westerby', 'Yarrowby', 'Ashby', 'Calder', 'Dunleavy', 'Hartwell', 'Marlowe', 'Penrose']
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

EMAIL_TOPICS = [  # the crowd's earlier mail: other topics than the open cards, so no card's story shows up twice
    ('Renewal quote for {company}', 'Hi {owner},\n\nOur plan renews next month. Could you send a renewal quote for {n} seats? Finance wants it in this quarter.\n\nThanks,\n{first}'),
    ('Security questionnaire', 'Hi {owner},\n\nOur IT team sent the attached security questionnaire. Could you fill it in by Friday? Most of it is about where data is stored.\n\nBest,\n{first}'),
    ('Receipt for {inv}', 'Hello,\n\nCould you send a copy of the receipt for {inv}? Our bookkeeper needs it with the company address on it.\n\nKind regards,\n{first}\nAccounts, {company}'),
    ('Webinar recording?', 'Hi {owner},\n\nI missed the webinar on Tuesday. Is there a recording I can share with {other}?\n\n{first}'),
    ('Exporting contacts to CSV', 'Hey {owner},\n\nHow do I export contacts with their custom fields? The export I tried had only names. Notes: https://wiki.{domain}/crm/exports/2026/10/very-long-path-to-check-wrapping-of-unbroken-links-in-cards-and-previews\n\n{first}'),
    ('Onboarding for new hires', 'Hi {owner},\n\nWe hired {n} new account managers. Could we book an onboarding session for them in the first week of November?\n\n{first}'),
    ('Case study draft', 'Hi {owner},\n\nThe case study draft looks good. Two small changes from our side are in the doc. Happy for it to go live after that.\n\n{first}'),
    ('Following up on our call', 'Hi {owner},\n\nThanks for the time today. As discussed: a pilot for 10 users starting the 3rd, success criteria by Friday, and a check-in after two weeks. Did I miss anything?\n\n{first}'),
]
SLACK = [('#sales', 'Can someone take the {company} renewal call tomorrow? I am out.'), ('#support', '{company} asks whether the Outlook add-in works on Mac. Anyone tried?'),
         ('#product', 'Draft spec for bulk actions is in the doc, comments welcome before Thursday.'), ('#general', 'Lunch order closes at 11:30 today.'),
         ('Direct message', '{owner}, did you see the email from {first} at {company}? They want an answer today.'),
         ('#sales', 'Won {company} 🎉 47 seats, annual.'), ('#support', 'Who knows the history with {company}? Their admin is asking about an old discount.')]
INTERCOM = ['How do I export all contacts with their custom fields?', 'We were charged twice this month, can you check?',
            'Is there a way to bulk assign accounts to a teammate?', 'The Outlook add-in shows a blank panel after the update.',
            'Can we get an extension of our trial for two more weeks?', 'Does your API support webhooks for deal stage changes?']
FOLLOW_UPS = ['For context: we are on the Growth plan, about 15 users.', 'Thanks, that worked.', 'One more question: can teammates see each other’s notes?', 'Great, we will try it this week.']
LINKEDIN = ['Hi Alex, saw your post about shared inboxes. We are hiring at {company}, any advice on tooling?',
            'We are 12 in sales now, mostly on Outlook. Does that change anything?', 'Thanks, that helps. I will start a trial next week.']
WHATSAPP = [('Morgan family', 'Groceries: oat milk, apples, bread, birthday candles 🎂'),
            ('Sam Whitlock', 'Tennis Saturday at 10? Court 3 is free.'), ('Dana Kerrigan', 'Sending you the photos from the weekend tonight'),
            ('Football parents', 'Match moved to 9:30, bring the orange shirts'), ('Sam Whitlock', 'Bring the spare racket, mine snapped a string')]
ANDROID = [('Bank', 'Card payment of €42.10 at Orchard Street Bakery'), ('Parcel', 'Your parcel arrives today between 13:00 and 15:00'),
           ('Calendar', 'Pilot check-in in 15 minutes'), ('Train', 'Your 08:12 train is 6 minutes late'), ('Weather', 'Rain expected from 16:00')]


# One story per open card: its own people, its own thread (several messages, both ways) and a draft written for it,
# so a card's chat, its sources and its Graph pages all tell the same story. (m, me, subject, body): minutes ago,
# whether the owner wrote it. Bodies use {first} for the person, the rest is spelled out.
STORIES = [
    {'key': 'pricing', 'person': ('Mila Underhay', 'Northwind Analytics'), 'project': 'Northwind rollout', 'status': 'prepared', 'urgent': True, 'minutes': 20,
     'title': 'Reply to Mila about pricing for Northwind Analytics',
     'body': 'They want annual pricing for 25 seats, maybe 40 next year, and whether onboarding is included. They decide this month.',
     'thread': [(2900, False, 'Pricing for our sales team', 'Hi Alex,\n\nWe are comparing three tools for our sales team. Could you send pricing for annual billing?\n\nThanks,\nMila'),
                (1500, True, 'Re: Pricing for our sales team', 'Hi Mila,\n\nHappy to. How many seats are you thinking of, to start and in a year?\n\nAlex'),
                (45, False, 'Re: Pricing for our sales team', 'Hi Alex,\n\n25 seats to start, probably 40 by next autumn. Is onboarding included at that size? We want to decide before the end of the month.\n\nMila')],
     'draft': ('email', 'Hi Mila,\n\nFor 25 seats on annual billing it is €29 a seat a month, so €8,700 for the year. Moving to 40 seats later keeps the same price per seat.\n\nOnboarding is included from 10 seats: two live sessions for your team and a call with your admin.\n\nWould Thursday or Friday suit for a short call to walk through it?\n\nBest,\nAlex')},
    {'key': 'demo', 'person': ('Hanne Ashgrove', 'Brightpath Logistics'), 'status': 'pending', 'minutes': 70,
     'title': 'Prepare for the Brightpath Logistics demo',
     'body': 'Thursday 14:00 with Hanne and Elif from operations. They want to see reminders on shared inboxes.',
     'thread': [(4300, True, 'Demo next week?', 'Hi Hanne,\n\nGood to meet you at the meetup. Would a 45 minute demo next week work?\n\nAlex'),
                (190, False, 'Re: Demo next week?', 'Hi Alex,\n\nThursday at 14:00 works for us. I will bring Elif from operations. Could you show how reminders work for shared inboxes? That is our main pain.\n\nBest,\nHanne')]},
    {'key': 'invoice', 'person': ('Caspian Northcott', 'Cobalt & Finch'), 'status': 'prepared', 'urgent': True, 'minutes': 110,
     'title': 'Answer Cobalt & Finch about invoice INV-4821',
     'body': 'Their second reminder: INV-4821 is 14 days overdue. Stripe shows the card payment failed on the 9th.',
     'thread': [(10300, False, 'Invoice INV-4821', 'Hello,\n\nPlease find attached invoice INV-4821 for October, due in 7 days.\n\nKind regards,\nCaspian\nAccounts, Cobalt & Finch'),
                (240, False, 'Re: Invoice INV-4821 overdue', 'Hello,\n\nOur records show INV-4821 is now 14 days overdue. Could you let us know when we can expect payment, or whether something is wrong with it?\n\nKind regards,\nCaspian')],
     'draft': ('email', 'Hi Caspian,\n\nSorry about this. The card payment for INV-4821 failed on the 9th because the card on file had expired. I have updated it and paid the invoice this morning, so it should show on your side today.\n\nThanks for the reminder,\nAlex')},
    {'key': 'api', 'person': ('Jonah Kettering', 'Glasswing Apps'), 'status': 'prepared', 'minutes': 180, 'job': True,
     'title': "Answer Jonah's API rate limit question",
     'body': 'Their nightly sync gets 429 errors after about 3,000 requests. A short answer with the limit and a fix.',
     'thread': [(600, False, 'Rate limit on the contacts endpoint?', 'Hey Alex,\n\nIs there a rate limit on the contacts endpoint? Our nightly sync starts getting 429s after about 3,000 requests.\n\nJonah'),
                (420, True, 'Re: Rate limit on the contacts endpoint?', 'Hi Jonah,\n\nThere is. Let me check the exact numbers with the team and come back to you today.\n\nAlex'),
                (300, False, 'Re: Rate limit on the contacts endpoint?', 'Thanks! If it helps, it is one API key, and we page through 100 contacts at a time.\n\nJonah')],
     'draft': ('email', 'Hi Jonah,\n\nThe contacts endpoint allows 600 requests a minute per API key. Past that it answers 429 with a Retry-After header, so waiting that many seconds and going on is enough.\n\nWith 100 contacts per page your sync stays under the limit with a short pause every 600 pages. Or use the bulk export, which needs one request for everything.\n\nAlex')},
    {'key': 'recap', 'person': ('Wren Fairhaven', 'Harborline Health'), 'project': 'Harborline pilot', 'status': 'pending', 'minutes': 230,
     'title': 'Send Wren the pilot recap',
     'body': 'You promised a written recap of the pilot after Tuesday’s call: 10 users from the 3rd, check-in after two weeks.',
     'thread': [(1600, True, 'Pilot call Tuesday', 'Hi Wren,\n\nLooking forward to Tuesday. I will send a written recap after the call.\n\nAlex'),
                (1300, False, 'Re: Pilot call Tuesday', 'Thanks for the time today. A recap would help, I need it for our IT lead before Friday.\n\nWren')],
     'draft': ('email', 'Hi Wren,\n\nHere is the recap of Tuesday’s call:\n\n- Pilot for 10 users, starting on the 3rd\n- Success means fewer missed follow-ups and one shared view of the pipeline\n- Your IT lead gets the security answers by Friday\n- We check in after two weeks to decide on the rollout\n\nTell me if I missed anything.\n\nAlex')},
    {'key': 'partner', 'person': ('Dorian Yarrowby', 'Pinecrest Legal'), 'project': 'Q4 partner program', 'status': 'pending', 'minutes': 290,
     'title': 'Decide on the Pinecrest Legal partnership call',
     'body': 'Dorian asks for 20 minutes about an integration their clients keep asking for.',
     'thread': [(3000, False, 'Partnership idea', 'Hi Alex,\n\nI run partnerships at Pinecrest Legal. Our clients keep asking for a CRM integration and yours came up three times this month.\n\nDorian'),
                (2800, True, 'Re: Partnership idea', 'Hi Dorian,\n\nInteresting. What do you have in mind?\n\nAlex'),
                (330, False, 'Re: Partnership idea', 'A shared client list and matter updates flowing into your timeline. Open to a 20 minute call next week?\n\nDorian')]},
    {'key': 'redlines', 'person': ('Anouk Valemont', 'Vantage Rail'), 'status': 'snoozed', 'snooze_hours': 50, 'minutes': 340,
     'title': 'Review the contract redlines from Vantage Rail',
     'body': 'Two points matter to them: the liability cap in section 9 and the data retention period.',
     'thread': [(380, False, 'Contract redlines', 'Hi Alex,\n\nAttached are our legal team’s redlines. Two points matter most to us: the liability cap in section 9 and the data retention period. The rest is cosmetic.\n\nCheers,\nAnouk')]},
    {'key': 'support', 'slack': True, 'company': 'Tidewater Foods', 'status': 'prepared', 'minutes': 400,
     'title': 'Reply in #support about duplicate reminders',
     'body': 'Tidewater Foods gets every reminder twice since this morning’s deploy. Priya and Tomasz are on the thread.',
     'slack_thread': [(460, 'Priya Okonedo', 'Heads up: Tidewater Foods reports duplicate reminders since this morning.'),
                      (450, 'Tomasz Varga', 'Seeing it too, only for teams with shared inboxes. Started around 08:00.'),
                      (440, 'Priya Okonedo', 'Alex, can you tell them what is going on? Their admin is asking.')],
     'draft': ('slack', 'Looking into it now. It started with the 08:00 deploy and only hits shared inboxes. I will post here and tell Tidewater once it is fixed, within the hour.')},
    {'key': 'car', 'family': True, 'status': 'pending', 'minutes': 520,
     'title': 'Book the car service before Friday', 'body': 'Robin asked on WhatsApp; the garage needs a day’s notice.',
     'chat': ('Robin Morgan', [(560, False, 'Can you book the car service before Friday? The garage needs a day’s notice.'),
                               (545, True, 'Sure. Same garage as last time?'),
                               (530, False, 'Yes, Linden Motors. Thursday morning is best, I need the car in the afternoon.')])},
    {'key': 'kids', 'family': True, 'status': 'prepared', 'minutes': 580,
     'title': 'Who picks up the kids on Wednesday?', 'body': 'Robin has the dentist at 4 and Jules has practice. Your calendar is free after 15:00.',
     'group': ('Morgan family', [(700, 'Robin Morgan', 'Who picks up the kids on Wednesday? I have a dentist appointment at 4.'),
                                 (690, 'Jules Morgan', 'Not me, I have practice until 5.'),
                                 (650, 'Robin Morgan', 'Alex?')]),
     'draft': ('whatsapp', 'I can do Wednesday. I will leave work at 15:30 and pick them up at 4.')},
    {'key': 'intro', 'person': ('Matteo Brightwater', 'Kestrel Energy'), 'status': 'pending', 'minutes': 640,
     'title': 'Thank Matteo for the intro to Sunniva',
     'body': 'Matteo connected you with Sunniva at Quarry Lane Studios, who already booked a call.',
     'thread': [(2000, False, 'Intro: Sunniva <> Alex', 'Hi both,\n\nAs promised, connecting you two. Sunniva leads growth at Quarry Lane Studios and is looking at tooling for their account managers. I will let you take it from here.\n\nBest,\nMatteo')],
     'others': [('Sunniva Juniper', 'Quarry Lane Studios', (1700, False, 'Re: Intro: Sunniva <> Alex', 'Thanks Matteo! Alex, I booked a slot for next Tuesday. Looking forward to it.\n\nSunniva'))],
     'draft': ('email', 'Hi Matteo,\n\nThank you for the intro. Sunniva and I talk on Tuesday. I owe you a coffee next time you are in town.\n\nAlex')},
    {'key': 'quiet', 'person': ('Yara Oakhurst', 'Saltmarsh Media'), 'project': 'Q4 partner program', 'status': 'pending', 'minutes': 700,
     'title': 'Follow up with Yara: no reply in 6 days', 'body': 'You sent the proposal for a co-marketing webinar last week and heard nothing since.',
     'thread': [(9000, False, 'Webinar together?', 'Hi Alex,\n\nWould you be up for a joint webinar in November? Our audience asks about follow-up habits a lot.\n\nYara'),
                (8700, True, 'Re: Webinar together?', 'Hi Yara,\n\nLove it. Proposal attached: 45 minutes on the 19th, you host, we bring the case study.\n\nAlex')],
     'draft': ('email', 'Hi Yara,\n\nChecking in on the webinar proposal. Does the 19th still work for you? If November is too tight, December is fine too.\n\nAlex')},
]

# Closed cards, one per earlier email: what the card said, by the email's topic (EMAIL_TOPICS order)
CLOSED_TITLES = ['Send {company} the renewal quote', 'Fill in the security questionnaire for {company}', 'Send {company} the receipt',
                 'Send {first} the webinar recording', 'Answer {first} about exports', 'Book onboarding with {company}',
                 'Approve the {company} case study', 'Confirm the pilot plan with {first}']
# Promises, each in its story's thread: (direction, story, message, who, what, due in days, status)
PROMISES = [('mine', 'recap', 0, 'Wren Fairhaven', 'send the pilot recap', 0, 'open'), ('mine', 'api', 1, 'Jonah Kettering', 'come back with the rate limits today', 0, 'open'),
            ('mine', 'pricing', 1, 'Mila Underhay', 'send annual pricing', 1, 'open'), ('mine', 'demo', 0, 'Hanne Ashgrove', 'show reminders on shared inboxes', 3, 'open'),
            ('theirs', 'recap', 1, 'Wren Fairhaven', 'get the IT lead’s security questions', 2, 'open'), ('theirs', 'intro', 1, 'Sunniva Juniper', 'book a call', -1, 'kept'),
            ('theirs', 'partner', 2, 'Dorian Yarrowby', 'send their integration wish list', 5, 'open'), ('theirs', 'invoice', 1, 'Caspian Northcott', 'confirm the payment arrived', 1, 'open')]
# A card handed to Claude Code, with its result (agent_jobs.py), and sign-ins saved for the browser (browser_login.py)
JOB = {'story': 'api', 'tool': 'claude', 'files': ['rate-limits.md'],
       'summary': 'Checked the API docs: the contacts endpoint allows 600 requests a minute per key and answers 429 with Retry-After past that.\n\n'
                  'Wrote the answer in rate-limits.md, with the bulk export as the faster option.'}
SAVED_LOGINS = [('billing.cobaltandfinch.example', 'alex@lumenlabs.example'), ('portal.vantagerail.example', 'alex.morgan')]


# Boards (Graph › Projects, Board): projects of one kind in stages, each moved by a message. Made up like the rest.
# Per project: name, domain, contact, the messages (days ago, from the owner?, what it says) and its moves
# (stage, why, the message that shows it). The last move is where it stands; next is the owner's next step.
D = 24 * 60
BOARDS = [
    {'name': 'Partner listings', 'about': 'Partners we list our app with, one project per partner', 'stages': [{'name': 'Contacted', 'hint': 'Asked or applied, no yes yet'}, {'name': 'Agreed', 'hint': 'They said yes'},
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
    {'name': 'Customer onboarding', 'about': 'New customers we are setting up, one project per customer', 'stages': [{'name': 'Kickoff', 'hint': 'Signed, first call planned'}, {'name': 'Setting up', 'hint': 'Import and settings'},
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
    {'name': 'Support escalations', 'about': 'Support cases that needed more than a reply', 'stages': [{'name': 'Open'}, {'name': 'Waiting on us'}, {'name': 'Waiting on them'}, {'name': 'Solved'}],
     'projects': [
         ('Kittiwake export bug', 'kittiwake.example', 'Signe Paulsen', [(2, False, 'The export drops custom fields')],
          [('Open', 'Signe reported missing custom fields', 0)], 'Reproduce it'),
         ('Halyard sync delay', 'halyard.example', 'Desmond Oduya', [(5, False, 'Sync takes hours'), (4, True, 'Asked for their logs')],
          [('Open', 'Desmond reported slow sync', 0), ('Waiting on them', 'asked for their logs', 1)], 'Wait for the logs'),
     ]},
]


BY_HAND = {'Moorhen Data': ('Live', 1)}  # the owner dragged it there, a day ago
# Projects on no board yet that are one kind of work: a review suggested a board for them ("Start a board for Hiring?")
SUGGESTED = {'name': 'Hiring', 'visibility': 'only_you', 'about': 'Roles we are hiring for, one project per role',
             'reason': 'Three open roles, each with candidates writing in',
             'stages': [{'name': 'Sourced', 'hint': 'Candidates found or applied'}, {'name': 'Screening'}, {'name': 'Interviews'}, {'name': 'Offer'}, {'name': 'Hired'}],
             'projects': [('Hire: support lead', 'tallowcrest.example', 'Imogen Hale', [(6, False, 'Applying for the support lead role, CV attached'), (4, True, 'Thanks, can we talk Thursday')]),
                          ('Hire: backend engineer', 'gullwing.example', 'Tobiah Renn', [(9, False, 'Three backend candidates for you to look at'), (2, True, 'Let us meet the second one')]),
                          ('Hire: product designer', 'mossgate.example', 'Signe Laurier', [(3, False, 'Portfolio for the product designer role')])]}


def board_data():
    """Boards, their projects, the messages behind each move and one open card on a project waiting for a reply."""
    events, boards, cards = [], [], []
    for b in BOARDS:
        board = {'name': b['name'], 'about': b['about'], 'stages': b['stages'], 'projects': []}
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
    suggestion = {k: SUGGESTED[k] for k in ('name', 'about', 'reason', 'stages', 'visibility')}
    suggestion['projects'] = []
    for name, domain, contact, msgs in SUGGESTED['projects']:
        first, key, keys = contact.split()[0], slug(name), []
        for i, (days_ago, mine, text) in enumerate(msgs):
            k, thread = f'hire:{key}:{i}', f'th{key}'
            events.append({'key': k, 'account_id': 'work-gmail', 'kind': 'gmail.message', 'minutes_ago': days_ago * D + 60 + i * 9, 'title': name.split(': ')[1].capitalize(),
                           'body': f'Hi {first},\n\n{text}.\n\nBest,\nAlex' if mine else f'Hi Alex,\n\n{text}.\n\n{first}',
                           'metadata': {'from': f"{OWNER['name']} <{OWNER['email']}>" if mine else f'{contact} <{first.lower()}@{domain}>',
                                        'to': f'{first.lower()}@{domain}' if mine else OWNER['email'], 'labels': ['SENT'] if mine else ['INBOX'],
                                        'thread_id': thread, 'message-id': f'<{thread}.{i}@{domain}>', 'source_url': f'https://mail.example/#inbox/{thread}'}})
            keys.append(k)
        suggestion['projects'].append({'name': name, 'events': keys})
    boards.append({'suggestion': suggestion})
    cards.append({'key': 'board-bramblebox', 'title': 'Follow up with Hugo at Bramblebox: no reply in 52 days', 'body': 'You asked for a listing and nudged twice; nothing back.',
                  'status': 'pending', 'events': ['board:bramblebox:2'], 'accounts': ['work-gmail'], 'minutes_ago': 300, 'source': 'email to Hugo Penrose',
                  'project': 'Bramblebox', 'urgent': False, 'snooze_hours': None})
    return events, boards, cards


def person(rng, used, avoid=()):
    while True:  # avoid: the stories' first and last names, so a name in a card is never someone else
        name = rng.choice([f for f in FIRST if f not in avoid]) + ' ' + rng.choice([n for n in LAST if n not in avoid])
        if name not in used:
            used.add(name)
            return name


def slug(text):
    return ''.join(c for c in text.lower().replace(' ', '') if c.isalnum())


def build(profile):
    rng = random.Random(SEED)
    cast = {}  # the stories' people: their own threads only, never a random email
    for story in STORIES:
        for name, company in [story['person']] if 'person' in story else []:
            cast[name] = company
        for name, company, _ in story.get('others', []):
            cast[name] = company
    cast['Elif Everly'] = 'Brightpath Logistics'
    used = set(cast)
    companies = [{'name': c, 'domain': slug(c.replace('&', 'and')) + '.example'} for c in COMPANIES]
    domain = {c['name']: c['domain'] for c in companies}
    people = [{'name': n, 'email': f"{n.split()[0].lower()}@{domain[c]}", 'company': c, 'domain': domain[c], 'story': True} for n, c in cast.items()]
    crowd = []
    for company in companies:
        for _ in range(rng.choice((1, 2, 2, 3))):
            name = person(rng, used, {part for n in cast for part in n.split()})
            first = name.split()[0]
            crowd.append({'name': name, 'email': f"{first.lower()}@{company['domain']}", 'company': company['name'], 'domain': company['domain']})
    people += crowd
    regulars = [p for p in people if p.get('story')] * 5
    events = []
    topics = {}  # an earlier email's key: (topic, person), for the closed cards
    said = {}  # (kind, person): messages so far in their conversation
    last = {}  # (kind, person): when their conversation last moved

    def reply_at(kind, p, minutes, n):
        """A follow-up comes soon after the message before it, the way a chat goes (the Graph groups those)."""
        if n:
            minutes = max(1, last[kind, p['name']] - rng.randint(3, 25))
        last[kind, p['name']] = minutes
        return minutes
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
            p = rng.choice(crowd + regulars if kind in ('calendar.event', 'salesflare.meeting') else crowd)  # the stories' people meet too
            other = rng.choice(crowd)['name'].split()[0]
            key = f'{kind}:{i}'
            if kind in ('gmail.message', 'salesflare.email'):
                topic = rng.randrange(len(EMAIL_TOPICS))
                subject, text = EMAIL_TOPICS[topic]
                topics[key] = (topic, p)
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
                    {'channel_name': channel.lstrip('#'), 'conversation_type': 'im' if channel == 'Direct message' else 'channel', 'user_id': f'U{1000 + i % 3}',
                     'message_ts': f'17610{i:05d}.000100', 'reply_count': rng.choice((0, 0, 1, 4)), 'source_url': f'https://lumenlabs.slack.example/archives/C1/p{i}'}, key)
            elif kind == 'intercom.conversation':  # one conversation per person, so the Graph shows it as one row
                n = said[kind, p['name']] = said.get((kind, p['name']), -1) + 1
                minutes = reply_at(kind, p, minutes, n)
                conversation = 9000 + people.index(p)
                add(account, kind, minutes, f"{p['name']} ({p['company']})", INTERCOM[people.index(p) % len(INTERCOM)] if n == 0 else FOLLOW_UPS[(n - 1) % len(FOLLOW_UPS)],
                    {'from': p['email'], 'state': rng.choice(('open', 'open', 'closed')), 'priority': rng.random() < 0.2,
                     'conversation_id': str(conversation), 'source_url': f'https://inbox.example/conversation/{conversation}'}, key)
            elif kind == 'whatsapp.message':
                chat, text = rng.choice(WHATSAPP)
                add(account, kind, minutes, chat, text, {'chat_name': chat, 'chat_id': slug(chat), 'from_me': rng.random() < 0.3, 'group': chat in ('Morgan family', 'Football parents'),
                     'sender_display': chat if chat not in ('Morgan family', 'Football parents') else 'Robin Morgan', 'message_id': f'wa{i}'}, key)
            elif kind == 'android.notification':
                app, text = rng.choice(ANDROID)
                add(account, kind, minutes, app, text, {'app_name': app, 'package_name': 'example.' + app.lower(), 'truncated': False}, key)
            elif kind == 'linkedin.message':
                n = said[kind, p['name']] = said.get((kind, p['name']), -1) + 1
                minutes = reply_at(kind, p, minutes, n)
                add(account, kind, minutes, p['name'], LINKEDIN[n % len(LINKEDIN)].format(company=p['company']),
                    {'from': p['name'], 'headline': f"Head of Sales at {p['company']}", 'conversation_id': 'li-' + slug(p['name']),
                     'source_url': 'https://linkedin.example/messaging/' + slug(p['name'])}, key)
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
    person_of = {p['name']: p for p in people}
    cards, keys = [], {}

    def mail(story, n, m, me, subject, body, p, thread):
        key = f"story:{story['key']}:{n}"
        frm, to = (f"{OWNER['name']} <{OWNER['email']}>", p['email']) if me else (f"{p['name']} <{p['email']}>", OWNER['email'])
        add('work-gmail', 'gmail.message', m, subject, body, {'from': frm, 'to': to, 'labels': ['SENT'] if me else ['INBOX'] + (['UNREAD'] if m < 600 else []),
            'thread_id': thread, 'message-id': f'<{thread}.{n}@{p["domain"] if not me else OWNER["domain"]}>', 'source_url': f'https://mail.example/#inbox/{thread}'}, key)
        return key

    for i, story in enumerate(STORIES):
        out = keys[story['key']] = []
        if 'person' in story:
            p = person_of[story['person'][0]]
            thread = f't-{story["key"]}'
            for n, (m, me, subject, body) in enumerate(story['thread']):
                out.append(mail(story, n, m, me, subject, body, p, thread))
            for name, _, (m, me, subject, body) in story.get('others', []):
                out.append(mail(story, len(out), m, me, subject, body, person_of[name], thread))
            account, source, to = 'work-gmail', 'email from ' + p['name'], p
        elif 'slack_thread' in story:
            first = story['slack_thread'][0][0]
            for n, (m, who, text) in enumerate(story['slack_thread']):
                key = f"story:{story['key']}:{n}"
                add('work-slack', 'slack.message', m, f'#support: {who}', text, {'channel_name': 'support', 'channel_id': 'C0SUPPORT', 'conversation_type': 'channel',
                    'user_id': f'U{2000 + n}', 'message_ts': f'1761{m:06d}.000100', 'thread_ts': f'1761{first:06d}.000100', 'reply_count': 2 if n == 0 else 0,
                    'source_url': f'https://lumenlabs.slack.example/archives/C0SUPPORT/p{first}'}, key)
                out.append(key)
            account, source, to = 'work-slack', 'Slack #support', None
        else:
            chat, lines = story.get('chat') or story['group']
            group = 'group' in story
            for n, line in enumerate(lines):
                m, who, text = (line[0], None if line[1] else chat, line[2]) if not group else line
                key = f"story:{story['key']}:{n}"
                add('home-whatsapp', 'whatsapp.message', m, chat, text, {'chat_name': chat, 'chat_id': slug(chat), 'from_me': who is None, 'group': group,
                    **({'sender_display': who} if who else {}), 'message_id': f"wa-{story['key']}-{n}"}, key)
                out.append(key)
            account, source, to = 'home-whatsapp', 'WhatsApp from Robin Morgan', None
        card = {'key': 'open' + str(i), 'title': story['title'], 'body': story['body'], 'status': story['status'], 'events': out, 'accounts': [account],
                'minutes_ago': story['minutes'], 'source': source, 'project': story.get('project'), 'urgent': bool(story.get('urgent')),
                'snooze_hours': story.get('snooze_hours')}
        kind, body = story.get('draft') or (None, None)
        if kind == 'email':
            last = story['thread'][-1][2]
            card['next_action'] = {'kind': 'email', 'account_id': 'work-gmail', 'from': OWNER['email'], 'to': [to['email']], 'cc': [], 'bcc': [],
                                   'subject': last if last.startswith('Re: ') else 'Re: ' + last, 'label': 'Send to ' + to['name'].split()[0], 'to_name': to['name'],
                                   'body': body, 'revision': f'rev{i:04d}'}
        elif kind == 'slack':
            card['next_action'] = {'kind': 'slack', 'account_id': 'work-slack', 'to_name': '#support', 'body': body, 'label': 'Reply in #support', 'revision': f'rev{i:04d}'}
        elif kind == 'whatsapp':
            card['next_action'] = {'kind': 'whatsapp', 'account_id': 'home-whatsapp', 'to_name': 'Morgan family', 'chat_id': 'morganfamily', 'body': body,
                                   'label': 'Send to Morgan family', 'revision': f'rev{i:04d}'}
        cards.append(card)
    hanne = person_of['Hanne Ashgrove']
    add('work-calendar', 'calendar.event', -2900, 'Demo with Brightpath Logistics', 'Video call. Hanne and Elif from operations: reminders on shared inboxes.',
        {'attendees': [OWNER['email'], hanne['email'], person_of['Elif Everly']['email']], 'organizer': OWNER['email'], 'start_minutes_ago': -2900,
         'duration_minutes': 45, 'status': 'confirmed', 'location': 'https://meet.example/brightpath-demo', 'source_url': 'https://calendar.example/event/brightpath-demo'}, 'story:demo:meeting')
    cards[1]['events'].append('story:demo:meeting')
    add('work-stripe', 'stripe.event', 20160, 'invoice.payment_failed', 'Cobalt & Finch: €1,180.00, card expired',
        {'customer_name': 'Cobalt & Finch', 'type': 'invoice', 'source_url': 'https://dashboard.stripe.example/invoices/inv-4821'}, 'story:invoice:stripe')
    cards[2]['events'].append('story:invoice:stripe')
    # Same person? Robin writes from work with their own address in CC and chats on WhatsApp: one person by itself. Two
    # Theo Marchettis at two addresses stay two, with a question; so do Nina and Nina Kowalczyk, both at Saltmarsh Media.
    robin_work, robin_home = 'Robin Morgan <robin.morgan@harborlinehealth.example>', 'Robin Morgan <robin@mail.example>'
    add('work-gmail', 'gmail.message', 2200, 'School forms', 'Can you sign the school forms tonight? Sent from work.',
        {'from': robin_work, 'to': OWNER['email'], 'cc': robin_home, 'labels': ['INBOX'], 'thread_id': 't-school', 'message-id': '<t-school.0@harborlinehealth.example>',
         'source_url': 'https://mail.example/#inbox/t-school'}, 'story:same:0')
    add('work-gmail', 'gmail.message', 2100, 'Re: School forms', 'Signed, they are on the kitchen table.',
        {'from': f"{OWNER['name']} <{OWNER['email']}>", 'to': robin_work, 'cc': robin_home, 'labels': ['SENT'], 'thread_id': 't-school',
         'message-id': '<t-school.1@lumenlabs.example>', 'source_url': 'https://mail.example/#inbox/t-school'}, 'story:same:1')
    for n, (minutes, mine, text) in enumerate(((1400, False, 'Home by 7, shall I get pizza?'), (1390, True, 'Yes please'), (300, False, 'On my way'))):
        add('home-whatsapp', 'whatsapp.message', minutes, 'Robin Morgan', text, {'chat_name': 'Robin Morgan', 'chat_id': 'robin-morgan@chat.example', 'from_me': mine,
            'from': 'You' if mine else 'Robin Morgan', 'sender_id': None if mine else 'robin-morgan@chat.example', 'group': False, 'message_id': f'wa-same{n}'}, f'story:same:wa{n}')
    for n, (who, minutes, subject) in enumerate((('Theo Marchetti <theo@vantagerail.example>', 3000, 'Timetable data for the pilot'),
                                                 ('Theo Marchetti <theo.marchetti@mail.example>', 2600, 'Running club on Saturday'),
                                                 ('Nina <nina.k@saltmarshmedia.example>', 2500, 'Podcast slot next week'),
                                                 ('Nina Kowalczyk <nina@saltmarshmedia.example>', 1900, 'Podcast questions'))):
        add('work-gmail', 'gmail.message', minutes, subject, 'Hi Alex, a quick one about this. Thanks!',
            {'from': who, 'to': OWNER['email'], 'labels': ['INBOX'], 'thread_id': f't-same{n}', 'message-id': f'<t-same{n}@mail.example>',
             'source_url': f'https://mail.example/#inbox/t-same{n}'}, f'story:same:mail{n}')
    events.sort(key=lambda e: -e['minutes_ago'])

    closed = profile['cards_by_status']
    earlier = [e for e in events if e['key'] in topics]
    titled = []
    for e in earlier:
        topic, p = topics[e['key']]
        title = CLOSED_TITLES[topic].format(first=p['name'].split()[0], company=p['company'])
        if title not in [t for t, _ in titled]:
            titled.append((title, e))
    for i in range(min(40, closed.get('completed', 0) + closed.get('rejected', 0), len(titled))):
        title, e = titled[i]
        topic, p = topics[e['key']]
        status = 'rejected' if i % 4 == 3 else 'completed'
        cards.append({'key': f'closed{i}', 'title': title,
                      'body': f"From {p['name']} at {p['company']}.", 'status': status, 'events': [e['key']], 'accounts': [e['account_id']],
                      'minutes_ago': max(e['minutes_ago'] - 20, 30), 'source': 'email from ' + p['name'], 'choice': 'complete' if status == 'completed' else 'reject'})
    promises = [{'direction': d, 'who': who, 'what': what, 'due_days': due, 'status': status, 'event': keys[story][n]}
                for d, story, n, who, what, due, status in PROMISES]
    job = {**JOB, 'card': 'open' + str(next(i for i, s in enumerate(STORIES) if s['key'] == JOB['story']))}
    network = {
        'owner': {'member_id': 'm_me', 'name': OWNER['name'], 'email': OWNER['email'], 'username': OWNER['username']},
        'work': {'id': 'fl_5a4db0c0ffee0001', 'name': 'Lumen Labs', 'team': TEAM},
        'family': {'id': 'fl_5a4db0c0ffee0002', 'name': 'Morgans', 'team': FAMILY},
        'friends': {'id': 'fr_5a4db0c0ffee0003', 'friends': FRIENDS, 'request_in': FRIEND_REQUEST_IN, 'request_out': FRIEND_REQUEST_OUT},
        'asks': [  # what teammates' Machines ask this one, kept for the owner
            {'id': 'fa_sandbox0001', 'fleet': 'work', 'from': 'm_team_priya', 'kind': 'relationships', 'question': {'about': 'meridianrobotics.example'}},
            {'id': 'fa_sandbox0002', 'fleet': 'work', 'from': 'm_team_tomasz', 'kind': 'anything', 'question': {'text': 'Did anyone talk to Tidewater Foods about their renewal this month?'}},
            {'id': 'fa_sandbox0003', 'fleet': 'work', 'from': 'm_team_priya', 'kind': 'handoff',
             'question': {'title': 'Answer Harborline Health about SSO', 'summary': 'Their IT lead wants SAML before the pilot. I am out Thursday and Friday.', 'contact': person_of['Wren Fairhaven']['email']}},
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
            'events': events + board_events, 'cards': cards + board_cards, 'promises': promises, 'network': network, 'agent_job': job,
            'saved_logins': [{'site': site, 'username': user} for site, user in SAVED_LOGINS], 'boards': boards}


if __name__ == '__main__':
    print(json.dumps(build(json.loads((HERE / 'profile.json').read_text())), indent=1, ensure_ascii=False))
