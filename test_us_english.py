"""User-facing text is in US English (Organization, color, -ize) until The Machine goes multilingual.

Checks prose only: Markdown outside code, the dashboard and scripts, and comments and strings in Python. Identifiers,
stored values and API fields keep their old spelling (a bare lowercase literal like 'cancelled', an attribute, a call,
an object key), and so do quoted user content and third-party names, listed in ALLOWED.
"""
import io
import pathlib
import re
import subprocess
import tokenize
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
UK = re.compile(r'(?<![\w.$#/-])((?:re)?organis(?:e|ed|es|ing|er|ers|ation|ations)|colour(?:s|ed|ing)?|behaviours?'
                r'|favour(?:s|ed|ite|ites)?|honour(?:s|ed|ing)?|neighbours?|centre[sd]?|cancell(?:ed|ing)|licences?|catalogues?'
                r'|analys(?:e|ed|ing)|judgements?|labell(?:ed|ing)|recognis(?:e|ed|es|ing|able)|summaris(?:e|ed|es|ing)'
                r'|prioritis(?:e|ed|es|ing)|personalis(?:e|ed|ation)|authoris(?:e|ed|es|ing|ation)|customis(?:e|ed|ation)'
                r'|optimis(?:e|ed|ation)|normalis(?:e|ed|es|ing)|realis(?:e|ed|es|ing)|minimis(?:e|ed|ation)|travell(?:ed|ing)'
                r'|modell(?:ed|ing)|fulfilment|programmes?|grey|defence|whilst|learnt|acknowledgements?)(?![\w(-])', re.I)
SKIP = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'TRADEMARKS.md', 'package-lock.json', 'package.json', 'wacli-pin.json', 'test_us_english.py'}
SUFFIXES = {'.py', '.md', '.html', '.mjs', '.js', '.ts', '.json', '.sh', '.ps1', '.webmanifest', '.yml', '.yaml', '.txt'}
# (file, words on the line): kept on purpose
ALLOWED = [
    ('assistant.py', 'learned|learnt|remembered'),  # matches what the model writes
    ('skills.py', "'LICENCE'"),  # a file name in third-party skills
    ('dashboard.html', "keywords:'organisation organization"),  # the palette finds it typed either way
    ('test_dismiss_and_snooze.py', "I didn't realise"),  # a reason as the owner types it
    ('test_crm.py', "'Organisation'"),  # a spreadsheet's column header as it may be written
]


def hits(text, code):
    found = []
    for m in UK.finditer(text):
        w, s, e = m.group(1), m.start(1), m.end(1)
        before, after = text[s - 1:s], text[e:e + 1]
        if code and w.islower() and ((before and before in '\'"`' and after == before) or (after == ':' and text[e + 1:e + 2] != ' ')
                                      or after == '=' or text[e:e + 2] == ' ='):
            continue
        found.append((text.count('\n', 0, s), w))
    return found


def problems(path, text):
    if path.suffix == '.md':
        prose = re.sub(r'```.*?```|`[^`\n]*`|\]\([^)]*\)', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)
        found = hits(prose, False)
    elif path.suffix == '.py':
        found = []
        for t in tokenize.generate_tokens(io.StringIO(text, newline='').readline):
            if t.type in (tokenize.COMMENT, tokenize.STRING) or tokenize.tok_name[t.type] in ('FSTRING_MIDDLE', 'TSTRING_MIDDLE'):
                if t.type == tokenize.STRING and re.fullmatch(r'[rbuRBU]*([\'"])[a-z_]+\1', t.string):
                    continue
                found += [(t.start[0] - 1 + n, w) for n, w in hits(t.string, True)]
    else:
        found = hits(text, True)
    lines = text.split('\n')
    return [f'{path.relative_to(ROOT)}:{n + 1}: {w}' for n, w in found
            if not any(path.name == f and words in lines[n] for f, words in ALLOWED)]


class UsEnglish(unittest.TestCase):
    def test_user_facing_text_is_in_us_english(self):
        files = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        bad = []
        for name in files:
            path = ROOT / name
            if name in SKIP or name.startswith(('brand/', 'sandbox/fixtures/')) or path.suffix not in SUFFIXES or not path.is_file():
                continue
            bad += problems(path, path.read_text(encoding='utf-8', errors='replace'))
        self.assertEqual(bad, [], 'UK spellings in user-facing text (use US English, or add a reasoned entry to ALLOWED):\n' + '\n'.join(bad))

    def test_it_catches_uk_prose_and_leaves_identifiers(self):
        self.assertEqual([w for _, w in hits("label:'Organisations', t='Send cancelled'", True)], ['Organisations', 'cancelled'])
        self.assertEqual(hits("status='cancelled' and x.behaviour() and graph.neighbours(db) and {cancelled:'Canceled'}", True), [])
        self.assertEqual([w for _, w in hits('The colour of the centre', False)], ['colour', 'centre'])


if __name__ == '__main__':
    unittest.main()
