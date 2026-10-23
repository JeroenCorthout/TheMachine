"""The message key in the desktop's keyring (message_keyring.py): moved there only once the keyring checks out and
the key it gives back opens what is sealed; waited for (never made anew) while the keyring is locked, not running or
gives a wrong key; kept in memory once read; moved back to a file on request; exports still open everything.

Most tests use a keyring in memory (FakeKeyring). RealSecretService runs the real secret-tool and busctl against a
throwaway GNOME keyring on a private session bus (dbus-run-session), never the desktop's own keyring; it is skipped
where those tools are missing."""
import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

import test_message_seal
from test_message_seal import CANARY, SOURCES, assistant, files
import browser_login
import mail_grants
import message_keyring
import message_seal
import moving

ROOT = Path(__file__).resolve().parent
Unavailable = message_keyring.Unavailable


class FakeKeyring:
    """A Secret Service in memory: it can be down, locked (and unlock after a few looks), or give back another key."""

    def __init__(self):
        self.items, self.locked, self.down, self.no_tools = {}, False, False, False
        self.unlock_after, self.looks, self.reads, self.lie = None, 0, 0, None

    def _up(self):
        if self.down:
            raise Unavailable('no-service')

    def ready(self):
        if self.no_tools:
            raise Unavailable('no-tools')

    def state(self, attributes):
        self._up()
        self.looks += 1
        if self.unlock_after is not None and self.looks >= self.unlock_after:
            self.locked = False
        key = tuple(sorted(attributes.items()))
        return 'missing' if key not in self.items else 'locked' if self.locked else 'unlocked'

    def collection(self):
        self._up()
        return '/org/freedesktop/secrets/collection/test', self.locked

    def read(self, attributes):
        state = self.state(attributes)
        if state != 'unlocked':
            raise Unavailable(state)
        self.reads += 1
        return self.lie or self.items[tuple(sorted(attributes.items()))]

    def store(self, attributes, value):
        self._up()
        if self.locked:
            raise Unavailable('locked')
        self.items[tuple(sorted(attributes.items()))] = value

    def clear(self, attributes):
        self._up()
        self.items.pop(tuple(sorted(attributes.items())), None)


