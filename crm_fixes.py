"""Easy fixes a CSV import suggests in its preview (crm_import.py), each shown as before and after, and applied only
when the owner leaves it ticked: spaces, capitals, phone numbers (in international form, the country inferred from
the file and the Machine's time zone), email addresses (mailto:, a mistyped gmail.com), websites as their domain,
dates in any common format, values with a currency or k/M ("€12k" is 12000), several tags in one cell, a stage
spelled differently from the board's, and the same row twice in the file (merged into the first).

All of it is rules, on the Machine. A cell the rules can't read for sure (a date like 03/04 with nothing else in the
column to tell, a number with no country code and no country to go by, a stage no board has) is ambiguous: those
cells, a few at most, are what the owner's own model may look at (crm_import.ai_evidence), and what it suggests is
checked here like any other fix before it is shown."""
from __future__ import annotations

import datetime as dt
import difflib
import re

import graph

TYPES = {'spaces': 'Extra spaces', 'case': 'Capitals', 'split': 'Name and email in one cell', 'email': 'Email typos',
         'phone': 'Phone numbers', 'website': 'Websites', 'date': 'Dates', 'value': 'Values', 'tags': 'Tags',
         'stage': 'Stages', 'merge': 'Same row twice'}
FIELD_TYPE = {'close_date': 'date', 'phone': 'phone', 'value': 'value', 'stage': 'stage'}  # what an ambiguous cell is
SHOWN = 300  # fixes listed in the preview; the counts cover all

CALLING = {'BE': '32', 'NL': '31', 'FR': '33', 'DE': '49', 'GB': '44', 'IE': '353', 'ES': '34', 'PT': '351', 'IT': '39',
           'CH': '41', 'AT': '43', 'LU': '352', 'DK': '45', 'SE': '46', 'NO': '47', 'FI': '358', 'PL': '48', 'CZ': '420',
           'GR': '30', 'RO': '40', 'HU': '36', 'EE': '372', 'LT': '370', 'LV': '371', 'US': '1', 'CA': '1', 'AU': '61',
           'NZ': '64', 'IN': '91', 'SG': '65', 'ZA': '27', 'BR': '55', 'MX': '52', 'JP': '81', 'IL': '972', 'AE': '971'}
ZONES = {'Brussels': 'BE', 'Amsterdam': 'NL', 'Paris': 'FR', 'Berlin': 'DE', 'London': 'GB', 'Dublin': 'IE', 'Madrid': 'ES',
         'Lisbon': 'PT', 'Rome': 'IT', 'Zurich': 'CH', 'Vienna': 'AT', 'Luxembourg': 'LU', 'Copenhagen': 'DK',
         'Stockholm': 'SE', 'Oslo': 'NO', 'Helsinki': 'FI', 'Warsaw': 'PL', 'Prague': 'CZ', 'Athens': 'GR',
         'Bucharest': 'RO', 'Budapest': 'HU', 'Tallinn': 'EE', 'Vilnius': 'LT', 'Riga': 'LV', 'Toronto': 'CA',
         'Vancouver': 'CA', 'Montreal': 'CA', 'Auckland': 'NZ', 'Kolkata': 'IN', 'Calcutta': 'IN', 'Singapore': 'SG',
         'Johannesburg': 'ZA', 'Sao_Paulo': 'BR', 'Mexico_City': 'MX', 'Tokyo': 'JP', 'Jerusalem': 'IL', 'Dubai': 'AE'}
US_DATES = {'US'}  # month first

TYPOS = {'gmial.com': 'gmail.com', 'gmal.com': 'gmail.com', 'gamil.com': 'gmail.com', 'gmai.com': 'gmail.com',
         'gnail.com': 'gmail.com', 'gmail.co': 'gmail.com', 'gmail.con': 'gmail.com', 'gmaill.com': 'gmail.com',
         'hotmial.com': 'hotmail.com', 'hotmai.com': 'hotmail.com', 'hotmal.com': 'hotmail.com', 'hotmail.con': 'hotmail.com',
         'yaho.com': 'yahoo.com', 'yahooo.com': 'yahoo.com', 'yahoo.con': 'yahoo.com', 'outlok.com': 'outlook.com',
         'outloo.com': 'outlook.com', 'outlook.con': 'outlook.com', 'iclod.com': 'icloud.com', 'icloud.con': 'icloud.com'}
