"""Place suggestions for the Calendar form's Where field.

Local first: places the owner already used on their calendar, and the organizations in their Graph with a known
location. Then, when that isn't enough, addresses from Photon (photon.komoot.io), a free geocoder on OpenStreetMap
data. Only the typed text goes out, from this server (never the browser), at most one request a second, cached, with
an honest User-Agent. Set "place_search": false in the config to keep every search local."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PHOTON = 'https://photon.komoot.io/api/'
AGENT = 'the-machine/1 (+https://themachine.computer)'
MIN_LOCAL, MIN_REMOTE, MAX_QUERY, LIMIT = 2, 3, 120, 6
GAP = 1.0  # seconds between two outside requests, for the whole Machine

_lock = threading.Lock()
_last = [0.0]
_cache: dict[str, list] = {}


def clean(text):
    return ' '.join(str(text or '').split())


def local(db, query, limit=LIMIT):
    """Earlier event locations and Graph organizations whose words match, the most used and strongest first."""
    q = clean(query).casefold()
    if len(q) < MIN_LOCAL:
        return []
    likes = ['%' + w.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for w in q.split()[:6]]
    every = lambda column: ' AND '.join(f"{column} LIKE ? ESCAPE '\\'" for _ in likes)  # every typed word, in any order
    found, seen = [], set()

    def add(value, label, detail, source):
        value = clean(value)[:300]
        if value and value.casefold() not in seen and not value.lower().startswith(('http://', 'https://')):
            seen.add(value.casefold())
            found.append({'value': value, 'label': clean(label)[:120] or value, 'detail': clean(detail)[:160], 'source': source})

    try:
        rows = db.execute("SELECT json_extract(metadata,'$.location') AS place,count(*) AS n,max(occurred_at) AS last FROM events "
                          "WHERE kind='calendar.event' AND deleted=0 AND " + every("lower(json_extract(metadata,'$.location'))") +
                          ' GROUP BY lower(place) ORDER BY n DESC,last DESC LIMIT 20', likes).fetchall()
    except Exception:  # noqa: BLE001 - an old database without events: nothing local
        rows = []
    for row in rows:
        place = clean(row[0])
        if len(place) <= 200 and '\n' not in str(row[0]):
            add(place, place.split(',')[0], ', '.join(p.strip() for p in place.split(',')[1:]) or 'Used before', 'calendar')
    try:
        orgs = db.execute("SELECT n.name,coalesce(json_extract(e.fields,'$.location'),json_extract(p.profile,'$.location')) AS place "
                          'FROM graph_nodes n '
                          "LEFT JOIN org_edits e ON e.domain=json_extract(n.info,'$.domain') "
                          "LEFT JOIN org_profiles p ON p.domain=json_extract(n.info,'$.domain') AND p.status='found' "
                          "WHERE n.kind='org' AND place IS NOT NULL AND place!='' AND " + every("lower(n.name||' '||place)") +
                          ' ORDER BY n.strength DESC LIMIT 10', likes).fetchall()
    except Exception:  # noqa: BLE001 - no Graph yet
        orgs = []
    for name, place in orgs:
        add(clean(name) + ', ' + clean(place), name, place, 'graph')
    return found[:limit]


def address(properties):
    """A clean one-line address from Photon's fields: name, street and number, postcode and city, country."""
    p = properties or {}
    street = clean(' '.join(x for x in (clean(p.get('street')), clean(p.get('housenumber'))) if x))
    city = clean(' '.join(x for x in (clean(p.get('postcode')), clean(p.get('city') or p.get('town') or p.get('village') or p.get('county'))) if x))
    name = clean(p.get('name'))
    parts = []
    country = clean(str(p.get('country') or '').split(' / ')[0])  # "België / Belgique / Belgien": its first name
    for part in (name, street, city, country):
        if part and part.casefold() not in (x.casefold() for x in parts):
            parts.append(part)
    if not parts:
        return None
    label = name or street or city
    return {'value': ', '.join(parts)[:300], 'label': label[:120], 'detail': ', '.join(x for x in parts if x != label)[:160], 'source': 'map'}


def photon(query, fetch=None, clock=time.monotonic, sleep=time.sleep, timeout=6):
    """Addresses for the typed text from Photon, with names as they are on the spot (Grote Markt, not Great Market
    Square). Only the text goes out."""
    q = clean(query)[:MAX_QUERY]
    if len(q) < MIN_REMOTE:
        return []
    key = q.casefold()
    if key in _cache:
        return _cache[key]
    url = PHOTON + '?' + urllib.parse.urlencode({'q': q, 'limit': LIMIT})
    with _lock:  # one at a time, a second apart
        wait = GAP - (clock() - _last[0])
        if wait > 0:
            sleep(wait)
        try:
            raw = (fetch or _get)(url, timeout)
        finally:
            _last[0] = clock()
    try:
        features = json.loads(raw or b'{}').get('features') or []
    except ValueError:
        return []
    found, seen = [], set()
    for feature in features[:LIMIT * 2]:
        place = address((feature or {}).get('properties'))
        if place and place['value'].casefold() not in seen:
            seen.add(place['value'].casefold())
            found.append(place)
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = found[:LIMIT]
    return _cache[key]


def _get(url, timeout):
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - a fixed https host
        return response.read(500_000)


def same(place, mine):
    """A map address for a place the owner has already: the same name, and its street in what they wrote."""
    street = place['detail'].split(',')[0].strip().casefold()
    return place['label'].casefold() == mine['label'].casefold() and (not street or street in mine['value'].casefold())


def suggest(db, config, query, fetch=None):
    """Local places first, then addresses from the map when there are few, without the places the owner already has.
    A failing map search keeps the local ones."""
    found = local(db, query)
    if len(found) < 3 and (config or {}).get('place_search', True) is not False:
        try:
            seen = {f['value'].casefold() for f in found}
            found += [p for p in photon(query, fetch) if p['value'].casefold() not in seen and not any(same(p, f) for f in found)]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
    return found[:LIMIT + 2]
