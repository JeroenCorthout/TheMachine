"""Explicit, revision-bound next actions. No model or arbitrary command execution."""
import base64
import calendar_writes
from contextlib import contextmanager
import email.policy
import portable
import datetime
from email.message import EmailMessage
from email.utils import format_datetime
import hashlib
import google_api
import mail_attachments
import ms_graph
import signatures
import html
from html.parser import HTMLParser
import json
import mimetypes
import os
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit

SEND_SCOPES = {'https://www.googleapis.com/auth/gmail.send',
               'https://www.googleapis.com/auth/gmail.compose',
               'https://www.googleapis.com/auth/gmail.modify', 'https://mail.google.com/'}


def mail_account(account):
    """A mailbox that can send what the owner approves: Gmail (gog or broker) or Outlook (broker or own app)."""
    service = account.get('service', 'gmail')
    return (service == 'gmail' and account.get('transport') in ('gog', 'broker')) or (
        service == 'outlook' and account.get('transport') in ('broker', 'graph'))


def outlook(account):
    return account.get('service') == 'outlook'


def granted_scopes(config, credential, account):
    """The scopes the owner granted this account: from gog's own list, or from the broker (Graph's short names for
    an Outlook account)."""
    if outlook(account):
        try:
            return ms_graph.scopes(config, credential, account)
        except ms_graph.GraphError:
            raise ValueError('I could not verify Outlook permissions. Nothing was sent.') from None
    if account.get('transport') == 'broker':
        try:
            return google_api.scopes(config, credential, account)
        except google_api.BrokerError:
            raise ValueError('I could not verify Gmail permissions. Nothing was sent.') from None
    env = os.environ.copy()
    env.update(GOG_KEYRING_BACKEND='file', GOG_KEYRING_PASSWORD=credential(config, 'gog-password'))
    env.pop('DBUS_SESSION_BUS_ADDRESS', None)
    result = subprocess.run([config['gog'], 'auth', 'list', '--json', '--no-input'], env=env, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('I could not verify Gmail permissions. Nothing was sent.')
    auth = next((a for a in json.loads(result.stdout).get('accounts', [])
                 if a['email'].lower() == account['email'].lower() and a.get('client') == account['client']), None)
    return set(auth.get('scopes', [])) if auth else set()


# Email attachments. A draft may attach files the preparation saved in its
# own folder or in memory/assistant/; nothing else (no credentials, no arbitrary home files).
# Each file is copied into the service's own store when the draft is saved, so it survives a
# reboot and the owner approves exactly those bytes: the SHA-256 is part of the draft revision.
_WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or Path.home() / '.openclaw/workspace').expanduser()
ATTACHMENT_SOURCES = (str(_WORKSPACE / 'data/assistant-attachments'), str(_WORKSPACE / 'memory/assistant'))
ATTACHMENT_STORE = str(Path.home() / '.local/share/personal-assistant/attachments')
MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024  # Gmail's 25 MB limit applies after base64 encoding.


def attachment_store(config):
    return Path(config.get('attachment_dir', ATTACHMENT_STORE))


def human_size(size):
    return f'{size} B' if size < 1024 else f'{size / 1024:.0f} KB' if size < 1024 * 1024 else f'{size / 1048576:.1f} MB'


def attachment_list(action):
    return ', '.join(f"{a['name']} ({human_size(a['size'])})" for a in action.get('attachments', []))


def store_attachments(paths, config):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 10:
        raise ValueError('attachments is a list of one to ten file paths')
    sources = [Path(s).resolve() for s in config.get('attachment_sources', ATTACHMENT_SOURCES)]
    if (opened := mail_attachments.open_root(config)):  # a run's readable copy of an email attachment
        sources.append(opened.resolve())
    store, records, total = attachment_store(config), [], 0
    for path in paths:
        if not isinstance(path, str) or '\x00' in path:
            raise ValueError('Attachment paths must be file paths')
        real = Path(path.replace('~', str(Path.home()), 1) if path.startswith('~/') else path).resolve()
        if not any(real.is_relative_to(source) for source in sources) or not real.is_file():
            raise ValueError('Attach only files you saved in workspace/data/assistant-attachments/<job id>/ or memory/assistant/')
        data = mail_attachments.read(real, config)  # an email attachment kept sealed opens with the message key
        total += len(data)
        if not data or total > MAX_ATTACHMENT_BYTES:
            raise ValueError('Attachments must be non-empty and at most 18 MB together')
        name = real.name
        if len(name) > 150 or re.search(r'[\x00-\x1f/\\]', name):
            raise ValueError('Give attachments a plain file name of at most 150 characters')
        digest = hashlib.sha256(data).hexdigest()
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        mail_attachments.store_copy(config, store / digest, data, mail_attachments.from_email(real, config))
        records.append({'name': name, 'sha256': digest, 'size': len(data),
                        'type': mimetypes.guess_type(name)[0] or 'application/octet-stream'})
    if len({r['name'] for r in records}) != len(records):
        raise ValueError('Attachments need distinct file names')
    return records


def attachment_bytes(record, config):
    data = mail_attachments.read(attachment_store(config) / record['sha256'], config)
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('An attachment changed after you reviewed it. Nothing was sent.')
    return data


# Owner-approved sends: each needs the Send review and Send now (Telegram) or the dashboard dialog.
SEND_KINDS = ('email', 'email_demo', 'linkedin', 'slack', 'x', 'whatsapp', 'intercom')
# Owner-approved changes to The Machine's own code: Merge reviews the fix, Merge now applies it.
APPROVE_KINDS = SEND_KINDS + ('merge', 'calendar')
# The last step of a task taken up to it (a filled-in form, a cart, a payment): the owner reviews
# what will be submitted, and their confirm runs only that step.
FINISH_KINDS = ('finish',)
FINISH_VERBS = ('Submit', 'Pay', 'Book', 'Confirm', 'Order', 'Buy', 'Publish', 'Post', 'Sign', 'Accept', 'Decline',
                'Approve', 'Create', 'Delete', 'Cancel', 'Register', 'Renew', 'File', 'Upload', 'Schedule', 'Reserve',
                'Apply', 'Transfer', 'Deploy', 'Send', 'Request', 'Unsubscribe', 'Close', 'Archive', 'Invite',
                'Share', 'Update', 'Save', 'Claim', 'Return', 'Refund', 'Reply')
TEXT_KINDS = ('linkedin', 'slack', 'x', 'whatsapp', 'intercom')  # a message without a subject
LINKEDIN_INBOX = Path(__file__).resolve().parent / 'integrations/linkedin_inbox.py'
X_INBOX = Path(__file__).resolve().parent / 'integrations/x_inbox.py'
X_URLISH = re.compile(r'(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?:/\S*)?')


def x_length(text):
    """Length as X counts a post (like x_inbox.py): every link 23, CJK and emoji 2."""
    text = unicodedata.normalize('NFC', text)
    return 23 * len(X_URLISH.findall(text)) + sum(1 if o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247 else 2
                                                   for o in map(ord, X_URLISH.sub('', text)))


def text_length(action, body):
    return x_length(body) if action['kind'] == 'x' and action.get('reply_to') else len(body)


def intercom_replies_on(account):
    """As connectors/intercom.py replies_on (which checks again at send): the owner's own reply token and admin id, and
    never an account from a Fleet grant."""
    shared = account.get('shared')
    if shared and not (isinstance(shared, dict) and shared.get('origin')):
        return False
    return (bool(account.get('reply_credential')) and account['reply_credential'] != account.get('credential')
            and re.fullmatch(r'[0-9]{1,20}', str(account.get('reply_admin_id') or '')) is not None)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def actions(value):
    if isinstance(value,str):value=json.loads(value)
    return value.get('actions',[]) if value and value.get('kind')=='menu' else [value] if value else []


def selected(value,revision):
    return next((a for a in visible_actions(value) if a['revision']==revision),None)


def visible_actions(value):
    choices=actions(value)
    email_ready=any(a['kind'] in APPROVE_KINDS+FINISH_KINDS for a in choices)
    return [a for a in choices if a['kind']!='prepare' or (not email_ready and
            not re.match(r'(?i)^Draft (?:shorter|longer|another|alternative|follow-up)\b',a.get('label','')))]


def label(value):
    if value['kind'] == 'x' and not value.get('label'):
        return 'Reply on X' if value.get('reply_to') else 'Send'
    if value['kind'] == 'calendar':
        return calendar_writes.label(value)
    return value.get('label') or {'email':'Send','email_demo':'Send','linkedin':'Send','slack':'Send','whatsapp':'Send','intercom':'Send reply','merge':'Merge fix','prepare':'Prepare next step','open':'Open original','handoff':'Hand off'}.get(value['kind'],'Continue')


def preview(value):
    if value['kind'] == 'linkedin':
        return linkedin_preview(value)
    if value['kind'] == 'x':
        return x_preview(value)
    if value['kind'] == 'merge':
        return merge_preview(value)
    if value['kind'] == 'calendar':
        return calendar_writes.preview(value)
    if value['kind'] == 'finish':
        message = ('\n\n' + ('To ' + value['to_name'] + ':\n' if value.get('to_name') else '') + value['message']) if value.get('message') else ''
        files = '\n\nAttachments: ' + attachment_list(value) if value.get('attachments') else ''
        return value['preview'] + message + files + ('\n\n' + value['url'] if value.get('url') else '')
    if value['kind'] == 'whatsapp':
        return 'WhatsApp message to ' + value['to_name'] + '\n\n' + value['body']
    if value['kind'] == 'intercom':
        return 'Intercom reply to ' + value['to_name'] + ', as ' + value['as_name'] + '\n\n' + value['body']
    if value['kind'] == 'handoff':
        return 'Hand off to ' + value['to_name'] + ' through The Machine\n\n' + value['body']
    if value['kind'] == 'slack':
        return 'Slack message to ' + value['to_name'] + (' (' + value['to_email'] + ')' if value.get('to_email') else '') + '\n\n' + value['body'] + delegate_note(value)
    return email_preview(value) if value['kind'] in ('email','email_demo') else value['instructions']


def linkedin_preview(action):
    to = action['to_name'] + (' (linkedin.com/in/' + action['profile'] + ')' if action.get('profile') else '')
    return 'LinkedIn message to ' + to + '\n\n' + action['body']


def x_preview(action):
    if action.get('reply_to'):
        quoted = ' '.join((action.get('to_text') or '').split())
        quoted = quoted if len(quoted) <= 200 else quoted[:199] + '…'
        return 'Reply on X as @' + action['handle'] + ' to @' + action['to_screen_name'] + (': “' + quoted + '”' if quoted else '') + '\n\n' + action['body']
    return 'X message as @' + action['handle'] + ' to ' + action['to_name'] + '\n\n' + action['body']


def delegate_note(action):
    if not action.get('delegate'):
        return ''
    return f"\n\nAfter sending, the card waits {action['wait_days']} day{'s' if action['wait_days'] > 1 else ''} for {action['to_name'] + '’s' if action.get('to_name') else 'a'} reply; it closes when they answer."


def delegation(value, result, name=None):
    """Hand the task to someone: after the send, the card waits for their reply and closes when
    they answer; without one it comes back after wait_days."""
    if 'wait_days' in value and not value.get('delegate'):
        raise ValueError('wait_days goes with delegate:true')
    if not value.get('delegate'):
        return
    if value['delegate'] is not True:
        raise ValueError('delegate is true or left out')
    days = value.get('wait_days', 3)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
        raise ValueError('wait_days is a whole number of days from 1 to 14')
    result.update(delegate=True, wait_days=days)
    if name:
        result['to_name'] = name


