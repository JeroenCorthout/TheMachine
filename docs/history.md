# Historical documentation snapshot · 2026-10-20

This is the README before the documentation cleanup. It preserves dated repairs, old capabilities, test counts and rollout receipts **as historical evidence, not current instructions**. Later source, current documentation and fresh runtime receipts take precedence. Do not copy old commands or restore old databases from this archive.

[Current documentation](../README.md).

# The Machine

A private, self-hosted assistant inbox for [OpenClaw](https://docs.openclaw.ai). It reads your email, calendar, chat and phone notifications, has your own OpenClaw agent look for what actually needs you, and sends a few short suggestions to Telegram. Tap **Do it** and the agent carries the task out.

Built on [OpenClaw](https://docs.openclaw.ai) (MIT); see [third-party notices](../THIRD_PARTY_NOTICES.md). Website: [themachine.computer](https://themachine.computer). **Early release:** intended for a single owner who already uses OpenClaw. Managed installation is Linux-only.

**October 17 reliability fixes:** profile drafts use a fixed, mixed-source sample and preserve concurrent USER.md edits. Setup verifies connector reads before reporting success; unfinished catch-up resumes after a restart. Graph updates preserve older task context, exact account-scoped threads and valid feedback, and refresh without losing the current view. Permissions and normal polling budgets are unchanged.

## Quick start

```bash
git clone https://github.com/JeroenCorthout/TheMachine.git ~/.openclaw/workspace/tools/personal-assistant
cd ~/.openclaw/workspace/tools/personal-assistant
./install.sh
```

Read [Requirements](../README.md#requirements) first, or follow the [Getting started](../docs/getting-started.md) checklist. No OpenClaw yet? The installer offers to install it and walks you through its own setup (your model, the Gateway service, a Telegram bot). Then it checks prerequisites and asks a few questions, with answers found on this machine filled in (your Telegram ID from OpenClaw, your Tailscale login, installed sources). It installs the service and the OpenClaw plugin, then walks you through connecting each source: sign-ins and keys, IDs found for you, a test read and the first sync with real counts. It offers the dashboard on your tailnet and a starter `USER.md`, and has your agent draft how you write and what matters to you from your own recent messages (shown to you before anything is saved), then a **preview**: your agent reviews the last 7 days once and the cards appear in the terminal and on the dashboard, with nothing sent and nothing done in your accounts. After the preview it offers to turn on reviews and Telegram cards; read [Security model](../README.md#security-model) first. Every step can be skipped and picked up later with `assistant connect <source>`, `./install.sh --preview` and `./install.sh --activate`; `assistant doctor` checks everything and says how to fix what isn't right. Run the installer again to upgrade; `./install.sh --uninstall` removes it again (see [Install](../README.md#install)).

Contents: [What it does](../README.md#what-it-does) · [Architecture](../README.md#architecture) · [Requirements](../README.md#requirements) · [Install](../README.md#install) · [Configuration](../README.md#configuration) · [Sources](../README.md#sources) · [Reviews](../README.md#reviews) · [Telegram and the dashboard](../README.md#telegram-and-the-dashboard) · [Do it](../README.md#do-it) · [The Machine fixes itself](../README.md#the-machine-fixes-itself) · [Related signals](../README.md#related-signals-optional-local-embeddings) · [Security model](../README.md#security-model) · [Command line](../README.md#command-line) · [Operations](../README.md#operations) · [Adding a source](../README.md#adding-a-source) · [Known limitations](../README.md#known-limitations) · [Tests](../README.md#tests)

## What it does

- **Collects** signals from Gmail, Google Calendar, Slack, the Salesflare CRM, Android notifications (through the OpenClaw node app), WhatsApp (through a local `wacli` store) and the OpenClaw Gateway's own failures, plus GitHub, Stripe, LinkedIn, X and RSS through [connector plugins](../docs/adding-a-source.md). Collection is incremental and read-only by default; everything goes into one private SQLite database with full-text search.
- **Reviews** waiting signals in isolated turns of your existing OpenClaw main agent. The agent saves a suggestion only when something is worth your time: an unanswered request, a deadline, a problem it can fix, or an email of yours that slipped.
- **Delivers** suggestions to a phone-friendly dashboard on your tailnet (installable as an app, with push notifications) and, optionally, as Telegram cards through your existing OpenClaw bot.
- **Keeps track of promises** in your messages, both ways: "I'll send the deck Friday" from you, "we'll get back to you next week" from them. Each is checked when it's due. Yours that are still open arrive with the work prepared; theirs arrive with a short nudge drafted, and close by themselves if they write back first. See [Promises](../README.md#promises).
- **Remembers who and what** in a living graph of people and projects: how close you are to each person (it grows with every exchange and fades with time), their usual rhythm and who has gone quiet, what they are to you, what's still open between you, and which projects are heating up or cooling, including signals that probably belong to one and work nobody has named yet. Do it runs, reviews and the dashboard's Graph tab all use it.
- **Acts** when you tap **Do it**: the agent drafts the email, LinkedIn, Slack or WhatsApp message, or X reply (with a Send button), makes the fix, does the research, or hands the task to a colleague and waits for their reply, and reports back on the same card. Cards it's sure about arrive [already done up to the last click](../README.md#prepared-ahead): you only confirm the Send, Submit, Pay or Book. It also [fixes bugs in The Machine itself](../README.md#the-machine-fixes-itself): small proven fixes merge on their own with Undo for a day, the rest you merge with one tap. **Change** lets you reply with instructions; **Snooze**, **Dismiss** and **Mark done** work as you'd expect, and your choices steer later reviews.
- **Works with your teammates' Machines** (beta, through The Machine Network; a self-hosted Machine can join too): in a [Fleet](../docs/fleet.md) your Machine can ask theirs who knows someone at a company, what they know about the people in your next meeting, and who is already handling an email you both got; ask a teammate for a warm intro, hand them a thread, and split the follow-ups after a meeting you were both in. A daily or weekly team brief says in a few ranked lines what needs the team (customers waiting on someone and for how long, meetings and companies you share, companies gone quiet), without receipts, newsletters or vendor mail, and you can ask the Fleet anything: each teammate's Machine drafts an answer from its own mail, and only what they send comes back. What a teammate asks of you arrives as a card under Suggestions. Answers are short summaries under one rule per kind of question for each Fleet, with exceptions per teammate if you want them, never the messages themselves. With **Friends** you add someone outside your Fleets by their exact username; once they accept, your Machines can ask each other who knows someone, about a meeting you are both in, for a warm intro, and when you are busy (busy times only), each under rules you set per friend.

The service itself contains no model. All interpretation happens in your OpenClaw agent, with your model provider: see [Bring your own model](../docs/models.md) for choosing one, including local models.

## Architecture

```text
Gmail · Calendar · Slack · CRM · Android notifications · WhatsApp · OpenClaw Gateway · plugins (GitHub, Stripe, LinkedIn, X, RSS)
                │  (read-only collectors, one thread and checkpoint per account)
                ▼
assistant.py (systemd user service) → private SQLite + full-text search, local API on 127.0.0.1
                │
                ▼
OpenClaw main agent, isolated review turns started by the service → short suggestions
                │
                ▼
Telegram bot (via OpenClaw) ⇄ owner decisions ⇄ dashboard on your tailnet · `assistant` CLI
                │
                ▼
Do it → isolated preparation turn of the same agent → result, draft + Send, or blocker
```

| Path | Role |
|---|---|
| `assistant.py` | Collectors, SQLite store, local API, dashboard server, Telegram outbox, review and preparation workers |
| `dashboard.html`, `sw.js`, `manifest.webmanifest`, `brand/` | Owner dashboard and installable web app |
| `cli.py` | The `assistant` command line |
| `next_actions.py` | Approved sends: email (Gmail via `gog`), LinkedIn messages and X replies and DMs (OpenClaw browser), Slack direct messages (Maton) and WhatsApp messages (`wacli`) |
| `preparation_state.py`, `rollout_health.py` | Preparation bookkeeping and health checks |
| `openclaw-plugin/` | `personal-assistant-bridge`: the `assistant_signals` tool, Telegram buttons and replies, Android feed capture, review/preparation guards |
| `*.mjs` | Small Node helpers that call the OpenClaw Gateway (`openclaw-root.mjs` finds the installed package) |
| `integrations/browser-lease.py` | Shares the OpenClaw browser between concurrent Do it runs |
| `browser_viewer.py`, `deploy/browser/` | Optional: a browser of its own on a virtual display that you watch and use from yours (noVNC, 127.0.0.1 only), and bringing a tab to the front on a desktop |
| `browser_login.py` | The sign-in dialog: fills a sign-in page in the assistant's browser with what you type on the dashboard, and keeps saved sign-ins encrypted |
| `integrations/browser_cdp.py` | Talks to a browser's DevTools address directly, for the LinkedIn and X readers when one is set |
| `AGENTS.md`, `PREPARATION.md` | Instructions the plugin gives the agent for reviews and for Do it runs (templates; see below) |
| `install.sh`, `installer.py` | Install, upgrade and uninstall |
| `config.example.json` | Every configuration key with example values |
| `embeddings.py` | Optional related signals by meaning, through a local embedding server |
| `graph.py` | People and projects: a living graph rebuilt from signals, card decisions, project labels and merges |
| `deploy/` | Example systemd units (the service, an optional local embedding server) and Caddyfile |
| `site/` | The project website, [themachine.computer](https://themachine.computer) (GitHub Pages, `.github/workflows/pages.yml`) |
| `test_*.py`, `test_*.mjs` | Offline tests |

Python's standard library and SQLite are enough: no database server, queue, web framework or extra scheduler. Runtime data lives in `~/.local/share/personal-assistant/`: `config.json`, `events.sqlite`, tokens and receipts.

**Data model.** The main tables are `events` (with an `event_search` full-text index), `checkpoints`, `sync_activity`, `analyses` (reviews), `review_runs`, `suggestions`, `decisions`, `deliveries` (the Telegram outbox), `preparations` and `action_jobs` (approved sends). Provider IDs deduplicate events per account; a changed event gets a new version and becomes reviewable again. Each source page commits its events and next cursor in one transaction, so a crash replays the page instead of losing or duplicating it.

**Boundaries.** Every account has an explicit ID and `boundary` (`work:Acme`, `personal:phone`, `owner:system`…). Every read names its accounts explicitly; app names and chat labels never decide which company an item belongs to.

## Requirements

- Linux with systemd user services (macOS works for development and tests, not as a managed service).
- **Python 3.12+** (standard library only).
- **Node.js 24.16+ within the 24.x series, or 26.1+** (`>=24.16.0 <25 || >=26.1.0`, matching OpenClaw's engine range). Node 22 and 25 are unsupported. The installer resolves mise/asdf shims and the installed npm package.
- **OpenClaw 2026.10.5 or later**, with the Gateway running. Its Telegram channel (your bot) is optional: without it, cards reach you through the dashboard app. Without it, `./install.sh` in a terminal offers to install and set it up (below). CI tests 2026.10.5 and the latest release every week; see [Known limitations](../README.md#known-limitations).
- Optional, per feature:
  - `gog` (Google Workspace CLI) for Gmail and Calendar (OAuth per account, file keyring; `assistant connect gmail` signs you in).
  - **Tailscale** for dashboard access (the dashboard only serves your Tailscale identity).
  - **Caddy** only if you want the dashboard on your own domain (`deploy/Caddyfile`). Not needed: `tailscale serve` gives it an HTTPS address on your tailnet.
  - `wacli` for WhatsApp; a Maton connection for Slack (allowed to post as you, to send Slack messages); a Salesflare API key.
  - The `gh` CLI logged in as you for GitHub; a restricted read-only key (or a Maton connection) for Stripe.
  - For LinkedIn and X: your accounts signed in to the OpenClaw browser (profile `openclaw`). To use another browser profile set `LINKEDIN_INBOX_BROWSER_PROFILE` / `X_INBOX_BROWSER_PROFILE`; to read through a browser's DevTools address directly instead of OpenClaw's browser commands (faster and steadier on a small, busy server) set `LINKEDIN_INBOX_BROWSER_CDP` / `X_INBOX_BROWSER_CDP`, e.g. `http://127.0.0.1:9222`. When a read finds the browser signed out, the Connections tab says "Signed out of LinkedIn" with what to do (on a hosted install: a Sign in again link to the server's browser).
  - The OpenClaw Android app paired as a node for phone notifications ([pairing it, also from outside your network](../docs/getting-started.md#pairing-your-android-phone)).

## Install

```bash
./install.sh                      # interactive install, or upgrade
./install.sh --non-interactive    # same, answers from environment variables (below)
./install.sh --no-start           # write files/link plugin; do not start service or reload plugin
./install.sh --preview            # existing install: one review of the last 7 days, shown only (assistant preview)
./install.sh --activate           # existing install: enable AI reviews and Telegram delivery
./install.sh --skip-plugin        # leave the OpenClaw plugin alone
./install.sh --uninstall          # remove service, plugin and CLI link; keep your data
./install.sh --uninstall --purge  # also delete ~/.local/share/personal-assistant
```

What it does, in order:

1. **OpenClaw** (in a terminal): when the `openclaw` CLI is missing, it offers to install it (`npm install -g openclaw@<tested version>`, into `~/.local` when npm's global folder isn't yours; never sudo; Node.js comes first, and it says where to get it). When OpenClaw was never set up, it says what The Machine needs from it (a model, the Gateway as a service, a Telegram bot from @BotFather) and starts OpenClaw's own `openclaw onboard --install-daemon`; with no Telegram bot yet, `openclaw channels add --channel telegram`. Each step asks first. An OpenClaw that is set up with a bot gets no questions.
2. **Prerequisites:** Python, Node and the `openclaw` CLI are required, with fix hints. Run it as your own user, not root. A new install also needs `tailscale` (the dashboard only opens for your Tailscale login), unless you pass the login and host yourself. `gog`, `caddy` and `wacli` are optional per feature. `systemctl` is required to start the managed service; use `--no-start` for manual setup.
3. **Config:** if `~/.local/share/personal-assistant/config.json` doesn't exist, a short wizard (your name, Telegram chat ID (optional, Enter skips), Tailscale login, dashboard host, time zone, which sources, and for mail and chat your email and company, or "personal") builds it from `config.example.json`. A wrong answer is asked again with what is wrong, and Ctrl+C writes nothing. The company defaults to the one in your email address; "personal" keeps everything personal (`personal:me`). When you talk to a named OpenClaw Telegram account (bot), it is saved as `telegram_account`. The Gateway source is only added when the Gateway runs as a systemd user unit. An existing config is kept as is: only missing top-level keys get their defaults (never the example's owner, Telegram, Tailscale or host; the time zone is this machine's), and install paths (`app_dir`, `openclaw`, `node`, `openclaw_package_dir`) are refreshed.
4. **Secrets:** creates `api-token`, `owner-token` and the Web Push key `vapid.json` when absent, mode 600 in a 700 directory. Existing secrets are never replaced.
5. **CLI:** links `~/.local/bin/assistant` to `cli.py`, and says how to add `~/.local/bin` to your PATH when it isn't.
6. **Plugin:** `openclaw plugins install --link --force --accept-capabilities openclaw-plugin/`, `openclaw plugins enable personal-assistant-bridge`, then a reload. Running the installer accepts the local bridge's OpenClaw capability consent: it registers the assistant tool, owner Telegram commands and event hooks described in the security model. The plugin reads `AGENTS.md` and `PREPARATION.md` from `app_dir` (this checkout), so edits there apply to the next turn.
7. **Service:** writes `~/.config/systemd/user/personal-assistant.service` with this checkout's paths, then `daemon-reload`, `enable` and `restart`. Without lingering it says how to keep the service running after you log out (`sudo loginctl enable-linger $USER`); without a systemd user session (`sudo su`, a container) it says how to get one or run `python3 assistant.py serve` yourself.
8. **Health:** waits for `http://127.0.0.1:<port>/health`. Plugin install/enable/reload failures, systemd failures and an unanswered health check return a nonzero exit code; rerun after resolving the reported step.
9. **Guided setup** (in a terminal, once healthy): connects each source that isn't set up yet (as `assistant connect`, below), restarts the service once and shows the first sync per source; offers `tailscale serve` for the dashboard (and says what is in the way: Serve not enabled on the tailnet yet, or your user not allowed) and a starter `USER.md` when missing or still OpenClaw's template; then `assistant profile` (below) when `USER.md` doesn't say how you write yet; then a preview and, after it, turning on reviews and Telegram. Every question can be answered no. It ends with what is left, if anything.

`--non-interactive` reads: `ASSISTANT_OWNER_NAME`, `ASSISTANT_TELEGRAM_OWNER` (empty: no Telegram), `ASSISTANT_TAILSCALE_OWNER` (required), `ASSISTANT_DASHBOARD_HOST` (required), `ASSISTANT_TIMEZONE`, `ASSISTANT_CONNECTORS` (comma list of `gmail,calendar,slack,crm,android,whatsapp,gateway`, or `none`; default: what is installed or linked here), `ASSISTANT_EMAIL`, `ASSISTANT_GOG_CLIENT`, `ASSISTANT_BOUNDARY` (a company name, `work:<Company>` or `personal`; default: the company in your email address), `ASSISTANT_ANDROID_NODE_ID`, `ASSISTANT_WHATSAPP_STORE`. Tailscale login and host default to what `tailscale status` reports, the Telegram ID to the one OpenClaw already trusts (`commands.ownerAllowFrom`, the Telegram `allowFrom`, or approved pairing senders), the email to the first Google account gog knows. A tagged machine has no Tailscale login of its own, so set `ASSISTANT_TAILSCALE_OWNER` to the login you use on your phone. Non-interactive installs skip the guided setup; run `assistant connect` afterwards.

**Connecting a source:** `assistant connect <source>` (the installer runs the same steps). It asks only what it can't find, writes the account to `config.json` (keeping `config.json.bak`) and keys to their credential files (mode 600, typed without echo, never printed), reads the source once as the service would, and saves nothing if that read fails; it says what to fix instead. Then it restarts the service, only if that service reads this config, and shows the first sync.

It fits setups that aren't the example:

- **Several accounts per source** (two companies, a partner's calendar): `assistant connect gmail` asks whether to redo one or add another. A new account starts from the one you already have (same gog client, Maton key, Salesflare server, poll and active hours), never from its identity. It asks which boundary it belongs to (companies and people first, ones that source doesn't cover yet before ones it does) and follows your id naming (`globex-gmail` if yours look like `acme-gmail`).
- **Keys you already saved** are offered by name (`MATON_WORK_KEY in maton.env`), so Stripe can use the Maton key Slack uses. A shared key file keeps its other lines: only the one `NAME=value` line changes, and a new key for one source never replaces a key another source still uses.
- **Credential files anywhere** (outside the checkout, or systemd `LoadCredential`): paths in `credentials` are used as they are; doctor checks that the key name is in the file.
- **Calendars:** after sign-in it lists the calendars the account can see, so you can read a shared one (`calendar_id`).
- **Salesflare on another server** (staging, self-hosted): when the key is rejected it asks for the API address and derives the app address.
- **Your own dashboard domain** (Caddy, another proxy): `dashboard_host` counts when it answers, so nothing offers `tailscale serve` on top.
- **Another Gateway unit name:** the Gateway source finds it with `systemctl --user list-unit-files`.
- **Only personal accounts:** answer "personal" for the company; plugin sources ask too (`work:<Company>` or `personal:<name>`; only `work:` counts as work).
- **Several OpenClaw Telegram bots:** cards go out through `telegram_account` (default `default`); doctor says when you are approved on another one.
- `assistant connect` on its own also lists what this machine has that no source reads yet: another Google account in gog, a `gh` login, a paired phone, a linked wacli store.

- **Gmail and Calendar:** picks your gog OAuth client, or asks for a downloaded *Desktop app* client JSON (`gog auth setup` explains how to make one). Stores the gog file-keyring password in the `gog-password` credential: your `GOG_KEYRING_PASSWORD` when set, the password of the keyring you already have (never a new one over it), or for a new keyring one you type or a generated one. Signs in with `gog auth add --readonly` for Gmail and Calendar (browserless with `--manual` over SSH). Sending approved emails later needs `gmail.send`, and thread snoozes `gmail.modify`; connecting never asks for more than read access.
- **Slack:** your Maton API key (or one you saved before); the Slack connection is found in Maton with it (you pick one if there are several), and `team_id`, `user_id`, `email` and `slack_host` come from Slack itself.
- **Salesflare (`crm`):** an API key (or one you saved before); `user_id`, `team_id` and `email` come from `/me`.
- **WhatsApp:** the wacli store; offers `wacli auth` (QR code) when it isn't linked, then records `identity_sha256` from `wacli auth status`. It then offers to keep the store synced with a user service (`the-machine-wacli-<account>.service`, `wacli sync --follow`), unless another wacli already holds the store.
- **Android:** picks your paired Android node from `openclaw nodes list`, then asks per connected source with a phone app whether to skip that app's notifications (default no).
- **Plugins:** each connector declares what it asks in `SETUP` (see [Adding a source](../docs/adding-a-source.md)).
- **Telegram ID by hand:** message your bot; with DM pairing its reply shows your user ID. Approve yourself with `openclaw pairing approve telegram <code>`.
- **Dashboard by hand:** `tailscale serve --bg --https=8443 --set-path / http://127.0.0.1:8787`, then open `https://<machine>.<tailnet>.ts.net:8443/`. Its own port makes it its own origin, so the dashboard installs as a phone app next to OpenClaw's web app on :443 (two apps on one origin claim each other's pages). A custom domain is optional: `deploy/Caddyfile` and `deploy/caddy-machine.service`.
- **Preview and activation:** `./install.sh --preview` (or `assistant preview`) runs one regular review of the last 7 days and prints its cards, with progress while the agent works (usually a few minutes). The cards are real and stay on the dashboard, but they are never sent to Telegram, not even after activation, and nothing is prepared ahead; reviews and delivery stay off. `./install.sh --activate` turns them on.
- **First look:** you don't have to ask for that preview. With reviews still off, once OpenClaw has a model and a source has sent its first signals, the service starts one preview of the last 7 days by itself, as soon as the first import has settled (no new signals for 3 minutes). It is the same read-only preview: its cards show on the dashboard (with "Reviewing your last 7 days: 12 of 40 signals" while it runs), nothing is sent, nothing is prepared and nothing is done in your accounts; turning reviews on stays your choice. It runs once (a failed one is tried again after 30 minutes, three times at most), never after a preview of your own or once reviews were on; `review_worker.first_look: false` turns it off.
- To start with one source, set `ASSISTANT_CONNECTORS=gateway ./install.sh` (Gateway failures only), or choose just `gmail` in the wizard.
- Restart after config edits: `systemctl --user restart personal-assistant.service`.

### Your own instructions: USER.md and overlays

`AGENTS.md` (reviews) and `PREPARATION.md` (Do it runs) are neutral templates about *how* the assistant works. Put *who you are* in `USER.md` in your OpenClaw workspace: your name, companies and their accounts, important people, tone of voice for emails, which tools you use and standing preferences. Review and preparation turns get it in their context.

You don't have to write it all yourself. `assistant profile` has your agent read your own recent messages once (sent email counting double, and your own Slack, WhatsApp, LinkedIn and X messages; without quoted history, media placeholders, reactions, automated mail or more than two copies of one template), plus who you work with most and which senders you never answer. In one isolated turn (`agent:main:assistant-review-profile-<run>`, no messages, no file changes) it drafts your role, languages, your voice per channel (email reads nothing like WhatsApp: length, tone, greeting and sign-off for each), a few phrases that sound like you, what matters and what to leave alone. Right after, a second turn reads the first and latest messages with up to 15 of your closest people and, where they show it, notes in the graph what each is to you ("cofounder", "accountant", "customer at Globex"); the terminal lists them, and you can change them in the dashboard's Graph tab. `assistant roles` runs only that second step. You see the draft in the terminal; only on yes does it go into `USER.md`, as its own `## Observed from my messages` section. It replaces the empty starter lines it covers and leaves everything you wrote alone. Run it again later and it replaces only that section. Delete the section's marker line once you've edited it, and later runs leave it alone too. Confidential matters (deals, fundraising, legal, health, money, family) stay out. OpenClaw chat sessions load the first 4,000 characters of `USER.md`, so the draft is written for the room your file has left; what still doesn't fit is left out, least useful first, and listed (`--all` keeps it anyway).

If you want different house rules, edit your checkout's `AGENTS.md`/`PREPARATION.md` (keep `PREPARATION.md` under 30,000 characters; the plugin truncates beyond that, and a test checks it).

## Configuration

`config.json` is plain JSON; paths should be absolute (the installer expands `~`). Restart the service after changes. Top-level keys:

| Key | Default | Meaning |
|---|---|---|
| `owner_name` | — | Your name; preparations sign emails with your first name |
| `timezone` | `Europe/Amsterdam` (the installer sets this computer's) | IANA zone for your work hours, digests, snoozes and recaps (the dashboard's Work hours, or `assistant hours --timezone`) |
| `telegram_owner` | empty | Your Telegram chat ID; the only chat that gets cards and whose taps count. Empty: Telegram off, cards go to the dashboard app's notifications |
| `telegram_account` | `default` | The OpenClaw Telegram account (bot) that sends cards, when you run several; the installer sets it |
| `tailscale_owner` | — (required) | The Tailscale login allowed to open the dashboard |
| `dashboard_host` | — (required) | Host name the dashboard answers on, with `:port` if not 443 (the installer uses `<machine>.ts.net:8443`) |
| `dashboard_path` | `/` | Path the dashboard is served under on `dashboard_host` (`/assistant/` with Tailscale Serve `--set-path`) |
| `dashboard_aliases`, `dashboard_alias_path` | `[]`, `/assistant/` | Extra host names and their path |
| `port` | `8787` | Local API and dashboard port on 127.0.0.1 |
| `database` | — | SQLite file; tokens and keys sit next to it |
| `credentials` | — | Name → file path of each secret: `api-token`, `owner-token`, `gog-password`, `google-broker` (with a broker), and one per source credential. systemd `LoadCredential` with the same names takes precedence |
| `app_dir` | `~/.openclaw/workspace/tools/personal-assistant` | This checkout; the plugin reads `AGENTS.md`/`PREPARATION.md` here and protects it from edits |
| `fix_dir` | `~/.cache/the-machine/fixes` | Where Do it clones The Machine to fix it ([The Machine fixes itself](../README.md#the-machine-fixes-itself)) |
| `self_test`, `self_test_seconds`, `self_fix_push` | `scripts/test.py`, `1200`, `true` | The test command Merge now runs in the fix (a list), its time limit, and whether a merged fix is pushed |
| `self_fix_auto`, `self_fix_auto_push` | `true`, `false` | Small proven fixes to its own errors [merge by themselves](../README.md#the-machine-fixes-itself), with Undo merge for 24 hours; whether those are also pushed upstream |
| `openclaw`, `node` | `openclaw` / next to it | CLI paths for the Node helpers |
| `openclaw_package_dir` | `<node prefix>/lib/node_modules/openclaw` | Installed OpenClaw package (Gateway SDK) |
| `openclaw_state`, `agent_db` | OpenClaw defaults | OpenClaw state and agent transcript databases (read-only: Telegram receipts, progress steps) |
| `gog`, `wacli` | `gog`, `wacli` | Binaries for Google and WhatsApp |
| `google_broker` | — | `url` of a Google token broker, for accounts with `"transport": "broker"` (below) |
| `heartbeat_job_id` | `""` | Optional OpenClaw cron job ID of your main heartbeat, for its health line |
| `manage_url` | `""` | Optional https page where this install is set up (a hosted machine); the dashboard links to it from Settings (Account) and Add a connection |
| `needs_you_file` | unset | Optional JSON list of `{id, text, url, action}` notes from a hosting layer; the dashboard shows them under Needs you, with sources that are not fully set up and connections that need signing in again |
| `browser_url` | unset | Optional page that shows the assistant's browser (a hosted machine's viewer, or `./install.sh --browser-viewer`'s); a card waiting on the browser links to it |
| `browser_cdp_url` | `http://127.0.0.1:9222` | That browser's DevTools address on this machine; **Open the browser** brings the tab a task uses to the front through it (also when it opens a viewer) |
| `browser_vnc` | unset | Optional VNC server showing this computer's screen, as a unix socket path or a `127.0.0.1:port`; the dashboard then shows that screen live at `/browser/` (see *Your desktop's browser from anywhere*, below) |
| `browser_novnc` | `~/.local/share/machine-browser/web` | The noVNC files the dashboard serves for `browser_vnc` (`python3 browser_viewer.py web DIR [NOVNC]` builds them) |
| `browser_lease_script` | `integrations/browser-lease.py` | Lease helper Do it runs use for the shared browser |
| `delivery_enabled` | `false` | Send cards to Telegram (leave off while you test) |
| `delivery_batch_size` | `5` | Cards per delivery pass |
| `digest_at` | `08:30` | Morning digest time; also when work held over a day off goes out |
| `work_hours` | Mon–Fri, `08:00`–`23:00`, mornings `09:00` | [Work hours](../README.md#work-hours): `days` (1 = Monday … 7, or `"mon-fri"`), `start`/`end` (nothing is sent outside them; may wrap past midnight), `morning` (when Tomorrow, 3 days and 1 week snoozes and returning threads land) |
| `bootstrap_days` | `7` | History collected on first import |
| `poll_seconds` | `300` | Default source poll interval (per account `poll_seconds` overrides) |
| `http_timeout` | `15` | Dashboard request timeout |
| `slack_thread_retention_days` | `30` | How long known Slack threads are rechecked |
| `slack_read_seconds` | `180` | At most one Slack history or thread read this often (never under 65 s); see below |
| `slack_active_minutes` / `slack_recent_hours` / `slack_quiet_hours` | `60` / `6` / `24` | How long a Slack conversation rests after a read: someone wrote in it today / this week / longer ago |
| `slack_dm_check_minutes` | `10` | How often a direct message is checked for a new message (a cheap call; a history read follows only when there is one) |
| `preparation_concurrency` | `3` | Do it runs at once |
| `prepare_ahead`, `prepare_ahead_per_day` | `true`, `10` | [Prepared ahead](../README.md#prepared-ahead): cards you'd very likely say yes to arrive done up to their last step |
| `prompt_receipts` | `<data>/prompt-receipts` | Telegram Change-prompt receipts |
| `attachment_dir`, `attachment_sources` | `<data>/attachments`; workspace `data/assistant-attachments`, `memory/assistant` | Where email attachments are stored, and the only folders they may come from |
| `excluded_email_subjects` | calendar "Accepted:" replies | Regexes; matching email subjects are never stored |
| `booking_links` | `{}` | `{"purpose": "url"}`; used when a draft offers to meet |
| `review_worker` | `enabled: false` | `enabled`, `first_look` (true: the automatic first preview, below; false turns it off), `min_signals` (40), `max_wait_minutes` (60), `gap_seconds` (600), `timeout_seconds` (1500), `outage_grace_seconds` (600), `retry_seconds` (60), `max_retry_seconds` (1800) |
| `weekly_recap` | `enabled: false` | `enabled`, `weekday` (1–7, default 5), `at` (`16:00`) |
| `embeddings` | `enabled: false` | Optional [related signals](../README.md#related-signals-optional-local-embeddings): `url`, `model`, `dims` (256), `floor` (0.6), `query_floor` (0.4) |
| `enrichment` | off | Optional [organization data](../README.md#organization-data-optional-enrichment): `provider` or `url`, `headers`, `key_file`/`key_env`, `fields`, `per_day` (50) |
| `accounts` | `[]` | Sources, below |

Every account has `id`, `service`, `transport` and `boundary`, plus optional `scope` (`work`/`personal`, overrides the label the boundary implies), `poll_seconds` and `exclude` (noise rules, below). `config.example.json` has one complete example per built-in source; each plugin in `connectors/` (GitHub, Stripe, X, LinkedIn, RSS) shows its own at the top of its file.

**Noise rules (`exclude`).** A list of rules; an item matching all conditions of any rule is never stored, and stored copies are removed. Conditions: `channels` (Slack channel names), `bots_only`, `packages` (Android package names), `chats` (WhatsApp chat IDs), `senders` (email address substrings), `routine_recurring` (unchanged instances of recurring calendar events), `titles` (exact titles) and `text` (regexes over title and body).

**Built-in noise rules**, on top of yours (`"default_exclude": false` on an account turns them off):

- **Android:** the phone's own status notices ("… is displaying over other apps", "Charging this device via USB", USB/wireless debugging, serial console; storage and security warnings still come through). App notifications stay even if one account from that service is connected: the phone may also use personal mailboxes or other workspaces not collected here. When you connect a source that also has a phone app (Gmail, Slack, WhatsApp, …), setup asks once whether to skip that app's notifications; yes adds an `exclude` rule with its `packages` to the Android account, which you can also write yourself.
- **Calendar:** unchanged instances of recurring placeholders: Lunch, Block(ed), Busy, Focus (time), Deep work, Home, Office, Commute, Gym, Stand up / Standup / Daily standup, Happy birthday!. A moved or one-off "Lunch with Ann" still counts.

## Sources

**Gmail and Calendar through a token broker.** Instead of `gog`, a Gmail or Calendar account can use `"transport": "broker"`: a service you run (a hosted control plane, say) keeps the Google OAuth client and refresh tokens, and this machine never sees either. Set `google_broker.url` and put this machine's token in the `google-broker` credential. For each call, `google_api.py` asks the broker for a short-lived access token for that one account and scope, then calls Google's REST API directly. The broker answers `POST {"email", "scope"}` with `{"access_token", "expires_in", "scopes"}`, `POST {"email"}` with the granted `scopes`, and 403 for a scope the owner didn't grant. Only a fixed list of Gmail and Calendar methods can be called; anything else is refused before a token is asked for. Sending and snoozing work only when the owner granted `gmail.send` or `gmail.modify` at the broker, as with `gog`.

Collectors read existing source content. The optional Salesflare `join_pipelines` setting also adds your own user to matching CRM accounts; it is empty by default. Each collector runs in its own thread with its own checkpoint, so a slow or failing source never blocks the others. Absence from a later read is never treated as deletion.

| `service` | Needs | Account keys |
|---|---|---|
| `gmail` | `gog` OAuth for the account, or a token broker | `email`, `client` (gog OAuth client), `excluded_terms` (text that drops a message, e.g. a warmup tag) |
| `calendar` | `gog` OAuth, or a token broker | `email`, `client`, `calendar_id`, `excluded_title_terms` |
| `slack` | A [Maton](https://maton.ai) Slack connection | `credential` + `credential_variable` (file with `MATON_API_KEY=`), `connection_id` (found in Maton during setup), `team_id`, `user_id`, `email`, `slack_host`, `fair_incremental` |
| `salesflare` | Salesflare API key | `api_base`, `app_base`, `credential`, `credential_variable`, `user_id`, `team_id`, `email`, `owner_emails`, `join_pipelines`, `accounts_per_batch`, `meeting_window_days`, `timeout_seconds` |
| `android` | OpenClaw Android app paired as a node | `transport: openclaw-feed`, `node_id`, `reconcile_seconds`, `direct_fallback`, `fallback_seconds` |
| `whatsapp` | `wacli` with a synced local store | `store`, `identity_sha256` (sha256 of the linked JID; the collector refuses a different account) |
| `gateway` | journald | `unit` (default `openclaw-gateway.service`), `bootstrap_hours` |
| `github` (plugin) | `gh` CLI logged in as you | `login`, `repos` (optional filter), `ci_repos` + `ci_branch` (failed runs) |
| `stripe` (plugin) | A restricted read-only Stripe key, or a Maton Stripe connection | `transport` (`api` or `maton`), `credential` (+ `credential_variable`, and `connection_id` found in Maton), `stripe_account`, `large_subscription_monthly` (500) |
| `linkedin` (plugin) | LinkedIn signed in to the OpenClaw browser | `active_hours` (`[7, 22]`, local time); config `linkedin_inbox` for the reader path |
| `x` (plugin) | X signed in to the OpenClaw browser | `handle`, `primary` (the account the browser is normally on), `active_hours` (`[7, 23]`), `long_posts` (X Premium: replies up to 25000 characters); config `x_inbox` for the reader path |
| `trello` (plugin) | A Trello API key and a read-only token | `board` (URL or ID, one board per account), `api_key`, `credential` (the token), `bootstrap_days` (7) |
| `rss` (plugin) | — | `url` |

Add a plugin account with `assistant connect github` (or `stripe`, `x`, `linkedin`, `trello`, `rss`): it asks for the fields that connector needs. You can also add accounts to `config.json` by hand, using the keys above (each connector's header has a full example), and restart the service.

- **Gmail:** follows Gmail history after a seven-day import; replays the retained window when history expires. Replies keep their new text whole and only 3,000 characters of quoted history. Sending approved drafts and snoozing threads use separate workers and only scopes already granted.
- **Calendar:** seven past and thirty future days, recurring events expanded, incremental sync token with a daily full refresh. A missing event is "unavailable", not "canceled". Context for email and preparation, not a reminder feed.
- **Slack:** conversations you already belong to; never joins channels or opens DMs. Per-conversation cursors, overlap rereads for edits, deferred retries for failed thread pages, and provider `Retry-After` honored across restarts (plus a random extra wait, so computers sharing a connection don't retry together). Slack allows apps outside its Marketplace about one history or thread read a minute per workspace, and a Maton connection gets no more than that, shared by everything on the same connection: this collector, another computer running The Machine for the same account, and your own scheduled posts that check a channel first. So the collector spends at most one such read every `slack_read_seconds` and leaves the rest. It reads busy conversations within the hour and quiet ones once a day, and reads a direct message only when Slack says it has a new message. Posting isn't held back by this: a send Slack refuses as rate limited waits and goes out a few minutes later. Polling a large workspace the first time can take hours. Don't connect the same Slack account on two computers if you can avoid it; they share that one read a minute.
- **Salesflare CRM:** only colleagues' emails that don't involve you (yours come from Gmail) and meetings with notes. With `join_pipelines`, it adds your own user to accounts linked to opportunities in those pipelines (a POST, never another user).
- **Android:** the plugin peeks OpenClaw's event queue for notifications from your phone node, with periodic direct reads to catch gaps. Repeated updates merge into one episode; one-time codes are omitted. Not a complete history.
- **WhatsApp:** reads your existing `wacli` store (`auth status`, `messages list` only, always `--read-only`), rereading the last seven days. New messages arrive through the `wacli sync` that `assistant connect whatsapp` sets up (or your own).
- **OpenClaw Gateway:** failed agent runs on Telegram, refused hand-offs and failed Telegram sends, grouped per half hour with secrets redacted. The first Gateway source also carries **The Machine's own errors** (`machine.fault`): see [The Machine fixes itself](../README.md#the-machine-fixes-itself).
- **GitHub:** notifications on threads you take part in (review requests, mentions, assignments, comments, threads you opened), each with the issue or PR and its latest comment. GET requests only; nothing is marked read. Each run checks that `gh` is logged in as `login`. Optional failed workflow runs per repository.
- **Stripe:** only events worth a look are stored: failed payments, disputes, ended subscriptions, scheduled cancellations, past due or unpaid, and new subscriptions of at least `large_subscription_monthly` a month. GET on the account, events and customers only; it never refunds, retries or changes anything. Each run checks the key belongs to `stripe_account`. For a direct key, create a restricted key with read access to Events, Customers and Account.
- **LinkedIn:** inbox conversations and messages (`connectors/linkedin.py` with `integrations/linkedin_inbox.py`), read through LinkedIn's own messaging API from a tab parked on `linkedin.com/robots.txt`, so nothing is marked read. Hourly in waking hours only (`poll_seconds`, `active_hours`), to keep automated LinkedIn traffic low. Pages follow LinkedIn's own cursors and commit with the messages; an unfinished scan continues after 30 seconds and keeps its lower bound through outages, however long; only a completed scan advances it. Spam is skipped. A busy browser retries in 10 minutes, signed out hourly, and setup and doctor say in plain words that LinkedIn is signed out in that browser. Every tab the reader opens is closed when it's done, also tabs a stopped run left behind; a sign-in tab you opened yourself stays. LinkedIn's query IDs are pinned; `linkedin_inbox.py discover` refreshes them when LinkedIn changes its client. A browser holds one LinkedIn sign-in, so a second LinkedIn account gets a browser profile of its own (`"browser_profile"`, and `"browser_cdp"` when read directly, on the account); it reads and sends there. No API key needed.
- **X:** replies, mentions and the DM inbox for each X account signed in to the OpenClaw browser (`connectors/x.py` with `integrations/x_inbox.py`), read through the owner's own signed-in x.com session (the same requests x.com makes, not X's developer API) from a background tab, so nothing is marked seen. Several accounts can share the browser through X's account switcher: x-inbox switches to the one it reads and always back to `primary`, one run at a time, and only while no other job holds the browser lease. An account can also have a browser profile of its own (`"browser_profile"`, and `"browser_cdp"` when read directly, on the account): it reads and sends there. Poll it hourly or less often (`poll_seconds`, default waking hours 07:00–23:00 via `active_hours`): X is sensitive to frequent automated reads. Encrypted X Chat isn't read. As with LinkedIn, a signed-out browser gets a plain-language hint and the reader closes the tabs it opened. No API key needed. Replies and DMs can be sent too, only after your approval (X Send, below).
- **When a task waits on the browser:** a card whose Do it run has been in the browser for 45 seconds or more, or that stopped right after a browser step to ask for help (a sign-in, a captcha, a code), shows **Open the browser**. It brings the tab that task uses to the front on the computer the browser runs on, and with `browser_url` (or `browser_vnc`) it also opens that viewer (on Telegram too, when it is an https page).
- **When a task waits on your sign-in:** a Do it run that hands a sign-in to you (a password, a code, a passkey: its blocker says so, and it keeps that tab open) shows **Waiting for you to sign in** with a **Sign in** button on the card. On Telegram the card gets the same button and a **Needs you to sign in** notification (and phone push); both open the dashboard's sign-in dialog for that task (`#signin-for=<task id>`, needs `dashboard_host`). Signing in through the dialog carries the task on by itself, the same as **Continue**. Signing in in the browser itself does too: while a task waits, the service looks every 20 seconds at which sign-in forms the shared browser shows (which fields, never what is in them), and once the form on that task's site was seen and is gone, it carries on, once per blocked run. Everything is per task: the site comes from that task's own run steps (the address opened in the tab it last used, also in earlier runs of the same task), the dialog and **Open the browser** only offer that site, and a sign-in on one site never carries on a task waiting on another. When a task's site can't be told, the dialog names none and leaves it to the browser. The dialog opens the task's sign-in page itself in the shared browser, also when its tab was closed (from the address its run left off at; it never types anything there), and waits up to 15 seconds for a form. A sign-in with nothing to type (itsme or eID, a passkey, a captcha, choosing an account) shows the browser's live view right in the dialog (with `browser_vnc`; otherwise **Open the browser**), and once that task's tab is back on its site past the sign-in, the task carries on by itself and the dialog closes. **Continue** stays as a fallback. The service never starts or retries a sign-in on its own.
- **Sign in here:** next to it (and on a signed-out LinkedIn or X source in Connections), a small dialog asks for the username, password and, when the page wants one, a one-time code, and fills them into the sign-in page in the assistant's browser over DevTools (127.0.0.1 only), then submits. Only the owner on the dashboard can use it; the agent, the model, the CLI and Telegram cannot, and nothing typed there is logged or quoted in an error. It only fills the site the dialog names, on https, and empties the password field again when the page does not move on. **Save for next time** keeps that site's username and password in `browser-logins.enc` in the data folder, encrypted with its own key (`browser-logins.key`, mode 600), on this machine only; Connections lists saved sign-ins with **Forget**. A move takes them along only with a passphrase. One-time codes are never saved. For a captcha or anything unusual, use **Open the browser**. Anything running as the service's own user, the agent's shell included, can in principle read the key file, so this keeps passwords out of the model's context and out of logs and exports, not away from a compromised machine.
- **A browser on a server you can see:** `./install.sh --browser-viewer` gives the assistant a Chrome of its own on a virtual display (Xtigervnc), shown in noVNC through websockify at `http://127.0.0.1:6080/vnc.html`, with Ctrl+C / Ctrl+V (Cmd on a Mac) working between your computer and that browser. Everything listens on 127.0.0.1 only; reach it through an SSH tunnel (`ssh -L 6080:127.0.0.1:6080 server`) or your own private proxy. It starts on first use and stops after 15 idle minutes. OpenClaw gets it as its `machine` browser profile (DevTools on 127.0.0.1:9222), and the dashboard links stuck cards to it. Needs `tigervnc-standalone-server websockify novnc matchbox-window-manager` and Chrome or Chromium.
- **Your desktop's browser from anywhere:** when the assistant's browser runs on a desktop you are not sitting at, the dashboard can show that screen live in noVNC and let you use it, from your phone too. Run a VNC server for the screen that only this computer can reach, ideally on a private unix socket, and set `browser_vnc` to it. Wayland: `wayvnc --config=<file with enable_auth=false> --unix-socket <runtime dir>/vnc.sock` (a wayvnc with `--unix-socket`), with the socket in a 0700 directory, as a user service. X11: for example `x11vnc -localhost -rfbport 5905 -forever -shared` and `browser_vnc` `127.0.0.1:5905` (a 127.0.0.1 port is open to every program on this computer, a unix socket only to you). Get noVNC (your distribution's `novnc` package, or a release from its GitHub project) and build the viewer files with `python3 browser_viewer.py web ~/.local/share/machine-browser/web /path/to/novnc`. **Open the browser** then opens `https://<dashboard_host>/browser/vnc.html` and brings the task's tab to the front. On Hyprland the view opens on the browser's own window (and focuses it there), fitted to a phone's width; pinch to zoom, drag with one or two fingers to move around, tap to click, with buttons for the whole screen, zoom and the on-screen keyboard (for a password or a code). Elsewhere it opens on the whole screen. Only the dashboard's owner gets that page, and the dashboard only connects its websocket for a page on the dashboard's own address, so another site can't use your tailnet identity to reach the screen. The VNC server needs no password of its own as long as only you can open its socket; never put it on a public or tailnet address without one.
- **Signing in from a server:** X and LinkedIn often refuse sign-ins, or keep asking for checks, from data-center addresses. If The Machine runs on a cloud server, let only its browser go out through a connection at your home or office: start Chrome with `--proxy-server=socks5://127.0.0.1:1055` and point that at Tailscale's userspace SOCKS5 proxy (`tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055`, then `tailscale up --exit-node=<your home computer>`), or at a residential proxy of your own. The rest of the server keeps its own route. Sign in once through `x.com/login`; the session stays in the browser profile.
- **Trello:** actions on the cards of one board per account (created, moved, renamed, due dates, comments, a card deleted), GET only, so the board is never marked viewed. Authorize the token with read scope only. Starts with the last seven days (`bootstrap_days`), then follows new actions with a short overlap; a long catch-up continues where it stopped.
- **RSS/Atom:** the example plugin; see [Adding a source](../docs/adding-a-source.md).

## Reviews

The service starts reviews itself (`review_worker.enabled`). When at least `min_signals` wait, or the oldest has waited `max_wait_minutes`, it starts one isolated turn of your main agent, `agent:main:assistant-review-signals-<run>`, with message delivery off. The plugin adds `USER.md` and `AGENTS.md` to that turn. The agent claims up to 120 signals, reads them through the `assistant_signals` tool, checks decisions, earlier suggestions and related evidence, saves suggestions with `when`/`due` timing, and finishes the analysis. One run at a time with a gap between runs; failed runs back off. The dashboard (Connections, Signal reviews) shows a running review at the top: how far it is (12 of 40 signals), from which accounts, and its latest step from its own tool calls ("Reading 20 emails", "Looking up Globex", "Suggesting: …", "3 suggestions so far"; earlier steps fold away; never message text or search words), updated every few seconds; then when the next one starts. Finished runs move to Recent runs. Unfinished claims transfer safely to the next run, and the service never restarts the Gateway.

**Things that slipped.** Each review prompt also gets up to five "Possibly slipped" lines of each kind: your own sent emails whose new text asks something (a ? in a link doesn't count) and whose thread has had nothing since, 4-21 days later, but only in threads the other person started or to people who had written to you before you started the thread, so your outreach never shows up; and questions addressed directly to you (at most two To recipients, not Gmail's Updates/Promotions/Social/Forums categories, from someone you have emailed before) with no reply from you in the thread after 2-14 days. Three or more emails sent the same day with the same subject pattern count as outreach too and are left out. Anything already on a card is skipped. The `slipped` table records which review got each one; it's offered again only if that review didn't save within six hours. The review checks for an answer elsewhere before making a card.

### Promises

Reviews record every promise they find in a message, with `promise`, instead of making a card: one you made in something you sent (`mine`), or one someone made to you (`theirs`, in their message or in your ask they took on), with who, what and a date when one was said. Most are never spelled out and look different per person, language and relationship, so the review judges what a message means in its conversation (reading the request in `earlier_in_conversation`), with examples as a guide rather than words to look for. Each goes into the `promises` table, once per message, person, direction and distinct promised action. Nothing shows up on Telegram yet.

- **When it's checked:** yours on the morning it's due (at `digest_at`, never sooner than three hours after it was recorded), theirs the morning after their date. Without a date: yours after 3 days, theirs after a week.
- **Closing by itself:** someone else's promise made by email closes when that sender replies in the original thread (sender-only fallback if no thread was recorded). Chats run on, so a later chat message is no sign it was kept: those stay open for the review or the check. Their new message is reviewed as usual, so a delay can become a new promise. Every review prompt lists the open promises, and the review closes one (`kept` or `dropped`, with the reason) or moves its date when a later message shows it.
- **The card:** a promise still open when it's checked becomes a card (`promise:<id>`), prepared ahead under the usual limits: for yours the thing you promised, for theirs a short nudge. If they answer while the card is on its way, it closes. At most 5 such cards a day.
- **Where you see them:** the dashboard's Suggestions tab has a **Promises** list (You promised, Promised to you) with Done and Drop for open ones, `assistant promises` lists them, and the weekly recap counts kept and open ones.

Your decisions and written reasons are summarized into every review prompt, and lasting facts you state are saved (`remember_fact`) and listed, removable, on the dashboard. Style notes that hold beyond one card (signatures, formatting, tone) become **draft rules** that every later draft follows. Each Do it result says whether it's finished or what it needs from you, and the run sorts your Change notes (style, missing fact, keep going, new ask). The Feedback tab shows the split, the hit rate per day or week, and markers where the rules changed.

**Slack messages and hand-offs.** A Do it result can carry a Slack direct message from your own Slack account (the Maton Slack connection, as your user) to one person, named by Slack `user_id` or work email, with their name. `SlackTransport` in `next_actions.py` is separate from the read-only Slack source and can only look the person up (`users.info`, `users.lookupByEmail`), open the DM, post one message and read that DM back. Before sending it checks that the Slack name matches and that it's an active person, not a bot. An unclear outcome stays uncertain and is checked by finding exactly one message from you with that text in the DM; it's never resent. `delegate:true` (on Slack, or an email to one address) hands the task over: after the send the card is snoozed for `wait_days` (default 3; a weekday at 09:00) with a watch on that person. When they reply (in that DM, or in that email thread or to that email), the card closes itself and their reply is reviewed as an answer to the card; without a reply it comes back saying so. Sending needs a Maton Slack connection that is allowed to post as you.

**LinkedIn messages** use the same approval path. The `linkedin` action either replies in a conversation from the task's own LinkedIn signals (`conversation_id`), or writes to a profile slug you gave (`profile` plus `to_name`). Preflight resolves the recipient without sending, and a profile whose name doesn't match `to_name` blocks the send. `LinkedInTransport` runs `linkedin_inbox.py send --yes` with the job ID as token; the reader logs its intent before the one write (fsynced, token admission locked) and never reuses a token. An unclear outcome stays uncertain and is checked through `linkedin_inbox.py verify` by exact message identity, not by matching text or time; missing or damaged send evidence stays uncertain and is never resent. A busy browser just waits.

**X replies and DMs** use the same approval path, through the same signed-in x.com session. The `x` action either replies to a post from the task's own `x.mention` signals (`reply_to`, its tweet ID) or answers in a DM conversation from its `x.dm` signals (`conversation_id`), from the account the signal came in on: plain text, no media, at most 280 characters for a reply (links count 23, as on X; 25000 with `long_posts`) and 10000 for a DM. Send shows the account, the post replied to or the conversation, and the text. Preflight resolves the post or conversation without writing. `XTransport` then runs `x_inbox.py send --yes` with the job ID as token. A reply goes through X's own composer (`x.com/intent/post`): the tab first checks the composer holds exactly the approved text for the approved post, then clicks Reply once, and blocks every other post request. A DM goes to the DM inbox API (`dm/new2`) with a request ID derived from the token. The reader logs its intent before the one write (fsynced, in `~/.local/share/x-inbox/sends.jsonl`) and never sends a token that is already in the log; it switches accounts only under its run lock and always switches back. An unclear outcome stays uncertain and is checked through `x_inbox.py verify` (the returned post ID, else that account's reply to the post with the same text; for a DM the request ID), never resent. Encrypted X Chat conversations are refused. By hand, `x_inbox.py send --handle <h> --primary <p> --reply-to <id> --text '…'` only previews; `--check` also fills X's composer without posting; only `--yes` sends.

**WhatsApp messages** use the same approval path, and only from a card: the owner's Send now, never `send_now`. The `whatsapp` action replies in a chat from the task's own `whatsapp.message` signals (`chat_id`, from the account the message came in on): plain text, no media, at most 4000 characters. `WhatsAppTransport` checks read-only first (the linked number still matches `identity_sha256`, the chat is in the store), then makes the one writable wacli call, `wacli send text --to <chat> --message …`, without `--read-only`; every other wacli call stays read-only. With the sync running, wacli hands the send to it. wacli has no send token, so an unclear outcome stays uncertain and is checked by finding exactly one message from you with that text in the chat since the send was approved; it's never resent. A busy store waits.

## Telegram and the dashboard

**No Telegram?** Telegram is optional. Install the dashboard as an app and turn on its notifications: cards, reminders, results and the weekly recap arrive as phone notifications instead.
- **Android (Chrome):** open the dashboard, tap ⋮ → **Add to Home screen** (or **Install app**), open The Machine from your home screen, open **Settings** (the gear at the top) and tap the bell.
- **iPhone/iPad (iOS 16.4 or newer, Safari):** open the dashboard, tap **Share** → **Add to Home Screen**, open The Machine from your Home Screen (notifications only work there, not in a Safari tab), open **Settings** (the gear) and tap the bell.
- Turn Telegram off or back on any time: `assistant telegram off`, `assistant telegram on <your Telegram user ID>`. `--remove-bot` with off also deletes the bot and its token from OpenClaw. Without Telegram, **Change** is typed on the dashboard card.

### Work hours

One setting for when The Machine works with you: on the dashboard under **Settings** (the gear at the top) → **Work hours** (phone-friendly), with `assistant hours`, or as `work_hours` in `config.json`.
- **Work days** (default Monday–Friday): work cards (from `work:` sources) wait for a work day, and snoozed work and returning email threads come back on one. Personal cards keep their timing.
- **Messages from / until** (default 08:00–23:00): nothing is sent outside these hours, to Telegram or the phone; it waits until they start. Reviews keep running in the background. A range like 20:00–04:00 wraps past midnight.
- **Snoozes land at** (default 09:00): where Tomorrow, 3 days and 1 week land, and when a snoozed email thread comes back.
- **Morning digest** (`digest_at`, default 08:30) and **time zone** (`timezone`; the installer takes this computer's, and the dashboard offers the device's when it differs).

Saved from the dashboard, it takes effect at once and is written to `config.json` (the old file stays as `config.json.bak`).

**Telegram** uses OpenClaw's existing bot and message route, never a second poller.
- The outbox records intent before sending. A send with an unknown outcome is held as `uncertain` and reconciled against the chat; it is never resent blindly.
- Cards and reminders go out only within your [work hours](../README.md#work-hours) (by default 08:00–23:00). Non-urgent work cards wait for your next work day (by default Monday after a weekend). Several cards may go out together; only the first buzzes.
- Buttons: **Do it**, **Change**, **Snooze** (2 hours, tomorrow, 3 days, 1 week, pick a date), **Dismiss** (with a reason), **Mark done**, **Undo** (five minutes). They edit the existing card instead of sending new messages; a finished Do it sends one short Ready notice. Each row holds up to three buttons that fit a phone's width, in order, so a Send shares its row with Mark done (`controls()` in `assistant.py` and `packRows` in the plugin follow the same rule).
- **Change** opens Telegram's native reply box. Only a reply to that exact prompt, within ten minutes, counts as instructions. `/assistant_feedback <id> <text>` is the fallback.
- The weekly recap (`weekly_recap`, off by default) arrives once per week, Fridays from 16:00 local time. Short sections with one point per line, so it reads at a glance on a phone: the hit rate first, then cards and decisions, what it noticed about how you acted (including how many drafts went out as written since the writing rules last changed), busy sources without results, promises (kept, open, past their date), people and projects (heating up, cooling, gone quiet, new), and behind the scenes (reviews, backlog, token usage).

**The dashboard** shows the same cards grouped as Needs your decision, Ready for you, Preparing and Coming back later, plus the open [promises](../README.md#promises), with Work/Personal filters, deadlines, sources and their health, review runs, feedback and learned facts. A card shows its main action and two more; the rest, such as **Hand off** to a teammate in a Fleet, sit under **More**, and **Not needed** is one compact choice. When reviews are off, the dashboard says so, with the way to turn them on. Any signal, on a card or in the Events and Graph tabs, opens in a built-in reader: the full email with its images and attachments (fetched read-only), or the chat, Slack or calendar text, so you don't need the original app. At inbox zero you can poke the sleeping robot; poke it a few times and it offers a 20-second mini game, Catch the Zzz. It can be installed as a phone app and sends a push notification for each card, with or without Telegram. **Settings** (the gear at the top right) holds your [work hours](../README.md#work-hours), this device's light mode and phone notifications, a link to your Fleet rules when you are in a Fleet, and on a hosted Machine the way to your account page; connections stay under Connections. It is served on `127.0.0.1:<port>` and reached through Tailscale Serve or Caddy; see [Security model](../README.md#security-model).

## Do it

**Do it** queues a preparation: an isolated turn `agent:main:assistant-review-preparation-<job>` of your main agent with `USER.md`, `PREPARATION.md`, the card, your latest note and the full evidence. Up to `preparation_concurrency` run at once and share the OpenClaw browser through a lease. The agent does the work and saves a short result with `prepare` (longer output goes in `memory/assistant/` in the workspace), or records a plain-language blocker. Emails come back as a draft with a **Send** button. In Telegram the card shows the draft itself (to, cc, subject, text), so **Send** there sends exactly that revision and **Edit** opens it in the dashboard; a draft too long for the card, or one where you still pick the recipient, first shows a full preview and sends on **Send now**. Read result on the dashboard shows the draft too. Sends go through the account's Gmail, and the card then says who it went to and when. A run interrupted by a crash or outage resumes with its history instead of redoing finished steps.

<a id="prepared-ahead"></a>**Prepared ahead.** When the review is confident you'd say yes, the work starts before you tap anything, and the card arrives done up to the one step that commits something. A reply arrives as a draft with **Send**; a form arrives filled in with **Submit…**, an invoice set up with **Pay…**, a booking picked with **Book…**. That button shows what goes out (amounts, dates, names, text), and **Confirm** runs only that last step, checks that it went through and reports back. If the page shows something else by then, it stops and tells you instead. Nothing is sent, submitted, paid or changed before your confirm.

## The Machine fixes itself

The service keeps track of its own bugs and can fix them. Small fixes that prove themselves merge on their own and stay undoable for a day; everything else waits for your Merge.

- **What it records.** Every error raised in The Machine's own code (logged by a worker, or a thread that dies) is kept per code location and day: the exception type, file, line and function, how often, and the code version. Never the error message, arguments or values, which can hold private content. Trouble outside the code (a source or the network down, a full disk, a busy database) isn't recorded as a bug.
- **How it reaches you.** The first `gateway` source turns them into `machine.fault` signals: the first occurrence of a day, then again at 3, 10, 30, 100… A review suggests a fix when an error recurs or breaks something you rely on.
- **Do it** never touches the running code (the plugin blocks that). The agent clones it into `fix_dir/<job id>`, finds the cause, fixes it, adds a test, runs the whole suite and commits. The card then has a **Merge fix** button with the summary, the files and the full diff.
- **Merge now** runs the tests again in that clone (`self_test`, by default `scripts/test.py`), checks that neither the running code nor the fix changed since you reviewed it and that the running code has no local changes, then fast-forwards the running checkout to exactly that commit, pushes it when the branch has an upstream (`self_fix_push`) and restarts the service. Anything off means nothing is merged, with the reason on the card. `send_now` can't approve a merge; only the small proven-fix exception below is automatic. A fix whose base moved has to be redone. A change to the OpenClaw plugin takes effect after `openclaw plugins reload personal-assistant-bridge`.
- **Merging by itself:** a fix to one of its own recorded errors merges without a tap when it is small and proven: at most 3 Python files of the service and 50 lines besides the tests, a new test that fails without the fix (checked on a copy of the old code), and no changes to the plugin, AGENTS.md/PREPARATION.md, the database layout or the config. One a day at most. The card says it merged and has **Undo merge** for 24 hours, which reverts the commits, pushes and restarts. It undoes itself when the merged version records the same error again, or any new error within the first hour. `self_fix_auto: false` turns it off; anything that doesn't qualify keeps the Merge fix button. A fix that merged by itself stays on this machine: it isn't pushed upstream (often the public project) unless you set `self_fix_auto_push: true`, and the card suggests pushing it or opening a pull request. A later push never carries along commits that aren't upstream yet, and an undo is pushed only when its fix was.

## People and projects

A living map of who you deal with and what's going on, across every account. `graph.py` rebuilds it from scratch every ten minutes (and soon after a label or merge) as a pure function of the signals, your card decisions, project labels and merges, so a rule change also applies to the past. It needs no setup and nothing leaves the machine (unless you turn on [organization data](../README.md#organization-data-optional-enrichment)); a rebuild over 5,000 signals with probable links and unnamed work takes under half a second.

- **People** come from identifiers only: email addresses (Gmail, Calendar, Intercom, Salesflare contacts), WhatsApp numbers, Slack users, LinkedIn and X conversations, GitHub logins. Your own addresses (configured, plus any address your sent mail comes from), shared role addresses at your domains (info@, support@), your own name on other channels, newsletters (Gmail Promotions/Updates/Social/Forums, no-reply and mailing senders, unless you ever wrote to them), bots and mass mailings (over 20 recipients) are left out.
- **Who is who:** Slack users are looked up once a month (`users.info`, a few per Slack page); their email joins them to their mail. Names are only names (what people or their tools add is dropped: "Kim Lee (Initech)", "Ann Bell | Acme", "Sam at Globex", "via LinkedIn", emoji, a degree; "Doe, Jane" becomes Jane Doe). The same full name on two different channels is one person, unless one channel has two identifiers with that name. The agent's `graph` `merge` or `different` (two person ids) overrides either way.
- **Closeness** adds per day of contact, capped at 1 a day, so a burst of chat messages is one day: you writing 0.6 (split over recipients), them writing to you 0.3 (0.15 by email until you've written to them), a meeting 0.5 (split over attendees), present in a group or CC 0.05–0.15. Card decisions add once per card for the people it was directly about: Do it, Send or Mark done +1, a Change note +0.5, Not relevant or Wrong facts −0.5 ("Already handled" counts neither way). Everything fades by half every 60 days (`strength × ½^(days/60)`); below 0.01 it isn't stored. Labels: close (3+), regular (1+), occasional (0.3+).
- **Rhythm:** a contact day is one with contact between you and them (a message either way, a meeting), counted in your timezone (`timezone` in config); signals they're merely on, like a teammate answering a customer, don't count, and "last contact" follows the same rule ("last seen" for someone never in direct contact). each person's usual gap is the median of their last eight gaps between contact days. Someone you wrote to on at least two days has **gone quiet** when the silence is longer than three times their usual gap and at least two weeks (and under a year). Future meetings show as "next meeting".
- **Projects** are named by the review agent (`finish_analysis` `projects: {"name": [event_ids]}`, `suggest` `project`) and with `graph` `project` + `event_ids`; `begin_analysis` returns the live names to reuse. A label spreads to the rest of its thread (email thread, Slack thread, Intercom conversation, GitHub issue, recurring meeting; chats within two days). Heat is 0.2 per signal plus your card decisions, fading by half every 21 days; the state compares now with two weeks ago (heating up, active, cooling, dormant). `close_project` ends one; naming it again reopens it.
- **Links:** people seen together on a signal (2–8 people, half-life 45 days), a person's part in a project (0.3 per signal, 45 days), organizations by non-freemail domain (named from CRM accounts when known).
- **Probably part of a project:** a signal no label reached that reads like a labeled one (local embedding cosine ≥ 0.72) and shares a person with it is shown as probably part of that project, at half weight (heat 0.1, a person's part 0.15). Meaning alone isn't enough, since it groups by genre (every partnership email reads like every other one). Meetings are left out (a bare calendar title reads like every other one with the same people), and so are closed projects. Review batches show the guess as `probably_project`; a review that agrees names it in `finish_analysis` `projects`, which makes it a normal label. The Graph tab marks those signals "probably part of it". It needs the local embeddings (see Related signals).
- **Unnamed work:** threads from the last 45 days with no project that read alike (cosine ≥ 0.75 between thread averages) and share a person form a group when it spans three or more threads, at least two of them not meetings, and you wrote or met in one. Chats are left out: they read alike per person, whatever the topic. A person on more than 150 threads links nothing. Up to two groups go into a review prompt as "Possibly one piece of work nobody named", each once, again only after it has doubled; the review names it with `graph` `project` or skips it.
- **Roles** (what someone is to you): only what you say on the dashboard (Graph tab, a person, Edit) or what a review reads in a message (`graph` with `node_id` and `role`, a few words: "accountant", "CFO at Globex"); your word stays until you change it. Sources don't set one: being in the CRM, paying through Stripe or sharing a surname says too little about what someone is to you. Instead each review gets up to five of the strongest people with no role (closeness 0.5 and up, strongest first, each asked again after 30 days) with those facts as hints, reads their messages and sets a role only when the messages show it; the dashboard marks those "a review read it in your messages". Onboarding does the same up front in its own turn: after the `USER.md` draft, `assistant profile` starts a roles run for the 15 closest people with no role. The service hands it each person's closeness, organization, projects, hints and their first and latest direct messages with you (long emails keep their start and end, where a signature is; meetings as titles), three people per page, and the turn sets roles with `graph`. `assistant roles` runs only that step, for example after connecting more sources. Stripe events add their customer as a faint contact and name the organization when the CRM doesn't. Roles show on people in the Graph tab, in preparation context, `graph` results and the recap's gone-quiet line.
- **Roles, kept honest:**
  - **Evidence and Who's who:** a review gives the message it read a role in (`event_id`, one of that person's own signals) and a short `quote` copied from it; the service refuses a quote that isn't in the message. The Graph tab's **Who's who** lists the review roles you haven't confirmed, closest first, with that line and Read: **✓ Right** makes it your word, Edit changes it. **✓ All N are right**, above the list, confirms every one of them at once after one check. The weekly recap counts the ones waiting.
  - **Learning from corrections:** when you replace a review's role, `role_corrections` keeps the old and new one; the roles run and the review prompt get the latest eight as examples of what you mean by a role.
  - **Just in time:** people on open cards with no role are asked first in reviews (even when faint); `suggest` answers with `no_role_yet` for the people on a new card, and a Do it job's context names them, so whoever has the evidence open can set it.
  - **Organizations:** a role per organization too (`graph` `node_id` `org:…`: "accounting firm", "customer", "supplier", "possible acquirer"), set by the roles run, reviews or you on its page. People there carry it in preparation context ("at Globex (customer)"), and the roles run sees the roles of others at the same organization. Each review gets up to two organizations with no role, with their people's roles and their projects; one is asked again as soon as one of its people gets a role, or when its last ask didn't show its people's roles yet, not only after 30 days.
  - **Rechecks:** a review's role is offered again (at most three per review) when it was read more than 90 days ago and there was contact since, when new hints showed up since (now billed through Stripe, say), or when it was saved without its line (roles from before this existed). The review sets it again, the same or a new one. Your own roles are never rechecked.
  - **Relations:** `graph` with `node_id`, `related_to` and `relation` ("assistant of", "sister of", "introduced by", read "<node_id> is <relation> <related_to>") links two people (`graph_relations`, a `relation` link in the graph). Both people's pages show it with Remove; your word stays.
  - **Email warm-up traffic:** when a review or roles pass reads that an organization's mail is only a warm-up tool's templated exchanges, it sets `graph` `warmup` on it (`hidden_domains`). That domain and its people leave the graph (Organizations, Who's who, the people on cards) and aren't looked up by enrichment. The Graph tab lists them under "Hidden as warm-up" with Undo; your undo stays.
  - **Used:** preparations write to each person as you would to who they are; open cards on the dashboard sort by deadline, then by who they're about (closeness, +2 when their organization pays through Stripe), so a cofounder or a paying customer comes before a stranger; the recap's gone-quiet line falls back to the organization's role ("at Globex, customer").
- **Names:** Rename on a person's or project's page in the Graph tab, or Edit on an organization's (owner only). A person's or organization's name is kept in `graph_names` and wins over the names in their messages; an empty one gives that back. A project is renamed in place (cards keep their project by name and follow); renaming it to another project's name joins the two.
- **Open between you:** a person's or project's page (`graph` `node_id`, the Graph tab) lists open promises both ways (Done and Drop on the dashboard), open cards on their signals (for a project also cards with its name) and facts learned on those cards. Preparation context adds one "open:" part per person and project, leaving out the task's own card.
- **Tables:** `projects`, `event_projects`, `identity_names`, `graph_merges`, `person_roles` (with `event_id`, `quote`, a `hints` snapshot and `checked_at`), `role_corrections`, `graph_relations`, `graph_names`, `graph_offers` and `graph_role_asks` hold what was said, offered or asked; `graph_nodes`, `graph_links`, `graph_events` (complete retained membership with a `probable` flag; readers display at most 40 recent signals), `graph_unnamed` and `graph_state` are recomputed. `suggestions.project` keeps a card's project.
- **Uses:** the agent's `graph` action (no args: overview; `query`; `node_id` with links, recent signals and spreading activation two hops out; `event_ids`), a "People and projects" block in each Do it job message (who the people on the task are, their rhythm, projects and who they're often with), the dashboard's Graph tab (`/api/graph`, `?q=`, `?node=`), and the weekly recap.

## Related signals (optional, local embeddings)

Off unless you set it up. Full-text search finds the same words; this finds the same *meaning*: a reply worded differently, the same topic in another source, an answer that already came in. Reviews and Do it runs get a `related` action that looks up signals by meaning, only within the accounts the call names (checked like every other read), and a Do it job message ends with a short "Possibly related" list from the task's own accounts when there are close matches. **Nothing leaves your machine:** signal text goes only to an embedding server you run on `127.0.0.1`, never to a model provider.

**Setup.**

1. Run an embedding server on loopback. Any OpenAI-compatible `/v1/embeddings` endpoint on this machine works (one that also lists its model in `/v1/models`); the tested one is llama.cpp's `llama-server --embedding` with an EmbeddingGemma GGUF, for example `embeddinggemma-300M-Q8_0.gguf` from [`ggml-org/embeddinggemma-300M-GGUF`](https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF) (about 330 MB). If OpenClaw's local memory search is on, it already has both: the model file it uses (`memory.search.local.modelPath` in `openclaw.json`, often in `~/.cache/qmd/models/`) and a bundled `llama-server` under `~/.openclaw/tools/llama.cpp/`. Reuse them rather than downloading again.
2. [`deploy/embeddinggemma.service`](../deploy/embeddinggemma.service) is an example systemd user unit: bound to `127.0.0.1:8792`, `Nice=10`, low CPU and IO priority, `MemoryMax=2G`. Set the binary, model path and name in it (or in `~/.config/embeddinggemma.env`), then `systemctl --user enable --now embeddinggemma`. Check it with `curl -s 127.0.0.1:8792/v1/models`.
3. In `config.json`: `"embeddings": {"url": "http://127.0.0.1:8792", "model": "embeddinggemma-300m", "dims": 256}`. `model` must be the name the server reports (llama-server's `--alias`); the service refuses a server that serves another model. Optional `floor` (cosine for signal-to-signal matches, default 0.6) and `query_floor` (for a text query, default 0.4). The installer adds this block with `enabled: false`: set that to `true` (or drop it), then restart the service.
4. Embed what you already have: `python3 assistant.py embed-backfill` (oldest first, with progress; `--batch` sets events per call). It's safe while the service runs; the service itself embeds new and changed signals in the background, newest first.

**How it works.** `event_vectors` holds one normalized float32 vector per event version (Matryoshka-truncated to `dims`), deleted with its event; changing `model` or `dims` re-embeds. The embedded text is the title plus the first 2,000 characters of the body, without quoted earlier messages. `assistant_signals related` takes explicit `account_ids` and `event_ids` and/or a `query`, `limit` up to 20 and optional `exclude_event_ids`, and returns the `events` shape plus a `similarity`; the given events themselves are never returned. With a query, the meaning ranking is fused with full-text ranking. `events` with `semantic:true` and a `query` uses the same fused ranking. `account_ids:["all"]` searches every configured account (for `events` and `related` only; reviews still name their accounts). The dashboard's Events tab searches All accounts by default, by meaning and words together, and labels each result with its account. If the embedding server is down, the service logs `embeddings error=<class>` and retries after five minutes; queries still get keyword matches (`semantic:false`), and nothing else is affected.

**Costs.** No GPU needed. On CPU the server embeds a few signals per second; a first backfill of 5,000 signals takes about 30–40 minutes on a 4-core desktop CPU, and each new signal well under a second. The model takes about 0.3–0.5 GB of RAM while the server runs and no CPU when idle. On a small 2-core arm64 server (October 2026) it was slower and bigger: about 130 tokens a second on one core (a few thousand signals take a few hours once), and 370 MB idle growing to 850 MB while embedding with `--no-repack`, or 300 MB more without it (llama.cpp repacks the weights on arm64 for speed). A first `openclaw memory index` (below) took another 940 MB while it ran. Next to OpenClaw and a browser that is tight on 4 GB of RAM; 8 GB is comfortable. Give the unit its own `MemoryMax` and `MemorySwapMax=0`, so when it grows too big the kernel ends it alone and systemd restarts it. Vectors are about 1 KB each at 256 dimensions (5 MB for 5,000 signals). Search is brute-force cosine in plain Python, about 0.1 s over 5,000 vectors.

**OpenClaw's memory search on the same server.** One server can serve both. Point OpenClaw at it as a provider (the model name containing `embeddinggemma` makes OpenClaw add EmbeddingGemma's task prompts), then build its index once at low priority:

```bash
openclaw config set --batch-json '[{"path":"models.providers.machine-embeddings","value":{"api":"openai-completions","baseUrl":"http://127.0.0.1:8792/v1","apiKey":"local","models":[]}},{"path":"memory.search","value":{"enabled":true,"provider":"machine-embeddings","model":"embeddinggemma-300m","sources":["memory","sessions"]}}]'
systemctl --user restart openclaw-gateway
nice -n 19 openclaw memory index --agent main
```

An explicit provider fails closed: while the server is down, `memory_search` answers "unavailable" instead of falling back to keywords, so keep the unit's `Restart=`.

**Off again:** remove `embeddings` (or set `enabled: false`) and restart; the `event_vectors` table can stay or be dropped.

## Organization data (optional, enrichment)

Off unless you set it up. With a key for an enrichment service, The Machine looks up the organizations in the graph by domain and keeps a small profile of each: industry, size, location, founded, a short description, LinkedIn and website. It describes the company, never what it is to you; that stays a role.

**Setup.** Put the key in a file only you can read (`chmod 600`), then add to `config.json` and restart:

- A preset: `"enrichment": {"provider": "apollo", "key_file": "~/.local/share/personal-assistant/enrichment.key"}`. Presets: `apollo`, `peopledatalabs`, `hunter`.
- Any other service that answers a GET with JSON about a domain: `"enrichment": {"url": "https://api.example.com/companies?domain={domain}", "headers": {"Authorization": "Bearer {key}"}, "key_file": "…"}`. `{key}` can go in the url or the headers; `key_env` reads it from an environment variable instead. The key never goes in the config itself.
- Common response shapes are understood: flat, or wrapped in `organization`, `company`, `data` or `result`, with the usual field names. If yours differs, `fields` adds paths of your own: `{"industry": ["category.sector"], "about": ["profile.text"]}`.

Check it with `assistant enrich globex.com` (looks one up now) and `assistant enrich` (service and counts).

**What is sent.** Only organization domains from the graph, never free-mail domains, your own domains, names, addresses or messages. Only organizations whose people you deal with: first those you write with both ways or meet, then those you wrote to; never one where people only wrote in (cold outreach) or were only copied, newsletter and no-reply senders, or campaign sender domains (news., email., e. and the like); a paying customer always. Five per pass every ten minutes and at most 50 a day (`per_day`), each looked up again after 90 days. After an error (rejected key, rate limit, unreachable) it waits an hour, and that domain a day; errors never contain the url or the key. Lookups count against your plan with that service.

**Where it shows.** The Graph tab (industry in the organization list; a summary, the description and links on its page), `graph` results (`about`, `profile`), preparation context ("at Globex (customer; Manufacturing, 51-200 people, Springfield, founded 1989)") and the roles run. Profiles are kept in `org_profiles`.

**When it's wrong.** Edit on an organization's page changes its name, website, LinkedIn, industry, size, location, founded and description. What you write is kept in `org_edits`, wins over every later look-up, and an emptied field gives the looked-up one back. **Wrong company, clear details** drops a bad match and stops looking that domain up; **Look it up again** (or `assistant enrich <domain>`) undoes it.

**Off again:** remove `enrichment` (or set `"enabled": false`) and restart.

## Security model

Read this before you turn on delivery.

- **Owner only.** Telegram cards go only to `telegram_owner`, and the plugin accepts taps and replies only from that private chat. The dashboard answers only requests that arrive through Tailscale Serve or Caddy on 127.0.0.1 with your `tailscale_owner` identity and the configured host; decisions also need a CSRF-protected same-origin POST. There is no public endpoint. `/health` exposes nothing private.
- **Two tokens.** The local API token (`api-token`, used by the CLI and the agent's tool) can read and save results but cannot record decisions or approve sends; owner actions need the plugin's second key (`owner-token`). The CLI refuses owner actions.
- **Reviews are read-only.** Review turns can only use `assistant_signals`, memory lookups and read-only file tools.
- **Do it gives the agent broad authority.** OpenClaw can't show approval prompts in background runs, so a Do it run can run commands, use the logged-in browser, edit files and act in your accounts, including payments, deletions and signing, as far as the card and your note ask. The plugin blocks only reading credentials and editing the assistant's own code, the OpenClaw config/extensions and systemd units. Fixes to The Machine are made in a clone and reach the running code only through a tested Merge now or the small proven-fix exception below. These are guardrails against mistakes, not a sandbox. Source messages are treated as untrusted evidence, but the agent reads them; prompt injection remains a risk you accept by using Do it. Use a model and accounts you trust with that.
- **Its own code changes only through a tested merge.** Do it never edits the running code. A fix merges after your Merge now, or by itself only when it is small, fixes a recorded error, comes with a test that fails on the old code, and leaves the plugin, the agent's instructions, the database layout and the config alone. Every merge can be undone for 24 hours, and one that merged by itself undoes itself if its error returns.
- **Privacy.** Collected content stays in your SQLite database, but reviews and preparations send it to your agent's model provider. The optional related signals embed text only through the local server you configure; the optional organization data sends only organization domains to the enrichment service you configure. Logs contain account IDs, counts and redacted errors only. Source credentials never reach the model.

## Command line

`assistant` (linked by the installer; code in `cli.py`) talks to the running service with the local API token; `connect` and `doctor` work on this machine's files and also run while the service is down. Every command takes `--json`.

| Command | What it does |
|---|---|
| `assistant tasks` | Outstanding tasks; `--later`, `--done` or `--all` for the other tabs |
| `assistant show <id>` | One task (a unique ID prefix is enough) with decisions, feedback and full evidence |
| `assistant promises` | Open promises, yours and those made to you; `--all` adds the ones closed in the last two weeks |
| `assistant search "<text>"` | Full-text search over all sources; `--account <id>` narrows it |
| `assistant events --account <id> --ids 12,13` | Specific signals, or `--query` within explicit accounts |
| `assistant sources` | Connection health per source |
| `assistant reviews` | Review backlog and recent runs |
| `assistant feedback` | Recent decisions and written feedback |
| `assistant prepare <id> --file brief.md` | Save a preparation on an accepted task |
| `assistant preview` | Before activation: one review of the last 7 days; prints the cards, sends nothing |
| `assistant roles` | Your agent reads the first and latest messages with your closest people and notes what each is to you in the graph (also the last step of `assistant profile`; `--again` to run it again right away) |
| `assistant profile` | Your agent drafts how you write and what matters to you from your own messages, and notes what your closest people are to you in the graph; the draft is saved to `USER.md` only after your yes, within the 4,000 characters OpenClaw loads (`--again` for a fresh draft, `--all` to keep every line) |
| `assistant connect [<source>]` | Set up or redo a source (sign-in or key, IDs, a test read, first sync); without a source, lists what still needs connecting. `--account <id>` picks or adds one of several |
| `assistant hours` | Your [work hours](../README.md#work-hours); `--days mon-fri --start 08:00 --end 23:00 --morning 09:00 --digest 08:30 --timezone Europe/Brussels` changes them and restarts the service |
| `assistant telegram status\|on <id>\|off` | Telegram is optional: turn card delivery there off (cards then reach you through the dashboard app) or back on. `off --remove-bot` also deletes the bot and its token from OpenClaw |
| `assistant doctor` | Checks the service, OpenClaw bridge, Telegram approval, every source, reviews, dashboard and `USER.md`; every problem comes with its fix. Exits 1 when something is broken |
| `assistant enrich [domain]` | Organization data from your enrichment service: status, or look up one domain now |
| `assistant export` | Everything The Machine knows and how it is set up, in one archive (`--keys` adds your keys, encrypted with a passphrase you pick; `--estimate` only says how big). See [Moving to another computer](../README.md#moving-to-another-computer) |
| `assistant import <file>` | Put an export back on a fresh install and list what to connect again |
| `assistant raw <action> '<json>'` | Any other non-owner service action |

## Operations

- **Service:** `systemctl --user status personal-assistant.service`; logs with `journalctl --user -u personal-assistant.service -n 50 --no-pager`. Enable lingering (`loginctl enable-linger $USER`) to start at boot.
- **Plugin:** after editing `openclaw-plugin/`, run `openclaw plugins reload personal-assistant-bridge --wait`; no Gateway restart needed.
- **One collector only:** don't run `assistant.py sync-once` while the service runs. Stop the service before manual syncs or migrations and resume from the existing checkpoints. Never reset a checkpoint to get past throttling.
- **Google sign-in expired:** a Gmail or Calendar source whose stored sign-in Google revoked shows `google_signin_expired_run_gog_auth_add` and retries hourly. Run `gog auth add <email> --client <client>` again; the next retry picks it up.
- **Backups:** use SQLite's backup API (`sqlite3 events.sqlite ".backup copy.sqlite"`); never copy the live file without its WAL.
- **Rollback:** disable a source by removing its account from `config.json`; disable reviews with `review_worker.enabled: false`; stop delivery with `delivery_enabled: false`; disable the plugin with `openclaw plugins disable personal-assistant-bridge`. Keep the database.

### Moving to another computer

`assistant export` writes one archive with the databases (signals, suggestions, people and organizations with your own edits, promises), `config.json`, connectors you built yourself, your OpenClaw workspace files (`AGENTS.md`, `USER.md`, memory) and who may talk to your Telegram bot. Databases are copied with SQLite's backup API, so the service can keep running. Keys and tokens stay out unless you pass `--keys`. Without them the archive is a plain `.zip`, which Windows, macOS and Linux open without extra tools. With `--keys` the whole archive is a `.tar.gz.enc`, encrypted with a passphrase you pick (AES-256, the passphrase stretched with PBKDF2 through `openssl`), and only that passphrase opens it; a zip that Windows opens on its own could only use weak encryption, so keys never go in one. OpenClaw's own chat history and model sign-in stay behind.

On the other computer, install The Machine first, then `assistant import the-machine-export-<date>.zip` (or `.tar.gz.enc`; it asks the passphrase; older `.tar.gz` exports import too). It refuses to overwrite a setup that already has sources unless you pass `--force`, which keeps a copy of the current data folder first. A file you already have there (say `USER.md`) is kept, with the imported one next to it as `.from-export`. It ends with what to connect again: your model, Google (a hosted server's Google access does not move along), and without `--keys` every source's key and the Telegram bot token. Restart the service and check with `assistant doctor`.

The hosted version has the same export on its Account page.

### Catch-up and search guarantees

- **GitHub:** at most five 50-item pages per run, with a frozen `since`/`before` window and a persisted next page. The completed checkpoint advances only after that window drains; events and paging state commit together. New arrivals go into the next window. Previous-page fingerprints are checked before and after a continuation; shifted pages replay that window instead of skipping older items. The five-page work budget remains, with extra read-only anchor checks.
- **Stripe:** at most ten 100-item pages per run. The fixed time window and `starting_after` cursor survive restarts and failed reads. The next window overlaps the completed second (`created[gte]`); event IDs deduplicate replays.
- **X mentions:** at most four pages per read. An unfinished `mention_catchup` retains the bottom cursor and original floor, without advancing the completed post ID. Catch-up resumes at the configured X polling interval, not an increased rate.
- **Coverage:** all three connectors retain incomplete coverage in their checkpoint while more pages wait. A successful bounded read is not a claim of complete catch-up; inspect Sync details.
- **X account switching:** the existing browser lease reserves selection, work and restoration atomically, including primary-account reads and sends. No real-Chrome focus or desktop input. Other jobs wait until cleanup releases the lease. Inventory and public-reply previews/verifications remain shared and never switch accounts.
- **Semantic freshness:** seed/candidate vectors must match their event version, model and dimensions. Changed seeds refresh on demand; stale candidates wait for backfill. Slow old requests cannot overwrite newer vectors, and an in-flight changed/deleted seed is not used. Keyword fallback and account isolation remain.

These are collection/retrieval fixes, not additional approval requirements. Normal owner-authorized work keeps its existing permissions. Regression tests use disposable databases/leases and the actual X reader JavaScript with synthetic responses; no real sends or browser connections.

**Downgrading:** do not run older collectors while `catchup` or `page.mention_catchup` is nonempty: old readers ignore that continuation. Keep the service stopped or hold the affected source until cursor-aware code is restored. Preserve the live database, receipts and owner choices; never reset a checkpoint or restore a backup over newer decisions.

## Adding a source

Sources can be added as a single-file plugin in `connectors/` without touching `assistant.py`. [docs/adding-a-source.md](../docs/adding-a-source.md) walks through one, with [`connectors/rss.py`](../connectors/rss.py) (RSS and Atom feeds) as a working example. The rules for any source:

1. Use an existing credential for the exact account, with only the read permission needed. Give the source its own account ID, boundary and checkpoint.
2. Add an adapter that returns the common event fields: `account_id`, a stable `source_id`, `kind`, `occurred_at`, `title`, `body`, `metadata` (with a source URL where possible) and `deleted`. No AI, guessing or writes in collectors.
3. Fetch incrementally and commit events with the next cursor atomically, so replays are idempotent.
4. Test identity checks, pagination, interrupted commits and expired cursors with fixtures, then do a real read and check its health on the dashboard.

## Known limitations

- **Change prompt and OpenClaw versions.** The native Telegram reply prompt (`openclaw-plugin/change-prompt.ts`) uses the Telegram extension API and a bridge for three client helpers. It resolves them from the installed build's export table, without a version pin or hardcoded content hashes, so OpenClaw updates don't break it. CI loads the real transport modules of 2026.10.5 and of the latest release. If a release removes or renames a helper, Change falls back to a plain prompt before sending (replies still work); an uncertain send never triggers that fallback. The Gateway SDK entry points used elsewhere (`plugin-sdk/gateway-runtime`, `system-event-runtime`, `plugin-entry`) are public but experimental; check them when upgrading OpenClaw.
- **Telegram or the dashboard app** for delivery (no email or SMS); the dashboard needs Tailscale (or your own reverse proxy that sets the same identity headers).
- **Gmail and Calendar need `gog`** or a token broker you run, Slack needs Maton; there are no built-in OAuth flows.
- **Not realtime.** Sources poll (five minutes by default; Slack can take hours on a large workspace), and reviews batch signals.
- **Android and WhatsApp are best effort**: notifications cleared during a gap are lost; WhatsApp depends on `wacli` staying synced.
- Single owner, single machine. Not a multi-user product.

## Tests

```bash
python3 scripts/test.py
```

The runner returns nonzero if any suite fails and isolates HOME/config from your live installation. The tests are offline: they use temporary databases, a temporary OpenClaw workspace (`test-env.mjs`) and stubs for the Gateway, `openclaw` and `systemctl`. `test_installer.py` runs the installer against a temporary `HOME`. `test_ux_dashboard.mjs` drives the dashboard in Chromium through the `playwright-core` that ships with OpenClaw and `/usr/bin/chromium`; point `PLAYWRIGHT_CORE` and `CHROMIUM_PATH` elsewhere if yours differ. The Node tests that load the plugin need the `openclaw` package (`OPENCLAW_PACKAGE_DIR`). `test_openclaw_compat.mjs` also loads the real SDK and installs the plugin into a disposable OpenClaw home, with no Gateway or messages.

## Contributing and security

Something's wrong? The dashboard's footer link opens a [GitHub issue](https://github.com/JeroenCorthout/TheMachine/issues/new); leave out personal data and paste only the relevant lines of `assistant doctor` or the logs.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for development and pull requests, and [SECURITY.md](../SECURITY.md) for private vulnerability reports and publishing checks. CI runs every Python and Node suite on two supported toolchains and scans all reachable history for secrets. Personal account data belongs outside the checkout.

## License

AGPL-3.0-only, see [LICENSE](../LICENSE). Built on OpenClaw (MIT); third-party licenses are listed in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The code is open source under AGPL-3.0. The names The Machine and The Fleet, the logo, and the hosted service and The Machine Network are not covered by the license. See [TRADEMARKS.md](../TRADEMARKS.md).


## Final-step and self-repair recovery · October 17

- **Retry keeps the task:** an owner retry, restart or continuation keeps the exact confirmed final step and its preview. A new Change starts the newly requested work instead.
- **Finish means verified:** completed steps lose their Submit/Pay/Book button. A positively unperformed step can resume after sign-in; an unknown outcome is checked in the original app before any repeat. Preparations save `finish_state` as `completed`, `not_performed` or `uncertain`.
- **Git before restart, receipts before effects:** Merge and Undo record their exact Git receipt in SQLite before push or restart. Undo commits carry an action-bound marker; restart recovery finds that commit instead of reverting it again. Pending optional effects resume without repeating the mutation.
- **Tested code only:** the clone and running tree are checked before and after tests and again under a merge/undo lock at mutation. A small automatic fix must run the recorded failing application path on old code and pass on fixed code; import/collection errors and unrelated failures are not proof. The stored receipt includes test IDs and failure frames.
- **Full proposal:** Download complete diff serves the immutable base-to-head change for the reviewed action revision, with its checksum, even if the clone branch later moves. Long previews remain compact. Telegram's merge review opens this dashboard preview.
- **Rollback timing:** error timing is tracked per code version, separately from daily signal aggregation. A new error triggers automatic Undo only within the true first hour after that merge; the fixed error retains its 24-hour rule. Legacy aggregates contribute only their known last occurrence.

These changes add no approval tier or execution restriction to owner-requested work. SQLite migrations are additive (`preparations.action_id`, `merge_effects`, `fault_versions`); keep the live database and owner decisions on source rollback. See `test_action_recovery.py`, `test_owner_boundary.py`, `test_finish_merge_dashboard.mjs` and `test_telegram_handler.mjs`.


## Review repairs · October 18, 2026

Reviewed the new promises, Graph projects/roles, enrichment, setup and recovery work in both repositories. Repairs keep the existing broad Do it / written-instruction authority; no extra approval tier or per-tool confirmation was added.

- **Promises:** closing/rescheduling and reminder creation share one transaction. A failed save rolls back the card, preparation and promise link together. Distinct commitments to the same person in one message can both be kept; repeat submissions still deduplicate. Email replies must come from the promised sender in the original thread (sender-only fallback when no thread was recorded). A reply means “answered”, not proof the promised work was completed; normal review still checks its meaning.
- **Graph:** only current-version vectors from the configured embedding model/dimensions participate. Turning embeddings off stops semantic grouping. Rebuilds publish one coherent snapshot, so an older rebuild cannot overwrite a newer owner edit. On merged identities the latest owner role wins, its original evidence stays reachable, and explicitly cleared roles/relationships stay cleared.
- **Review context:** an unadmitted/failed review cannot consume the monthly role prompt or unnamed-project offer. Admission retries get the context again; successful review/committed-analysis evidence consumes the offer. Historical offers without a retained run remain consumed.
- **Dashboard:** role and rename forms stay bound to the person actually shown, even when another page is loading. Late responses cannot open a different person's edit form.
- **Enrichment:** transient errors keep the last known company profile while retaining the error and retry timestamp. No new provider or paid lookup was enabled.
- **Recovery:** a Gateway “submit a new turn” response prevents reuse of that admission key but does not prove prior execution ended. Check/stop that exact run first; only terminal proof permits one existing recovery attempt. Network uncertainty retains custody and the same key.
- **CLI:** `assistant roles --json` emits only JSON, including when no role work remains.

Regression coverage: `test_review_integrity.py`, real Chromium delays in `test_roles_dashboard.mjs`, exact Gateway envelopes in `test_review_gateway.mjs`, and the full Python/Node suites. Tests use temporary databases/browser contexts, not live owner decisions or external task actions.

**Activation/rollback:** restart only `personal-assistant.service` after committing tested source. Keep the live database, configuration and owner feedback on source rollback. The promise uniqueness migration preserves IDs/notes/card links but is not compatible with old three-column-conflict code: rollback by reverting the other changes while retaining the four-column promise schema and matching INSERT/lookup. Never restore a stale database over new owner input. A private SQLite backup and source archives precede this activation.

The installer enables the bridge’s required conversation hooks through OpenClaw’s own config command; `assistant doctor` checks the grant instead of calling a loaded-but-inert bridge healthy.

## October 19 reliability repairs

Owner-authorized execution stays broad. No new generic approvals were added. These repairs prevent lost writes and invented success, not requested work.

- Organization edits reserve a write transaction before reading and merging fields; concurrent partial edits retain both changes.
- WhatsApp saves the pre-send message IDs and dispatch timestamp before the admitted write. Reconciliation only accepts one exact own message newer than that boundary; earlier identical messages and ambiguous/legacy sends remain unconfirmed and are never resent automatically.
- Inbox readers hold a per-reader/profile lock throughout their tab lifetime. A lost open receipt is reconciled through an exact persisted unique URL marker, not a difference between browser inventories. Unrelated concurrent tabs are never cleanup candidates. The shared browser lease is still required; stopped-run cleanup occurs only after acquiring it.
- The service acquires its lifetime lock before opening its database, preventing startup from racing offline maintenance.

Tests: `test_oct19_repairs.py`, WhatsApp transport and LinkedIn/X tab fixtures; run the complete Python and Node suites with a disposable HOME. No live owner decisions or messages are needed for these tests.

Customer export/import: configured credentials under the workspace are secret files, omitted without a passphrase and restored/rebased in encrypted exports. Stop `personal-assistant` and close SQLite readers before importing. Import takes the service lock, rejects existing WAL/SHM files without deleting them, validates every database before replacing files, and installs each database atomically. Start the service afterwards. Trello cursor rejection preserves the original unread time window, so catch-up cannot silently jump forward seven days.

## October 20 recovery review

Saved browser sign-ins now serialize across separate dashboard requests; a disconnected browser after Submit is reported as unconfirmed, not signed in. Slack rate-limit records identify the read method without retaining response content. Browser-source failures distinguish unavailable web-client queries, HTTP rejection and browser transport failures using fixed labels only. These changes preserve polling/checkpoints and broad owner-authorized execution; they add no approval step. Two concurrent collectors may still share Slack's upstream quota.

Activation: commit tested code, then restart only the Assistant service. Roll back source by reverting this repair, leaving the live database, saved sign-ins, decisions, checkpoints and delivery receipts intact. Private operation evidence belongs in the workspace report, not this public README.


## October 20 follow-up: receipt and refresh recovery

Fleet answers and notices are acknowledged only after their local save commits. The relay retains an unacknowledged answer for up to three days; its receipt identifies the exact answer stage, so an old acknowledgment cannot consume a newer update. Old app/server pairs remain compatible. Do not clear checkpoints or replay a possibly completed external action to “fix” a timeout.

Fleet refreshes leave unchanged forms in place, preserve typed drafts when data changes, and ignore superseded responses. Telegram draft limits count emoji, escaped punctuation and the owner's feedback together; a trimmed draft opens the full review rather than offering direct Send.

Tests: `test_fleet_receipts.py`, `test_fleet_refresh_dashboard.mjs` (delayed real Chromium responses), and `test_next_actions.CardDraft`. Run `scripts/test.py` in the OSS version, or the private Python/Node suites with a disposable HOME. These tests do not send real business messages or make owner decisions.

These repairs add no new owner-approval restrictions. Explicit owner instructions remain authority to carry out the requested task; proactive preparation still does not authorize unrelated consequential actions.

Activation: commit and test first, restart only the Assistant service locally, and use the maintained hosted app updater for hosted instances. Roll back source, preserving the database and its new receipt records, owner decisions, sign-ins and checkpoints. An older relay without acknowledgments keeps its previous delivery behavior; upgrade the relay and app together for durable receipt recovery.

Uncertain Fleet/Friends questions retain a committed operation ID before sending. The updated relay returns the original question IDs on retry and rejects changed proposals under the same identity. Old relays ignore the new field; they must be updated for this guarantee. Request hashes/IDs follow the existing 30-day question-metadata retention.
