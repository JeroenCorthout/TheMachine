#!/usr/bin/env python3
"""Run every Python and Node suite with a disposable HOME and no account configuration.

Each test file runs in its own process with its own HOME, several at once (as many as the computer has cores, at
most 8; THE_MACHINE_TEST_JOBS=1 runs them one by one). A file's output is printed when it's done, in full when it
failed. Name files to run only those: scripts/test.py test_moving.py test_fleet_dashboard.mjs"""
import concurrent.futures
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


def openclaw_package(node):
    """OpenClaw beside this node (a global npm install), else wherever the `openclaw` on PATH comes from
    (a per-user install, as on hosted machines)."""
    beside = Path(node).parent.parent / 'lib' / 'node_modules' / 'openclaw'
    if beside.is_dir():
        return None
    found = shutil.which('openclaw')
    for folder in Path(os.path.realpath(found)).parents if found else ():
        if folder.name == 'openclaw' and (folder / 'package.json').exists():
            return str(folder)
    return None


# Files that test Linux-only parts, skipped elsewhere with the reason (macOS runs natively under launchd; on
# Windows The Machine runs inside WSL2, which is Linux, so there only the portable core is tested natively).
BROWSER_VIEWER = 'the browser sign-in runs Chrome in the browser viewer (Xvfb and noVNC), which is Linux-only for now'
SKIP = {
    'test_browser_login.py': {'darwin': BROWSER_VIEWER, 'win32': BROWSER_VIEWER},
}


# On Windows The Machine runs inside WSL2, which is Linux, so the Linux jobs test it. Natively, Windows runs the
# parts that do not depend on the operating system: the portable core, connectors, parsing and the Node helpers.
WINDOWS_NATIVE = {
    'test_portable.py', 'test_install.py', 'test_gateway.py', 'test_profile.py', 'test_connectors.py',
    'test_new_connectors.py', 'test_connector_shortcut.py', 'test_connector_trello.py', 'test_github_stripe.py',
    'test_salesflare.py', 'test_google_broker.py', 'test_mail_grants.py', 'test_discovery.py', 'test_enrichment.py', 'test_email_bodies.py',
    'test_exclusions.py', 'test_gmail_exclusions.py', 'test_ingestion_recovery.py', 'test_android_coalescing.py',
    'test_friends.py', 'test_fleet_free.py', 'test_fleet_brief.py', 'test_fleet_receipts.py', 'test_review_worker.py',
    'test_browser_cdp.py', 'test_browser_login_recovery.py', 'test_android_feed.mjs', 'test_x_paging.mjs',
    'test_taglines.mjs', 'test_telegram_receipt.mjs',
}
IN_WSL2 = 'on Windows The Machine runs inside WSL2 (Linux), which the Linux jobs test'


def skipped(file):
    if sys.platform == 'win32' and file.name not in WINDOWS_NATIVE:
        return IN_WSL2
    for platforms, reason in SKIP.get(file.name, {}).items():
        if sys.platform.startswith(platforms):
            return reason
    return None


def browsers():
    """Chrome or Chromium, by name on PATH, then where macOS and Windows install them."""
    for name in ('chromium', 'chromium-browser', 'google-chrome-stable', 'google-chrome', 'chrome'):
        if found := shutil.which(name):
            yield found
    if sys.platform == 'darwin':
        yield from ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium')
    if os.name == 'nt':
        for base in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')):
            if base:
                yield str(Path(base) / 'Google/Chrome/Application/chrome.exe')


def main():
    node = shutil.which('node')
    if not node:
        print('Node.js is required; see README prerequisites.', file=sys.stderr)
        return 1
    node = subprocess.check_output([node,'-p','process.execPath'],text=True).strip()
    if not (ROOT / 'node_modules' / '@openclaw' / 'gateway-client').is_dir():  # the pinned packages (package-lock.json), as the installer does
        npm = shutil.which('npm')
        if not npm or subprocess.run([npm, 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=ROOT).returncode:
            print('npm ci failed: the pinned Node packages are needed for the tests.', file=sys.stderr)
            return 1
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    # A HOME under /tmp would exercise the service's forbidden PrivateTmp attachment path.
    with tempfile.TemporaryDirectory(prefix='themachine-tests-',dir=cache) as home:
        env = {k:v for k,v in os.environ.items() if not k.startswith(('ASSISTANT_','OPENCLAW_','GOG_'))
               and k not in ('DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME')}
        env.update(HOME=home,PATH=str(Path(node).parent)+os.pathsep+os.environ.get('PATH',''))
        if os.name == 'nt':
            env['PYTHONUTF8'] = '1'  # the console's code page can't print everything the suites print
        package = os.environ.get('OPENCLAW_PACKAGE_DIR') or openclaw_package(node)
        if package:
            env['OPENCLAW_PACKAGE_DIR'] = package
        browser = os.environ.get('CHROMIUM_PATH') or next((found for found in browsers() if Path(found).exists()), None)
        if browser:
            env['CHROMIUM_PATH'] = browser
        chosen = sys.argv[1:]
        files = [ROOT / name for name in chosen] if chosen else sorted(ROOT.glob('test_*.py')) + sorted(ROOT.glob('test_*.mjs'))
        jobs = max(1, int(os.environ.get('THE_MACHINE_TEST_JOBS') or min(os.cpu_count() or 2, 8)))
        limit = int(os.environ.get('THE_MACHINE_TEST_TIMEOUT') or 600)
        skips = {file.name: reason for file in files if (reason := skipped(file))}
        files = [file for file in files if file.name not in skips]
        for name, reason in sorted(skips.items()):
            if reason != IN_WSL2:
                print(f'skip {name}: {reason}', flush=True)
        if wsl2 := sum(reason == IN_WSL2 for reason in skips.values()):
            print(f'skip {wsl2} files: {IN_WSL2}', flush=True)

        def run(file):
            command = [sys.executable, '-m', 'unittest', file.stem] if file.suffix == '.py' else [node, str(file)]
            started = time.monotonic()
            own = Path(home) / file.name
            own.mkdir()
            try:
                # Windows finds the home folder through USERPROFILE, not HOME.
                result = subprocess.run(command, cwd=ROOT, env={**env, 'HOME': str(own), 'USERPROFILE': str(own)}, timeout=limit,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
                ok, output = result.returncode == 0, result.stdout
            except subprocess.TimeoutExpired as timeout:
                out = timeout.output or ''
                ok, output = False, (out.decode(errors='replace') if isinstance(out, bytes) else out) + f'\nTimed out after {limit} s'
            return file.name, ok, output, time.monotonic() - started

        failed, took = [], []
        print(f'Running {len(files)} test files, {jobs} at a time' + (f' ({len(skips)} skipped on {sys.platform})' if skips else ''), flush=True)
        with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
            # the biggest files first, so a slow one doesn't start last and keep everyone waiting
            started = [pool.submit(run, file) for file in sorted(files, key=lambda f: -f.stat().st_size)]
            for done in concurrent.futures.as_completed(started):
                name, ok, output, seconds = done.result()
                took.append((seconds, name))
                print(f'{"ok  " if ok else "FAIL"} {name} ({seconds:.0f} s)', flush=True)
                if not ok:
                    failed.append(name)
                    print(output.rstrip() + '\n', flush=True)
        print('Slowest: ' + ', '.join(f'{name} {seconds:.0f} s' for seconds, name in sorted(took, reverse=True)[:5]))
        print('\n'+('Failed: '+', '.join(sorted(failed)) if failed else 'All Python and Node suites passed.'))
        return 1 if failed else 0

if __name__=='__main__':
    sys.exit(main())