def validate(value, config, allowed_accounts, recipient_options=None, linkedin_threads=None, x_threads=None, whatsapp_chats=None,
             intercom_conversations=None, calendar_events=None):
    if value is None:
        return None
    if isinstance(value,list):
        if not 1<=len(value)<=3:raise ValueError('Offer one to three useful next actions, not a menu of everything')
        if any(not isinstance(item,dict) for item in value):raise ValueError('Each next action must be one concrete choice')
        choices=[validate(item,config,allowed_accounts,recipient_options,linkedin_threads,x_threads,whatsapp_chats,intercom_conversations,calendar_events) for item in value]
        if len({item['revision'] for item in choices})!=len(choices) or len({label(item).lower() for item in choices})!=len(choices):
            raise ValueError('Next actions must be distinct concrete choices')
        return {'kind':'menu','actions':choices}
    if not isinstance(value, dict):
        raise ValueError('Next action must be an email draft, local preparation or original-page link')
    if value.get('label') is not None and (not isinstance(value['label'],str) or not 1<=len(value['label'])<=28
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 ,’\-/]+',value['label'])
            or value['label'].lower() in ('done','mark done','dismiss','reject','snooze','change','prepare','continue','do it','yes')):
        raise ValueError('Use a short specific action label, not a generic or owner-status button')
    if value.get('kind') in PLAIN_LINKS and isinstance(value.get('body'), str):
        value = {**value, 'body': body_text(value['body'])}  # they arrive as plain text: a Markdown link would show raw
    if value.get('kind') == 'calendar':
        # An event added, changed or deleted on a calendar the owner can write: shown on the card, done on their tap.
        result = calendar_writes.validate(value, config, calendar_events)
    elif value.get('kind') == 'open':
        if set(value) - {'kind', 'url', 'label'} or urlsplit(str(value.get('url', ''))).scheme != 'https':
            raise ValueError('Use an HTTPS original-page link, not executable instructions')
        if not value.get('label','').startswith(('Open ','View ','Read ')):
            raise ValueError('Link labels must say Open, View or Read, never pretend to execute a change')
        if len(value['url']) > 2000 or urlsplit(value['url']).username or not urlsplit(value['url']).hostname:
            raise ValueError('Invalid original-page link')
        result = dict(value)
    elif value.get('kind') == 'prepare':
        if set(value)-{'kind','label','instructions'} or not value.get('label'):
            raise ValueError('A follow-up preparation needs a specific label and instructions')
        # A follow-up is another Do it turn under the same limits so it may
        # apply a change; it can never pay, send or create calendar events.
        if not value['label'].startswith(('Draft ','Prepare ','Investigate ','Check ','Compare ','Research ',
                                          'Apply ','Fix ','Update ','Implement ','Test ','Set up ','Configure ')):
            raise ValueError('Follow-up labels describe drafting, checking, research or applying the task’s change')
        if not isinstance(value.get('instructions'),str) or not value['instructions'].strip() or len(value['instructions'])>2000:
            raise ValueError('Provide the concrete next preparation, at most 2000 characters')
        result=dict(value)
    elif value.get('kind') == 'finish':
        # Everything up to the final click is done; the owner confirms that click. Its run
        # repeats the step from these instructions, since a tab or session may have expired.
        if set(value)-{'kind','label','instructions','preview','url','message','to_name','attachments'} or not value.get('label'):
            raise ValueError('A final step needs a label, the exact instructions and a preview of what goes out')
        if value['label'].split()[0] not in FINISH_VERBS:
            raise ValueError('A final step label starts with what it does: ' + ', '.join(FINISH_VERBS[:8]) + '…')
        for field in ('instructions', 'preview'):
            if not isinstance(value.get(field),str) or not value[field].strip() or len(value[field])>2000 or '\x00' in value[field]:
                raise ValueError('Give the exact final step and what it submits, each at most 2000 characters')
        if 'url' in value and (not isinstance(value['url'],str) or len(value['url'])>2000 or urlsplit(value['url']).scheme!='https'
                               or urlsplit(value['url']).username or not urlsplit(value['url']).hostname):
            raise ValueError('The final step’s page is an HTTPS link')
        # A message the step posts (a reply in a support inbox, a chat or DM in a web app): its exact
        # text, which the card shows and the owner can edit like a draft; Confirm types exactly that.
        if 'message' in value and (not isinstance(value['message'],str) or not value['message'].strip()
                                   or len(value['message'])>8000 or '\x00' in value['message']):
            raise ValueError('message is the exact text the step posts, at most 8000 characters')
        if 'to_name' in value and (not isinstance(value['to_name'],str) or not 1<=len(value['to_name'].strip())<=100 or '\n' in value['to_name']):
            raise ValueError('to_name is who the message goes to, on one line')
        if 'to_name' in value and 'message' not in value:
            raise ValueError('to_name goes with message')
        result=dict(value)
        if 'message' in result:result['message']=result['message'].strip()
        # Files the step uploads (a receipt with a reply, a document a form asks for): only files this
        # Machine saved for the card, copied into the store like an email's, so the owner sees and opens
        # exactly those bytes on the card and Confirm uploads exactly them.
        if 'attachments' in value:result['attachments']=store_attachments(value['attachments'],config)
    elif value.get('kind') == 'email_demo':
        if set(value)-{'kind','label','subject','body'} or value.get('label','Send')!='Send':
            raise ValueError('The Send demo is preview-only with fixed fictional addresses')
        for field,limit in [('subject',250),('body',12000)]:
            if not isinstance(value.get(field),str) or not value[field].strip() or len(value[field])>limit or '\x00' in value[field]:
                raise ValueError('Provide the concrete demo email')
        if '\r' in value['subject'] or '\n' in value['subject']:raise ValueError('Invalid demo subject')
        result={'kind':'email_demo','label':'Send','from':'the-machine@example.invalid',
                'to':['alex@example.invalid'],'cc':[],'bcc':[],'subject':value['subject'],'body':value['body']}
    elif value.get('kind') == 'email':
        fields = {'kind', 'label','account_id', 'to', 'cc', 'bcc', 'subject', 'body', 'thread_id', 'in_reply_to', 'references', 'signature', 'snooze_days', 'attachments',
                  'delegate', 'wait_days', 'to_name'}
        if set(value) - fields:
            raise ValueError('Unsupported email fields')
        if not isinstance(value.get('signature', True), bool):
            raise ValueError('signature is true (default: your signature is added) or false')
        # Any of the owner's Gmail or Outlook accounts for the same company (or the same address) as the task's signals: a task
        # that came from a calendar or Slack still sends from that company's mailbox.
        task_accounts = [a for a in config['accounts'] if a['id'] in allowed_accounts]
        boundaries = {a.get('boundary') for a in task_accounts if a.get('boundary')}
        emails = {str(a.get('email') or '').lower() for a in task_accounts if a.get('email')}
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and mail_account(a)), None)
        if not account or (account['id'] not in allowed_accounts and account.get('boundary') not in boundaries
                           and str(account.get('email') or '').lower() not in emails):
            raise ValueError('Use a Gmail or Outlook account of this task’s company')
        result = {'kind': 'email', 'account_id': account['id'], 'from': account['email']}
        if value.get('label'):
            if value['label']!='Send' and not value['label'].startswith('Send '):raise ValueError('Email button labels must say Send, such as Send reply')
            result['label']=value['label']
        for field in ('to', 'cc', 'bcc'):
            addresses = value.get(field, [])
            if not isinstance(addresses, list) or len(addresses) > 20 or any(
                    not isinstance(a, str) or len(a)>254 or not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+', a)
                    for a in addresses):
                raise ValueError('Recipients must be explicit email addresses')
            result[field] = addresses
        if not result['to']:
            # No verified recipient: the owner picks from the thread's own addresses, which the
            # service supplies. The model cannot add options.
            options = [a for a in (recipient_options or []) if a not in result['cc'] + result['bcc']][:6]
            if not options:
                raise ValueError('An email needs a recipient, and the task’s emails have no address to offer instead')
            result['to_options'] = options
        for field, limit in [('subject', 250), ('body', 12000)]:
            text = value.get(field)
            if not isinstance(text, str) or not text.strip() or len(text) > limit or '\x00' in text:
                raise ValueError('Provide a concrete subject and draft body within the size limit')
            if field == 'subject' and ('\r' in text or '\n' in text):
                raise ValueError('Subject cannot contain another header')
            result[field] = closing_spaced(text) if field == 'body' else text
        # The real signature (Gmail's own, or the one set or found for this account) is added at send time; the body ends at the name.
        result['signature'] = value.get('signature', True)
        if value.get('attachments'):
            result['attachments'] = store_attachments(value['attachments'], config)
        for field in ('thread_id', 'in_reply_to', 'references'):
            if value.get(field):
                text = value[field]
                # A Gmail thread ID is hex; an Outlook conversation ID is base64
                pattern = (r'[A-Za-z0-9+/=_-]{1,400}' if outlook(account) else r'[a-fA-F0-9]{1,64}') if field == 'thread_id' else r'<[^\s<>\r\n]+>' if field == 'in_reply_to' else r'<[^\s<>\r\n]+>(?: <[^\s<>\r\n]+>)*'
                if not isinstance(text, str) or len(text) > 1000 or not re.fullmatch(pattern, text):
                    raise ValueError('Invalid email thread header')
                result[field] = text
        if result.get('thread_id') and not result.get('in_reply_to'):
            raise ValueError('Thread replies need the original RFC Message-ID, not only a Gmail thread ID')
        days = value.get('snooze_days', 3 if result.get('thread_id') else 0)
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 14:
            raise ValueError('snooze_days is a whole number of days from 0 (no snooze) to 14')
        if days and not result.get('thread_id'):
            raise ValueError('Only a reply in an existing thread can be snoozed after sending')
        if days:
            # After sending, the thread leaves the inbox until then; a reply brings it back sooner.
            result['snooze_days'] = days
        if value.get('delegate') and len(result['to']) != 1:
            raise ValueError('Hand a task to one person: delegate needs exactly one To address')
        if 'to_name' in value and not value.get('delegate'):
            raise ValueError('to_name goes with delegate:true')
        name = value.get('to_name')
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or '\n' in name):
            raise ValueError('to_name is the person’s name')
        delegation(value, result, name and name.strip())
    elif value.get('kind') == 'linkedin':
        # A LinkedIn message: a reply in a conversation from this task's own signals, or a new
        # message to a profile slug the owner gave. Sent only after the owner's approval.
        if set(value) - {'kind', 'label', 'account_id', 'conversation_id', 'profile', 'to_name', 'body'}:
            raise ValueError('Unsupported LinkedIn message fields')
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and a.get('service') == 'linkedin'), None)
        if not account or account['id'] not in allowed_accounts:
            raise ValueError('Use the LinkedIn account belonging to this task')
        result = {'kind': 'linkedin', 'account_id': account['id']}
        if value.get('label'):
            if value['label'] != 'Send' and not value['label'].startswith('Send '):
                raise ValueError('LinkedIn button labels must say Send, such as Send reply')
            result['label'] = value['label']
        threads = linkedin_threads or {}
        if bool(value.get('conversation_id')) == bool(value.get('profile')):
            raise ValueError('A LinkedIn message needs either the conversation_id from this task or a profile slug, not both')
        if value.get('conversation_id'):
            if value['conversation_id'] not in threads:
                raise ValueError('Reply only in a LinkedIn conversation from this task’s own messages')
            result.update(conversation_id=value['conversation_id'], to_name=threads[value['conversation_id']])
        else:
            if not isinstance(value['profile'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_%-]{1,99}', value['profile']):
                raise ValueError('profile is the slug from linkedin.com/in/<slug>/')
            name = value.get('to_name')
            if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100 or '\n' in name:
                raise ValueError('Give to_name: the person’s name as shown on that profile; the send checks it')
            result.update(profile=value['profile'], to_name=name.strip())
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or len(body) > 8000 or '\x00' in body:
            raise ValueError('Provide the concrete LinkedIn message, at most 8000 characters')
        result['body'] = body.strip()
    elif value.get('kind') == 'x':
        # An X reply to a post, or a DM, from this task's own X signals; integrations/x_inbox.py sends it
        # through the owner's signed-in x.com session in the OpenClaw browser, only after their Send now.
        if set(value) - {'kind', 'label', 'account_id', 'reply_to', 'conversation_id', 'body'}:
            raise ValueError('Unsupported X fields; plain text only, no media')
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and a.get('service') == 'x'), None)
        if not account or account['id'] not in allowed_accounts:
            raise ValueError('Use the X account belonging to this task')
        result = {'kind': 'x', 'account_id': account['id'], 'handle': account['handle']}
        if value.get('label'):
            if not re.match(r'(?:Send|Reply)\b', value['label']):
                raise ValueError('X button labels must say Send or Reply, such as Reply on X')
            result['label'] = value['label']
        threads = x_threads or {}
        if bool(value.get('reply_to')) == bool(value.get('conversation_id')):
            raise ValueError('An X next action needs either reply_to (a post id) or conversation_id (a DM), not both')
        if value.get('reply_to'):
            post = (threads.get('mentions') or {}).get(str(value['reply_to']))
            if not post or post.get('account_id') != account['id']:
                raise ValueError('Reply only to a post from this task’s own X mentions (reply_to is its tweet_id)')
            result.update(reply_to=str(value['reply_to']), to_screen_name=post['screen_name'], to_name=post.get('name') or '@' + post['screen_name'],
                          to_text=post.get('text') or '', max_length=25000 if account.get('long_posts') else 280)
        else:
            chat = (threads.get('dms') or {}).get(str(value['conversation_id']))
            if not chat or chat.get('account_id') != account['id']:
                raise ValueError('Send DMs only in an X conversation from this task’s own messages')
            result.update(conversation_id=str(value['conversation_id']), to_name=chat['name'], max_length=10000)
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or '\x00' in body or text_length(result, body.strip()) > result['max_length']:
            raise ValueError(f"Provide the concrete X {'reply' if value.get('reply_to') else 'message'}, at most {result['max_length']} characters (links count 23)")
        result['body'] = body.strip()
    elif value.get('kind') == 'slack':
        # A Slack direct message from the owner's own Slack account, mostly to hand a task to a
        # colleague. Any Slack account in the task's boundary; sent only after the owner's Send now.
        if set(value) - {'kind', 'label', 'account_id', 'user_id', 'to_email', 'to_name', 'body', 'delegate', 'wait_days'}:
            raise ValueError('Unsupported Slack message fields')
        boundaries = {a.get('boundary') for a in config['accounts'] if a['id'] in allowed_accounts}
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and a.get('service') == 'slack'), None)
        if not account or (account['id'] not in allowed_accounts and account.get('boundary') not in boundaries):
            raise ValueError('Use the Slack account of this task’s company')
        result = {'kind': 'slack', 'account_id': account['id']}
        if value.get('label'):
            if value['label'] != 'Send' and not value['label'].startswith('Send '):
                raise ValueError('Slack button labels must say Send, such as Send to Sam')
            result['label'] = value['label']
        if bool(value.get('user_id')) == bool(value.get('to_email')):
            raise ValueError('A Slack message needs either the person’s Slack user_id (from a Slack signal) or their work email as to_email')
        if value.get('user_id'):
            if not isinstance(value['user_id'], str) or not re.fullmatch(r'[UW][A-Z0-9]{6,20}', value['user_id']):
                raise ValueError('user_id is a Slack user ID such as U012ABC34')
            result['user_id'] = value['user_id']
        else:
            if not isinstance(value['to_email'], str) or len(value['to_email']) > 254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value['to_email']):
                raise ValueError('to_email is one email address')
            result['to_email'] = value['to_email'].lower()
        name = value.get('to_name')
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100 or '\n' in name:
            raise ValueError('Give to_name: the person’s name as shown in Slack; the send checks it')
        result['to_name'] = name.strip()
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or len(body) > 8000 or '\x00' in body:
            raise ValueError('Provide the concrete Slack message, at most 8000 characters')
        result['body'] = body.strip()
        delegation(value, result)
    elif value.get('kind') == 'whatsapp':
        # A WhatsApp message in an existing chat, from the owner's linked account; wacli sends it only after the
        # owner's Send now. Text only. The chat is one from this task's own WhatsApp messages, or, when the owner
        # talks to this person on WhatsApp although the task came in another way, their existing chat that the
        # service looked up in that account (whatsapp_chats marks it so): the card switches to it.
        if set(value) - {'kind', 'label', 'account_id', 'chat_id', 'to_name', 'body'}:
            raise ValueError('Unsupported WhatsApp fields; plain text only, no media')
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and a.get('service') == 'whatsapp'), None)
        if not account:
            raise ValueError('Use one of the owner’s WhatsApp accounts')
        result = {'kind': 'whatsapp', 'account_id': account['id']}
        if value.get('label'):
            if value['label'] != 'Send' and not value['label'].startswith('Send '):
                raise ValueError('WhatsApp button labels must say Send, such as Send reply')
            result['label'] = value['label']
        chat = (whatsapp_chats or {}).get(str(value.get('chat_id') or ''))
        if not chat or chat.get('account_id') != account['id']:
            raise ValueError('Send only in an existing WhatsApp chat: one from this task’s own messages (its metadata.chat_id), '
                             'or the person’s chat in this account (chat_id, or to_name to look it up)')
        result.update(chat_id=str(value['chat_id']), to_name=chat.get('name') or 'WhatsApp chat', max_length=4000)
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or len(body) > 4000 or '\x00' in body:
            raise ValueError('Provide the concrete WhatsApp message, at most 4000 characters')
        result['body'] = body.strip()
    elif value.get('kind') == 'intercom':
        # A reply in an Intercom conversation from this task's own messages, as the owner's own Intercom admin (the one
        # who signed in with Intercom for replies). It goes out only after the owner's Send on this exact text. A plain
        # comment: never a note, an assignment, a close or a tag. A token shared through a Fleet only reads.
        if set(value) - {'kind', 'label', 'account_id', 'conversation_id', 'body'}:
            raise ValueError('Unsupported Intercom fields; a plain reply only: no notes, assigning, closing or tags')
        account = next((a for a in config['accounts'] if a['id'] == value.get('account_id') and a.get('service') == 'intercom'), None)
        if not account or account['id'] not in allowed_accounts:
            raise ValueError('Use the Intercom account belonging to this task')
        if not intercom_replies_on(account):
            raise ValueError('Replies are not on for this Intercom account: the owner turns them on by signing in with Intercom '
                             'themselves (a Fleet-shared token only reads). Give the reply text in the body instead.')
        result = {'kind': 'intercom', 'account_id': account['id'], 'admin_id': str(account['reply_admin_id']),
                  'as_name': account.get('reply_admin_name') or 'your Intercom admin'}
        if value.get('label'):
            if not re.match(r'(?:Send|Reply)\b', value['label']):
                raise ValueError('Intercom button labels must say Send or Reply, such as Send reply')
            result['label'] = value['label']
        conversation = (intercom_conversations or {}).get(str(value.get('conversation_id') or ''))
        if not conversation or conversation.get('account_id') != account['id']:
            raise ValueError('Reply only in an Intercom conversation from this task’s own messages (conversation_id is its metadata.conversation_id)')
        result.update(conversation_id=str(value['conversation_id']), to_name=conversation.get('name') or 'Intercom conversation', max_length=10000)
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or len(body) > 10000 or '\x00' in body:
            raise ValueError('Provide the concrete Intercom reply, at most 10000 characters')
        result['body'] = body.strip()
    elif value.get('kind') == 'handoff':
        # Hand the task to a teammate in the owner's Work Fleet, through The Machine (docs/fleet.md): their
        # Machine gets it as a handoff to accept. Nothing goes by itself: the button opens the Fleet handoff
        # form with this text, for the owner to read, edit and send. The caller checks they are a teammate.
        if set(value) - {'kind', 'label', 'to_email', 'to_name', 'body'}:
            raise ValueError('Unsupported handoff fields')
        result = {'kind': 'handoff'}
        if value.get('label'):
            if not value['label'].startswith('Hand off'):
                raise ValueError('Handoff button labels say Hand off, such as Hand off to Sam')
            result['label'] = value['label']
        if not isinstance(value.get('to_email'), str) or len(value['to_email']) > 254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value['to_email']):
            raise ValueError('to_email is the teammate’s work email, as in the Fleet')
        result['to_email'] = value['to_email'].lower()
        name = value.get('to_name')
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100 or '\n' in name:
            raise ValueError('Give to_name: the teammate’s name')
        result['to_name'] = name.strip()
        body = value.get('body')
        if not isinstance(body, str) or not body.strip() or len(body.strip()) > 1000 or '\x00' in body:
            raise ValueError('Provide the handoff message itself (what they need to know), at most 1000 characters')
        result['body'] = body.strip()
    elif value.get('kind') == 'merge':
        result = merge_action(value, config)
    else:
        raise ValueError('Only email drafts, LinkedIn, Slack, X and WhatsApp messages, Fleet handoffs, local preparation and original-page links are supported; no payments or arbitrary writes')
    result['revision'] = hashlib.sha256(canonical(result).encode()).hexdigest()[:12]
    return result