SUFFIXES = {s.casefold(): s for s in ('BV', 'NV', 'BVBA', 'SRL', 'SA', 'SAS', 'AG', 'GmbH', 'LLC', 'Inc', 'Ltd', 'PLC', 'AB', 'AS', 'Oy', 'Co', 'LLP')}
PARTICLES = {'van', 'de', 'der', 'den', 'von', 'la', 'le', 'du', 'di', 'da', 'del', 'dos', 'ten', 'ter', 'te', 'het'}
MONTHS = {m: i + 1 for i, names in enumerate((
    'jan january januari janvier januar enero', 'feb february februari fevrier février februar febrero',
    'mar march maart mrt mars marz märz marzo', 'apr april avril abril', 'may mei mai mayo',
    'jun june juni juin junio', 'jul july juli juillet julio', 'aug august augustus aout août agosto',
    'sep sept september septembre septiembre', 'oct october oktober okt octobre octubre',
    'nov november novembre noviembre', 'dec december dez dezember decembre décembre diciembre'))
    for m in names.split()}
CURRENCIES = {'€': 'EUR', '$': 'USD', '£': 'GBP', '¥': 'JPY'}
MULTIPLY = {'k': 1e3, 'm': 1e6, 'mn': 1e6, 'mio': 1e6, 'bn': 1e9}


def region_of(config):
    """The owner's country (two letters) for phone numbers and dates: `country` in the config, else the time zone's."""
    country = str((config or {}).get('country') or '').strip().upper()
    if country in CALLING:
        return country
    zone = str((config or {}).get('timezone') or '')
    if zone.startswith('America/') and zone.split('/')[-1] not in ZONES:
        return 'US' if zone.split('/')[-1] in ('New_York', 'Chicago', 'Denver', 'Los_Angeles', 'Phoenix', 'Anchorage', 'Detroit') else None
    if zone.startswith('Australia/'):
        return 'AU'
    return ZONES.get(zone.split('/')[-1])


# ------------------------------------------------------------------ one cell each

def spaces(value):
    fixed = ' '.join(value.replace(' ', ' ').split())
    return fixed if fixed != value else None


def _word(word, first, person):
    if '-' in word:
        return '-'.join(_word(w, first and i == 0, person) for i, w in enumerate(word.split('-')))
    low = word.casefold()
    if not person and low in SUFFIXES:
        return SUFFIXES[low]
    if person and not first and low in PARTICLES:
        return low
    for lead in ("o'", "d'", 'mc'):
        if low.startswith(lead) and len(low) > len(lead) + 1:
            return lead[0].upper() + lead[1:] + low[len(lead)].upper() + low[len(lead) + 1:]
    return low[:1].upper() + low[1:]


def case(value, person):
    """ALL CAPS or all lowercase names in capitals (Jane van Dam, Acme Robotics BV). A short all-caps organization name
    is likely its acronym (IBM) and stays; so does a domain."""
    letters = [c for c in value if c.isalpha()]
    if len(letters) < 2 or (not person and re.fullmatch(r'\S+\.[a-z]{2,24}', value)):
        return None
    shout = value.isupper() and (person or (len(value.split()) >= 2 and len(letters) > 5))
    if not (shout or value.islower()):
        return None
    fixed = ' '.join(_word(w, i == 0, person) for i, w in enumerate(value.split(' ')))
    return fixed if fixed != value else None


SPLIT = re.compile(r'\s*"?([^"<>@]+?)"?\s*<\s*(?:mailto:)?([^<>\s]+@[^<>\s]+)\s*>\s*')


def split(value):
    """("Jane Doe <jane@acme.com>") -> ('Jane Doe', 'jane@acme.com'), or None."""
    found = SPLIT.fullmatch(value)
    return (' '.join(found[1].split()), found[2]) if found else None


def email(value):
    fixed = value.strip().removeprefix('mailto:').strip().replace(',', '.')
    if '@' not in fixed:
        return None
    local, _, domain = fixed.rpartition('@')
    domain = domain.lower().rstrip('.')
    domain = TYPOS.get(domain) or re.sub(r'\.(con|cmo|cm|comm)$', '.com', domain)
    fixed = local + '@' + domain
    return fixed if fixed.lower() != value.lower() else None


def phone(value, code):
    """The number in international form (+32475123456), or None when it can't be read for sure."""
    if re.search(r'[a-z]', value, re.I):
        return None
    raw = re.sub(r'\(0\)', '', value)
    digits = re.sub(r'[^\d+]', '', raw)
    if digits.count('+') > 1 or ('+' in digits and not digits.startswith('+')):
        return None
    if digits.startswith('+'):
        fixed = digits
    elif digits.startswith('00'):
        fixed = '+' + digits[2:]
    elif code == '1' and len(digits) == 10:
        fixed = '+1' + digits
    elif code == '1' and len(digits) == 11 and digits.startswith('1'):
        fixed = '+' + digits
    elif code == '39':
        fixed = '+39' + digits
    elif code and digits.startswith('0') and not digits.startswith('00'):
        fixed = '+' + code + digits[1:]
    else:
        return None
    return fixed if re.fullmatch(r'\+[1-9]\d{7,14}', fixed) else None


