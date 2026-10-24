"""Organisation data from an enrichment service, looked up by domain.

Works with any service that answers GET <url with the domain> with JSON about the company: a preset
(apollo, peopledatalabs, hunter) or your own url template with {domain} and {key}, and headers that can
use {key} too. The key is read from a file or an environment variable, never stored in the config.
Only organisation domains from the graph are sent (never free-mail or the owner's own domains), a few
at a time, each looked up again after REFRESH_DAYS: first those of people the owner writes with both ways or
meets, never those only cold outreach, newsletters or no-reply senders come from (see due). What comes back is
normalised to a small profile (industry, size, location, founded, about, links, logo) that the graph, reviews and
preparations show next to the organisation; it describes the company, never what it is to the owner. An answer about
another company (the service matched a page that merely sits on the domain) is dropped, as if nothing was found.
Work contacts are looked up too, on their own budget, with `people_url` (see people_due)."""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import security_levels

REFRESH_DAYS = 90
RETRY_HOURS = 24        # after an error (timeout, rate limit)
PER_PASS = 5
PER_DAY = 50
# Paired with the Network, a self-hosted Machine gets hosted's organisation budget (the control plane's 1,000 a day).
PAIRED_PER_PASS = 50
PAIRED_PER_DAY = 1000
PRESETS = {
    'apollo': {'url': 'https://api.apollo.io/api/v1/organizations/enrich?domain={domain}', 'headers': {'X-Api-Key': '{key}'}},
    'peopledatalabs': {'url': 'https://api.peopledatalabs.com/v5/company/enrich?website={domain}', 'headers': {'X-Api-Key': '{key}'},
                       'people_url': 'https://api.peopledatalabs.com/v5/person/enrich?email={email}'},
    'hunter': {'url': 'https://api.hunter.io/v2/companies/find?domain={domain}&api_key={key}',
               'people_url': 'https://api.hunter.io/v2/people/find?email={email}&api_key={key}'},
}
# Where common services keep each field; the first one present wins. A config "fields" map adds its own paths first.
FIELDS = {
    'name': ['name', 'display_name', 'legalName', 'legal_name'],
    'industry': ['industry', 'category.industry', 'industries.0', 'category.sector'],
    'employees': ['employees_num', 'employee_count', 'estimated_num_employees', 'metrics.employees', 'employees'],
    'size': ['size', 'employee_range', 'metrics.employeesRange', 'employees_range'],
    'founded': ['founded', 'founded_year', 'foundedYear', 'year_founded'],
    'location': ['headquarters', 'location.name', 'location.locality', 'location.city', 'geo.city', 'city'],
    'country': ['location.country_code', 'location.country', 'geo.countryCode', 'country'],
    'about': ['about_us', 'description', 'summary', 'short_description', 'seo_description', 'overview'],
    'linkedin_url': ['linkedin_url', 'linkedin.url', 'linkedin.handle', 'linkedin'],
    'website': ['website', 'website_url', 'url', 'domain'],
    'type': ['type', 'company_type', 'ownership'],
    'logo': ['logo', 'logo_url', 'logoUrl', 'image_url', 'logo.url', 'image'],
}
LEADAPI = 'https://leadapi.salesflare.com'
LOGO = re.compile(r'https://[A-Za-z0-9.-]{1,200}\.[A-Za-z]{2,}(?::\d{2,5})?/[^\s"\'<>\\]{0,500}')
WRAPPERS = ('organization', 'company', 'data', 'result')


class EnrichmentError(Exception):
    pass


def settings(config):
    """The enrichment settings with a preset filled in, or None when it isn't set up."""
    raw = config.get('enrichment')
    if not isinstance(raw, dict) or raw.get('enabled') is False:
        return None
    merged = {**PRESETS.get(str(raw.get('provider') or '').lower(), {}), **{k: v for k, v in raw.items() if v not in (None, '')}}
    if not merged.get('people_url') and str(merged.get('url') or '').startswith(LEADAPI + '/enrich/companies?'):
        merged['people_url'] = LEADAPI + '/enrich/people?name={name}&domain={domain}'  # Salesflare's lead API knows people too
    return merged if merged.get('url') and '{domain}' in merged['url'] else None


def key(cfg):
    if cfg.get('key_env') and os.environ.get(cfg['key_env']):
        return os.environ[cfg['key_env']].strip()
    if cfg.get('key_file'):
        path = Path(os.path.expanduser(cfg['key_file']))
        directory = os.environ.get('CREDENTIALS_DIRECTORY')  # systemd LoadCredential, when used
        if directory and (Path(directory) / path.name).exists():
            path = Path(directory) / path.name
        return path.read_text().strip()
    return ''


def value(data, path):
    for part in path.split('.'):
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)] if int(part) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def normalise(data, fields=None):
    """A small profile from whatever shape the service returns; None when it found nothing."""
    if isinstance(data, list):
        data = data[0] if data else None
    for wrapper in WRAPPERS:
        if isinstance(data, dict) and isinstance(data.get(wrapper), dict):
            data = data[wrapper]
            break
    if not isinstance(data, dict):
        return None
    profile = {}
    for field, paths in FIELDS.items():
        for path in list((fields or {}).get(field) or []) + paths:
            found = value(data, path)
            if found in (None, '', [], {}) or isinstance(found, (dict, list)):
                continue
            if field == 'logo':
                if not isinstance(found, str) or not LOGO.fullmatch(found.strip()):
                    continue  # only a plain https address; the dashboard loads it as an image, nothing else
                profile[field] = found.strip()
                break
            if field in ('employees', 'founded'):
                try:
                    found = int(found)
                except (TypeError, ValueError):
                    continue
            else:
                found, limit = ' '.join(str(found).split()), 300 if field == 'about' else 120
                if len(found) > limit:
                    found = found[:limit - 1].rsplit(' ', 1)[0].rstrip(',.;:') + '…'
            if field == 'linkedin_url' and not found.startswith('http'):
                found = 'https://www.linkedin.com/company/' + found.strip('/').rsplit('/', 1)[-1]
            profile[field] = found
            break
    return profile if profile.get('name') or profile.get('industry') or profile.get('about') else None


def answer(cfg, url, secret, timeout):
    """The raw answer from the owner's own configured service, None for not found. Errors never carry the key or url."""
    headers = {'Accept': 'application/json', 'User-Agent': 'the-machine/1 (+https://themachine.computer)'}
    headers.update({k: str(v).replace('{key}', secret) for k, v in (cfg.get('headers') or {}).items()})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            return response.read(2_000_000)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in (404, 422):
            return None
        raise EnrichmentError({401: 'the service rejected the key', 403: 'the service refused (key or plan)',
                               429: 'rate limited'}.get(error.code, f'HTTP {error.code}')) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise EnrichmentError('unreachable: ' + type(getattr(error, 'reason', error)).__name__) from None


def fetch(cfg, domain, timeout=15):
    """The profile for one domain, None when the service doesn't know it. Errors never carry the key or url."""
    secret = key(cfg)
    if not secret and ('{key}' in cfg['url'] or '{key}' in json.dumps(cfg.get('headers') or {})):
        raise EnrichmentError('no key: set key_file or key_env in enrichment')
    url = cfg['url'].replace('{domain}', urllib.parse.quote(domain)).replace('{key}', urllib.parse.quote(secret))
    body = answer(cfg, url, secret, timeout)
    if body is None:
        return None
    try:
        profile = normalise(json.loads(body or b'null'), cfg.get('fields'))
    except ValueError:
        raise EnrichmentError('the answer was not JSON') from None
    return None if profile and another_company(profile, domain) else profile


