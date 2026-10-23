# Getting started

From installation to your first cards. Setup time depends on account sign-in, source catch-up and the model; each step says how to check it worked.

## Before you start

- [ ] **OpenClaw** 2026.10.5 or later, with the Gateway running. Telegram is optional: with a bot, check that it answers you in Telegram; without one, cards reach you through the dashboard app (see Phone notifications below). No OpenClaw yet? Skip this one: the installer offers to install it and walks you through its setup (step 1).
- [ ] **Node.js** 24.16+ (24.x) or 26.1+, and **Python 3.12+**.
- [ ] **Tailscale** on this machine and your phone, logged in (`sudo tailscale up`), for the dashboard.
- [ ] Your own user account, not root. Linux: a systemd user session (a normal login; over SSH that works too). macOS: you are signed in to the desktop. Windows: see [Windows](#windows-beta).
- [ ] Per source you want: `gog` for Gmail and Calendar, `wacli` for WhatsApp, the OpenClaw Android app paired as a node for phone notifications ([how](#pairing-your-android-phone)), a [Maton](https://maton.ai) Slack connection, a Salesflare API key, `gh` for GitHub. You can add any of them later.

## 1. Install

```bash
git clone https://github.com/Salesflare/TheMachine.git ~/.openclaw/workspace/tools/personal-assistant
cd ~/.openclaw/workspace/tools/personal-assistant
./install.sh
```

Without OpenClaw, the installer first offers to install it, then starts OpenClaw's own setup and tells you what to pick there: your model provider, yes to running the Gateway as a service, and, optionally, Telegram as a channel (skip it to use the dashboard app's notifications instead). For Telegram you make a bot first: message @BotFather, send /newbot, choose a name, then find a free username ending in "bot", and paste the token it gives you. Check: your bot answers you in Telegram.

The wizard fills in what it can find, so most questions only need Enter:

- **Telegram ID (optional):** press Enter to skip Telegram. Otherwise taken from OpenClaw (your owner entry or approved senders). If OpenClaw doesn't know you yet, the installer asks you to message your bot, reads your ID from the pairing request and offers to approve it.
- **Tailscale login and dashboard host:** from `tailscale status` (on a tagged machine, type the login you use on your phone).
- **Company:** from your email address; answer "personal" if these are private accounts.
- **Sources:** suggested from what is installed and linked here (gog, a linked WhatsApp, a paired Android phone).
- **Email:** from the Google accounts gog already knows. Outlook: `assistant connect outlook`.

### macOS (beta)

The same three commands. Get the prerequisites with [Homebrew](https://brew.sh): `brew install python@3.12 node@24 git`, plus `brew install gogcli` for Gmail and Calendar, and the [Tailscale app](https://tailscale.com/download/mac). The service runs as a launchd agent (`~/Library/LaunchAgents/computer.themachine.assistant.plist`) that starts at login; its log is in `~/Library/Logs/TheMachine/assistant.log`. OpenClaw's Gateway runs as its own launchd agent (`openclaw gateway install`). Not on macOS yet: the browser viewer (`--browser-viewer`) and the Gateway log source.

### Windows (beta)

The Machine runs inside WSL2 (Linux), next to OpenClaw's Gateway, which OpenClaw also recommends on Windows. In PowerShell:

```powershell
irm https://raw.githubusercontent.com/Salesflare/TheMachine/main/install.ps1 | iex
```

It installs WSL2 if needed (once as Administrator, then a restart), Ubuntu 24.04 (it asks for a new Linux user name and password), turns on systemd, installs Git, Python, Node.js 24 and Tailscale inside it (sign it in to your tailnet when it shows a link), gets The Machine and runs `./install.sh`, which continues as below. It also starts WSL when you sign in to Windows, so the service keeps running. Already use OpenClaw's Windows Hub with its own `OpenClawGateway` distro? It installs into that one. Run commands through WSL: `wsl -- bash -lc 'assistant doctor'`. Set `THE_MACHINE_DRY_RUN=1` first to only see what it would do.

## 2. Connect your sources

Right after the service starts, the installer walks through each source:

| Source | What happens |
|---|---|
| Outlook, Outlook Calendar | Asks the client ID of your own Entra app registration (Microsoft Entra admin center, App registrations, New registration; allow public client flows), then signs you in with a device code: **read-only** mail and calendar, plus sending if you say yes. Calendar reuses the same sign-in. |
| Gmail, Calendar | Picks your gog OAuth client (or helps you add one), stores a keyring password (typed or generated, mode 600) and signs you in with **read-only** Gmail and Calendar access. Calendar reuses the same sign-in. |
| Slack | Asks for your Maton API key (hidden), then finds the Slack connection in Maton (asks which one if you have several), your team, user and email itself. |
| Salesflare | Asks for an API key (hidden), then finds your user and team. |
| WhatsApp | Finds the wacli store, offers to link it (QR code), records which account it is and keeps it synced with a user service. |
| Android | Picks your paired Android phone from OpenClaw. For each source you connect that also has a phone app (Gmail, Slack, WhatsApp, …) it asks whether to skip that app's notifications; the default keeps them, since the phone may show other inboxes too. |
| Gateway log | Finds the Gateway's systemd unit. |
| Plugins | `github`, `stripe`, `intercom`, `x`, `linkedin`, `trello`, `rss`: not part of the installer; `assistant connect github` (and so on) asks for the few fields each needs. |

Setup verifies a supported read before reporting the account connected. A busy/rate-limited browser source can be saved for its first sync, explicitly unverified; saved configuration is not proof a read succeeded. Afterwards you see the first sync per source: how many signals came in and from how far back.

Skipped one? `assistant connect gmail` (or `slack`, `crm`, `whatsapp`, `android`, `github`, …) does the same any time, and so does the dashboard: Connections, **+ Add**. `assistant connect` on its own lists what is still missing.

### Pairing your Android phone

The Android source reads your phone's notifications through the OpenClaw Android app, paired with your Gateway as a node. The Gateway stays on loopback; the phone reaches it over `wss://` (OpenClaw refuses plain `ws://` outside your LAN):

1. Install OpenClaw on the phone from [Google Play](https://play.google.com/store/apps/details?id=ai.openclaw.app).
2. Give the phone a `wss://` way in. Pick one:
   - **Tailscale:** both on your tailnet, then `openclaw config set gateway.tailscale.mode serve` and restart the Gateway. `openclaw qr` then advertises that address.
   - **Your own reverse proxy with TLS** (Caddy, nginx), for a phone that is not on your tailnet. Forward only WebSocket upgrades on a long random path to the Gateway, let the proxy set `X-Forwarded-For` to the phone's address, and trust only that proxy:
     ```bash
     openclaw config set gateway.trustedProxies '["127.0.0.1","::1"]' --strict-json
     ```
     Without it the Gateway refuses proxied clients (`proxy_attribution_required`). With it, a phone coming through the proxy is never taken for a local client, which OpenClaw would approve without asking. A Caddy example:
     ```
     gateway.example.com {
     	@phone {
     		path /node-<long random string>
     		header Connection *Upgrade*
     		header Upgrade websocket
     	}
     	handle @phone {
     		rewrite * /
     		reverse_proxy 127.0.0.1:18789
     	}
     	handle {
     		respond 404
     	}
     }
     ```
3. Make a setup code and scan it in the app (add a Gateway, scan the QR, or paste the code). It is single use, works for 10 minutes and, with `--limited`, gives the phone no admin access to your Gateway:
   ```bash
   openclaw qr --limited --url wss://gateway.example.com/node-<long random string>   # or without --url for Tailscale
   ```
4. The official app is usually approved by the code itself. If it still waits, approve only your phone's request (platform `android`): `openclaw devices list`, `openclaw devices approve <requestId>`, then the same with `openclaw nodes list` and `openclaw nodes approve <requestId>` if a node request follows.
5. On the phone, allow notification access when the app asks. In the app's settings, turn on **Forward Notification Events**: the source reads that live feed, with a direct read every 10 minutes to catch gaps.
6. `assistant connect android` picks the paired phone. Check: `openclaw nodes list` shows it connected, and the dashboard's Sources show Android.

When it stops reading, the source says why: `android_offline` (the app is not connected; Android may have stopped it to save battery), `android_notifications_unavailable` (notification access was taken away), `android_feed_unavailable` (Forward Notification Events is off, or the bridge plugin is not loaded), `android_identity_mismatch` (the phone was unpaired: pair it again and run `assistant connect android`). WhatsApp, Telegram, Signal and Discord notifications never come through: OpenClaw leaves them out.

## 3. Open the dashboard

The installer offers to publish the dashboard on your tailnet (`tailscale serve`, at `https://<machine>.<tailnet>.ts.net:8443/`). No domain or certificate to set up, and only your Tailscale login can open it. If Serve is not enabled on your tailnet yet, it prints the link to enable it. On your phone, open the address it prints and add it to your home screen.

### Phone notifications (no Telegram needed)

The dashboard installs as a phone app and notifies you of each card, result, reminder and the weekly recap. With it, Telegram is optional.

- **Android (Chrome):** open the dashboard, tap ⋮ → **Add to Home screen** (or **Install app**). Open The Machine from your home screen, open **Settings** (the gear at the top) → **General** and tap the bell, then allow notifications.
- **iPhone/iPad (iOS 16.4 or newer):** open the dashboard in Safari, tap **Share** → **Add to Home Screen**. Open The Machine from your Home Screen (not a Safari tab: iOS only allows notifications there), open **Settings** (the gear) → **General**, tap the bell and allow.

Turn Telegram off or on later with `assistant telegram off` / `assistant telegram on <your Telegram user ID>`.

## 4. Tell the agent who you are

The installer offers a starter `USER.md` in your OpenClaw workspace with your name, companies and email (added under OpenClaw's template if that is all it has). Reviews and drafts read it every time.

Then it offers to fill in the rest from what you actually write: your agent reads your own recent messages once (nothing is sent) and drafts your role, languages, how you write on each channel (email, Slack, WhatsApp, …), what matters to you and what to leave alone. Then it reads your first and latest messages with your closest people and notes what each is to you ("cofounder", "accountant", "customer at Globex") and what their organisations are ("customer", "supplier", "possible acquirer"), where the messages show it, each with the line it read it in. Confirm them with ✓ (or all at once with **All N are right**) or change them in the Graph tab's Who's who (reviews learn from your changes), and `assistant roles` runs that step again later. You see the draft first; it's saved only if you say yes, and it never overwrites what you wrote. Redo it later with `assistant profile`, or edit `USER.md` yourself. Check: `USER.md` has an "Observed from my messages" section.

## 5. Preview, then turn it on

The installer offers a **preview**: your agent reviews the last 7 days once and shows the cards in the terminal and on the dashboard. Nothing is sent and nothing is done in your accounts. Skip it and the service starts that same preview by itself (the *first look*) once a source is connected and your main model can run (signed in or with a key): a quick pass over your newest mail as soon as it starts coming in (people waiting on you first), then the full week once the first import has settled. The dashboard shows how far it is, and each card as soon as it is ready.

If the cards look useful, say yes to **turning on reviews and card delivery** (Telegram and/or the dashboard app). Read [Security model](../README.md#security-model) first: **Do it** authorizes the offered task; prepare-ahead may start read-only preparation before a tap, but stops before its consequential final step. You can also do both later with `./install.sh --preview` and `./install.sh --activate`.

On a server without a screen, `./install.sh --browser-viewer` gives the assistant a browser you can watch and use from your own (sign in to LinkedIn or X there, or help when a task waits on a sign-in). On a desktop you are not always at, the dashboard can show its screen live instead (`browser_vnc`), so you can take over from your phone. A short note in the viewer says what the browser is for, which task is using it, and when one waits there for you to sign in. See [README](../README.md#sources).

## Check everything

```bash
assistant doctor
```

One pass over the service, the OpenClaw bridge, your Telegram approval, every source's last sync, reviews, the dashboard and `USER.md`. Every problem comes with the command that fixes it. It contacts none of your accounts; it only reads what is on this machine and checks that your dashboard address answers (your own domain counts, not just `tailscale serve`).

## Later

- Add or redo a source: `assistant connect <source>`. With one already there it asks whether to redo it or add another (a second company, a partner's calendar); the new one reuses your client, keys and settings and follows your naming. `assistant connect` on its own also shows accounts this machine has that nothing reads yet.
- Sending approved emails needs `gmail.send` on top of read-only access; add it with gog when you want it. Connecting never asks for more than read access.
- Company facts next to each organisation (industry, size, location): add a key for an enrichment service you use (Apollo, People Data Labs, Hunter or any that looks companies up by domain). Only domains are sent. When a look-up gets a company wrong, Edit on its page fixes the details or clears them. See [Organisation data](../README.md#organisation-data-optional-enrichment). With a service that knows people too, people with a company address get a job title, photo and location ([People data](intelligence.md#people-data)).
- Upgrade: inspect local changes, then `python3 scripts/release.py update` (it takes the newest signed release, and only if its signature checks out) and `./install.sh`. Preserve custom work; see [Operations](operations.md#update-and-rollback). Your config, data and secrets are kept.
- Backups: on your own computer nothing backs up by itself. A nightly encrypted [restic](https://restic.net) backup of your home folder is enough: it holds `~/.openclaw` (workspace, memory, settings) and The Machine's data, including the message key `message-content.key` (without it, the messages can't be read; moved to your keyring with `assistant message-key to-keyring`, it is in your keyring instead, for GNOME in `~/.local/share/keyrings`). Leave out what an install makes again (`~/.npm`, `~/.cache`, `~/.local/lib`, `~/.openclaw/npm`, `node_modules`, model files such as `*.gguf`), and keep the repository password somewhere else. `assistant export --keys` makes a one-file copy too.

## Moving from hosted to your own computer

Everything moves in one file: what The Machine knows, your settings, connectors you built, your workspace files (`AGENTS.md`, `USER.md`, memory) and who may talk to your Telegram bot.

1. On the hosted Account page, click **Export everything**. The file is always encrypted. Pick a passphrase if your keys should come along; without one you get an export code to save, and you connect those sources again. Download the file; it is deleted from your server after 24 hours.
2. On your own computer, follow steps 1 above to install The Machine.
3. Stop the target service first: `systemctl --user stop personal-assistant.service`. Close other database readers, then
   put it back: `assistant import the-machine-export-<date>.tar.gz.enc` (it asks the export code or passphrase).
4. Connect what the import lists. Always: your model, and Google or Microsoft (hosted access works through the hosting and does not move along: `assistant connect gmail --account <id>`, or `outlook`, and the same for the calendar). Without a passphrase also every source's key and your Telegram bot token.
5. `systemctl --user start personal-assistant.service`, then `assistant doctor`.

The same works between two of your own computers: `assistant export --keys` on the old one.