def website(value):
    domain = graph.website_domain(value)
    return domain if domain and domain != value else None


def _iso(y, m, d):
    y = int(y)
    y = 2000 + y if y < 100 else y
    try:
        return dt.date(y, int(m), int(d)).isoformat()
    except ValueError:
        return None


def date(value, order):
    """The date as YYYY-MM-DD; order says day or month first for 03/04/2026 ('dmy', 'mdy'), None when unknown."""
    v = ' '.join(value.strip().lower().replace(',', ' ').split())
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        return None
    found = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})[t ][\d:.+z -]*', v) or re.fullmatch(r'(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})', v)
    if found:
        return _iso(*found.groups())
    found = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})', v)
    if found:
        a, b, y = (int(x) for x in found.groups())
        if a > 12 or (b <= 12 and order == 'dmy'):
            return _iso(y, b, a)
        if b > 12 or order == 'mdy':
            return _iso(y, a, b)
        return None
    found = re.fullmatch(r'(\d{1,2})(?:st|nd|rd|th|e|er|\.)? ?([a-zéû]+)\.? (\d{2}|\d{4})', v)
    if found and found[2] in MONTHS:
        return _iso(found[3], MONTHS[found[2]], found[1])
    found = re.fullmatch(r'([a-zéû]+)\.? (\d{1,2})(?:st|nd|rd|th)? (\d{2}|\d{4})', v)
    if found and found[1] in MONTHS:
        return _iso(found[3], MONTHS[found[1]], found[2])
    return None


def date_order(values, region):
    """Day or month first, from the column itself (a 25/12 says day first), else from the owner's country; None when
    neither tells."""
    for v in values:
        found = re.fullmatch(r'\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}\s*', v)
        if found and int(found[1]) > 12:
            return 'dmy'
        if found and int(found[2]) > 12:
            return 'mdy'
    return ('mdy' if region in US_DATES else 'dmy') if region else None


def money(value):
    """(amount, currency or None) from "€12k", "12.000,50 EUR", "$1.2M", or None."""
    v = value.strip().replace(' ', ' ')
    currency = None
    for sign, code in CURRENCIES.items():
        if sign in v:
            currency, v = code, v.replace(sign, '')
    found = re.fullmatch(r'\s*([A-Za-z]{3})?\s*(.*?)\s*([A-Za-z]{3})?\s*', v)
    for code in (found[1], found[3]) if found else ():
        if code and code.upper() in ('EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CAD', 'AUD', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'INR'):
            currency = code.upper()
    v = found[2] if found else v
    mult = re.fullmatch(r'(.*?)\s*(k|m|mn|mio|bn)\.?', v, re.I)
    factor = 1
    if mult:
        v, factor = mult[1], MULTIPLY[mult[2].lower()]
    v = v.replace(' ', '').replace("'", '')
    if not re.fullmatch(r'-?[\d.,]+', v) or not re.search(r'\d', v):
        return None
    if ',' in v and '.' in v:
        decimal = ',' if v.rfind(',') > v.rfind('.') else '.'
        v = v.replace('.' if decimal == ',' else ',', '').replace(',', '.')
    elif ',' in v:
        v = v.replace(',', '') if re.fullmatch(r'-?\d{1,3}(,\d{3})+', v) else v.replace(',', '.')
    elif v.count('.') > 1 or re.fullmatch(r'-?\d{1,3}(\.\d{3})+', v) and factor == 1:
        v = v.replace('.', '')
    try:
        amount = float(v) * factor
    except ValueError:
        return None
    return amount, currency


def number(amount):
    return str(int(amount)) if float(amount).is_integer() else f'{amount:.2f}'.rstrip('0')


def tags(value):
    parts = [' '.join(p.split()) for p in re.split(r'[,;|/\n]', value)]
    parts = list(dict.fromkeys(p for p in parts if p))
    return parts if len(parts) > 1 or (parts and parts[0] != value) else None


def stage(value, stages):
    """The board's own stage for a stage spelled another way ("proposal sent" for Proposal), or None."""
    low = ' '.join(re.sub(r'[^\w]+', ' ', value.casefold()).split())
    names = {' '.join(re.sub(r'[^\w]+', ' ', s.casefold()).split()): s for s in stages}
    if not low or value in stages:
        return None
    if low in names:
        return names[low] if names[low] != value else None
    inside = {s for k, s in names.items() if k and (k in low.split() or k in low or low in k)}
    if len(inside) == 1:
        return inside.pop()
    close = difflib.get_close_matches(low, list(names), n=1, cutoff=0.8)
    return names[close[0]] if close else None