class Keyring(unittest.TestCase):
    fill = test_message_seal.Protected.fill
    exported = test_message_seal.Protected.exported

    def setUp(self):
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('MESSAGE_PROTECTION_TEST', None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        run = Path(self.temp.name) / 'run'  # the runtime folder in memory ($XDG_RUNTIME_DIR)
        run.mkdir(mode=0o700)
        os.environ['XDG_RUNTIME_DIR'] = str(run)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir(mode=0o700)
        self.path = self.data / 'events.sqlite'
        self.keyring = FakeKeyring()
        backend = unittest.mock.patch.object(message_keyring, 'BACKEND', self.keyring)
        backend.start()
        self.addCleanup(backend.stop)
        message_keyring.forget()
        self.addCleanup(message_keyring.forget)
        self.fill(assistant.Store(self.path, protect=True))
        self.logins = browser_login.LoginStore(self.data)
        self.logins.save('example.invalid', 'pat@example.invalid', 'throwaway-password')
        self.key_file = message_seal.key_path(self.path)
        self.key = message_seal.load_key(self.path)
        self.said = []

    def moved(self):
        self.assertTrue(message_keyring.to_keyring(self.path, say=self.said.append))
        return message_keyring.marker(self.path)

    def reboot(self):
        """/run is empty again and the new process has read nothing yet."""
        shutil.rmtree(Path(os.environ['XDG_RUNTIME_DIR']) / 'the-machine-key', ignore_errors=True)
        message_keyring.forget()

    def no_key_on_disk(self):
        encoded = base64.b64encode(self.key)
        for path in self.data.rglob('*'):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(encoded, path.read_bytes(), path)

    def readable(self):
        store = assistant.Store(self.path, protect=True)
        self.assertEqual(len(store.events(['acct'], query=CANARY.lower())), len(SOURCES))
        self.assertEqual(browser_login.LoginStore(self.data).get('example.invalid')['password'], 'throwaway-password')

    # ------------------------------------------------------------ moving there

    def test_the_move_checks_each_step_and_the_file_goes_last(self):
        mark = self.moved()
        self.assertTrue(self.key_file.is_symlink())
        self.assertEqual(Path(os.readlink(self.key_file)), message_keyring.runtime_path(mark['id']))
        self.assertTrue(message_seal.in_keyring(self.path) and message_seal.held_elsewhere(self.path))
        self.assertEqual(list(self.keyring.items.values()), [base64.b64encode(self.key)])
        self.assertEqual(oct(message_keyring.runtime_path(mark['id']).stat().st_mode & 0o777), '0o600')
        self.assertFalse(self.key_file.with_name(message_keyring.MOVING).exists())
        self.no_key_on_disk()  # nor in the marker, which holds only a keyed hash of it
        self.assertIn('in your keyring now', self.said[-1])
        self.assertEqual(message_seal.problems(self.path), [])
        self.readable()
        self.assertFalse(message_keyring.to_keyring(self.path, say=self.said.append))  # done already
        self.assertEqual(len(self.keyring.items), 1)

    def test_no_keyring_here_leaves_the_file_as_it_was(self):
        before = self.key_file.read_bytes()
        for broken in ('down', 'no_tools', 'locked'):
            setattr(self.keyring, broken, True)
            with self.assertRaises(Unavailable):
                message_keyring.to_keyring(self.path, say=self.said.append)
            setattr(self.keyring, broken, False)
            self.assertFalse(self.key_file.is_symlink())
            self.assertEqual(self.key_file.read_bytes(), before)
            self.assertFalse(message_seal.in_keyring(self.path))
            self.assertEqual(self.keyring.items, {})  # the probe's test item went too
        os.environ['XDG_RUNTIME_DIR'] = str(Path(self.temp.name) / 'nowhere')
        with self.assertRaises(Unavailable) as raised:
            message_keyring.to_keyring(self.path, say=self.said.append)
        self.assertEqual(raised.exception.reason, 'no-runtime')
        self.readable()

    def test_a_keyring_that_gives_back_another_key_stops_the_move(self):
        self.keyring.lie = base64.b64encode(os.urandom(32))
        with self.assertRaises(Unavailable):
            message_keyring.to_keyring(self.path, say=self.said.append)
        self.assertFalse(self.key_file.is_symlink())
        self.assertFalse(message_seal.in_keyring(self.path))
        self.assertEqual(self.keyring.items, {})
        self.keyring.lie = None
        self.readable()

    def test_a_key_file_that_does_not_open_the_data_never_moves(self):
        self.key_file.write_bytes(base64.b64encode(os.urandom(32)) + b'\n')
        with self.assertRaises(message_seal.SealError):
            message_keyring.to_keyring(self.path, say=self.said.append)
        self.assertFalse(self.key_file.is_symlink() or message_seal.in_keyring(self.path))
        self.assertEqual(self.keyring.items, {})

    def test_a_hosted_key_is_not_moved(self):
        memory = Path(self.temp.name) / 'host' / message_seal.KEY_NAME
        memory.parent.mkdir()
        self.key_file.rename(memory)
        self.key_file.symlink_to(memory)
        with self.assertRaises(Unavailable):
            message_keyring.to_keyring(self.path, say=self.said.append)
        self.assertEqual(Path(os.readlink(self.key_file)), memory)
        self.assertEqual(self.keyring.items, {})

    def test_an_interrupted_move_is_safe_and_finishes_when_run_again(self):
        real_link = os.link
        with unittest.mock.patch.object(message_keyring.os, 'link', side_effect=OSError('power cut')):
            with self.assertRaises(OSError):
                message_keyring.to_keyring(self.path, say=self.said.append)
        self.assertFalse(self.key_file.is_symlink())  # the file still holds the key, so nothing is lost
        message_keyring.forget()
        self.assertEqual(message_seal.load_key(self.path), self.key)
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-keyring-unfinished'])
        self.readable()
        self.moved()
        self.assertEqual(len(self.keyring.items), 1)  # the same item, not a second one
        # a cut after the switch leaves the old file as MOVING: the next run (or start) overwrites and removes it
        real_link(message_keyring.runtime_path(message_keyring.marker(self.path)['id']), self.key_file.with_name(message_keyring.MOVING))
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-leftover'])
        self.assertFalse(message_keyring.to_keyring(self.path, say=self.said.append))
        self.assertFalse(self.key_file.with_name(message_keyring.MOVING).exists())
        self.assertEqual(message_seal.problems(self.path), [])

    # ------------------------------------------------------------ starting with the key in the keyring

    def test_locked_at_boot_the_service_waits_says_why_and_starts_once_it_unlocks(self):
        self.moved()
        self.reboot()
        self.keyring.locked, self.keyring.unlock_after = True, self.keyring.looks + 4
        heard = []
        self.assertTrue(message_seal.wait_for_key(self.path, say=self.said.append, every=0.01, limit=5, protect=True,
                                                  waiting=heard.append))
        self.assertIn('Waiting for the message key from your keyring (the keyring is locked)', self.said[-1])
        self.assertEqual(heard[0], 'the keyring is locked')
        self.assertIsNone(heard[-1])  # over: the dashboard's address goes back to the app
        self.assertTrue(self.key_file.is_file())  # the runtime copy, for the other processes and the Gateway's plugin
        self.readable()

    def test_a_keyring_that_can_not_be_read_never_gets_a_new_key(self):
        self.moved()
        sealed_logins = (self.data / browser_login.SEALED).read_bytes()
        for broken in ('down', 'locked', 'lie', 'missing'):
            self.reboot()
            if broken == 'lie':
                self.keyring.lie = base64.b64encode(os.urandom(32))
            elif broken == 'missing':
                saved = dict(self.keyring.items)
                self.keyring.items.clear()
            else:
                setattr(self.keyring, broken, True)
            self.assertIsNone(message_seal.load_key(self.path))
            with self.assertRaises(message_seal.SealError):
                message_seal.load_key(self.path, create=True)
            with self.assertRaises(message_seal.SealError):
                assistant.Store(self.path, protect=True)
            with self.assertRaises(mail_grants.GrantError):
                mail_grants._sealer({'database': str(self.path)})
            with self.assertRaises(ValueError):
                browser_login.LoginStore(self.data).save('other.invalid', 'pat', 'another-throwaway')
            self.assertFalse(message_seal.wait_for_key(self.path, say=self.said.append, every=0.01, limit=0.03, protect=True))
            self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-keyring-waiting'])
            self.assertTrue(self.key_file.is_symlink() and not self.key_file.exists())  # no new key anywhere
            self.assertEqual((self.data / browser_login.SEALED).read_bytes(), sealed_logins)
            self.assertFalse((self.data / browser_login.STORE).exists() or (self.data / browser_login.KEY).exists())
            self.keyring.down = self.keyring.locked = False
            self.keyring.lie = None
            if broken == 'missing':
                self.keyring.items.update(saved)
        self.reboot()
        self.readable()

    def test_a_runtime_copy_with_another_key_is_never_used(self):
        mark = self.moved()
        message_keyring.forget()
        message_keyring.runtime_path(mark['id']).write_bytes(base64.b64encode(os.urandom(32)) + b'\n')
        with self.assertRaises(message_seal.SealError):
            message_seal.load_key(self.path)

    def test_once_read_the_key_is_kept_in_memory_so_a_flaky_keyring_does_not_matter(self):
        mark = self.moved()
        self.reboot()
        self.assertEqual(message_seal.load_key(self.path), self.key)
        reads = self.keyring.reads
        self.keyring.down = True  # the keyring restarts, or crashes
        message_keyring.runtime_path(mark['id']).unlink()  # and /run lost the copy too
        self.assertEqual(message_seal.load_key(self.path), self.key)
        self.assertTrue(message_keyring.runtime_path(mark['id']).is_file())  # put back for the plugin
        self.assertEqual(self.keyring.reads, reads)
        self.readable()

    def test_failed_reads_are_not_retried_on_every_call(self):
        self.moved()
        self.reboot()
        self.keyring.down = True
        message_seal.load_key(self.path)
        looks = self.keyring.looks
        for _ in range(5):
            self.assertIsNone(message_seal.load_key(self.path))
        self.assertEqual(self.keyring.looks, looks)
        self.assertEqual(message_keyring.last_error(self.path).reason, 'no-service')

    def test_the_dashboard_says_why_it_waits_and_frees_its_address_after(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        page = assistant.KeyWaitingPage({'port': port})
        page('the keyring is locked')
        try:
            for path, kind in (('/', 'text/html'), ('/health', 'application/json')):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                connection.request('GET', path)
                response = connection.getresponse()
                body = response.read().decode()
                self.assertEqual(response.status, 503)
                self.assertTrue(response.getheader('Content-Type').startswith(kind))
                self.assertIn('the keyring is locked', body)
                connection.close()
            self.assertFalse(json.loads(body)['ok'])
        finally:
            page(None)
        with socket.socket() as again:
            again.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            again.bind(('127.0.0.1', port))  # the app can take it

    # ------------------------------------------------------------ the way back, and moving to another computer

    def test_to_file_puts_the_key_back_checked_and_removes_the_keyring_copy(self):
        mark = self.moved()
        self.reboot()
        self.keyring.locked, self.keyring.unlock_after = True, self.keyring.looks + 3  # flaky at first: tried again
        self.assertTrue(message_keyring.to_file(self.path, say=self.said.append, every=0))
        self.assertFalse(self.key_file.is_symlink())
        self.assertEqual(oct(self.key_file.stat().st_mode & 0o777), '0o600')
        self.assertEqual(base64.b64decode(self.key_file.read_bytes().strip()), self.key)
        self.assertFalse(message_seal.in_keyring(self.path))
        self.assertFalse(message_keyring.runtime_path(mark['id']).exists())
        self.assertEqual(self.keyring.items, {})
        self.assertEqual(message_seal.problems(self.path), [])
        self.readable()
        self.moved()
        self.assertTrue(message_keyring.to_file(self.path, say=self.said.append, keep_in_keyring=True))
        self.assertEqual(len(self.keyring.items), 1)

    def test_to_file_never_writes_a_key_it_could_not_read(self):
        self.moved()
        self.reboot()
        self.keyring.down = True
        with self.assertRaises(Unavailable):
            message_keyring.to_file(self.path, say=self.said.append, attempts=2, every=0)
        self.assertTrue(self.key_file.is_symlink() and message_seal.in_keyring(self.path))
        self.keyring.down, self.keyring.lie = False, base64.b64encode(os.urandom(32))
        with self.assertRaises(Unavailable):
            message_keyring.to_file(self.path, say=self.said.append, attempts=2, every=0)
        self.assertTrue(self.key_file.is_symlink() and message_seal.in_keyring(self.path))

    def test_an_export_opens_everything_and_never_holds_the_key_or_the_marker(self):
        config = self.data / 'config.json'  # the data folder an export takes
        config.write_text(json.dumps({'database': str(self.path), 'accounts': []}))
        self.moved()
        self.reboot()  # the export reads the key from the keyring itself
        exported = self.exported(config)
        self.assertFalse([n for n in exported if message_seal.KEY_NAME in n or message_keyring.MARKER in n])
        self.assertIn(CANARY.encode(), exported['data/events.sqlite'])  # opened, as every export
        # on another computer it is sealed again with a key of its own, in a file
        there = Path(self.temp.name) / 'there' / 'events.sqlite'
        there.parent.mkdir()
        there.write_bytes(exported['data/events.sqlite'])
        store = assistant.Store(there, protect=True)
        self.assertTrue(store.protected and message_seal.key_path(there).is_file())
        self.assertFalse(message_seal.in_keyring(there))
        self.assertNotIn(CANARY.encode(), files(there))
        # and imported here, it is sealed with the key in the keyring
        for name in ('events.sqlite', 'events.sqlite-wal', 'events.sqlite-shm'):
            (self.data / name).unlink(missing_ok=True)
        self.path.write_bytes(exported['data/events.sqlite'])
        self.assertTrue(assistant.Store(self.path, protect=True).protected)
        self.assertTrue(self.key_file.is_symlink())
        self.assertNotIn(CANARY.encode(), files(self.path))
        self.readable()

    def test_config_can_ask_for_the_keyring_at_start(self):
        config = {'database': str(self.path), 'message_key_store': 'keyring'}
        with unittest.mock.patch.object(assistant.threading, 'Thread'):
            assistant.message_key_keeper(config)
        self.assertTrue(message_seal.in_keyring(self.path) and self.key_file.is_symlink())
        self.readable()
        with unittest.mock.patch.object(assistant.threading, 'Thread'):
            assistant.message_key_keeper(config)  # already there: nothing moves again
        self.assertEqual(len(self.keyring.items), 1)


SANDBOX = r'''
import base64, json, os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import message_keyring, message_seal
data = sys.argv[2]
db = os.path.join(data, 'events.sqlite')
os.makedirs(os.environ['XDG_RUNTIME_DIR'] + '/keyring', mode=0o700, exist_ok=True)
daemon = subprocess.Popen(['gnome-keyring-daemon', '--foreground', '--unlock', '--components=secrets',
                           '--control-directory=' + os.environ['XDG_RUNTIME_DIR'] + '/keyring'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
daemon.stdin.write(b'throwaway-password')
daemon.stdin.close()
out = {}
try:
    # Query the bus itself first: querying Secret.Service before our daemon owns the name
    # auto-starts a different daemon and races the throwaway unlocked collection.
    for _ in range(200):
        names = subprocess.run(['busctl', '--user', '--json=short', 'call', 'org.freedesktop.DBus',
                                '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ListNames'],
                               capture_output=True, text=True, check=True)
        if 'org.freedesktop.secrets' in json.loads(names.stdout).get('data', [[]])[0]:
            break
        if daemon.poll() is not None:
            raise RuntimeError('The isolated test keyring exited during startup')
        time.sleep(0.1)
    else:
        raise RuntimeError('The isolated test keyring did not acquire its service name')
    message_keyring.BACKEND.collection()
    key = message_seal.load_key(db, create=True)
    with open(os.path.join(data, 'browser-logins.sealed'), 'wb') as f:
        f.write(message_seal.Sealer(key).seal_bytes(b'{}'))
    message_keyring.probe()
    out['moved'] = message_keyring.to_keyring(db, say=lambda _: None)
    out['link'] = os.path.islink(message_seal.key_path(db))
    mark = message_keyring.marker(db)
    os.unlink(message_keyring.runtime_path(mark['id']))
    message_keyring.forget()
    out['read_back'] = message_seal.load_key(db) == key
    out['back_to_file'] = message_keyring.to_file(db, say=lambda _: None)
    out['file'] = os.path.isfile(message_seal.key_path(db)) and not os.path.islink(message_seal.key_path(db))
    out['same_key'] = message_seal.load_key(db) == key
    out['item_left'] = message_keyring.BACKEND.state(message_keyring.attributes(mark['id']))
    message_keyring.to_keyring(db, say=lambda _: None)
    mark = message_keyring.marker(db)
    collection, _ = message_keyring.BACKEND.collection()
    subprocess.run(['busctl', '--user', 'call', 'org.freedesktop.secrets', '/org/freedesktop/secrets',
                    'org.freedesktop.Secret.Service', 'Lock', 'ao', '1', collection], capture_output=True, check=True)
    os.unlink(message_keyring.runtime_path(mark['id']))
    message_keyring.forget()
    started = time.monotonic()
    out['locked_key'] = message_seal.load_key(db)
    out['locked_seconds'] = time.monotonic() - started
    out['locked_reason'] = message_keyring.last_error(db).reason
    try:
        message_seal.load_key(db, create=True)
        out['made_new'] = True
    except message_seal.SealError:
        out['made_new'] = False
    out['still_link'] = os.path.islink(message_seal.key_path(db)) and not os.path.exists(message_seal.key_path(db))
finally:
    daemon.terminate()
    daemon.wait(10)
print(json.dumps(out))
'''


@unittest.skipUnless(all(shutil.which(t) for t in ('gnome-keyring-daemon', 'dbus-run-session', 'secret-tool', 'busctl')),
                     'needs gnome-keyring-daemon, dbus-run-session, secret-tool and busctl')
class RealSecretService(unittest.TestCase):
    def test_a_throwaway_gnome_keyring_holds_the_key_and_a_locked_one_is_waited_for_without_a_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            home, run, data = Path(temp, 'home'), Path(temp, 'run'), Path(temp, 'data')
            for folder in (home, run, data):
                folder.mkdir(mode=0o700)
            # Only this environment: the private bus, its activated services and the daemon never see the desktop's
            env = {'HOME': str(home), 'XDG_RUNTIME_DIR': str(run), 'XDG_DATA_HOME': str(home / '.local/share'),
                   'XDG_CONFIG_HOME': str(home / '.config'), 'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
            done = subprocess.run(['dbus-run-session', '--', sys.executable, '-c', SANDBOX, str(ROOT), str(data)],
                                  env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {**out, 'moved': True, 'link': True, 'read_back': True, 'back_to_file': True, 'file': True,
                               'same_key': True, 'item_left': 'missing', 'locked_key': None, 'locked_reason': 'locked',
                               'made_new': False, 'still_link': True})
        self.assertLess(out['locked_seconds'], 5)  # no unlock prompt waited for


if __name__ == '__main__':
    unittest.main()
