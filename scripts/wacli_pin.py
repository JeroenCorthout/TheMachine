#!/usr/bin/env python3
"""Moves wacli-pin.json to the newest release of the wacli fork.

    python3 scripts/wacli_pin.py check              print the pinned and the newest tag
    python3 scripts/wacli_pin.py bump [--verify]    pin the newest release; --verify also downloads every archive,
                                                    checks it against checksums.txt and its build attestation (gh)

The checksums come from the release's checksums.txt; with --verify each archive must match it and carry a GitHub
build provenance attestation from the fork's repository. The daily wacli-pin workflow runs `bump --verify` and opens
a pull request when the pin moved."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import wacli_release  # noqa: E402

TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+-picture\.[0-9]+')


def get(url, accept='application/octet-stream'):
    headers = {'Accept': accept, 'User-Agent': 'the-machine-wacli-pin'}
    if os.environ.get('GH_TOKEN') and url.startswith('https://api.github.com/'):
        headers['Authorization'] = f"Bearer {os.environ['GH_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as response:
        return response.read()


def newest_tag(repository, fetch=get):
    tag = json.loads(fetch(f'https://api.github.com/repos/{repository}/releases/latest', 'application/vnd.github+json'))['tag_name']
    if not TAG.fullmatch(tag):
        raise SystemExit(f'unexpected release tag {tag!r}')
    return tag


def updated_pin(data, tag, checksums):
    """The pin for tag, its sha256 per platform read from checksums.txt; every pinned platform must be there."""
    new = {**data, 'tag': tag}
    sums = {}
    for line in checksums.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})\s+\*?(\S+)', line.strip())
        if match:
            sums[match[2]] = match[1]
    new['sha256'] = {}
    for key in sorted(data['sha256']):
        name = wacli_release.archive_name(new, key)
        if name not in sums:
            raise SystemExit(f'{tag} has no {name} in checksums.txt')
        new['sha256'][key] = sums[name]
    return new


def verify(data):
    """Downloads every pinned archive and checks its sha256 and build attestation."""
    with tempfile.TemporaryDirectory() as folder:
        for key, digest in data['sha256'].items():
            path = Path(folder) / wacli_release.archive_name(data, key)
            path.write_bytes(get(wacli_release.download_url(data, key)))
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise SystemExit(f'{path.name} does not match checksums.txt')
            subprocess.run(['gh', 'attestation', 'verify', str(path), '--repo', data['repository']],
                           check=True, stdout=subprocess.DEVNULL)
            print(f'verified {path.name}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['check', 'bump'])
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    data = wacli_release.pin()
    tag = newest_tag(data['repository'])
    print(f"pinned {data['tag']}, newest {tag}")
    if args.command == 'check' or tag == data['tag']:
        return 0
    checksums = get(f"https://github.com/{data['repository']}/releases/download/{tag}/checksums.txt").decode()
    new = updated_pin(data, tag, checksums)
    if args.verify:
        verify(new)
    wacli_release.PIN_FILE.write_text(json.dumps(new, indent=2) + '\n')
    print(f'pinned {tag}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
