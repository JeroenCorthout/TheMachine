"""The pinned wacli build: installed only when its download matches the pinned sha256, the sync unit asks for profile
pictures only when the installed wacli offers them, and the pin moves only to a release with every platform's checksum."""
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import wacli_release

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wacli_pin', ROOT / 'scripts/wacli_pin.py')
wacli_pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wacli_pin)

BINARY = b'#!/bin/sh\necho wacli 9.9.9-picture.1\n'


def tarball(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class PinnedInstall(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.blob = tarball({'wacli': BINARY, 'LICENSE': b'MIT License\n', 'README.md': b'readme\n'})
        self.data = {'repository': 'Example/wacli', 'tag': 'v9.9.9-picture.1',
                     'sha256': {'linux_amd64': hashlib.sha256(self.blob).hexdigest()}}
        self.urls = []

    def fetch(self, blob):
        def fetch(url):
            self.urls.append(url)
            return blob
        return fetch

    def test_the_pin_covers_every_platform_the_fork_builds(self):
        data = wacli_release.pin()
        self.assertEqual(data['repository'], 'Salesflare/wacli')
        self.assertRegex(data['tag'], r'^v\d+\.\d+\.\d+-picture\.\d+$')
        self.assertEqual(set(data['sha256']), {'linux_amd64', 'linux_arm64', 'darwin_amd64', 'darwin_arm64', 'windows_amd64'})
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', digest) for digest in data['sha256'].values()))

    def test_platforms_and_release_asset_names(self):
        self.assertEqual(wacli_release.platform_key('Linux', 'x86_64'), 'linux_amd64')
        self.assertEqual(wacli_release.platform_key('Linux', 'aarch64'), 'linux_arm64')
        self.assertEqual(wacli_release.platform_key('Darwin', 'arm64'), 'darwin_arm64')
        self.assertEqual(wacli_release.platform_key('Windows', 'AMD64'), 'windows_amd64')
        self.assertIsNone(wacli_release.platform_key('Linux', 'riscv64'))
        self.assertIsNone(wacli_release.platform_key('FreeBSD', 'x86_64'))
        self.assertEqual(wacli_release.download_url(self.data, 'linux_amd64'),
                         'https://github.com/Example/wacli/releases/download/v9.9.9-picture.1/wacli_9.9.9-picture.1_linux_amd64.tar.gz')
        self.assertTrue(wacli_release.archive_name(self.data, 'windows_amd64').endswith('_windows_amd64.zip'))

    def test_installs_the_verified_binary(self):
        path = wacli_release.install(self.folder / 'bin', self.data, 'linux_amd64', fetch=self.fetch(self.blob))
        self.assertEqual(path, self.folder / 'bin/wacli')
        self.assertEqual(path.read_bytes(), BINARY)
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        self.assertEqual((self.folder / 'bin/wacli-LICENSE').read_text(), 'MIT License\n')
        self.assertEqual(self.urls, [wacli_release.download_url(self.data, 'linux_amd64')])

    def test_a_download_that_does_not_match_is_never_unpacked(self):
        other = tarball({'wacli': b'something else', 'LICENSE': b'x'})
        with self.assertRaisesRegex(RuntimeError, 'did not match'):
            wacli_release.install(self.folder / 'bin', self.data, 'linux_amd64', fetch=self.fetch(other))
        self.assertFalse((self.folder / 'bin').exists())
        with self.assertRaisesRegex(RuntimeError, 'no wacli build'):
            wacli_release.install(self.folder / 'bin', self.data, 'linux_arm64', fetch=self.fetch(self.blob))
        self.assertEqual(len(self.urls), 1)  # no build for that platform: nothing downloaded

    def test_windows_zip(self):
        blob = zipped({'wacli.exe': BINARY, 'LICENSE': 'MIT License\n'})
        data = {**self.data, 'sha256': {'windows_amd64': hashlib.sha256(blob).hexdigest()}}
        path = wacli_release.install(self.folder, data, 'windows_amd64', fetch=self.fetch(blob))
        self.assertEqual(path.name, 'wacli.exe')
        self.assertEqual(path.read_bytes(), BINARY)

    def test_picture_support_comes_from_the_sync_help(self):
        self.assertTrue(wacli_release.supports_pictures('  --profile-picture-budget N   answer read-only ...'))
        self.assertFalse(wacli_release.supports_pictures('  --send-spacing DURATION'))
        self.assertFalse(wacli_release.supports_pictures(None))


class PinBump(unittest.TestCase):
    data = {'repository': 'Example/wacli', 'tag': 'v1.0.0-picture.1', 'sha256': {'linux_amd64': '0' * 64, 'windows_amd64': '1' * 64}}

    def test_takes_each_platforms_checksum_from_the_release(self):
        sums = '\n'.join([f"{'a' * 64}  wacli_1.1.0-picture.1_linux_amd64.tar.gz", f"{'b' * 64}  wacli_1.1.0-picture.1_windows_amd64.zip",
                          f"{'c' * 64}  wacli_1.1.0-picture.1_linux_arm64.tar.gz"])
        new = wacli_pin.updated_pin(self.data, 'v1.1.0-picture.1', sums)
        self.assertEqual(new, {'repository': 'Example/wacli', 'tag': 'v1.1.0-picture.1', 'sha256': {'linux_amd64': 'a' * 64, 'windows_amd64': 'b' * 64}})

    def test_a_release_missing_a_platform_is_refused(self):
        with self.assertRaises(SystemExit):
            wacli_pin.updated_pin(self.data, 'v1.1.0-picture.1', f"{'a' * 64}  wacli_1.1.0-picture.1_linux_amd64.tar.gz")

    def test_only_fork_release_tags(self):
        answer = lambda tag: (lambda url, accept=None: json.dumps({'tag_name': tag}).encode())
        self.assertEqual(wacli_pin.newest_tag('Example/wacli', answer('v1.2.0-picture.3')), 'v1.2.0-picture.3')
        for tag in ('v1.2.0', 'v1.2.0-picture.3; rm -rf /', '../x'):
            with self.assertRaises(SystemExit):
                wacli_pin.newest_tag('Example/wacli', answer(tag))


if __name__ == '__main__':
    unittest.main()
