"""The pinned wacli build: one release of github.com/Salesflare/wacli (upstream openclaw/wacli plus read-only profile
picture lookups through `wacli sync --follow --profile-picture-budget N`), checked against the sha256 kept in
wacli-pin.json before anything is unpacked. `python3 scripts/wacli_pin.py bump` moves the pin to the fork's newest
release (the daily wacli-pin workflow opens that as a pull request)."""
import hashlib
import io
import json
import os
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

PIN_FILE = Path(__file__).with_name('wacli-pin.json')
PICTURE_FLAG = '--profile-picture-budget'
PICTURES_PER_DAY = 50  # the mirror's own cap; enrichment asks at most this many a day too


def pin(path=PIN_FILE):
    return json.loads(Path(path).read_text())


def version(data):
    return data['tag'].removeprefix('v')


def platform_key(system=None, machine=None):
    """linux_amd64 and the like, or None when the fork has no build for this computer."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
    key = f'{system}_{arch}'
    return key if arch and system in ('linux', 'darwin', 'windows') else None


def archive_name(data, key):
    return f"wacli_{version(data)}_{key}.{'zip' if key.startswith('windows') else 'tar.gz'}"


def download_url(data, key):
    return f"https://github.com/{data['repository']}/releases/download/{data['tag']}/{archive_name(data, key)}"


def _fetch(url):
    with urllib.request.urlopen(url, timeout=300) as response:
        return response.read()


def unpack(blob, key):
    """(binary, licence text) from a release archive."""
    name = 'wacli.exe' if key.startswith('windows') else 'wacli'
    if key.startswith('windows'):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            files = {Path(n).as_posix().lstrip('./'): n for n in archive.namelist()}
            return archive.read(files[name]), archive.read(files['LICENSE']).decode()
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = {m.name.lstrip('./'): m for m in archive.getmembers() if m.isfile()}
        return archive.extractfile(members[name]).read(), archive.extractfile(members['LICENSE']).read().decode()


def install(directory, data=None, key=None, fetch=_fetch):
    """Downloads the pinned build for this computer into directory, verified against its pinned sha256, and returns
    the binary's path. Raises RuntimeError when there is no build or the download doesn't match."""
    data = data or pin()
    key = key or platform_key()
    if not key or key not in data['sha256']:
        raise RuntimeError('there is no wacli build for this computer')
    blob = fetch(download_url(data, key))
    if hashlib.sha256(blob).hexdigest() != data['sha256'][key]:
        raise RuntimeError('the wacli download did not match its pinned checksum')
    binary, licence = unpack(blob, key)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('wacli.exe' if key.startswith('windows') else 'wacli')
    (directory / 'wacli-LICENSE').write_text(licence)
    temporary = target.with_name(target.name + '.new')
    temporary.write_bytes(binary)
    temporary.chmod(0o755)
    os.replace(temporary, target)
    return target


def supports_pictures(help_text):
    """Whether `wacli sync --help` offers the profile picture budget."""
    return PICTURE_FLAG in (help_text or '')