PLACEHOLDER = re.compile(r'(?i)\[(?:name|first name|your name|recipient|company|date|time|link|url|amount|insert[^\]]*|x{2,}|\.\.\.|…)\]|\{\{[^}]*\}\}|<(?:name|link|date)>|\bTODO\b|\bTBD\b|\bXXX\b|lorem ipsum')
# Only the sender's own "here's the file" phrasing; "thanks for the attached invoice" is fine.
ATTACHED = re.compile(r"(?i)\b(?:I(?:'ve| have)? attached|attached (?:is|are|you'll find|please find)|(?:please )?find attached|see (?:the )?attached|I(?:'m| am) attaching|in bijlage (?:vind|stuur|zit)|(?:vind|stuur) (?:je|u) (?:hierbij|in bijlage)|bijgevoegd vind|ci-joint|anbei)\b")

# A final step whose instructions point at a file this Machine saved (to upload it) must list it.
SAVED_FILE = re.compile(r'(?:data/assistant-attachments|memory/assistant)/\S*[^\s/.,;:)]')


def check(value, result='', own_addresses=()):
    """Mistakes a Do it result can't go out with: checked before it's saved, so the
    run fixes them itself instead of the owner tapping Change. Returns plain problems; empty is fine."""
    problems = []
    for action in actions(value):
        text = (action.get('subject') or '') + '\n' + (action.get('body') or action.get('preview') or '') + ('\n' + action['message'] if action.get('message') else '')
        if PLACEHOLDER.search(text):
            problems.append(f"“{action.get('label') or action['kind']}” still has a placeholder ({PLACEHOLDER.search(text)[0]}): fill it in from the evidence")
        if re.search(r'\[[^\]]+\]\((?!https?://)[^)]*\)', text):
            problems.append('A link has no real https:// address')
        if action['kind'] == 'finish' and not action.get('attachments') and (
                ATTACHED.search(text) or SAVED_FILE.search(action['instructions'])):
            problems.append(f"“{action['label']}” uploads or mentions a file but lists none: add attachments:[<paths>] so the owner sees it, or reword it")
        if action['kind'] == 'email':
            if ATTACHED.search(action['body']) and not action.get('attachments'):
                problems.append('The email mentions an attachment but has none: add attachments:[<paths>] or reword it')
            own = {a.lower() for a in own_addresses if a}
            if own and action['to'] and set(a.lower() for a in action['to']) <= own:
                problems.append('The email only goes to your own address: use the other person’s verified address')
    if not any(a['kind'] in ('email', 'email_demo') for a in actions(value)) and re.search(r'(?im)^\s*\**subject:\**\s*\S', result or ''):
        problems.append('The result contains an email (a Subject: line) but no email next action: put the ready email in next_actions so it gets a Send button')
    return problems


def finish_text_field(action):
    """What the owner edits in a final step: its message when it posts one, otherwise what goes out (preview)."""
    return 'message' if action.get('message') else 'preview'


def draft_text(action):
    """The editable text of a ready draft or final step."""
    return action[finish_text_field(action)] if action['kind'] == 'finish' else action.get('body')