def valid(field, after, ctx):
    """Whether a suggested value (the model's) is a value the import takes for that field."""
    after = str(after or '').strip()
    if field == 'close_date':
        return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', after)) and _iso(*after.split('-')) == after
    if field == 'phone':
        return bool(re.fullmatch(r'\+[1-9]\d{7,14}', after))
    if field == 'value':
        return bool(re.fullmatch(r'\d+(\.\d+)?', after))
    if field == 'stage':
        return after in ctx.get('stages', ())
    return False


# ------------------------------------------------------------------ a whole file

def suggest(kind, rows, mapping, ctx):
    """(fixes, ambiguous) for the rows: fixes as {'id', 'line', 'field', 'type', 'before', 'after'} (one per cell; a
    split also has 'extra', the other field it fills), ambiguous as {'line', 'field', 'value'} cells the rules can't
    read for sure."""
    fields = {i: f for i, f in mapping.items()}
    code = CALLING.get(ctx.get('region') or '')
    by_code = {}
    for i, f in fields.items():
        if f == 'phone':
            for r in rows:
                found = re.match(r'\s*(?:\+|00)(\d{1,3})', r[i])
                for size in (1, 2, 3):
                    if found and found[1][:size] in CALLING.values():
                        by_code[found[1][:size]] = by_code.get(found[1][:size], 0) + 1
                        break
    if not code and by_code:
        code = max(by_code, key=by_code.get)  # the country most numbers in the file have
    orders = {i: date_order([r[i] for r in rows], ctx.get('region')) for i, f in fields.items() if f == 'close_date'}
    fixes, ambiguous = [], []
    for n, row in enumerate(rows, start=2):
        for i, field in fields.items():
            before = row[i]
            if not before:
                continue
            fix = type_ = extra = None
            if field in ('name', 'first_name', 'last_name', 'org') or (kind == 'org' and field == 'name'):
                if field in ('name', 'org') and '<' in before and split(before):
                    name, address = split(before)
                    if kind == 'person' and field == 'name':
                        fix, type_, extra = name, 'split', {'email': address}
                if not fix:
                    person = kind == 'person' and field != 'org'
                    fix, type_ = case(spaces(before) or before, person), 'case'
                    if fix is None and spaces(before):
                        fix, type_ = spaces(before), 'spaces'
            elif field == 'email':
                parts = split(before)
                if parts:
                    fix, type_, extra = email(parts[1]) or parts[1], 'split', {'name': parts[0]}
                else:
                    fix, type_ = email(before), 'email'
            elif field == 'phone':
                fix, type_ = phone(before, code), 'phone'
                if fix == before:
                    fix = None
                elif fix is None and not re.fullmatch(r'\+[1-9]\d{7,14}', before):
                    ambiguous.append({'line': n, 'field': field, 'value': before})
            elif field == 'website' and kind == 'org':
                fix, type_ = website(before), 'website'
            elif field == 'close_date':
                fix, type_ = date(before, orders.get(i)), 'date'
                if fix is None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', before):
                    ambiguous.append({'line': n, 'field': field, 'value': before})
            elif field == 'value':
                read = money(before)
                if read and number(read[0]) != before:
                    fix, type_ = number(read[0]), 'value'
                    if read[1] and read[1] != ctx.get('currency'):
                        extra = {'currency': read[1]}
                elif not read:
                    ambiguous.append({'line': n, 'field': field, 'value': before})
            elif field == 'tags':
                found = tags(before)
                if found:
                    fix, type_ = ', '.join(found), 'tags'
            elif field == 'stage' and kind == 'project':
                stages = ctx.get('stages_by_line', {}).get(n) or ctx.get('stages') or ()
                if before not in stages and before.casefold() not in {s.casefold() for s in stages}:
                    fix, type_ = stage(before, stages), 'stage'
                    if fix is None and stages:
                        ambiguous.append({'line': n, 'field': field, 'value': before})
            elif field in ('title', 'location', 'notes', 'about', 'industry', 'board'):
                fix, type_ = spaces(before), 'spaces'
            if fix and fix != before:
                item = {'id': f'{n}:{field}', 'line': n, 'field': field, 'type': type_, 'before': before, 'after': fix}
                if extra:
                    item['extra'] = extra
                fixes.append(item)
    return fixes, ambiguous


def chosen(fixes, choice):
    """The fixes the owner kept: choice is {'types': [...] or 'all', 'off': [fix ids]}; None keeps none."""
    if not isinstance(choice, dict):
        return []
    types = choice.get('types')
    off = {str(i) for i in choice.get('off') or ()}
    return [f for f in fixes if (types == 'all' or f['type'] in (types or ())) and f['id'] not in off]
