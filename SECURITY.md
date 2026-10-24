# Security and privacy

The short version, for people deciding whether to install: [themachine.computer/security](https://themachine.computer/security). This file is the technical detail behind it.

## Supported release

Security fixes target the latest `main` revision and ship as signed releases: tags `release-YYYYMMDD-HHMM` signed with the key in `deploy/release-signers`. Update with `python3 scripts/release.py update`, which installs only a release whose signature checks out; hosted Machines check the same signature before every update. CI checks OpenClaw 2026.10.5 and a current release; future releases and older snapshots are not automatically certified by those tests.
This is an early release for one owner on a trusted machine, not a public multi-user service.

## Reporting a vulnerability

Use this repository's GitHub **Security → Report a vulnerability** form for a private report.
If that form is unavailable, contact the maintainer through the GitHub profile first and
arrange a private channel. Do not put exploit details or private data in a public issue.
Include affected revisions, a synthetic reproduction and the expected security boundary.
Never include actual tokens, messages or a production database.

## Code scanning

[Aikido](https://www.aikido.dev) scans this repository and the hosted service's code for security issues,
vulnerable dependencies and leaked secrets, on every pull request and every day. It also scans the hosted
service's Azure setup, through a scoped read-only connection, and its control plane servers.
An AI agent goes through the findings every morning: critical and
high issues are fixed the same day, medium ones are fixed or dismissed with a specific reason, and low ones
are handled in batches. Fixes to this repository arrive as pull requests and are reviewed before they merge.

The release key holder also needs the official `@aikidosec/ci-api-client`, with its Aikido API key in the client's
protected local configuration. `scripts/release.py cut` requests a scan of the exact `origin/main` commit before
signing it. High and critical dependency, SAST, IaC and secret findings block release; an unavailable scanner,
invalid receipt or timeout blocks it as well. The token is never included in release commands or source control.

## Deployment boundaries

- Keep the service bound to `127.0.0.1`. The dashboard is intended for Tailscale Serve or
  the provided identity-checking reverse proxy. Do not expose the local API directly.
- Keep config, SQLite data, attachments and credentials outside the Git checkout.
  The installer uses a private data directory and mode 600 secret files.
- Reviews and preparations send relevant private evidence to the configured model provider.
  Choose that provider and its data settings accordingly.
- **Do it grants broad account and machine access for the accepted task.** The plugin's
  guards are not a sandbox. Source messages are untrusted and may contain prompt injection.
  Read the README's security model before activating reviews or delivery.
- Messages use exact owner-approved Send actions; complete Telegram drafts can be sent directly from their card. Explicit owner requests use `send_now` where supported. WhatsApp retains a card confirmation. Unknown sends are reconciled, never blindly repeated. Collectors read by default; configured CRM joins are an opt-in exception.
- Fresh installs collect configured sources with recurring reviews and delivery disabled. One read-only first-look preview may run after setup; it sends no cards or external actions.
  `./install.sh --activate` explicitly enables both after setup.
- The dashboard's sign-in dialog fills sites in the assistant's browser on the owner's behalf and can save
  those passwords in the data folder, encrypted with the message key below (on a hosted Machine that key is
  never on the disk, so disk backups can't open them). They never go to the model, the logs or a support bundle;
  an export holds them only with a passphrase. On a self-hosted install the key sits next to them for the service
  user: protect that user's account and disk (full-disk encryption) accordingly. Without message protection they
  keep a key of their own next to them, as before.
- The dashboard keeps its last cards, the open chat and the Graph's lists on each device so it opens at once:
  encrypted (AES-GCM, a key the browser can't export), for one owner, at most 7 days, never a token, and wiped on
  Sign out or when the session ends ([On the device](docs/architecture.md#on-the-device)). On a shared computer, sign out.
- The browser's own cookies and sign-ins (its profile) are the browser's: protected by the account and the disk,
  not by The Machine's keys. Anything running as the service user, the agent included, can use them.
- Credentials a tool prints (provider tokens, JWTs, private keys, `password=` and `Authorization:` values,
  cookie headers) are replaced with a placeholder before OpenClaw writes its session transcripts, so they don't
  stay on disk or in backups. The turn that printed one is not changed, and Codex's own session records are
  not covered.
- Message contents from every source are encrypted in the database by default, on every install, with a key
  next to it for the service user ([configuration](docs/configuration.md#message-protection)). It protects
  copies of the database and tools without the key, not against root on the machine. Back the key up with the
  database: without it the messages are lost. `"message_protection": false` turns it off. Email attachments a
  Do-it run saves are encrypted on disk with the same key; the run reads a private copy that is removed when it
  ends. Files a run makes itself are not. Stores other programs own are not covered: OpenClaw's session
  transcripts and WhatsApp's `wacli` store.
- Backups contain private evidence and may include secrets. Protect them like the live data.
- Keep the operating system patched. On a dedicated Ubuntu server, let `unattended-upgrades` install the
  `-security` pockets daily, and reboot when `/run/reboot-required` appears, at a quiet hour, then check that
  the OpenClaw Gateway and `personal-assistant` came back. Update Node and OpenClaw yourself, after testing,
  rather than unattended. The hosted service does exactly this on every server.

## Publishing a fork

Inspect every tracked file and all reachable history before making a fork public. Scan
for secrets, check commit author/committer emails, and inspect screenshots and sample data.
Use GitHub's noreply address if you do not want to publish your email address.
`.gitignore` and CI reduce accidental leaks; they cannot detect every kind of personal information.

If a real secret was committed, revoke it first. Removing the file in a later commit does
not remove it from history or copies that were already fetched. Then clean the affected
history and follow GitHub's guidance on removing sensitive repository data.