def revise(action, subject=None, body=None):
    """The owner's own edit of a ready draft: same recipients, account and attachments, new text and revision.
    A final step (kind:finish) keeps its instructions and page; its message (or preview) becomes the edited
    text, marked edited so its Confirm run types exactly that."""
    if action['kind'] == 'finish':
        field = finish_text_field(action)
        result = {k: v for k, v in action.items() if k != 'revision'}
        if body is not None:
            if not isinstance(body, str) or not body.strip() or '\x00' in body or len(body.strip()) > (8000 if field == 'message' else 2000):
                raise ValueError('The text needs some words, within the size limit')
            if body.strip() != action[field]:
                result[field] = body.strip()
                result['edited'] = True
        result['revision'] = hashlib.sha256(canonical(result).encode()).hexdigest()[:12]
        return result
    if action['kind'] not in ('email',) + TEXT_KINDS:
        raise ValueError('Only email, LinkedIn, Slack, X, WhatsApp and Intercom drafts and final steps can be edited')
    result = {k: v for k, v in action.items() if k != 'revision'}
    if body is not None:
        if (not isinstance(body, str) or not body.strip() or '\x00' in body
                or text_length(action, body.strip()) > action.get('max_length', 8000 if action['kind'] in TEXT_KINDS else 12000)):
            raise ValueError('The draft needs some text, within the size limit')
        result['body'] = body.strip() if action['kind'] in TEXT_KINDS else closing_spaced(body)
    if subject is not None and action['kind'] not in TEXT_KINDS:
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 250 or '\r' in subject or '\n' in subject or '\x00' in subject:
            raise ValueError('The subject needs one line of text')
        result['subject'] = subject
    result['revision'] = hashlib.sha256(canonical(result).encode()).hexdigest()[:12]
    return result