def another_company(profile, domain):
    """True when the answer is about some other company: its website is not this domain's home page (a blog post, a
    form, a page on a hosting subdomain) and its name has nothing in common with the domain. Services match by the
    website a company page lists, so a one-person page that links to someone's article gets that company's domain.
    A website on the home page, or a name that shares a word with the domain, is trusted."""
    site = str(profile.get('website') or '').strip().lower()
    if not site:
        return False
    parts = urllib.parse.urlsplit(site if '://' in site else 'https://' + site)
    host = (parts.hostname or '').removeprefix('www.')
    if host == domain.removeprefix('www.') and re.fullmatch(r'/?(?:[a-z]{2}(?:[-_][a-z]{2})?/?)?', parts.path or ''):
        return False
    label = re.sub(r'[^a-z0-9]', '', domain.split('.')[0])
    name = str(profile.get('name') or '').lower()
    squashed = re.sub(r'[^a-z0-9]', '', name)
    words = [w for w in re.findall(r'[a-z0-9]+', name) if len(w) >= 3]
    return not (label and squashed and (label in squashed or squashed in label or any(w in label for w in words)))


# Free mail beyond the graph's own list (any country's Yahoo, Outlook, ...): a person's address there says nothing
# about a company. And sender subdomains that only send campaigns (news.brand.com, email.brand.com).
FREEMAIL = re.compile(r'^(?:[\w-]+\.)*(?:g(?:oogle)?mail|hotmail|outlook|live|msn|yahoo|ymail|rocketmail|icloud|aol|proton(?:mail)?|'
                      r'gmx|yandex|mail|zoho|fastmail|tutanota|tuta|qq|163|126|naver|libero|wanadoo|laposte|t-online|seznam|'
                      r'telenet|skynet|proximus|scarlet|pandora|ziggo|kpnmail|xs4all|btinternet|comcast|sbcglobal|bigpond)'
                      r'\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2})$')
MARKETING = re.compile(r'^(?:e|em|email|emails|mail|mailer|mailing|news|newsletter|newsletters|marketing|mkt|campaign|campaigns|'
                       r'info|updates?|notifications?|notify|alerts?|bounces?|reply|send|sender|go|t|click|links?|crm|hs|hubspot|'
                       r'mc|mg|sg|smtp|out|outbound|engage|promo|offers?|deals)\d*\.[\w-]+\.[a-z.]{2,8}$')


def worth(domain, people):
    """How much a look-up is worth for one organisation, from what the graph says about its people: 0 only for free
    mail and campaign sender subdomains, which are no company. Someone the owner writes with both ways or meets counts
    most, someone the owner only wrote to a little; an organisation whose people only wrote in (cold outreach), were
    only copied or only send no-reply mail still gets looked up, last, within the day's budget."""
    import graph
    if not domain or domain in graph.FREEMAIL or FREEMAIL.match(domain) or MARKETING.match(domain):
        return 0
    score = 0
    for info in people:
        addresses = [i.split(':', 1)[1] for i in info.get('identifiers') or [] if str(i).startswith('email:') and str(i).endswith('@' + domain)]
        if addresses and all(graph.bulk(a) for a in addresses):
            continue  # no-reply, newsletters, notifications
        wrote, heard, met = (int(info.get(k) or 0) for k in ('wrote', 'heard', 'met'))
        if met or (wrote and heard):
            score += 3
        elif wrote:
            score += 1
    return score or 0.5


