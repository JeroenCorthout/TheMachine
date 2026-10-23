# Contributing

The Machine is an early release for a single owner running OpenClaw on Linux.
Small fixes, clearer setup instructions and additional general-purpose sources are welcome.

## Development

1. Clone the repository. Use Python 3.12+ and a supported Node version from the README.
2. Install the pinned SDK: `npm install -g openclaw@2026.10.5`.
3. Install Chromium, or set `CHROMIUM_PATH` to an existing Chromium executable.
4. Run `python3 scripts/test.py`. It runs every Python and Node suite, each test file in its own process
   with a disposable HOME, several at once (`THE_MACHINE_TEST_JOBS=1` runs them one by one), and returns a
   failing exit code if any suite fails. Name files to run only those: `python3 scripts/test.py test_moving.py`.
   No real accounts or Gateway are needed.

If OpenClaw is installed outside the Node prefix, set `OPENCLAW_PACKAGE_DIR` to its package
directory. `PLAYWRIGHT_CORE` may point to another `playwright-core/index.mjs`.
CI tests the minimum supported Python/Node combination and a newer combination, including
the actual pinned SDK imports and the dashboard in Chromium.

The Python runtime uses the standard library. Keep additional runtime dependencies small
and explain why they are needed. The dashboard is plain HTML, CSS and JavaScript.

## Pull requests

- Explain the user-visible problem, the change and how it was checked.
- Add a regression test for a concrete behavior or failure boundary. Cosmetic changes
  do not need tests that merely repeat their implementation.
- Source adapters must verify account identity and save each page with its checkpoint
  atomically. Use fixtures to check pagination, replay and failures. See README “Adding a source”.
- Preserve the separation between collected evidence, owner decisions and approved sends.
  An unknown send outcome must never cause a blind resend.
- Use reserved example addresses and synthetic content in fixtures and screenshots.
  Never include real emails, chats, credentials, task exports, browser sessions or databases.
- Keep personal preferences in the OpenClaw workspace's `USER.md`, outside the checkout.

Merged changes reach installs only through a signed release (`python3 scripts/release.py cut`, by the release key holder; see [Operations](docs/operations.md#releases)).

Check `git diff --check` and the test suite before submitting. CI also scans the complete
reachable Git history with Gitleaks. A passing secret scan does not prove that text is
free of private information; review every added fixture and attachment yourself.

## Issues and security

For ordinary bugs, include the version, a redacted error category and steps to reproduce
with synthetic data. Do not attach a raw database, environment dump or full service logs.
Follow [SECURITY.md](SECURITY.md) for vulnerabilities.

Contributions are accepted under the project licence, AGPL-3.0-only (see [LICENSE](LICENSE)).