def closing_spaced(body):
    """Owner standard: a blank line between the closing ("Regards,") and the name."""
    lines = body.rstrip().split('\n')
    if (len(lines) >= 2 and lines[-2].strip().endswith(',') and len(lines[-2].strip()) <= 40
            and re.fullmatch(r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)?", lines[-1].strip())):
        lines.insert(-1, '')
    return '\n'.join(lines)


# Drafts that reach the recipient as plain text (WhatsApp, LinkedIn, X, a Fleet handoff): links go as words (URL).
# Email and Slack keep Markdown links: the send turns them into real links (body_html, slack_text).
PLAIN_LINKS = ('handoff', 'whatsapp', 'linkedin', 'x')
LINK = re.compile(r'\[([^\[\]\n]{1,200})\]\((https://[^\s()<>]{1,2000})\)|(https://[^\s<>]*[^\s<>.,;:!?)\]])')


def body_text(body):
    return LINK.sub(lambda m: f'{m[1]} ({m[2]})' if m[1] else m[3], body)


def body_html(body):
    parts, last = [], 0
    for m in LINK.finditer(body):
        parts.append(html.escape(body[last:m.start()]))
        url, text = (m[2], m[1]) if m[1] else (m[3], m[3])
        parts.append(f'<a href="{html.escape(url)}">{html.escape(text)}</a>')
        last = m.end()
    parts.append(html.escape(body[last:]))
    return ''.join(parts).replace('\n', '<br>\n')


class SignatureText(HTMLParser):
    BREAKS = {'br', 'div', 'p', 'tr', 'li', 'table'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.href, self.anchor = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAKS:
            self.parts.append('\n')
        elif tag == 'a':
            self.href, self.anchor = dict(attrs).get('href'), []

    def handle_endtag(self, tag):
        if tag in self.BREAKS:
            self.parts.append('\n')
        elif tag == 'a' and self.href is not None:
            text = ''.join(self.anchor).strip()
            self.parts.append(text if not text or self.href.startswith(('mailto:', 'tel:')) or text in self.href else f'{text} ({self.href})')
            self.href = None

    def handle_data(self, data):
        (self.anchor if self.href is not None else self.parts).append(data)


def signature_text(signature):
    parser = SignatureText()
    parser.feed(signature)
    parser.close()
    lines = [' '.join(line.split()) for line in ''.join(parser.parts).split('\n')]
    return '\n'.join(line for line in lines if line)


def email_preview(action):
    lines = ['From: ' + action['from'], 'To: ' + (', '.join(action['to']) or 'you choose: ' + ', '.join(action.get('to_options', [])))]
    for field in ('cc', 'bcc'):
        if action.get(field):
            lines.append(field.upper() + ': ' + ', '.join(action[field]))
    return ('\n'.join(lines) + '\nSubject: ' + action['subject'] + '\n\n' + action['body']
            + ('\n\n+ your signature' if action.get('signature') else '')
            + ('\n\nAttachments: ' + attachment_list(action) if action.get('attachments') else '')
            + ('\n' + snooze_note(action) if action.get('snooze_days') else '') + delegate_note(action))


def snooze_note(action):
    days = action['snooze_days']
    return f"After sending, the thread leaves your inbox for {days} day{'s' if days > 1 else ''} (a weekday morning; a reply brings it back sooner)." 


def mime_bytes(action, job, signature='', config=None):
    message = EmailMessage(policy=email.policy.SMTP)
    message['From'] = action['from']
    message['To'] = ', '.join(action['to'])
    for field in ('cc', 'bcc'):
        if action[field]:
            message[field] = ', '.join(action[field])
    message['Subject'] = action['subject']
    message['Message-ID'] = job['rfc_message_id']
    # The moment it goes out, not the approval: a re-approved or retried send can be days later.
    message['Date'] = format_datetime(datetime.datetime.now(datetime.timezone.utc))
    if action.get('in_reply_to'):
        message['In-Reply-To'] = action['in_reply_to']
        message['References'] = action.get('references', action['in_reply_to'])
    signature = signature if action.get('signature') else ''
    text = body_text(action['body']) + ('\n\n' + signature_text(signature) if signature else '')
    message.set_content(text, charset='utf-8')
    # Links behind their text, then a blank line and the real signature.
    page = '<div dir="ltr">' + body_html(action['body']) + '<br>\n<br>\n'
    if signature:
        page += '<div class="gmail_signature">' + signature + '</div>'
    message.add_alternative(page + '</div>', subtype='html', charset='utf-8')
    for record in action.get('attachments', []):
        maintype, _, subtype = record['type'].partition('/')
        message.add_attachment(attachment_bytes(record, config or {}), maintype=maintype, subtype=subtype or 'octet-stream',
                               filename=record['name'])
    return message.as_bytes()


def google_error_code(stdout):
    """The HTTP status Google returned, from gog's --json error, or None when it is unclear."""
    try:
        message = json.loads(stdout)['error']['message']
        codes = re.findall(r'"code":\s*(\d{3})', message)
        return int(codes[0]) if len(set(codes)) == 1 else None
    except (ValueError, KeyError, TypeError):
        return None


class EmailTransport:
    def __init__(self, config, credential, read):
        self.config, self.credential, self.read = config, credential, read
        self.signatures = {}

    def account(self, action):
        if action['kind']!='email':raise ValueError('Only a real owner-approved email can use the email transport')
        account = next(a for a in self.config['accounts'] if a['id'] == action['account_id'])
        if account['email'].lower() != action['from'].lower():
            raise ValueError('The sender account changed; review the draft again')
        return account

    def env(self):
        env = os.environ.copy()
        env.update(GOG_KEYRING_BACKEND='file', GOG_KEYRING_PASSWORD=self.credential(self.config, 'gog-password'))
        env.pop('DBUS_SESSION_BUS_ADDRESS', None)
        return env

    def preflight(self, action):
        account = self.account(action)
        if outlook(account):
            return self.outlook_preflight(action, account)
        if not (granted_scopes(self.config, self.credential, account) & SEND_SCOPES):
            raise ValueError('Gmail is connected read-only. Sending permission needs to be enabled for this account; nothing was sent.')
        profile = self.read(self.config, account, 'gmail', 'v1', 'gmail.users.getProfile',
                            {'userId': 'me'}, 'https://www.googleapis.com/auth/gmail.readonly')
        if profile.get('emailAddress', '').lower() != action['from'].lower():
            raise ValueError('Gmail sender identity could not be verified. Nothing was sent.')
        for record in action.get('attachments', []):
            try:
                attachment_bytes(record, self.config)
            except OSError:
                raise ValueError(f"The attachment {record['name']} is missing. Nothing was sent.")
        if action.get('signature'):
            self.signatures.pop(action['from'].lower(), None)  # read fresh: the owner may have changed it
            try:
                self.signature(action, account)
            except Exception:
                raise ValueError('I could not load your Gmail signature. Nothing was sent; tap Send again.')

    def signature(self, action, account):
        if not action.get('signature'):
            return ''
        key = action['from'].lower()
        if key not in self.signatures:  # Loaded by preflight, before the send is admitted.
            own = signatures.get(self.config, account['id'])
            if outlook(account):
                # Graph has no signature API: the one the owner set, else the one their sent mail ends with.
                self.signatures[key] = (own or self.detect_signature(account) or {}).get('html', '')
            elif own and own.get('source') == 'owner' and own['html'].strip():
                self.signatures[key] = own['html']  # the owner's override of Gmail's own
            else:
                send_as = self.read(self.config, account, 'gmail', 'v1', 'gmail.users.settings.sendAs.get',
                                    {'userId': 'me', 'sendAsEmail': action['from']}, 'https://www.googleapis.com/auth/gmail.readonly')
                self.signatures[key] = send_as.get('signature') or ''
        return self.signatures[key]

    def detect_signature(self, account):
        """Find an Outlook signature in the last sent messages and keep it; None when Outlook can't be read now."""
        try:
            found = signatures.detect(ms_graph.sent_bodies(self.config, self.credential, account),
                                      (str(self.config.get('owner_name') or '').split() or [''])[0])
        except ms_graph.GraphError:
            return None
        try:
            return signatures.save(self.config, account['id'], found, 'detected')
        except (OSError, ValueError):
            return {'html': found}

    def outlook_preflight(self, action, account):
        if 'Mail.Send' not in granted_scopes(self.config, self.credential, account):
            raise ValueError('Outlook is connected read-only. Connect it again with sending on; nothing was sent.')
        profile = self.read(self.config, account, 'gmail', 'v1', 'gmail.users.getProfile', {'userId': 'me'}, 'User.Read')
        if profile.get('emailAddress', '').lower() != action['from'].lower():
            raise ValueError('Outlook sender identity could not be verified. Nothing was sent.')
        for record in action.get('attachments', []):
            try:
                attachment_bytes(record, self.config)
            except OSError:
                raise ValueError(f"The attachment {record['name']} is missing. Nothing was sent.")
        if action.get('signature'):
            self.signatures.pop(action['from'].lower(), None)
            self.signature(action, account)  # set or found now; none found means none added

    def outlook_send(self, action, job, account):
        """A draft from the exact MIME (so the Message-ID and reply headers stay ours), then that draft is sent: its
        immutable ID is the sent message's. Without Mail.ReadWrite, sendMail with the MIME, then Sent Items."""
        mime = base64.b64encode(mime_bytes(action, job, self.signature(action, account), self.config))
        call = lambda *args, **kwargs: ms_graph.call(self.config, self.credential, account, *args, **kwargs)
        rejected = lambda error: error.category == 'outlook' and error.status and 400 <= error.status < 500 and error.status not in (408, 429)
        try:
            granted = ms_graph.scopes(self.config, self.credential, account)
        except ms_graph.GraphError:
            return {'not_sent': True}
        if 'Mail.ReadWrite' in granted:
            try:
                draft = call('messages.create', {}, body=mime, content_type='text/plain', write=True)
            except ms_graph.GraphError as error:
                # No draft, so nothing can have gone out: a definite refusal, or try again shortly.
                return {'rejected': error.status} if rejected(error) else {'not_sent': True}
            if not draft.get('id'):
                return {'not_sent': True}
            try:
                call('messages.send', {'id': draft['id']}, body=b'', content_type='application/json', write=True)
            except ms_graph.GraphError as error:
                return {'rejected': error.status} if rejected(error) else {}
            return {'message_id': draft['id']}
        try:
            call('sendMail', {}, body=mime, content_type='text/plain', write=True)
        except ms_graph.GraphError as error:
            return {'rejected': error.status} if rejected(error) else {}
        for _ in range(4):  # sendMail answers 202 without an ID: find it in Sent Items
            time.sleep(3)
            found = self.reconcile(action, job)
            if found:
                return found
        return {}

    def send(self, action, job):
        account = self.account(action)
        if outlook(account):
            return self.outlook_send(action, job, account)
        request = {'raw': base64.urlsafe_b64encode(mime_bytes(action, job, self.signature(action, account), self.config)).decode().rstrip('=')}
        if action.get('thread_id'):
            request['threadId'] = action['thread_id']
        if account.get('transport') == 'broker':
            try:
                result = google_api.call(self.config, self.credential, account, 'gmail.users.messages.send', {'userId': 'me'},
                                         'https://www.googleapis.com/auth/gmail.send', body=request, write=True)
            except google_api.BrokerError as error:
                # Same rule as below: only a definite client error from Gmail means nothing went out.
                if error.category == 'gmail' and error.status and 400 <= error.status < 500 and error.status not in (408, 429):
                    return {'rejected': error.status}
                return {}
            return {'message_id': result['id']} if result.get('id') else {}
        # Private temporary file avoids exposing the email in process arguments.
        # The worker must have committed exact owner approval/admission first.
        with tempfile.NamedTemporaryFile(mode='w',suffix='.json') as body:
            body.write(canonical(request));body.flush()
            command = [self.config['gog'], 'api', 'call', 'gmail', 'v1', 'gmail.users.messages.send',
                       '--params', '{"userId":"me"}', '--body', '@'+body.name, '--scope', 'https://www.googleapis.com/auth/gmail.send',
                       '--account', account['email'], '--client', account['client'], '--allow-write', '--force', '--no-input', '--json']
            result = subprocess.run(command, env=self.env(), capture_output=True, text=True, timeout=60)
        if result.returncode:
            # Gmail answered with a client error (bad address, too large, no permission):
            # the request was refused, so nothing went out. Anything else stays unknown.
            code = google_error_code(result.stdout)
            if code and 400 <= code < 500 and code not in (408, 429):
                return {'rejected': code}
            return {}  # Unknown outcome: never retry a Gmail send after an error/timeout.
        result = json.loads(result.stdout)
        return {'message_id': result['id']} if result.get('id') else {}

    def reconcile(self, action, job):
        if outlook(self.account(action)) and not job.get('rfc_message_id'):
            return {}
        result = self.read(self.config, self.account(action), 'gmail', 'v1', 'gmail.users.messages.list',
                           {'userId': 'me', 'q': 'in:sent rfc822msgid:' + job['rfc_message_id'], 'maxResults': 2},
                           'https://www.googleapis.com/auth/gmail.readonly')
        messages = result.get('messages', [])
        if len(messages) == 1:
            return {'message_id': messages[0]['id']}
        return {}  # Absence or ambiguity never means it is safe to resend.


class LinkedInBusy(Exception):
    """The shared browser is busy: try again shortly; nothing was sent."""


def browser_env(config, action, prefix):
    """A LinkedIn or X send goes out from the browser its account reads with, when it has its own."""
    account = next((a for a in config.get('accounts', []) if a.get('id') == action.get('account_id')), {})
    own = {f'{prefix}_{key.upper()}': account[key] for key in ('browser_profile', 'browser_cdp') if account.get(key)}
    return {**os.environ, **own} if own else None


class LinkedInTransport:
    """Sends one owner-approved LinkedIn message through integrations/linkedin_inbox.py (config
    "linkedin_inbox" overrides the path). The job id is the idempotency token: the reader logs the
    intent before writing and refuses a token it has seen."""

    def __init__(self, config):
        self.config = config

    def cli(self, action):
        return self.config.get('linkedin_inbox', str(LINKEDIN_INBOX))

    def target(self, action):
        return ['--conversation', action['conversation_id']] if action.get('conversation_id') else ['--profile', action['profile']]

    def run(self, action, args, timeout=240):
        result = subprocess.run([self.cli(action), *args], capture_output=True, text=True, timeout=timeout,
                                env=browser_env(self.config, action, 'LINKEDIN_INBOX'))
        if result.returncode == 2 and 'busy' in result.stderr.lower():
            raise LinkedInBusy()
        return result

    def preflight(self, action):
        if action['kind'] != 'linkedin':
            raise ValueError('Only an owner-approved LinkedIn message can use the LinkedIn transport')
        result = self.run(action, ['send', *self.target(action), '--text', action['body'], '--json'])
        if result.returncode:
            err = result.stderr.lower()
            raise ValueError('LinkedIn is signed out in the OpenClaw browser. Nothing was sent.' if 'signed out' in err
                             else 'I couldn’t find that LinkedIn recipient. Nothing was sent.' if 'no recipient' in err
                             else 'I couldn’t reach LinkedIn. Nothing was sent; tap Send again.')
        try:
            name = json.loads(result.stdout)['preview']['name']
        except (ValueError, KeyError, TypeError):
            raise ValueError('I couldn’t check the LinkedIn recipient. Nothing was sent.') from None
        if action.get('profile') and ' '.join(name.lower().split()) != ' '.join(action['to_name'].lower().split()):
            raise ValueError(f'That LinkedIn profile belongs to {name}, not {action["to_name"]}. Nothing was sent.')

    def send(self, action, job):
        try:
            result = self.run(action, ['send', *self.target(action), '--text', action['body'], '--send-id', 'assistant-' + job['id'], '--yes', '--json'])
        except LinkedInBusy:
            return {'not_sent': True}
        try:
            out = json.loads(result.stdout or '{}')
        except ValueError:
            return {}
        if out.get('sent') == 'sent':
            return {'message_id': out.get('messageUrn') or 'linkedin:' + job['id']}
        if out.get('sent') == 'failed' and isinstance(out.get('status'), int):
            return {'rejected': out['status']}
        return {}  # Unknown outcome: never resend; reconcile checks the conversation.

    def reconcile(self, action, job):
        result = self.run(action, ['verify', '--send-id', 'assistant-' + job['id']])
        if result.returncode:
            return {}
        out = json.loads(result.stdout)
        return {'message_id': out.get('message') or 'linkedin:' + job['id']} if out.get('found') else {}


class XBusy(LinkedInBusy):
    """The OpenClaw browser (or X's account switcher) is busy: try again shortly; nothing was sent."""


class XTransport:
    """Sends one owner-approved X reply or DM through integrations/x_inbox.py (config "x_inbox"
    overrides the path), in the owner's own signed-in x.com session in the OpenClaw browser. The job
    id is the token: the reader logs the intent before writing and never sends a token twice; an
    unclear outcome is checked on X, never resent."""

    def __init__(self, config):
        self.config = config

    def cli(self, action):
        return self.config.get('x_inbox', str(X_INBOX))

    def account(self, action):
        return next(a for a in self.config['accounts'] if a['id'] == action['account_id'])

    def args(self, action):
        account = self.account(action)
        target = ['--reply-to', action['reply_to']] if action.get('reply_to') else ['--conversation', action['conversation_id']]
        return ['send', '--handle', account['handle'], '--primary', account.get('primary', account['handle']), *target, '--text', action['body'],
                *(['--long'] if action.get('reply_to') and account.get('long_posts') else [])]

    def run(self, action, args, timeout=300):
        result = subprocess.run([self.cli(action), *args], capture_output=True, text=True, timeout=timeout,
                                env=browser_env(self.config, action, 'X_INBOX'))
        if result.returncode == 2 and 'busy' in result.stderr.lower():
            raise XBusy()
        return result

    def preflight(self, action):
        if action['kind'] != 'x':
            raise ValueError('Only an owner-approved X reply or message can use the X transport')
        result = self.run(action, [*self.args(action), '--json'])
        if result.returncode:
            err = result.stderr.lower()
            raise ValueError('X is signed out in the OpenClaw browser. Nothing was sent.' if 'signed out' in err or 'not signed in' in err
                             else 'That X post is no longer available. Nothing was sent.' if 'no longer available' in err
                             else 'I can’t send in this encrypted X chat yet. Nothing was sent.' if 'encrypted' in err
                             else 'That X conversation isn’t in the DM inbox any more. Nothing was sent.' if 'not in the x dm inbox' in err
                             else 'That X conversation is read-only. Nothing was sent.' if 'read-only' in err
                             else 'X’s rate limit was reached. Nothing was sent; tap Send again later.' if 'rate limit' in err
                             else 'That text is longer than X allows. Nothing was sent.' if 'longer than x allows' in err
                             else 'I couldn’t reach X. Nothing was sent; tap Send again.')
        try:
            out = json.loads(result.stdout)
            ok = out['reply_to']['id'] == action['reply_to'] if action.get('reply_to') else out['conversation']['id'] == action['conversation_id']
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            raise ValueError('I couldn’t check the X ' + ('post' if action.get('reply_to') else 'conversation') + '. Nothing was sent.')

    def send(self, action, job):
        try:
            result = self.run(action, [*self.args(action), '--send-id', 'assistant-' + job['id'], '--yes', '--json'])
        except XBusy:
            return {'not_sent': True}
        try:
            out = json.loads(result.stdout or '{}')
        except ValueError:
            return {}
        if out.get('sent') == 'sent':
            return {'message_id': ('x:' if action.get('reply_to') else 'xdm:') + str(out.get('id') or job['id'])}
        if out.get('sent') in ('failed', 'not-sent'):
            return {'rejected': out.get('status') or out.get('error') or 'refused'}
        return {}  # Unknown outcome: never resend; reconcile checks X.

    def reconcile(self, action, job):
        result = self.run(action, ['verify', '--send-id', 'assistant-' + job['id']])
        if result.returncode:
            return {}
        try:
            out = json.loads(result.stdout)
        except ValueError:
            return {}
        return {'message_id': ('x:' if action.get('reply_to') else 'xdm:') + str(out.get('id') or job['id'])} if out.get('found') else {}


class WhatsAppTransport:
    """Sends one owner-approved WhatsApp text into an existing chat with wacli. Only this send runs
    wacli without --read-only; every check before and after it stays read-only. When the sync keeps
    the store open, wacli hands the send to that running process. wacli has no idempotency token,
    so an unclear outcome is checked in the chat afterwards and never resent."""

    def __init__(self, config):
        self.config = config

    def account(self, action):
        return next(a for a in self.config['accounts'] if a['id'] == action['account_id'] and a.get('service') == 'whatsapp')

    def run(self, action, args, write=False, timeout=30):
        env = {k: v for k, v in os.environ.items() if k != 'WACLI_READONLY'}
        if not write:
            env['WACLI_READONLY'] = '1'
        command = [self.config.get('wacli') or 'wacli', '--store', self.account(action)['store'],
                   *([] if write else ['--read-only']), '--json', *args]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)

    def read(self, action, args):
        try:
            result = self.run(action, ['--timeout', '15s', *args], timeout=25)
            data = {} if result.returncode else json.loads(result.stdout or '{}')
        except (subprocess.TimeoutExpired, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get('success') is not True or not isinstance(data.get('data'), dict):
            raise ValueError('I couldn’t reach WhatsApp on this computer. Nothing was sent; tap Send again.')
        return data['data']

    def preflight(self, action):
        if action['kind'] != 'whatsapp':
            raise ValueError('Only an owner-approved WhatsApp message can use the WhatsApp transport')
        status = self.read(action, ['auth', 'status'])
        linked = status.get('linked_jid')
        if (status.get('authenticated') is not True or not isinstance(linked, str)
                or hashlib.sha256(linked.encode()).hexdigest() != self.account(action).get('identity_sha256')):
            raise ValueError('WhatsApp is signed out, or linked to another number. Nothing was sent.')
        rows = self.read(action, ['messages', 'list', '--chat', action['chat_id'], '--limit', '50']).get('messages')
        if not rows:
            raise ValueError('I couldn’t find that WhatsApp chat. Nothing was sent.')
        action['proof'] = {'whatsapp_before_ids': [r['MsgID'] for r in rows if r.get('MsgID')]}

    def send(self, action, job):
        try:
            result = self.run(action, ['--timeout', '90s', '--lock-wait', '30s', 'send', 'text', '--to', action['chat_id'],
                                       '--message', action['body']], write=True, timeout=150)
        except subprocess.TimeoutExpired:
            return {}
        try:
            out = json.loads(result.stdout or '{}')
        except ValueError:
            out = {}
        data = out.get('data') if isinstance(out.get('data'), dict) else {}
        if not result.returncode and out.get('success') is True and data.get('sent') is True and data.get('id'):
            return {'message_id': 'whatsapp:' + str(data['id'])}
        err = ((out.get('error') or '') if isinstance(out.get('error'), str) else '') + ' ' + result.stderr
        err = err.lower()
        if 'may still have gone through' in err or 'after dispatch' in err:
            return {}  # Unknown outcome: never resend; reconcile reads the chat.
        if 'lock' in err and 'store' in err:
            return {'not_sent': True}
        if 'not authenticated' in err or 'not logged in' in err or 'read-only' in err or 'invalid' in err:
            return {'rejected': 'refused'}
        return {}

    def reconcile(self, action, job):
        proof = action.get('proof') or {}
        stamp = proof.get('whatsapp_dispatched_at')
        if not stamp:
            return {}  # Legacy uncertain sends have no attributable dispatch boundary; never invent a receipt.
        try:
            since = datetime.datetime.fromisoformat(stamp)
            if since.tzinfo is None:
                return {}
            rows = self.read(action, ['messages', 'list', '--chat', action['chat_id'], '--from-me',
                                      '--after', since.isoformat(), '--limit', '50']).get('messages') or []
        except ValueError:
            return {}
        found = []
        for r in rows:
            try:
                occurred = datetime.datetime.fromisoformat(r.get('Timestamp', '').replace('Z', '+00:00'))
            except (ValueError, TypeError, AttributeError):
                continue
            if occurred.tzinfo is None:
                continue
            if (r.get('FromMe') is True and (r.get('Text') or '').strip() == action['body']
                    and r.get('MsgID') not in proof.get('whatsapp_before_ids', []) and occurred >= since):
                found.append(r)
        return {'message_id': 'whatsapp:' + str(found[0]['MsgID'])} if len(found) == 1 and found[0].get('MsgID') else {}


class IntercomTransport:
    """Posts one owner-approved reply into an Intercom conversation from the task's own messages, as the owner's own
    Intercom admin, with their reply token, through the connector's one allowed write (connectors/intercom.py Replier).
    Checks read-only first. Intercom has no idempotency key, so an unclear outcome is looked for in the conversation
    afterwards and never resent."""

    REFUSED = {'intercom_replies_shared': 'A token shared through your Fleet only reads. Sign in with Intercom yourself to reply. Nothing was sent.',
               'intercom_replies_off': 'Replies need your own Sign in with Intercom. Nothing was sent.',
               'account_identity_mismatch': 'Intercom is signed in as someone else now, or another workspace. Nothing was sent.',
               'missing_intercom_credential': 'Your Intercom reply sign-in is missing. Sign in with Intercom again. Nothing was sent.'}

    def __init__(self, config, connector, helpers):
        self.config, self.connector, self.helpers = config, connector, helpers

    def replier(self, action):
        account = next((a for a in self.config['accounts'] if a['id'] == action['account_id'] and a.get('service') == 'intercom'), None)
        if not account:
            raise ValueError('That Intercom account is no longer connected. Nothing was sent.')
        if str(action.get('admin_id') or '') != str(account.get('reply_admin_id') or ''):
            raise ValueError('This reply would go as someone else in Intercom. Nothing was sent.')
        try:
            return self.connector.Replier(self.config, account, self.helpers)
        except self.helpers.SourceError as error:
            raise ValueError(self.REFUSED.get(error.category, 'Intercom replies are off. Nothing was sent.')) from None

    def preflight(self, action):
        if action['kind'] != 'intercom':
            raise ValueError('Only an owner-approved Intercom reply can use the Intercom transport')
        replier = self.replier(action)
        try:
            replier.me()
            detail = replier.conversation(action['conversation_id'])
        except self.helpers.SourceError as error:
            raise ValueError(self.REFUSED.get(error.category, 'I couldn’t reach Intercom. Nothing was sent; tap Send again.')) from None
        action['proof'] = {'intercom_before_ids': [part for part, _, _ in replier.mine(detail)]}

    def find(self, replier, action, detail, since=None):
        text = self.connector.plain(self.connector.reply_html(action['body']))
        before = set((action.get('proof') or {}).get('intercom_before_ids') or [])
        found = [part for part, created, said in replier.mine(detail) if part not in before and said == text
                 and (since is None or (type(created) is int and created >= since))]
        return {'message_id': f"intercom:{action['conversation_id']}:{found[0]}"} if len(found) == 1 else {}

    def send(self, action, job):
        replier = self.replier(action)
        try:
            detail = replier.reply(action['conversation_id'], action['body'])
        except self.helpers.SourceError as error:
            if error.category == 'unsupported_intercom_method':
                return {'rejected': 'refused'}
            if error.status == 429:
                return {'not_sent': True}
            if error.status in (400, 401, 403, 404, 422):
                return {'rejected': error.status}
            return {}  # Unknown outcome: never resend; reconcile reads the conversation.
        return self.find(replier, action, detail) if isinstance(detail, dict) else {}

    def reconcile(self, action, job):
        stamp = (action.get('proof') or {}).get('intercom_dispatched_at')
        if not stamp:
            return {}
        try:
            since = int(datetime.datetime.fromisoformat(stamp).timestamp()) - 5
            replier = self.replier(action)
            return self.find(replier, action, replier.conversation(action['conversation_id']), since)
        except (ValueError, self.helpers.SourceError):
            return {}


def slack_text(body):
    """Plain draft text in Slack's own format: [text](https://url) links become <url|text>."""
    text = body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'\[([^\]\n]+)\]\((https://[^)\s]+)\)', lambda m: '<' + m[2].replace('&amp;', '&') + '|' + m[1] + '>', text)


def same_person(name, user):
    """The name in the draft is the Slack user's name: every word of it appears in their real or display name."""
    profile = user.get('profile') or {}
    known = ' '.join(str(x or '') for x in (user.get('real_name'), profile.get('real_name'), profile.get('display_name'), user.get('name'))).lower()
    words = set(re.findall(r'\w+', known))
    wanted = re.findall(r'\w+', name.lower())
    return bool(wanted) and all(w in words for w in wanted)


class SlackTransport:
    """Sends one owner-approved Slack direct message as the owner, through the Maton Slack connection.
    Separate from the read-only Slack source: only these methods (look up, open the DM, post, read it
    back), and only a direct message to one person; never a channel post."""
    METHODS = {'users.info': 'GET', 'users.lookupByEmail': 'GET', 'conversations.open': 'POST', 'chat.postMessage': 'POST', 'conversations.history': 'GET'}

    def __init__(self, config, credential):
        self.config, self.credential = config, credential

    def account(self, action):
        return next(a for a in self.config['accounts'] if a['id'] == action['account_id'] and a.get('service') == 'slack')

    def call(self, action, method, params):
        import urllib.parse
        import urllib.request
        account = self.account(action)
        secret = self.credential(self.config, account['credential'])
        found = re.findall(r'^' + re.escape(account['credential_variable']) + r'=(.*)$', secret, re.M)
        if len(found) != 1:
            raise RuntimeError('Slack credential unavailable')
        headers = {'Authorization': 'Bearer ' + found[0].strip().strip('"\''), 'Maton-Connection': account['connection_id'], 'Accept': 'application/json'}
        url = 'https://api.maton.ai/slack/api/' + method
        if self.METHODS[method] == 'GET':
            request = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers=headers)
        else:
            request = urllib.request.Request(url, data=json.dumps(params).encode(), method='POST',
                                             headers={**headers, 'Content-Type': 'application/json; charset=utf-8'})
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.headers.get('Maton-Connection') != account['connection_id']:
                raise RuntimeError('Slack connection mismatch')
            return json.load(response)

    def person(self, action):
        reply = (self.call(action, 'users.info', {'user': action['user_id']}) if action.get('user_id')
                 else self.call(action, 'users.lookupByEmail', {'email': action['to_email']}))
        if not reply.get('ok'):
            raise ValueError('I couldn’t find ' + action['to_name'] + ' in Slack. Nothing was sent.')
        user = reply['user']
        if user.get('deleted') or user.get('is_bot'):
            raise ValueError(action['to_name'] + '’s Slack account is deactivated or a bot. Nothing was sent.')
        if not same_person(action['to_name'], user):
            raise ValueError(f"That Slack user is {user.get('real_name') or user.get('name')}, not {action['to_name']}. Nothing was sent.")
        return user['id']

    def preflight(self, action):
        if action['kind'] != 'slack':
            raise ValueError('Only an owner-approved Slack message can use the Slack transport')
        self.person(action)

    def channel(self, action, user_id):
        reply = self.call(action, 'conversations.open', {'users': user_id})
        if not reply.get('ok'):
            raise RuntimeError('Slack direct message unavailable')
        return reply['channel']['id']

    def send(self, action, job):
        import urllib.error
        try:
            user_id = self.person(action)
            channel = self.channel(action, user_id)
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 and error.code not in (408, 429):
                return {'rejected': error.code}  # refused for good; nothing was posted
            return {'not_sent': True}
        except Exception:
            return {'not_sent': True}  # nothing was posted yet; try again shortly
        try:
            reply = self.call(action, 'chat.postMessage', {'channel': channel, 'text': slack_text(action['body']), 'unfurl_links': False})
        except urllib.error.HTTPError as error:
            if error.code == 429:
                return {'not_sent': True}  # Slack refused it for now and posted nothing: send it again shortly
            # A refused request (4xx) posted nothing; a server error might have.
            return {'rejected': error.code} if 400 <= error.code < 500 and error.code != 408 else {}
        except Exception:
            return {}  # unknown outcome: reconcile checks the conversation, never resends
        if reply.get('ok'):
            return {'message_id': f"slack:{channel}:{reply['ts']}", 'channel_id': channel, 'user_id': user_id}
        if reply.get('error') == 'ratelimited':
            return {'not_sent': True}
        return {'rejected': reply.get('error') or 'unknown'}

    def reconcile(self, action, job):
        user_id = self.person(action)
        channel = self.channel(action, user_id)
        since = datetime.datetime.fromisoformat(job['created_at']).timestamp() - 60
        reply = self.call(action, 'conversations.history', {'channel': channel, 'oldest': f'{since:.6f}', 'limit': 50})
        owner = self.account(action).get('user_id')
        text = slack_text(action['body'])
        found = [m for m in reply.get('messages', []) if m.get('user') == owner and m.get('text') == text]
        return {'message_id': f"slack:{channel}:{found[0]['ts']}", 'channel_id': channel, 'user_id': user_id} if len(found) == 1 else {}