def due(db, now, limit):
    """Organisation domains from the graph that were never looked up or are due again, those worth it most first
    (people the owner writes with both ways or meets, then the strongest); never ones only cold outreach, bulk
    senders or free mail come from. A paying customer is always worth it."""
    current = dt.datetime.fromisoformat(now)
    fresh = (current - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    retry = (current - dt.timedelta(hours=RETRY_HOURS)).isoformat()
    rows = db.execute("SELECT n.id,json_extract(n.info,'$.domain') domain,n.strength,json_extract(n.info,'$.paying') paying FROM graph_nodes n "
                      "LEFT JOIN org_profiles p ON p.domain=json_extract(n.info,'$.domain') "
                      "WHERE n.kind='org' AND json_extract(n.info,'$.domain') IS NOT NULL AND (p.domain IS NULL OR (p.status NOT IN ('error','wrong') AND p.fetched_at<?) OR (p.status='error' AND p.fetched_at<?))",
                      (fresh, retry)).fetchall()
    if not rows or limit <= 0:
        return []
    people = {}
    for org, info in db.execute("SELECT l.b,n.info FROM graph_links l JOIN graph_nodes n ON n.id=l.a WHERE l.kind='member' AND n.kind='person'"):
        try:
            people.setdefault(org, []).append(json.loads(info or '{}'))
        except ValueError:
            continue
    ranked = []
    for org, domain, strength, paying in rows:
        score = worth(domain, people.get(org, []))
        if paying and domain and not FREEMAIL.match(domain):
            score += 3
        if score:
            ranked.append((-score, -(strength or 0), domain))
    return [domain for _, _, domain in sorted(ranked)[:limit]]


def save(db, domain, status, profile, now):
    """Stores a look-up. A domain the owner marked as the wrong company keeps that mark."""
    db.execute('INSERT INTO org_profiles(domain,status,profile,fetched_at) VALUES(?,?,?,?) '
               "ON CONFLICT(domain) DO UPDATE SET status=excluded.status,profile=CASE WHEN excluded.status='error' THEN org_profiles.profile ELSE excluded.profile END,fetched_at=excluded.fetched_at "
               "WHERE org_profiles.status!='wrong'",
               (domain, status, json.dumps(profile) if profile else None, now))


def step(connect, config, now, per_pass=None, paired=False):
    """Look up a few organisations. Returns how many got a profile; stops at the first service error. Paired with the
    Network: hosted's budget (PAIRED_PER_DAY, PAIRED_PER_PASS) unless per_day says otherwise."""
    cfg = settings(config)
    if not cfg:
        return 0
    per_pass = per_pass or (PAIRED_PER_PASS if paired else PER_PASS)
    with connect() as db:
        today = now[:10]
        used = db.execute("SELECT count(*) FROM org_profiles WHERE substr(fetched_at,1,10)=? AND status!='wrong'", (today,)).fetchone()[0]
        domains = due(db, now, max(0, min(per_pass, int(cfg.get('per_day') or (PAIRED_PER_DAY if paired else PER_DAY)) - used)))
    found = 0
    for domain in domains:
        try:
            profile = fetch(cfg, domain)
        except EnrichmentError:
            with connect() as db:
                save(db, domain, 'error', None, now)
            raise
        with connect() as db:
            save(db, domain, 'found' if profile else 'none', profile, now)
        found += bool(profile)
    return found


def readable_name(domain):
    """A name to show for an organisation known only by its domain: "Remy" for remy.be, "Blue Fox" for blue-fox.io,
    "KBC" for kbc.be (no vowels: an acronym)."""
    label = registrable(str(domain or '').strip().lower().removeprefix('www.')).split('.')[0]
    words = [w for w in re.split(r'[-_]+', label) if w]
    if not words:
        return str(domain or '')
    return ' '.join(w.upper() if not re.search(r'[aeiouy0-9]', w) else w[:1].upper() + w[1:] for w in words)


def website(domain):
    """The website an email domain stands for: its own registrable domain (mail.remy.be is remy.be)."""
    return 'https://' + registrable(str(domain or '').strip().lower().removeprefix('www.'))


def summary(profile):
    """One short line: "Software Development, 2-10 people, Antwerp, founded 2014"."""
    if not profile:
        return ''
    size = profile.get('size') or (str(profile['employees']) if profile.get('employees') else '')
    bits = [profile.get('industry'), size and (size + ' people'), profile.get('location'), profile.get('founded') and f"founded {profile['founded']}"]
    return ', '.join(str(b) for b in bits if b)


def status(db, config):
    cfg = settings(config)
    counts = dict(db.execute('SELECT status,count(*) FROM org_profiles GROUP BY status').fetchall())
    last = db.execute("SELECT domain,fetched_at FROM org_profiles WHERE status='error' ORDER BY fetched_at DESC LIMIT 1").fetchone()
    host = urllib.parse.urlsplit(cfg['url']).hostname if cfg else None
    return {'service': host, 'found': counts.get('found', 0), 'unknown': counts.get('none', 0), 'errors': counts.get('error', 0),
            'last_error_at': last[1] if last else None, 'refresh_days': REFRESH_DAYS}


def lookup(connect, config, domain, now):
    """One domain now (assistant enrich <domain>): looked up, saved, returned."""
    cfg = settings(config)
    if not cfg:
        raise EnrichmentError('Enrichment is not set up: add "enrichment" to the config (see README, Enrichment)')
    domain = re.sub(r'^(?:https?://)?(?:www\.)?', '', str(domain or '').strip().lower()).split('/')[0]
    if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', domain):
        raise EnrichmentError('Give a domain, like globex.com')
    profile = fetch(cfg, domain)
    with connect() as db:
        db.execute("DELETE FROM org_profiles WHERE domain=? AND status='wrong'", (domain,))  # asked for by name: the owner wants it again
        save(db, domain, 'found' if profile else 'none', profile, now)
    return {'domain': domain, 'profile': profile}


# People: everyone in the graph with a full name and a company email address, looked up the same way, on their own
# budget. Only the technical skips, where a look-up can't work or would be wrong: no full name, only free mail (no
# company to look in), a shared inbox or a no-reply sender. The owner's own colleagues are looked up too, at the
# owner's own domain. What comes back is cut down to what the dashboard shows: job title, company, a photo (https
# only), a LinkedIn address and city and country. Kept on this Machine only (person_profiles).
PEOPLE_PER_PASS = 5
PEOPLE_PER_DAY = 50
# A self-hosted Machine paired with the Network gets what a hosted one gets: 3,000 a day, 25 a pass (every ten minutes).
PAIRED_PEOPLE_PER_DAY = 3000
PAIRED_PEOPLE_PER_PASS = 200  # a pass every ten minutes: a backfill of a few thousand people takes hours, not a day
PERSON_FIELDS = {
    'title': ['position', 'job_title', 'title', 'employment.title', 'experience.0.position', 'experience.0.title'],
    'company': ['company.name', 'job_company_name', 'employment.name', 'organization.name', 'experience.0.company.name'],
    'company_domain': ['company.domain', 'job_company_website', 'employment.domain', 'organization.primary_domain', 'company.website'],
    'photo': ['image_url', 'photo_url', 'avatar', 'picture', 'profile_pic_url', 'photo'],
    'linkedin_url': ['linkedin_url', 'linkedin.url', 'linkedin.handle', 'linkedin_username', 'linkedin'],
    'city': ['location.city', 'location_locality', 'geo.city', 'city'],
    'country': ['location.country_code', 'location_country', 'geo.countryCode', 'geo.country', 'country'],
}
PERSON_NAME = ['name', 'full_name', 'name.fullName', 'fullName']  # only to check it is the same person; never kept
PERSON_WRAPPERS = ('person', 'data', 'result')
LINKEDIN = re.compile(r'https://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]{2,100}/?')


def people_on(db):
    """The owner's Settings switch, Look up work contacts: on unless turned off, and never in a Careful Work Fleet."""
    return security_levels.people_lookup(db)


def set_people(db, on):
    db.execute('INSERT INTO enrich_prefs(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               ('people', 'on' if on else 'off'))
    return {'people': bool(on)}


def folded(text):
    import unicodedata
    return re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().casefold())


def same_person_name(found, asked):
    """One common first name or surname is not an identity match. Allow accents and middle names, but
    require the first and last name to agree when the provider supplies a name."""
    left, right = folded(found), folded(asked)
    return len(left) > 1 and len(right) > 1 and (left[0], left[-1]) == (right[0], right[-1])


def same_domain(a, b):
    a, b = (re.sub(r'^(?:https?://)?(?:www\.)?', '', str(x or '').strip().lower()).split('/')[0] for x in (a, b))
    return bool(a and b) and (a == b or a.endswith('.' + b) or b.endswith('.' + a))


SECOND_LEVEL = {'co', 'com', 'net', 'org', 'ac', 'gov', 'edu', 'ltd', 'plc', 'or', 'ne', 'gv'}


def registrable(domain):
    """The company's own domain for an address's domain: eu.example.com and team.example.com are example.com
    (example.co.uk keeps its three parts)."""
    labels = str(domain or '').strip('.').lower().split('.')
    keep = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return '.'.join(labels[-keep:])


def same_company(a, b):
    """Whether a looked-up company domain is the address's company: the same domain or a subdomain of it, or the same
    name at another top-level domain (example.nl for example.com)."""
    if same_domain(a, b):
        return True
    a, b = (registrable(re.sub(r'^(?:https?://)?(?:www\.)?', '', str(x or '').strip().lower()).split('/')[0]) for x in (a, b))
    name = a.split('.')[0]
    return len(name) >= 3 and name == b.split('.')[0]


def normalise_person(data, asked_name, domain, fields=None, why=None):
    """The small person profile the dashboard shows, from whatever shape the service returns; None when it found
    nothing, or when the answer is about someone else: a name whose first and last names do not match, or a company at another domain."""
    if isinstance(data, list):
        data = data[0] if data else None
    for wrapper in PERSON_WRAPPERS:
        if isinstance(data, dict) and isinstance(data.get(wrapper), dict):
            data = data[wrapper]
            break
    why = why if why is not None else []
    if not isinstance(data, dict) or data.get('success') is False:
        why.append('unknown')
        return None
    name = next((v for v in (value(data, p) for p in PERSON_NAME) if isinstance(v, str) and v.strip()), '')
    if name and asked_name and not same_person_name(name, asked_name):
        why.append('another person')
        return None
    profile = {}
    for field, paths in PERSON_FIELDS.items():
        for path in list((fields or {}).get(field) or []) + paths:
            found = value(data, path)
            if found in (None, '', [], {}) or isinstance(found, (dict, list, bool)):
                continue
            found = ' '.join(str(found).split())
            if field == 'photo':
                if not LOGO.fullmatch(found) or placeholder(found):
                    continue  # only a plain https address to a real photo; the Machine saves it as an image, nothing else
            elif field == 'linkedin_url':
                if not found.startswith('http'):
                    found = 'https://www.linkedin.com/in/' + found.strip('/').rsplit('/', 1)[-1]
                found = found.replace('http://', 'https://', 1).split('?')[0]
                if not LINKEDIN.fullmatch(found):
                    continue
            elif field == 'country':
                found = found.upper() if len(found) == 2 else found[:60]
            elif len(found) > 120:
                found = found[:119].rsplit(' ', 1)[0].rstrip(',.;:|') + '…'
            profile[field] = found
            break
    if profile.get('company_domain'):
        if not same_company(profile['company_domain'], domain):
            why.append('another company')
            return None  # someone of the same name at another company
        profile['company_domain'] = domain
    if profile.get('title') or profile.get('company') or profile.get('photo'):
        return profile
    why.append('nothing to show')
    return None


def placeholder(url):
    """LinkedIn's stock silhouette for someone without a photo (served from its static host, not its media one)."""
    return (urllib.parse.urlsplit(url).hostname or '').lower() == 'static.licdn.com'


def fetch_person(cfg, person, timeout=15, why=None):
    """The profile for one person ({email, name, domain}), None when the service doesn't know them (why says what
    it said: unknown, another person, another company or nothing to show)."""
    why = why if why is not None else []
    template = cfg['people_url']
    secret = key(cfg)
    if not secret and ('{key}' in template or '{key}' in json.dumps(cfg.get('headers') or {})):
        raise EnrichmentError('no key: set key_file or key_env in enrichment')
    words = str(person['name']).split()
    url = template
    for k, v in (('email', person['email']), ('name', person['name']), ('domain', person['domain']),
                 ('first_name', words[0] if words else ''), ('last_name', ' '.join(words[1:])), ('key', secret)):
        url = url.replace('{' + k + '}', urllib.parse.quote(str(v)))
    body = answer(cfg, url, secret, timeout)
    if body is None:
        why.append('unknown')
        return None
    try:
        return normalise_person(json.loads(body or b'null'), person['name'], person['domain'], cfg.get('people_fields'), why)
    except ValueError:
        raise EnrichmentError('the answer was not JSON') from None


def work_contact(info, name):
    """(email, domain) to look this person up by, or None when a look-up can't work: no full name, or no company
    address (free mail, a shared inbox, a no-reply sender). Their organisation's own address goes first."""
    import graph
    if not graph.plain_name(name) or graph.incomplete_name(name):
        return None  # a lookup needs their full name
    org = str(info.get('org') or '')
    preferred = org[4:] if info.get('org_by') == 'email' and org.startswith('org:') and not org.startswith('org:named:') else ''
    addresses = []
    for identifier in info.get('identifiers') or ():
        identifier = str(identifier)
        if not identifier.startswith('email:') or '@' not in identifier:
            continue
        address = identifier.split(':', 1)[1].lower()
        domain = address.rsplit('@', 1)[1]
        if domain in graph.FREEMAIL or FREEMAIL.match(domain) or MARKETING.match(domain) or graph.bulk(address) or graph.ROLE.match(address):
            continue
        addresses.append((domain != preferred, address, domain))
    if not addresses:
        return None
    address, domain = min(addresses)[1:]
    return address, registrable(domain)  # their company's own domain, not a mail subdomain


def people_due(db, now, limit):
    """People that were never looked up or are due again, the ones the owner deals with most first:
    [{'identifier', 'email', 'name', 'domain'}]. Nothing while the owner turned Look up work contacts off."""
    if limit <= 0 or not people_on(db):
        return []
    current = dt.datetime.fromisoformat(now)
    fresh = (current - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    retry = (current - dt.timedelta(hours=RETRY_HOURS)).isoformat()
    again = (current - dt.timedelta(days=PHOTO_RETRY_DAYS)).isoformat()
    since = backfill_since(db, now)
    try:
        saved = {r[0] for r in db.execute('SELECT identifier FROM person_photos')}
    except Exception:  # before the app made the table
        saved = set()
    left = False
    looked = {r[0]: (r[1], r[2], r[3]) for r in db.execute('SELECT identifier,status,fetched_at,profile FROM person_profiles')}
    ranked = []
    for node, name, strength, raw in db.execute("SELECT id,name,strength,info FROM graph_nodes WHERE kind='person'"):
        try:
            info = json.loads(raw or '{}')
        except ValueError:
            continue
        found = work_contact(info, name)
        if not found:
            continue
        identifier = 'email:' + found[0]
        status, at, profile = looked.get(identifier, (None, None, None))
        if status and not due_again(status, at, profile, identifier in saved, fresh, retry, again, since):
            continue
        left = left or bool(since and status and status != 'error' and at < since)
        both = info.get('met') or (info.get('wrote') and info.get('heard'))
        ranked.append((-(3 if both else 1), -(strength or 0), identifier, {'identifier': identifier, 'email': found[0], 'name': ' '.join(str(name).split()), 'domain': found[1]}))
    if since and not left:
        db.execute("UPDATE enrich_prefs SET value=? WHERE key='people_backfill'", ('done ' + now,))
    return [item for *_, item in sorted(ranked, key=lambda r: r[:3])[:limit]]


def due_again(status, at, profile, has_photo, fresh, retry, again, since):
    """Whether someone looked up before is due again: after 90 days (a day after an error), after 30 when the photo
    link had already expired and none is saved here, and once in the one-time backfill (looked up before it began)."""
    if status == 'error':
        return at < retry
    if at < fresh or (since and at < since):
        return True
    return status == 'found' and not has_photo and '"photo_gone": true' in (profile or '') and at < again


def backfill_since(db, now):
    """The one-time backfill: every work contact already looked up is looked up once more, closest first, within the
    daily limit (a large graph spreads over days). It starts the first time this code runs and is marked done in
    enrich_prefs (by people_due) once nobody looked up before it is left, so it never repeats. Returns its start while
    it runs. Someone with no match goes back to the normal interval."""
    row = db.execute("SELECT value FROM enrich_prefs WHERE key='people_backfill'").fetchone()
    if row is None:
        db.execute("INSERT OR IGNORE INTO enrich_prefs(key,value) VALUES('people_backfill',?)", (now,))
        return now
    return None if row[0].startswith('done') else row[0]


def save_person(db, identifier, status, profile, now):
    """Stores a person look-up; an error keeps what was there. A photo link that has already expired is not kept
    (photo_gone: looked up again after 30 days)."""
    if profile and profile.get('photo') and link_expired(profile['photo'], now):
        profile = {**{k: v for k, v in profile.items() if k != 'photo'}, 'photo_gone': True}
    db.execute('INSERT INTO person_profiles(identifier,status,profile,fetched_at) VALUES(?,?,?,?) '
               "ON CONFLICT(identifier) DO UPDATE SET status=excluded.status,profile=CASE WHEN excluded.status='error' THEN person_profiles.profile ELSE excluded.profile END,fetched_at=excluded.fetched_at",
               (identifier, status, json.dumps(profile) if profile else None, now))


def people_available(config):
    """Whether this Machine can look people up: hosted (the hosting agent does it through the control plane), or
    self-hosted with a people address on its own enrichment service."""
    raw = (config or {}).get('enrichment')
    if isinstance(raw, dict) and raw.get('by') == 'hosting':
        return True
    cfg = settings(config or {})
    return bool(cfg and cfg.get('people_url') and cfg.get('people') is not False)


def people_step(connect, config, now, per_pass=None, paired=False, stop=None):
    """Look up a few work contacts with the Machine's own service. Returns how many got a profile; stops at the first
    service error. Paired with the Network: hosted's budget (PAIRED_PEOPLE_PER_DAY, PAIRED_PEOPLE_PER_PASS) unless
    people_per_day says otherwise."""
    cfg = settings(config)
    if not cfg or not cfg.get('people_url') or cfg.get('people') is False:
        return 0
    per_pass = per_pass or (PAIRED_PEOPLE_PER_PASS if paired else PEOPLE_PER_PASS)
    per_day = int(cfg.get('people_per_day') or (PAIRED_PEOPLE_PER_DAY if paired else PEOPLE_PER_DAY))
    with connect() as db:
        used = db.execute("SELECT count(*) FROM person_profiles WHERE substr(fetched_at,1,10)=?", (now[:10],)).fetchone()[0]
        due = people_due(db, now, max(0, min(per_pass, per_day - used)))
    found = 0
    for person in due:
        if stop and stop():
            break
        why = []
        try:
            profile = fetch_person(cfg, person, why=why)
        except EnrichmentError:
            with connect() as db:
                save_person(db, person['identifier'], 'error', None, now)
            raise
        with connect() as db:
            save_person(db, person['identifier'], 'found' if profile else 'none', profile or ({'why': why[0]} if why else None), now)
        found += bool(profile)
    return found


# Photos: a person's photo is saved once on this Machine (person_photos), small and only as an image, so it shows from
# here and never expires. Right after a lookup: the photo link it brought, while that still works; otherwise their
# Gravatar, asked by a hash of their work address (gravatar.com never sees the address itself). One try per lookup, no
# retries beyond that, so never more outside calls than the day's lookups. Nobody with a photo: looked up again in 30 days.
PHOTO_BYTES = 200_000
PHOTO_RETRY_DAYS = 30
PHOTOS_PER_PASS = 250  # keeps up with a pass's lookups
GRAVATAR = 'https://gravatar.com/avatar/{hash}?s=160&d=404'


def photo_key(identifier):
    return hashlib.sha256(identifier.encode()).hexdigest()[:32]


def gravatar(identifier):
    """The Gravatar address for a work address (by its SHA-256, as Gravatar asks), None for anything else."""
    if not str(identifier).startswith('email:') or '@' not in identifier:
        return None
    return GRAVATAR.format(hash=hashlib.sha256(identifier.split(':', 1)[1].strip().lower().encode()).hexdigest())


def link_expired(url, now):
    """Whether a signed image link says it has expired: LinkedIn's e=<epoch>, Expires=<epoch>, or S3's
    X-Amz-Date with X-Amz-Expires."""
    try:
        query = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        at = dt.datetime.fromisoformat(now).timestamp()
        for name in ('e', 'expires'):
            value = query.get(name, '')
            if value.isdigit() and len(value) in (10, 13):
                return int(value) / (1000 if len(value) == 13 else 1) <= at
        if query.get('x-amz-date') and query.get('x-amz-expires', '').isdigit():
            start = dt.datetime.strptime(query['x-amz-date'], '%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
            return start.timestamp() + int(query['x-amz-expires']) <= at
    except (ValueError, TypeError, OverflowError):
        return False
    return False


def image_type(data):
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to the address checked beforehand (a public one), with the host's own name for TLS."""
    def __init__(self, host, address, **kwargs):
        super().__init__(host, **kwargs)
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def download_photo(url, timeout=10):
    """(bytes, type) of a photo, or None: https only, to a public address, no redirects, at most PHOTO_BYTES, and only
    a JPEG, PNG or WebP image (by its own bytes, not only its header). Each checked address in turn, IPv4 first, so a
    host without IPv6 still gets it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or not LOGO.fullmatch(url):
        return None
    try:
        port = parts.port or 443
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)}
        if not addresses or not all(ipaddress.ip_address(a.split('%')[0]).is_global for a in addresses):
            return None
    except (OSError, ValueError):
        return None
    for address in sorted(addresses, key=lambda a: (':' in a, a)):
        connection = _Pinned(parts.hostname, address, port=port, timeout=timeout, context=ssl.create_default_context())
        try:
            connection.connect()
        except OSError:
            connection.close()
            continue  # unreachable from here (no IPv6 route): the next address
        try:
            connection.request('GET', (parts.path or '/') + ('?' + parts.query if parts.query else ''),
                               headers={'Accept': 'image/jpeg,image/png,image/webp', 'User-Agent': 'the-machine/1 (+https://themachine.computer)'})
            response = connection.getresponse()
            if response.status != 200 or not (response.getheader('Content-Type') or '').lower().startswith('image/'):
                return None
            if int(response.getheader('Content-Length') or 0) > PHOTO_BYTES:
                return None
            data = response.read(PHOTO_BYTES + 1)
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            connection.close()
        kind = image_type(data)
        return (data, kind) if kind and len(data) <= PHOTO_BYTES else None
    return None


def photos_step(connect, now, limit=PHOTOS_PER_PASS, fetch=None):
    """Saves a photo for the people looked up since their last try, once each: the lookup's own link while it still
    works, else their Gravatar. Returns how many were saved."""
    fetch = fetch or download_photo
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS person_photo_tries(identifier TEXT PRIMARY KEY,tried_at TEXT NOT NULL)')
        if not people_on(db):
            return 0
        rows = db.execute("SELECT p.identifier,p.status,p.fetched_at,CASE WHEN p.status='found' THEN json_extract(p.profile,'$.photo') END FROM person_profiles p "
                          "LEFT JOIN person_photo_tries t ON t.identifier=p.identifier "
                          "WHERE p.status IN ('found','none') AND (t.tried_at IS NULL OR t.tried_at<p.fetched_at) "
                          "AND NOT EXISTS(SELECT 1 FROM person_photos s WHERE s.identifier=p.identifier) ORDER BY p.fetched_at DESC LIMIT ?", (limit,)).fetchall()
    saved = 0
    for identifier, status, fetched_at, url in rows:
        got = fetch(url) if url and not link_expired(url, now) and not placeholder(url) else None
        if not got and gravatar(identifier):
            got = fetch(gravatar(identifier))
        with connect() as db:
            row = db.execute('SELECT status,profile,fetched_at FROM person_profiles WHERE identifier=?', (identifier,)).fetchone()
            if not row or row[2] != fetched_at:
                continue  # looked up again meanwhile
            db.execute('INSERT INTO person_photo_tries(identifier,tried_at) VALUES(?,?) ON CONFLICT(identifier) DO UPDATE SET tried_at=excluded.tried_at', (identifier, now))
            if got:
                db.execute('INSERT INTO person_photos(identifier,key,type,data,saved_at) VALUES(?,?,?,?,?) ON CONFLICT(identifier) DO UPDATE SET '
                           'type=excluded.type,data=excluded.data,saved_at=excluded.saved_at', (identifier, photo_key(identifier), got[1], got[0], now))
                saved += 1
            if row[0] != 'found':
                continue
            try:
                profile = json.loads(row[1]) if row[1] else None
            except ValueError:
                profile = None
            if not profile:
                continue
            had = profile.pop('photo', None)
            if got:
                profile.pop('photo_gone', None)
            elif had:
                profile['photo_gone'] = True
            db.execute('UPDATE person_profiles SET profile=? WHERE identifier=?', (json.dumps(profile), identifier))
    return saved


# Contact photos: from the owner's own connections, with only what they already allow (Google contacts and the
# Workspace directory through the gog sign-in, Slack profiles). Personal contacts too: these stay on this Machine and
# are never sent anywhere. They fill in someone without a photo; a photo the person chose themselves (their own Google
# or Slack profile) comes before one the owner set on a contact, and generic placeholders are skipped. A link that
# didn't work is tried again after 30 days at most.
CONTACT_PHOTOS_PER_PASS = 100
CONTACT_PHOTO_DAYS = 30
CONTACT_PHOTO_RUN_HOURS = 24
CONTACT_SCHEMA = ('CREATE TABLE IF NOT EXISTS contact_photo_links(identifier TEXT PRIMARY KEY,url TEXT NOT NULL,chosen INTEGER NOT NULL,'
                  'source TEXT NOT NULL,seen_at TEXT NOT NULL,tried_at TEXT)',
                  'CREATE TABLE IF NOT EXISTS contact_photo_runs(source TEXT PRIMARY KEY,ran_at TEXT NOT NULL)')
GOOGLE_PHOTO = re.compile(r'https://lh[3-6]\.googleusercontent\.com/[^\s"\'<>\\]{1,500}')


def contact_tables(db):
    for statement in CONTACT_SCHEMA:
        db.execute(statement)


def graph_people(db):
    """Each person identifier in the graph, with its person."""
    try:
        return dict(db.execute("SELECT j.value,n.id FROM graph_nodes n, json_each(n.info,'$.identifiers') j WHERE n.kind='person'").fetchall())
    except Exception:
        return {}


def phone_identifier(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return 'wa:' + digits if str(value or '').strip().startswith('+') and 7 <= len(digits) <= 15 else None


def google_photo(person):
    """(identifiers, link, chosen) for a Google contact or directory person with a real photo, else None. The person's
    own profile photo comes first; Google's generated letter (default) is skipped."""
    photos = [p for p in (person.get('photos') or []) if isinstance(p, dict) and not p.get('default') and GOOGLE_PHOTO.fullmatch(str(p.get('url') or ''))]
    if not photos:
        return None
    kind = lambda p: ((p.get('metadata') or {}).get('source') or {}).get('type')
    best = min(photos, key=lambda p: kind(p) != 'PROFILE')
    url = str(best['url'])
    url = re.sub(r'=s\d+(-c)?$', '=s160-c', url) if re.search(r'=s\d+(-c)?$', url) else url + ('=s160-c' if '=' not in url.rsplit('/', 1)[-1] else '')
    ids = ['email:' + str(e.get('value')).strip().lower() for e in (person.get('emailAddresses') or [])
           if isinstance(e, dict) and '@' in str(e.get('value') or '')]
    ids += [i for i in (phone_identifier(n.get('canonicalForm')) for n in (person.get('phoneNumbers') or []) if isinstance(n, dict)) if i]
    return (list(dict.fromkeys(ids)), url, kind(best) == 'PROFILE') if ids else None


def slack_photo(user):
    """(identifiers, link, chosen) for a Slack user with a picture of their own (not Slack's generated one)."""
    profile = user.get('profile') or {}
    url = str(profile.get('image_192') or profile.get('image_512') or '')
    if user.get('is_bot') or user.get('deleted') or not profile.get('is_custom_image') or not LOGO.fullmatch(url) or not user.get('id'):
        return None
    return (['slack:' + str(user['id'])], url, True)


def contact_run_due(db, source, now):
    contact_tables(db)
    row = db.execute('SELECT ran_at FROM contact_photo_runs WHERE source=?', (source,)).fetchone()
    return not row or row[0] <= (dt.datetime.fromisoformat(now) - dt.timedelta(hours=CONTACT_PHOTO_RUN_HOURS)).isoformat()


def contact_run_done(db, source, now):
    contact_tables(db)
    db.execute('INSERT INTO contact_photo_runs(source,ran_at) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET ran_at=excluded.ran_at', (source, now))


def offer_photos(db, offers, source, now, known_only=True):
    """Keeps the photo links of people this Machine knows (by any identifier), one per identifier: a photo the person
    chose beats one set on a contact. Returns how many were kept."""
    contact_tables(db)
    known = graph_people(db) if known_only else None
    kept = 0
    for ids, url, chosen in offers:
        for identifier in ids:
            if known is not None and identifier not in known:
                continue
            row = db.execute('SELECT url,chosen FROM contact_photo_links WHERE identifier=?', (identifier,)).fetchone()
            if row and (row[1] > int(chosen) or (row[0] == url and row[1] == int(chosen))):
                db.execute('UPDATE contact_photo_links SET seen_at=? WHERE identifier=?', (now, identifier))
                continue
            db.execute('INSERT INTO contact_photo_links(identifier,url,chosen,source,seen_at,tried_at) VALUES(?,?,?,?,?,NULL) ON CONFLICT(identifier) DO UPDATE SET '
                       'url=excluded.url,chosen=excluded.chosen,source=excluded.source,seen_at=excluded.seen_at,tried_at=NULL',
                       (identifier, url, int(chosen), source, now))
            kept += 1
    return kept


def contact_photos_step(connect, now, limit=CONTACT_PHOTOS_PER_PASS, fetch=None):
    """Saves a contact photo for people in the graph who have none yet, the best link first. Returns how many."""
    fetch = fetch or download_photo
    retry = (dt.datetime.fromisoformat(now) - dt.timedelta(days=CONTACT_PHOTO_DAYS)).isoformat()
    with connect() as db:
        contact_tables(db)
        people = graph_people(db)
        have = {people[i] for (i,) in db.execute('SELECT identifier FROM person_photos') if i in people}
        rows = db.execute('SELECT identifier,url FROM contact_photo_links WHERE tried_at IS NULL OR tried_at<? '
                          'ORDER BY chosen DESC,seen_at DESC', (retry,)).fetchall()
    saved, tried = 0, 0
    for identifier, url in rows:
        node = people.get(identifier)
        if node is None or node in have:
            continue
        if tried >= limit:
            break
        tried += 1
        got = fetch(url)
        with connect() as db:
            db.execute('UPDATE contact_photo_links SET tried_at=? WHERE identifier=? AND url=?', (now, identifier, url))
            if got and not db.execute('SELECT 1 FROM person_photos WHERE identifier=?', (identifier,)).fetchone():
                db.execute('INSERT INTO person_photos(identifier,key,type,data,saved_at) VALUES(?,?,?,?,?)',
                           (identifier, photo_key(identifier), got[1], got[0], now))
                have.add(node)
                saved += 1
    return saved


# WhatsApp pictures: asked through the owner's always-on WhatsApp mirror (`wacli sync --follow
# --profile-picture-budget N`), which owns the session; this Machine's own WhatsApp reads stay read-only. Only people
# already in the graph without a photo, whose WhatsApp address the Machine has seen in a chat, at most 50 asks a day (the
# mirror keeps its own daily cap too). Each picture is saved once here. Someone who has none, or hides it from the owner,
# is asked again after 30 days. Nothing is sent to them: no message, read receipt or presence.
WHATSAPP_PHOTOS_PER_DAY = 50
WHATSAPP_PAUSE_HOURS = 6  # the mirror couldn't answer (off, older, restarting): try again later, not every pass
WHATSAPP_SCHEMA = 'CREATE TABLE IF NOT EXISTS whatsapp_photo_asks(identifier TEXT PRIMARY KEY,asked_at TEXT NOT NULL,picture_id TEXT)'
WHATSAPP_USER = re.compile(r'([0-9]{5,20})(?::[0-9]+)?@(s\.whatsapp\.net|lid)')
WHATSAPP_SCAN = 5000  # group messages read to find the address of someone met only in groups


def whatsapp_addresses(db, wanted, accounts):
    """{'wa:<digits>': (account id, jid)} for the wanted people: the one-to-one chat first, else a group message
    they sent (newest first)."""
    found = {}
    marks = ','.join('?' * len(accounts))
    for (chat, account) in db.execute(f"SELECT DISTINCT json_extract(metadata,'$.chat_id'),account_id FROM events WHERE kind='whatsapp.message' "
                                      f"AND account_id IN ({marks})", list(accounts)):
        match = WHATSAPP_USER.fullmatch(str(chat or ''))
        if match and 'wa:' + match[1] in wanted:
            found.setdefault('wa:' + match[1], (account, match[1] + '@' + match[2]))
    if len(found) < len(wanted):
        rows = db.execute(f"SELECT metadata,account_id FROM events WHERE kind='whatsapp.message' AND account_id IN ({marks}) "
                          f"AND json_extract(metadata,'$.chat_id') LIKE '%@g.us' ORDER BY occurred_at DESC LIMIT ?", [*accounts, WHATSAPP_SCAN])
        for metadata, account in rows:
            try:
                sender = (json.loads(metadata) if isinstance(metadata, str) else metadata or {}).get('sender_id')
            except (ValueError, AttributeError):
                continue
            match = WHATSAPP_USER.fullmatch(str(sender or ''))
            if match and 'wa:' + match[1] in wanted:
                found.setdefault('wa:' + match[1], (account, match[1] + '@' + match[2]))
                if len(found) == len(wanted):
                    break
    return found


def whatsapp_photos_step(connect, now, ask, accounts, limit=WHATSAPP_PHOTOS_PER_DAY, fetch=None):
    """Asks the WhatsApp mirror for the pictures of people in the graph who have none, strongest first, within the day's
    budget. ask(account id, jid) gives {'id', 'url'}, {} when they have none the owner can see, or None when the mirror
    can't answer now (then nothing more is asked for a while). Returns how many pictures were saved."""
    fetch = fetch or download_photo
    accounts = [a for a in accounts if a]
    if not accounts:
        return 0
    stamp = dt.datetime.fromisoformat(now)
    today = stamp.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT00:00:00')
    retry = (stamp - dt.timedelta(days=PHOTO_RETRY_DAYS)).isoformat()
    with connect() as db:
        db.execute(WHATSAPP_SCHEMA)
        contact_tables(db)
        paused = db.execute("SELECT ran_at FROM contact_photo_runs WHERE source='whatsapp:paused'").fetchone()
        if paused and paused[0] > (stamp - dt.timedelta(hours=WHATSAPP_PAUSE_HOURS)).isoformat():
            return 0
        left = limit - db.execute('SELECT count(*) FROM whatsapp_photo_asks WHERE asked_at>=?', (today,)).fetchone()[0]
        if left <= 0:
            return 0
        try:
            import graph
            gone = graph.forgotten(db)
        except Exception:
            gone = lambda identifier: False
        nodes = db.execute("SELECT n.id,j.value FROM graph_nodes n, json_each(n.info,'$.identifiers') j "
                           "WHERE n.kind='person' ORDER BY n.strength DESC").fetchall()
        photos = {i for (i,) in db.execute('SELECT identifier FROM person_photos')}
        with_photo = {node for node, identifier in nodes if identifier in photos}
        # A picture that didn't download (they have one, it isn't here) is asked again the next day, none in 30 days.
        again = (stamp - dt.timedelta(days=1)).isoformat()
        asked = {i: (at, again if picture else retry) for i, at, picture in db.execute('SELECT identifier,asked_at,picture_id FROM whatsapp_photo_asks')}
        wanted = []
        for node, identifier in nodes:
            at, since = asked.get(identifier, ('', retry))
            if (node in with_photo or not re.fullmatch(r'wa:[0-9]{5,20}', str(identifier)) or identifier in wanted
                    or at > since or gone(identifier)):
                continue
            wanted.append(identifier)
        addresses = whatsapp_addresses(db, set(wanted), accounts) if wanted else {}
    saved = 0
    for identifier in [i for i in wanted if i in addresses][:left]:
        account, jid = addresses[identifier]
        answer = ask(account, jid)
        if answer is None:
            with connect() as db:
                db.execute("INSERT INTO contact_photo_runs(source,ran_at) VALUES('whatsapp:paused',?) ON CONFLICT(source) DO UPDATE SET ran_at=excluded.ran_at", (now,))
            break
        got = fetch(answer['url']) if answer.get('url') else None
        with connect() as db:
            db.execute('INSERT INTO whatsapp_photo_asks(identifier,asked_at,picture_id) VALUES(?,?,?) ON CONFLICT(identifier) DO UPDATE SET '
                       'asked_at=excluded.asked_at,picture_id=excluded.picture_id', (identifier, now, answer.get('id') or None))
            if got and not db.execute('SELECT 1 FROM person_photos WHERE identifier=?', (identifier,)).fetchone():
                db.execute('INSERT INTO person_photos(identifier,key,type,data,saved_at) VALUES(?,?,?,?,?)',
                           (identifier, photo_key(identifier), got[1], got[0], now))
                saved += 1
    return saved


# Facts about people from what the connectors already read while they run: Slack profiles (the users.info lookup done
# for names), X's user objects in mentions and DMs, LinkedIn's messaging participants, Google contacts and the
# Workspace directory (the same daily read as their photos), and CRM contacts. Never an extra call, scope or sign-in.
# Each fact keeps where it came from and when (person_facts). What someone says about themselves (their own Slack,
# X, LinkedIn or Google profile) beats a record someone else keeps about them (a contact the owner made, the
# directory, the CRM), which beats a look-up's guess (person_profiles). Kept on this Machine, sealed like message
# contents, never sent anywhere; Delete permanently removes them.
FACT_FIELDS = {'title': 120, 'company': 120, 'headline': 220, 'bio': 400, 'location': 120, 'website': 200,
               'timezone': 64, 'phone': 32, 'x': 15, 'linkedin_url': 200}
STATED, RECORD = 2, 1  # their own words; a record about them. A look-up counts 0.
VIEW_FACTS = ('headline', 'bio')  # their own longer words: read at view time, never copied into the graph
FACT_SOURCES = {'slack': 'Slack', 'x': 'X', 'linkedin': 'LinkedIn', 'google': 'Google', 'salesflare': 'Salesflare'}
FACT_SCHEMA = ('CREATE TABLE IF NOT EXISTS person_facts(identifier TEXT NOT NULL,field TEXT NOT NULL,value TEXT NOT NULL,rank INTEGER NOT NULL,'
               'source TEXT NOT NULL,seen_at TEXT NOT NULL,PRIMARY KEY(identifier,field))')
WEBSITE = re.compile(r'https?://[A-Za-z0-9.-]{1,200}\.[A-Za-z]{2,}(?::\d{2,5})?(?:/[^\s"\'<>\\]{0,180})?')
X_PHOTO = re.compile(r'https://pbs\.twimg\.com/profile_images/\d{1,25}/[A-Za-z0-9_-]{1,100}_normal\.(?:jpe?g|png|webp)')
LINKEDIN_PHOTO = re.compile(r'https://media\.licdn\.com/dms/image/[^\s"\'<>\\]{10,600}')


def fact_tables(db):
    db.execute(FACT_SCHEMA)


def clean_fact(field, value):
    """The fact as it is kept, or None when it isn't one: plain text on one line, cut to its field's length;
    links, handles, numbers and time zones only in their own shape."""
    if field not in FACT_FIELDS or not isinstance(value, str):
        return None
    text = ' '.join(''.join(c for c in value if c.isprintable() or c.isspace()).split())[:FACT_FIELDS[field]].strip()
    if field == 'website':
        text = text if WEBSITE.fullmatch(text) else ''
    elif field == 'linkedin_url':
        text = text.split('?')[0] if LINKEDIN.fullmatch(text.split('?')[0]) else ''
    elif field == 'x':
        text = text.lstrip('@') if re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', text) else ''
    elif field == 'phone':
        text = text if re.fullmatch(r'\+?[0-9][0-9 ().-]{5,30}', text) and 6 <= len(re.sub(r'\D', '', text)) <= 15 else ''
    elif field == 'timezone':
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(text)
        except Exception:
            text = ''
    return text or None


def offer_facts(db, offers, source, now):
    """Keeps what a connector read about people: offers are (identifiers, {field: value}, rank). A fact of a higher
    rank stays; the same rank or higher replaces it. A field the same source now leaves empty (someone cleared their
    Slack title) goes. People deleted permanently get nothing. Returns how many facts changed."""
    fact_tables(db)
    try:
        import graph
        gone = graph.forgotten(db)
    except Exception:
        gone = lambda identifier: False
    changed = 0
    for ids, fields, rank in offers:
        for identifier in dict.fromkeys(ids):
            if not isinstance(identifier, str) or ':' not in identifier or gone(identifier):
                continue
            for field, raw in fields.items():
                if field not in FACT_FIELDS:
                    continue
                value = clean_fact(field, raw)
                row = db.execute('SELECT value,rank,source FROM person_facts WHERE identifier=? AND field=?', (identifier, field)).fetchone()
                if value is None:
                    if row and row[2] == source and raw is not None:
                        db.execute('DELETE FROM person_facts WHERE identifier=? AND field=?', (identifier, field))
                        changed += 1
                    continue
                if row and (row[1] > rank or (row[0] == value and row[1] == rank)):
                    if row[0] == value:
                        db.execute('UPDATE person_facts SET seen_at=? WHERE identifier=? AND field=?', (now, identifier, field))
                    continue
                db.execute('INSERT INTO person_facts(identifier,field,value,rank,source,seen_at) VALUES(?,?,?,?,?,?) ON CONFLICT(identifier,field) DO UPDATE SET '
                           'value=excluded.value,rank=excluded.rank,source=excluded.source,seen_at=excluded.seen_at',
                           (identifier, field, value, int(rank), source, now))
                changed += 1
    return changed


def people_facts(db, fields=None):
    """{identifier: {field: (value, rank, source, seen_at)}}."""
    try:
        rows = db.execute('SELECT identifier,field,value,rank,source,seen_at FROM person_facts').fetchall()
    except Exception:
        return {}
    out = {}
    for identifier, field, value, rank, source, seen_at in rows:
        if fields is None or field in fields:
            out.setdefault(identifier, {})[field] = (value, rank, source, seen_at)
    return out


def best_facts(found, fields=None):
    """The best of each field over a person's identities: the highest rank, then the latest."""
    best = {}
    for facts in found:
        for field, fact in facts.items():
            if (fields is None or field in fields) and (field not in best or (fact[1], fact[3]) > (best[field][1], best[field][3])):
                best[field] = fact
    return best


def with_facts(profile, found):
    """A person's profile with their facts: one they or a record state beats the look-up's guess; each says where it
    came from and when (sources). Their headline and bio aren't copied here (VIEW_FACTS)."""
    best = best_facts(found, [f for f in FACT_FIELDS if f not in VIEW_FACTS])
    if not best:
        return profile
    profile = dict(profile or {})
    sources = dict(profile.get('sources') or {})
    for field, (value, rank, source, seen_at) in best.items():
        if field in profile and profile[field] and rank <= 0:
            continue
        profile[field] = value
        sources[field] = {'from': FACT_SOURCES.get(source, source), 'at': seen_at[:10], 'own': rank >= STATED}
    profile['sources'] = sources
    return profile


def view_facts(db, ids):
    """A person's headline and bio in their own words, for their page."""
    found = people_facts(db, VIEW_FACTS)
    best = best_facts([found[i] for i in ids if i in found])
    return {field: {'text': value, 'from': FACT_SOURCES.get(source, source), 'at': seen_at[:10]}
            for field, (value, rank, source, seen_at) in best.items()}


def slack_facts(user):
    """What someone put on their own Slack profile: title, phone and time zone."""
    profile = user.get('profile') or {}
    if user.get('is_bot') or user.get('deleted') or not user.get('id'):
        return None
    return (['slack:' + str(user['id'])], {'title': profile.get('title') or '', 'phone': profile.get('phone') or '',
                                           'timezone': user.get('tz') or ''}, STATED)


def google_facts(person):
    """Job title, company and phone from a Google contact or directory person: their own profile says it (STATED), or
    the owner's contact or the directory does (RECORD)."""
    kind = lambda item: ((item.get('metadata') or {}).get('source') or {}).get('type')
    ids = ['email:' + str(e.get('value')).strip().lower() for e in (person.get('emailAddresses') or [])
           if isinstance(e, dict) and '@' in str(e.get('value') or '')]
    if not ids:
        return []
    orgs = [o for o in person.get('organizations') or [] if isinstance(o, dict) and (o.get('title') or o.get('name')) and not o.get('endDate')]
    org = min(orgs, key=lambda o: (not (o.get('metadata') or {}).get('primary'), kind(o) != 'PROFILE')) if orgs else {}
    phones = [n for n in person.get('phoneNumbers') or [] if isinstance(n, dict) and n.get('canonicalForm')]
    out = []
    if org:
        out.append((ids, {'title': org.get('title') or '', 'company': org.get('name') or ''}, STATED if kind(org) == 'PROFILE' else RECORD))
    if phones:
        out.append((ids, {'phone': str(phones[0]['canonicalForm'])}, RECORD))
    return out


def facts_from_signals(connect, now):
    """Once: the facts already in stored signals (LinkedIn headlines, CRM job titles), so people read before facts
    were kept get them too. Returns how many were kept."""
    with connect() as db:
        fact_tables(db)
        db.execute('CREATE TABLE IF NOT EXISTS enrich_prefs(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        if db.execute("SELECT 1 FROM enrich_prefs WHERE key='facts_from_signals'").fetchone():
            return 0
        offers = {}
        for kind, metadata, at in db.execute("SELECT kind,metadata,occurred_at FROM events WHERE deleted=0 AND kind IN "
                                             "('linkedin.message','salesflare.email','salesflare.meeting') ORDER BY occurred_at"):
            try:
                m = json.loads(metadata or '{}')
            except ValueError:
                continue
            if kind == 'linkedin.message' and m.get('headline') and m.get('conversation_id') and not m.get('group'):
                offers[('li:' + str(m['conversation_id']), 'headline')] = (str(m['headline']), STATED, 'linkedin')
            for c in (m.get('contacts') or ()) if kind != 'linkedin.message' else ():
                if isinstance(c, dict) and '@' in str(c.get('email') or '') and c.get('role'):
                    offers[('email:' + str(c['email']).lower(), 'title')] = (str(c['role']), RECORD, 'salesflare')
        kept = 0
        for (identifier, field), (value, rank, source) in offers.items():
            kept += offer_facts(db, [([identifier], {field: value}, rank)], source, now)
        db.execute("INSERT INTO enrich_prefs(key,value) VALUES('facts_from_signals',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
    return kept


def photo(db, key):
    """(bytes, type) of a saved photo by its key, for the dashboard."""
    if not re.fullmatch(r'[0-9a-f]{32}', str(key or '')):
        return None
    row = db.execute('SELECT data,type FROM person_photos WHERE key=?', (key,)).fetchone()
    return (bytes(row[0]), row[1]) if row and row[1] in ('image/jpeg', 'image/png', 'image/webp') else None


def people_status(db):
    counts = dict(db.execute('SELECT status,count(*) FROM person_profiles GROUP BY status').fetchall())
    return {'on': people_on(db), 'found': counts.get('found', 0), 'unknown': counts.get('none', 0), 'errors': counts.get('error', 0)}
