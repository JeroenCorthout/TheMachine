"""Organisation data from an enrichment service, looked up by domain.

Works with any service that answers GET <url with the domain> with JSON about the company: a preset
(apollo, peopledatalabs, hunter) or your own url template with {domain} and {key}, and headers that can
use {key} too. The key is read from a file or an environment variable, never stored in the config.
Only organisation domains from the graph are sent (never free-mail or the owner's own domains), a few
at a time, each looked up again after REFRESH_DAYS: first those of people the owner writes with both ways or
meets, never those only cold outreach, newsletters or no-reply senders come from (see due). What comes back is
normalised to a small profile (industry, size, location, founded, about, links) that the graph, reviews and
preparations show next to the organisation; it describes the company, never what it is to the owner."""
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
    'peopledatalabs': {'url': 'https://api.peopledatalabs.com/v5/company/enrich?website={domain}', 'headers': {'X-Api-Key': '{key}'}},
    'hunter': {'url': 'https://api.hunter.io/v2/companies/find?domain={domain}&api_key={key}'},
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
}
WRAPPERS = ('organization', 'company', 'data', 'result')


class EnrichmentError(Exception):
    pass


def settings(config):
    """The enrichment settings with a preset filled in, or None when it isn't set up."""
    raw = config.get('enrichment')
    if not isinstance(raw, dict) or raw.get('enabled') is False:
        return None
    merged = {**PRESETS.get(str(raw.get('provider') or '').lower(), {}), **{k: v for k, v in raw.items() if v not in (None, '')}}
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


def fetch(cfg, domain, timeout=15):
    """The profile for one domain, None when the service doesn't know it. Errors never carry the key or url."""
    secret = key(cfg)
    if not secret and ('{key}' in cfg['url'] or '{key}' in json.dumps(cfg.get('headers') or {})):
        raise EnrichmentError('no key: set key_file or key_env in enrichment')
    url = cfg['url'].replace('{domain}', urllib.parse.quote(domain)).replace('{key}', urllib.parse.quote(secret))
    headers = {'Accept': 'application/json', 'User-Agent': 'the-machine/1 (+https://themachine.computer)'}
    headers.update({k: str(v).replace('{key}', secret) for k, v in (cfg.get('headers') or {}).items()})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            body = response.read(2_000_000)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in (404, 422):
            return None
        raise EnrichmentError({401: 'the service rejected the key', 403: 'the service refused (key or plan)',
                               429: 'rate limited'}.get(error.code, f'HTTP {error.code}')) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise EnrichmentError('unreachable: ' + type(getattr(error, 'reason', error)).__name__) from None
    try:
        return normalise(json.loads(body or b'null'), cfg.get('fields'))
    except ValueError:
        raise EnrichmentError('the answer was not JSON') from None


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