# Fixes to The Machine itself. A Do it run never edits the running code: it clones it into its own
# folder under fix_dir, fixes and tests it there and commits. The owner reviews the diff; Merge now
# runs the tests again, fast-forwards the running checkout to exactly that commit and restarts.
FIX_DIR = str(Path.home() / '.cache/the-machine/fixes')
MAX_DIFF = 60000


def app_dir(config):
    return Path(config.get('app_dir') or Path(__file__).resolve().parent).expanduser().resolve()


def fix_dir(config):
    return Path(config.get('fix_dir') or FIX_DIR).expanduser().resolve()


def git(path, *args, timeout=60):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(f'git {args[0]} failed')
    return result.stdout


def merge_action(value, config):
    if set(value) - {'kind', 'label', 'clone', 'summary'}:
        raise ValueError('A merge needs only clone (the fixed copy) and summary (what the fix changes)')
    if value.get('label') and not value['label'].startswith('Merge'):
        raise ValueError('Merge button labels start with Merge, such as Merge fix')
    summary = value.get('summary')
    if not isinstance(summary, str) or not 10 <= len(summary.strip()) <= 500 or '\x00' in summary:
        raise ValueError('summary says in plain words what the fix changes, at most 500 characters')
    clone = Path(str(value.get('clone') or '')).expanduser().resolve()
    if not clone.is_relative_to(fix_dir(config)) or clone == fix_dir(config) or not (clone / '.git').exists():
        raise ValueError(f'Fix a clone of The Machine under {fix_dir(config)}/<job id>, made with git clone, not the running code')
    app = app_dir(config)
    try:
        base, head = git(app, 'rev-parse', 'HEAD').strip(), git(clone, 'rev-parse', 'HEAD').strip()
        if git(clone, 'status', '--porcelain'):
            raise ValueError('Commit the fix in the clone first: it has uncommitted changes')
        if subprocess.run(['git', '-C', str(clone), 'merge-base', '--is-ancestor', base, head], capture_output=True, timeout=30).returncode:
            raise ValueError('The fix is not on top of the running code: git pull in the clone, test again and commit')
        commits = int(git(clone, 'rev-list', '--count', f'{base}..{head}').strip())
        files = git(clone, 'diff', '--name-only', base, head).split()
        stat = git(clone, 'diff', '--stat', base, head).rstrip()
        diff = git(clone, 'diff', base, head)
        numstat = git(clone, 'diff', '--numstat', base, head)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('git is not available to check the fix') from None
    if not 1 <= commits <= 20 or not files:
        raise ValueError('Commit the fix in the clone: there is nothing to merge yet')
    result = {'kind': 'merge', 'label': value.get('label') or 'Merge fix', 'clone': str(clone), 'summary': ' '.join(summary.split()),
              'base': base, 'head': head, 'commits': commits, 'files': files[:200], 'stat': stat[-4000:],
              'diff': diff if len(diff) <= MAX_DIFF else diff[:MAX_DIFF] + '\n… preview shortened; download the complete diff.',
              'diff_clipped':len(diff)>MAX_DIFF,'diff_sha256':hashlib.sha256(diff.encode()).hexdigest(),
              'plugin': any(f.startswith('openclaw-plugin/') for f in files),
              # Lines changed outside the tests, for the safe-merge limits ('-' is a binary file).
              'lines': sum(int(a) + int(d) if a.isdigit() and d.isdigit() else 10**6
                           for a, d, path in (line.split('\t', 2) for line in numstat.splitlines() if line.count('\t') >= 2)
                           if not is_test(path))}
    return result


