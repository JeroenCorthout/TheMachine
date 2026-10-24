#!/usr/bin/env python3
"""The pinned OpenClaw package: the npm tarball checksum (its sha512 "integrity") for the version The Machine asks for,
kept in openclaw-pin.json. Before OpenClaw is installed or updated, the registry's record for that version must name
the pinned checksum; npm then checks the tarball it downloads against that record. A mismatch installs nothing, so a
Machine keeps the OpenClaw it runs. The hosting agent and the control plane's image build check the same file.

    python3 openclaw_pin.py check            the pin against the plugin's peerDependencies and the registry
    python3 openclaw_pin.py set <version>    pin <version> with the checksum the registry gives (review the diff)"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIN_FILE = ROOT / 'openclaw-pin.json'
REGISTRY = 'https://registry.npmjs.org'
VERSION = re.compile(r'\d{4}\.\d+\.\d+')
INTEGRITY = re.compile(r'sha512-[A-Za-z0-9+/]{86}==')


class PinMismatch(RuntimeError):
    """The registry names another checksum than the pinned one, or the version is not pinned: install nothing."""


def pin(path=PIN_FILE):
    return json.loads(Path(path).read_text())


def plugin_version(root=ROOT):
    return json.loads((Path(root) / 'openclaw-plugin/package.json').read_text())['peerDependencies']['openclaw']


def _fetch(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'the-machine-openclaw-pin'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def registry_integrity(version, package='openclaw', fetch=_fetch):
    return ((fetch(f'{REGISTRY}/{package}/{version}') or {}).get('dist') or {}).get('integrity') or ''


def check(version, data=None, fetch=_fetch):
    """The pinned checksum when the registry's record for `version` names it; PinMismatch otherwise. Network errors
    are raised as they are (the caller tries again later; nothing is installed meanwhile)."""
    data = data or pin()
    if version != data.get('version') or not INTEGRITY.fullmatch(data.get('integrity') or ''):
        raise PinMismatch(f'OpenClaw {version} is not the pinned version ({data.get("version")})')
    found = registry_integrity(version, data.get('package') or 'openclaw', fetch)
    if found != data['integrity']:
        raise PinMismatch(f'the registry checksum for OpenClaw {version} does not match the pinned one')
    return data['integrity']


def main(argv):
    if len(argv) == 3 and argv[1] == 'set' and VERSION.fullmatch(argv[2]):
        integrity = registry_integrity(argv[2])
        if not INTEGRITY.fullmatch(integrity):
            raise SystemExit(f'no sha512 checksum for openclaw {argv[2]}')
        PIN_FILE.write_text(json.dumps({'package': 'openclaw', 'version': argv[2], 'integrity': integrity}, indent=2) + '\n')
        print(f'pinned openclaw {argv[2]} {integrity}')
    elif len(argv) == 2 and argv[1] == 'check':
        data = pin()
        if data['version'] != plugin_version():
            raise SystemExit(f'openclaw-pin.json pins {data["version"]}, the plugin asks for {plugin_version()}')
        print('ok', data['version'], check(data['version'], data))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
