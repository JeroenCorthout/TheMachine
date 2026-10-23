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
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REFRESH_DAYS = 90
RETRY_HOURS = 24        # after an error (timeout, rate limit)
PER_PASS = 5
PER_DAY = 50
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
    """How much a look-up is worth for one organisation, from what the graph says about its people: 0 for free mail,
    a sender subdomain, or people who only ever wrote in (cold outreach) or were only copied. Someone the owner
    writes with both ways or meets counts most, someone the owner only wrote to a little."""
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
    return score


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


def step(connect, config, now, per_pass=PER_PASS):
    """Look up a few organisations. Returns how many got a profile; stops at the first service error."""
    cfg = settings(config)
    if not cfg:
        return 0
    with connect() as db:
        today = now[:10]
        used = db.execute("SELECT count(*) FROM org_profiles WHERE substr(fetched_at,1,10)=? AND status!='wrong'", (today,)).fetchone()[0]
        domains = due(db, now, max(0, min(per_pass, int(cfg.get('per_day') or PER_DAY) - used)))
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
    """The owner's Settings switch, Look up work contacts: on unless turned off."""
    try:
        row = db.execute("SELECT value FROM enrich_prefs WHERE key='people'").fetchone()
    except Exception:
        return True
    return not row or row[0] != 'off'


def set_people(db, on):
    db.execute('INSERT INTO enrich_prefs(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               ('people', 'on' if on else 'off'))
    return {'people': bool(on)}


def folded(text):
    import unicodedata
    return re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().casefold())


def same_domain(a, b):
    a, b = (re.sub(r'^(?:https?://)?(?:www\.)?', '', str(x or '').strip().lower()).split('/')[0] for x in (a, b))
    return bool(a and b) and (a == b or a.endswith('.' + b) or b.endswith('.' + a))


def normalise_person(data, asked_name, domain, fields=None):
    """The small person profile the dashboard shows, from whatever shape the service returns; None when it found
    nothing, or when the answer is about someone else: a name with no word in common, or a company at another domain."""
    if isinstance(data, list):
        data = data[0] if data else None
    for wrapper in PERSON_WRAPPERS:
        if isinstance(data, dict) and isinstance(data.get(wrapper), dict):
            data = data[wrapper]
            break
    if not isinstance(data, dict) or data.get('success') is False:
        return None
    name = next((v for v in (value(data, p) for p in PERSON_NAME) if isinstance(v, str) and v.strip()), '')
    if name and asked_name and not {w for w in folded(name) if len(w) > 1} & {w for w in folded(asked_name) if len(w) > 1}:
        return None
    profile = {}
    for field, paths in PERSON_FIELDS.items():
        for path in list((fields or {}).get(field) or []) + paths:
            found = value(data, path)
            if found in (None, '', [], {}) or isinstance(found, (dict, list, bool)):
                continue
            found = ' '.join(str(found).split())
            if field == 'photo':
                if not LOGO.fullmatch(found):
                    continue  # only a plain https address; the dashboard loads it as an image, nothing else
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
        if not same_domain(profile['company_domain'], domain):
            return None  # someone of the same name at another company
        profile['company_domain'] = domain
    return profile if profile.get('title') or profile.get('company') or profile.get('photo') else None


def fetch_person(cfg, person, timeout=15):
    """The profile for one person ({email, name, domain}), None when the service doesn't know them."""
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
        return None
    try:
        return normalise_person(json.loads(body or b'null'), person['name'], person['domain'], cfg.get('people_fields'))
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
    return min(addresses)[1:] if addresses else None


def people_due(db, now, limit):
    """People that were never looked up or are due again, the ones the owner deals with most first:
    [{'identifier', 'email', 'name', 'domain'}]. Nothing while the owner turned Look up work contacts off."""
    if limit <= 0 or not people_on(db):
        return []
    current = dt.datetime.fromisoformat(now)
    fresh = (current - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    retry = (current - dt.timedelta(hours=RETRY_HOURS)).isoformat()
    looked = {r[0]: (r[1], r[2]) for r in db.execute('SELECT identifier,status,fetched_at FROM person_profiles')}
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
        status, at = looked.get(identifier, (None, None))
        if status and not ((status != 'error' and at < fresh) or (status == 'error' and at < retry)):
            continue
        both = info.get('met') or (info.get('wrote') and info.get('heard'))
        ranked.append((-(3 if both else 1), -(strength or 0), identifier, {'identifier': identifier, 'email': found[0], 'name': ' '.join(str(name).split()), 'domain': found[1]}))
    return [item for *_, item in sorted(ranked, key=lambda r: r[:3])[:limit]]


def save_person(db, identifier, status, profile, now):
    """Stores a person look-up; an error keeps what was there."""
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


def people_step(connect, config, now, per_pass=PEOPLE_PER_PASS):
    """Look up a few work contacts with the Machine's own service. Returns how many got a profile; stops at the first
    service error."""
    cfg = settings(config)
    if not cfg or not cfg.get('people_url') or cfg.get('people') is False:
        return 0
    with connect() as db:
        used = db.execute("SELECT count(*) FROM person_profiles WHERE substr(fetched_at,1,10)=?", (now[:10],)).fetchone()[0]
        due = people_due(db, now, max(0, min(per_pass, int(cfg.get('people_per_day') or PEOPLE_PER_DAY) - used)))
    found = 0
    for person in due:
        try:
            profile = fetch_person(cfg, person)
        except EnrichmentError:
            with connect() as db:
                save_person(db, person['identifier'], 'error', None, now)
            raise
        with connect() as db:
            save_person(db, person['identifier'], 'found' if profile else 'none', profile, now)
        found += bool(profile)
    return found


def people_status(db):
    counts = dict(db.execute('SELECT status,count(*) FROM person_profiles GROUP BY status').fetchall())
    return {'on': people_on(db), 'found': counts.get('found', 0), 'unknown': counts.get('none', 0), 'errors': counts.get('error', 0)}