def is_test(path):
    return Path(path).name.startswith('test_') and path.endswith(('.py', '.mjs'))


SCHEMA_CHANGE = re.compile(r'(?im)^\+.*\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|VIEW|TRIGGER)\b|^\+.*\bexecutescript\(')


def unsafe_merge(action, config):
    """Why this fix waits for the owner's Merge, or None when The Machine may merge it by itself:
    a small Python change to the service, with a new test, that leaves the plugin, the agent's
    instructions, the database layout and the config alone. Undo stays available for 24 hours."""
    if config.get('self_fix_auto', True) is False:
        return 'merging by itself is off'
    files = action.get('files') or []
    code = [f for f in files if not is_test(f)]
    if action.get('plugin') or any(not f.endswith('.py') or '/' in f and not f.startswith('integrations/') for f in code):
        return 'it changes more than the service’s own Python code'
    if not any(is_test(f) and f.endswith('.py') for f in files):
        return 'it adds no test'
    if not code or len(code) > 3 or not isinstance(action.get('lines'), int) or action['lines'] > 50 or action.get('commits', 99) > 5:
        return 'it is bigger than a small fix (at most 3 files and 50 lines)'
    if SCHEMA_CHANGE.search(action.get('diff') or '') or action.get('diff_clipped') or action['diff'].endswith('the full change is in the clone.'):
        return 'it may change the database'
    return None


def merge_preview(action):
    return ('Merge into The Machine: ' + action['summary'] + '\n\n' + action['stat']
            + ('\n\nThe OpenClaw plugin changes too: it takes effect after openclaw plugins reload personal-assistant-bridge.' if action.get('plugin') else '')
            + '\n\nMerge now runs the tests again, then updates the running code and restarts The Machine.')


def full_merge_diff(action):
    """Read the immutable proposal, independent of the clone's current branch/worktree."""
    if action.get('kind')!='merge':raise ValueError('This is not a merge proposal')
    diff=git(Path(action['clone']),'diff',action['base'],action['head'])
    checksum=action.get('diff_sha256')
    if checksum and hashlib.sha256(diff.encode()).hexdigest()!=checksum:raise ValueError('The saved diff is no longer available')
    return diff


def test_command(config, clone):
    if config.get('self_test'):
        return list(config['self_test'])
    if (clone / 'scripts/test.py').exists():
        return ['python3', 'scripts/test.py']
    return ['sh', '-c', 'python3 -m unittest discover -p "test_*.py" && for f in test_*.mjs; do [ -e "$f" ] || continue; node "$f" || exit 1; done']


class MergeTransport:
    """Applies one owner-approved fix to The Machine's own code. Preflight checks that nothing moved
    since the owner reviewed it and that the tests pass; the merge is a fast-forward to exactly the
    reviewed commit, then the code is pushed (when it has an upstream) and the service restarts."""

    def __init__(self, config, restart=None):
        self.config, self.restart = config, restart

    def preflight(self, action):
        self.check(action)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ASSISTANT_', 'OPENCLAW_', 'GOG_'))}
        env['PYTHONDONTWRITEBYTECODE']='1'
        try:
            tests = subprocess.run(test_command(self.config, Path(action['clone'])), cwd=action['clone'], env=env, capture_output=True, text=True,
                                   timeout=int(self.config.get('self_test_seconds', 1200)))
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('The tests did not finish on this fix. Nothing was merged.') from None
        if tests.returncode:
            raise ValueError('The tests fail on this fix. Nothing was merged; tap Change to have it fixed.')
        if action.get('auto'):
            action['proof']=self.proves(action, env)
        self.check(action)  # tests may take minutes; never activate edits made meanwhile

    def check(self, action):
        if action['kind'] != 'merge':
            raise ValueError('Only an owner-approved fix can use the merge transport')
        app, clone = app_dir(self.config), Path(action['clone'])
        try:
            if git(app, 'rev-parse', 'HEAD').strip() != action['base']:
                raise ValueError('The Machine’s code changed since this fix was made. Nothing was merged; tap Change and ask to redo it on the current code.')
            if git(app, 'status', '--porcelain', '--untracked-files=no'):
                raise ValueError('The Machine’s code has uncommitted changes here. Nothing was merged.')
            if not clone.is_dir() or git(clone, 'rev-parse', 'HEAD').strip() != action['head'] or git(clone, 'status', '--porcelain'):
                raise ValueError('The fix changed after you reviewed it. Nothing was merged; tap Change to get it again.')
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('I could not check the fix. Nothing was merged.') from None

    @contextmanager
    def custody(self):
        app=app_dir(self.config)
        lock=Path(git(app,'rev-parse','--absolute-git-dir').strip())/'machine-deploy.lock'
        with lock.open('a') as handle:
            try:portable.lock(handle,blocking=False)
            except BlockingIOError:raise ValueError('Another merge or undo is running. Try this step again.') from None
            try:yield
            finally:portable.unlock(handle)

    def proves(self, action, env):
        """Merging by itself: the fix's new tests must fail on the code without the fix."""
        tests = [f for f in action['files'] if is_test(f) and f.endswith('.py')]
        runner=r'''
import json, pathlib, sys, traceback, unittest
class Result(unittest.TestResult):
    def __init__(self):super().__init__();self.failed=[]
    def record(self,test,error):
        frames=[]
        for f in traceback.extract_tb(error[2]):
            path=pathlib.Path(f.filename).resolve()
            if path.is_relative_to(pathlib.Path.cwd()):frames.append({'file':str(path.relative_to(pathlib.Path.cwd())),'function':f.name})
        self.failed.append({'test':test.id(),'kind':error[0].__name__,'frames':frames,'collection':isinstance(test,unittest.loader._FailedTest)})
    def addError(self,test,error):super().addError(test,error);self.record(test,error)
    def addFailure(self,test,error):super().addFailure(test,error);self.record(test,error)
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=Result();suite.run(result)
print('MACHINE_PROOF='+json.dumps({'ran':result.testsRun,'failed':result.failed,'skipped':len(result.skipped)}))
'''
        modules=[t[:-3].replace('/', '.') for t in tests]
        def run_tests(root):
            run=subprocess.run(['python3','-c',runner,*modules],cwd=root,env=env,capture_output=True,text=True,timeout=int(self.config.get('self_test_seconds',1200)))
            if run.returncode:raise ValueError('The regression test runner did not finish')
            return json.loads(run.stdout.rsplit('MACHINE_PROOF=',1)[1].splitlines()[0])
        with tempfile.TemporaryDirectory(prefix='machine-proof-') as tmp:
            try:
                subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', action['clone'], tmp], check=True, capture_output=True, timeout=120)
                git(Path(tmp), 'checkout', '--quiet', action['base'])
                git(Path(tmp), 'checkout', action['head'], '--', *tests)
                before=run_tests(tmp)
                after=run_tests(action['clone'])
            except (ValueError, IndexError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise ValueError('I could not check that the new test catches the bug. Not merged by itself.') from None
        if not before['failed']:
            raise ValueError('The new test also passes without the fix, so it doesn’t prove the fix. Not merged by itself.')
        expected={(f['file'],f['function']) for f in action.get('fault_frames',[]) if f.get('file') and f.get('function')}
        def exercises_fault(failure):
            frames=[f for f in failure['frames'] if not is_test(f['file'])]
            return not failure['collection'] and bool(frames) and (not action.get('fault_kind') or failure['kind']==action['fault_kind']) and (
                not expected or bool(expected & {(f['file'],f['function']) for f in frames}))
        if after['failed'] or not after['ran'] or any(f['collection'] for f in before['failed']) or not any(exercises_fault(f) for f in before['failed']):
            raise ValueError('The test did not exercise the recorded bug on the old code and pass on the fix. Not merged by itself.')
        return {'base':action['base'],'head':action['head'],'ran':after['ran'],'failures':before['failed']}

    def revert(self, action):
        with self.custody():return self._revert(action)

    def undo_receipt(self,action):
        marker='Machine-Undo: '+action['head']
        app=app_dir(self.config)
        found=git(app,'log','--fixed-strings','--grep='+marker,'--format=%H','HEAD').splitlines()
        for commit in found:
            if marker not in git(app,'show','-s','--format=%B',commit).splitlines():continue
            parent=git(app,'rev-parse',commit+'^').strip()
            return {'message_id':'undo:'+commit,'commit':commit,'below':parent}

    def _revert(self, action):
        """Commit an action-bound undo. Push/restart only after SQLite saves this receipt."""
        app = app_dir(self.config)
        saved=self.undo_receipt(action)
        if saved:return saved
        name = ['-c', 'user.name=The Machine', '-c', 'user.email=the-machine@localhost']
        try:
            if git(app, 'status', '--porcelain', '--untracked-files=no'):
                raise ValueError('The Machine’s code has uncommitted changes here. Nothing was undone.')
            if self.merged(action) is not True:
                raise ValueError('The fix is no longer in the running code. Nothing to undo.')
            before = git(app, 'rev-parse', 'HEAD').strip()
            try:
                git(app, *name, 'revert', '--no-edit', '--no-commit', f"{action['base']}..{action['head']}", timeout=120)
            except ValueError:
                subprocess.run(['git', '-C', str(app), 'revert', '--abort'], capture_output=True, timeout=60)
                raise ValueError('Later changes touch the same lines, so I could not undo it by itself. Nothing was undone.') from None
            git(app, *name, 'commit', '--quiet', '-m', 'Undo: ' + action['summary'][:200]+'\n\nMachine-Undo: '+action['head'], timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('git did not finish undoing it. Check The Machine’s code.') from None
        return self.undo_receipt(action)

    def send(self, action, job):
        with self.custody():return self._send(action,job)

    def _send(self, action, job):
        app = app_dir(self.config)
        try:
            self.check(action)
            git(app, 'fetch', '--quiet', '--no-tags', action['clone'], 'HEAD', timeout=120)
            if git(app, 'rev-parse', 'FETCH_HEAD').strip() != action['head']:
                return {'rejected': 'changed'}
            self.check(action)
            git(app, 'merge', '--ff-only', '--quiet', action['head'], timeout=120)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return {'rejected': 'merge'} if self.merged(action) is False else {}
        if not self.merged(action):
            return {}
        return {'message_id':'merge:'+action['head'][:12],'commit':action['head'],'below':action['base']}

    def complete(self,action,receipt,operation='merge'):
        """Optional effects after a durable receipt. Exact commit push, never later commits."""
        app=app_dir(self.config);commit=receipt['commit']
        if operation=='undo':
            try:allowed=not subprocess.run(['git','-C',str(app),'merge-base','--is-ancestor',action['head'],'@{u}'],capture_output=True,timeout=30).returncode
            except (OSError,subprocess.TimeoutExpired):allowed=False
        else:allowed=not action.get('auto') or self.config.get('self_fix_auto_push',False) is True
        pushed,held=self.push(receipt['below'],allowed,commit)
        current=git(app,'rev-parse','HEAD').strip()==commit and not git(app,'status','--porcelain','--untracked-files=no')
        return {'pushed':pushed,'held':held,'activation_ready':bool(current)}

    def push(self, below, allowed, commit=None):
        """Push the running code to its upstream when allowed and when everything below the new
        commits is upstream already, so nothing else goes out with them. Returns (pushed, held):
        pushed is True/False after a push, None without one; held says why one was skipped."""
        app = app_dir(self.config)
        try:
            git(app, 'rev-parse', '--abbrev-ref', '@{u}')
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return None, None
        if not self.config.get('self_fix_push', True):
            return None, None
        if not allowed:
            return None, 'auto'
        try:
            if subprocess.run(['git', '-C', str(app), 'merge-base', '--is-ancestor', below, '@{u}'], capture_output=True, timeout=30).returncode:
                return None, 'unshared'
            remote,branch=git(app,'rev-parse','--symbolic-full-name','@{u}').strip().removeprefix('refs/remotes/').split('/',1)
            git(app, 'push', '--quiet',remote,(commit or 'HEAD')+':refs/heads/'+branch, timeout=120)
            return True, None
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return False, None

    def merged(self, action):
        """True when the running code contains the fix, False when it is still at the reviewed base, None if unclear."""
        app = app_dir(self.config)
        try:
            if not subprocess.run(['git', '-C', str(app), 'merge-base', '--is-ancestor', action['head'], 'HEAD'], capture_output=True, timeout=30).returncode:
                return True
            return False if git(app, 'rev-parse', 'HEAD').strip() == action['base'] else None
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return None

    def reconcile(self, action, job):
        return {'message_id':'merge:'+action['head'][:12],'commit':action['head'],'below':action['base']} if self.merged(action) else {}


class ActionTransport:
    """Routes an approved send to Gmail, LinkedIn, Slack, X, WhatsApp or Intercom, a calendar change to Google Calendar,
    or a fix to The Machine itself, by its kind."""

    def __init__(self, email, linkedin, slack=None, x=None, merge=None, whatsapp=None, intercom=None, calendar=None):
        self.email, self.linkedin, self.slack, self.x, self.merge, self.whatsapp = email, linkedin, slack, x, merge, whatsapp
        self.intercom, self.calendar = intercom, calendar

    def pick(self, action):
        if action['kind'] == 'calendar':
            if not self.calendar:
                raise ValueError('Calendar changes aren’t set up. Nothing was changed.')
            return self.calendar
        if action['kind'] == 'merge':
            if not self.merge:
                raise ValueError('Merging fixes isn’t set up. Nothing was merged.')
            return self.merge
        if action['kind'] == 'slack':
            if not self.slack:
                raise ValueError('Slack sending isn’t set up. Nothing was sent.')
            return self.slack
        if action['kind'] == 'x':
            if not self.x:
                raise ValueError('X sending isn’t set up. Nothing was sent.')
            return self.x
        if action['kind'] == 'whatsapp':
            if not self.whatsapp:
                raise ValueError('WhatsApp sending isn’t set up. Nothing was sent.')
            return self.whatsapp
        if action['kind'] == 'intercom':
            if not self.intercom:
                raise ValueError('Intercom replies aren’t set up. Nothing was sent.')
            return self.intercom
        return self.linkedin if action['kind'] == 'linkedin' else self.email

    def preflight(self, action):
        return self.pick(action).preflight(action)

    def send(self, action, job):
        return self.pick(action).send(action, job)

    def reconcile(self, action, job):
        return self.pick(action).reconcile(action, job)


SNOOZE_LABEL = 'Snoozed'
MODIFY_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'


class ThreadSnooze:
    """Gmail has no snooze API: archive the thread under a label, then return it to the inbox."""

    def __init__(self, config, credential, read):
        self.config, self.credential, self.read = config, credential, read

    def account(self, account_id):
        return next(a for a in self.config['accounts'] if a['id'] == account_id)

    def allowed(self, account):
        try:
            return bool({MODIFY_SCOPE, 'https://mail.google.com/'} & granted_scopes(self.config, self.credential, account))
        except ValueError:
            raise RuntimeError('auth list failed') from None

    def write(self, account, method, params, body):
        if account.get('transport') == 'broker':
            try:
                return google_api.call(self.config, self.credential, account, method, params, MODIFY_SCOPE, body=body, write=True)
            except google_api.BrokerError:
                raise RuntimeError(f'{method} failed') from None
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as file:
            file.write(canonical(body));file.flush()
            result = subprocess.run([self.config['gog'], 'api', 'call', 'gmail', 'v1', method, '--params', canonical(params),
                                     '--body', '@' + file.name, '--scope', MODIFY_SCOPE, '--account', account['email'],
                                     '--client', account['client'], '--allow-write', '--force', '--no-input', '--json'],
                                    env=EmailTransport.env(self), capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f'{method} failed')
        return json.loads(result.stdout or '{}')

    def label(self, account):
        labels = self.read(self.config, account, 'gmail', 'v1', 'gmail.users.labels.list', {'userId': 'me'},
                           'https://www.googleapis.com/auth/gmail.readonly').get('labels', [])
        found = next((l['id'] for l in labels if l.get('name') == SNOOZE_LABEL), None)
        return found or self.write(account, 'gmail.users.labels.create', {'userId': 'me'},
                                   {'name': SNOOZE_LABEL, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'})['id']

    def thread(self, account, thread_id):
        return self.read(self.config, account, 'gmail', 'v1', 'gmail.users.threads.get',
                           {'userId': 'me', 'id': thread_id, 'format': 'minimal'}, 'https://www.googleapis.com/auth/gmail.readonly')

    def thread_labels(self, account, thread_id):
        thread = self.thread(account,thread_id)
        return {label for message in thread.get('messages', []) for label in message.get('labelIds', [])}

    def snooze(self, account_id, thread_id, sent_message_id):
        account = self.account(account_id)
        if outlook(account):
            return OutlookSnooze(self.config, self.credential).snooze(account, thread_id, sent_message_id)
        if not self.allowed(account):
            return 'no_permission'
        messages=self.thread(account,thread_id).get('messages',[])
        sent=next((m for m in messages if m.get('id')==sent_message_id and 'SENT' in m.get('labelIds',[])),None)
        if not sent:
            raise RuntimeError('sent_message_not_confirmed')
        # Delayed/retried snooze must not hide a reply that already arrived.
        if any('SENT' not in m.get('labelIds',[]) and int(m.get('internalDate',0))>=int(sent.get('internalDate',0)) for m in messages):
            return 'returned'
        label = self.label(account)
        self.write(account, 'gmail.users.threads.modify', {'userId': 'me', 'id': thread_id},
                   {'addLabelIds': [label], 'removeLabelIds': ['INBOX']})
        return 'snoozed'

    def wake(self, account_id, thread_id):
        account = self.account(account_id)
        if outlook(account):
            return OutlookSnooze(self.config, self.credential).wake(account, thread_id)
        label = self.label(account)
        labels = self.thread_labels(account, thread_id)
        if label not in labels:
            return 'returned'  # The owner already moved it; leave it where it is.
        # A reply already brought it back: only drop the label. Otherwise return it unread.
        add = [] if 'INBOX' in labels else ['INBOX', 'UNREAD']
        self.write(account, 'gmail.users.threads.modify', {'userId': 'me', 'id': thread_id},
                   {'addLabelIds': add, 'removeLabelIds': [label]})
        return 'returned'


class OutlookSnooze:
    """Outlook's own snooze has no API either: the thread's Inbox messages move to a "Snoozed" folder, and back to the
    Inbox when it's time (unread, unless a reply already came in). Message IDs are immutable, so they survive the moves."""

    def __init__(self, config, credential):
        self.config, self.credential = config, credential

    def call(self, account, method, params=None, body=None, write=False):
        try:
            return ms_graph.call(self.config, self.credential, account, method, params, body=body, write=write)
        except ms_graph.GraphError:
            raise RuntimeError(f'{method} failed') from None

    def allowed(self, account):
        try:
            return 'Mail.ReadWrite' in ms_graph.scopes(self.config, self.credential, account)
        except ms_graph.GraphError:
            raise RuntimeError('scopes failed') from None

    def folder(self, account, create=False):
        quoted = SNOOZE_LABEL.replace("'", "''")
        found = self.call(account, 'folders.list', {'$filter': f"displayName eq '{quoted}'", '$select': 'id'}).get('value') or []
        if found:
            return found[0]['id']
        if not create:
            return None
        return self.call(account, 'folders.create', {}, {'displayName': SNOOZE_LABEL, 'isHidden': False}, write=True)['id']

    def messages(self, account, thread_id):
        quoted = str(thread_id).replace("'", "''")
        return self.call(account, 'messages.list', {'$filter': f"conversationId eq '{quoted}'", '$top': 50,
                                                     '$select': 'id,parentFolderId,receivedDateTime,isRead'}).get('value') or []

    def snooze(self, account, thread_id, sent_message_id):
        if not self.allowed(account):
            return 'no_permission'
        try:
            known = ms_graph.folders(self.config, self.credential, account)
        except ms_graph.GraphError:
            raise RuntimeError('folders failed') from None
        messages = self.messages(account, thread_id)
        sent = next((m for m in messages if m.get('id') == sent_message_id and m.get('parentFolderId') == known.get('sentitems')), None)
        if not sent:
            raise RuntimeError('sent_message_not_confirmed')
        # Delayed/retried snooze must not hide a reply that already arrived.
        if any(m.get('parentFolderId') != known.get('sentitems') and m.get('receivedDateTime', '') >= sent.get('receivedDateTime', '')
               for m in messages if m.get('id') != sent_message_id):
            return 'returned'
        folder = self.folder(account, create=True)
        for message in messages:
            if message.get('parentFolderId') == known.get('inbox'):
                self.call(account, 'messages.move', {'id': message['id']}, {'destinationId': folder}, write=True)
        return 'snoozed'

    def wake(self, account, thread_id):
        folder = self.folder(account)
        if not folder:
            return 'returned'
        try:
            known = ms_graph.folders(self.config, self.credential, account)
        except ms_graph.GraphError:
            raise RuntimeError('folders failed') from None
        messages = self.messages(account, thread_id)
        snoozed = [m for m in messages if m.get('parentFolderId') == folder]
        if not snoozed:
            return 'returned'  # The owner already moved it; leave it where it is.
        replied = any(m.get('parentFolderId') == known.get('inbox') for m in messages)
        for message in snoozed:
            self.call(account, 'messages.move', {'id': message['id']}, {'destinationId': 'inbox'}, write=True)
        if not replied:  # A reply already brought it back: only move it. Otherwise return it unread.
            newest = max(snoozed, key=lambda m: m.get('receivedDateTime') or '')
            self.call(account, 'messages.update', {'id': newest['id']}, {'isRead': False}, write=True)
        return 'returned'
