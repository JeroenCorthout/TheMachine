# Configuration

[Documentation index](../README.md). Current reference; dated repair evidence lives in [History](history.md).

## Configuration

`config.json` is plain JSON; paths should be absolute (the installer expands `~`). Restart the service after changes. Top-level keys:

| Key | Default | Meaning |
|---|---|---|
| `owner_name` | — | Your name; preparations sign emails with your first name |
| `owner_emails`, `not_owner_emails` | `[]` | Addresses that are always yours, and ones that never are. The Machine also finds your own addresses itself ([Your addresses](intelligence.md#your-addresses)); these override it |
| `timezone` | `Europe/Amsterdam` (the installer sets this computer's) | IANA zone for your work hours, digests, snoozes and recaps (the dashboard's Work hours, or `assistant hours --timezone`) |
| `telegram_owner` | empty | Your Telegram chat ID; the only chat that gets cards and whose taps count. Empty: Telegram off, cards go to the dashboard app's notifications |
| `telegram_account` | `default` | The OpenClaw Telegram account (bot) that sends cards, when you run several; the installer sets it |
| `tailscale_owner` | — (required) | The Tailscale login allowed to open the dashboard |
| `dashboard_host` | — (required) | Host name the dashboard answers on, with `:port` if not 443 (the installer uses `<machine>.ts.net:8443`) |
| `dashboard_path` | `/` | Path the dashboard is served under on `dashboard_host` (`/assistant/` with Tailscale Serve `--set-path`) |
| `dashboard_aliases`, `dashboard_alias_path` | `[]`, `/assistant/` | Extra host names and their path |
| `port` | `8787` | Local API and dashboard port on 127.0.0.1 |
| `database` | — | SQLite file; tokens and keys sit next to it |
| `message_protection` | `true` | Encrypts message contents in the database; `false` opens them again ([below](#message-protection)) |
| `credentials` | — | Name → file path of each secret: `api-token`, `owner-token`, `gog-password`, `google-broker` and `microsoft-broker` (with a broker), and one per source credential. systemd `LoadCredential` with the same names takes precedence |
| `app_dir` | `~/.openclaw/workspace/tools/personal-assistant` | This checkout; the plugin reads `AGENTS.md`/`PREPARATION.md` here and protects it from edits |
| `fix_dir` | `~/.cache/the-machine/fixes` | Where Do it clones The Machine to fix it ([The Machine fixes itself](../README.md#the-machine-fixes-itself)) |
| `self_test`, `self_test_seconds`, `self_fix_push` | `scripts/test.py`, `1200`, `true` | The test command Merge now runs in the fix (a list), its time limit, and whether a merged fix is pushed |
| `self_fix_auto`, `self_fix_auto_push` | `true`, `false` | Small proven fixes to its own errors [merge by themselves](../README.md#the-machine-fixes-itself), with Undo merge for 24 hours; whether those are also pushed upstream |
| `openclaw`, `node` | `openclaw` / next to it | CLI paths for the Node helpers |
| `openclaw_package_dir` | `<node prefix>/lib/node_modules/openclaw` | Installed OpenClaw package (Gateway SDK) |
| `openclaw_state`, `agent_db` | OpenClaw defaults | OpenClaw state and agent transcript databases (read-only: Telegram receipts, progress steps) |
| `gog`, `wacli` | `gog`, `wacli` | Binaries for Google and WhatsApp |
| `google_broker` | — | `url` of a Google token broker, for accounts with `"transport": "broker"` (below). Their refresh tokens stay on this machine, in `mail-grants.json` next to the database, sealed with the message key (docs/sources.md). Disconnecting revokes a Google one at Google; one Google doesn't confirm is kept sealed only to try again (hourly, and when the control plane asks), and `revocations.json` keeps a receipt per disconnect (no token) for Connections |
| `microsoft_broker` | — | `url` of a Microsoft token broker, for Outlook accounts with `"transport": "broker"` |
| `zoom_broker` | — | `url` of a Zoom token broker, for Zoom accounts (meeting notes, [sources](sources.md)) |
| `zoom_transcripts` | `true` | Also store Zoom transcripts, next to the summaries |
| `microsoft_graph` | — | `client_id` and `tenant` (`common`) of your own Entra app, for Outlook accounts with `"transport": "graph"` |
| `salesflare_server` | — | `api_base` and `app_base` a new Salesflare account starts on instead of Salesflare's own (a hosted machine's control plane sets it for a team on staging). Only https addresses on salesflare.com subdomains count. An address typed when a key is rejected must be https too |
| `heartbeat_job_id` | `""` | Optional OpenClaw cron job ID of your main heartbeat, for its health line |
| `manage_url` | `""` | Optional https page where this install is set up (a hosted machine); the dashboard's Settings, **Account** tab reads the account there and opens its pages, and Add a connection links to it |
| `needs_you_file` | unset | Optional JSON list of `{id, text, url, action}` notes from a hosting layer; the dashboard shows them under Needs you, with sources that are not fully set up and connections that need signing in again |
| `browser_url` | unset | Optional page that shows the assistant's browser (a hosted machine's viewer, or `./install.sh --browser-viewer`'s); a card waiting on the browser links to it |
| `browser_cdp_url` | `http://127.0.0.1:9222` | That browser's DevTools address on this machine; **Open the browser** brings the tab a task uses to the front through it (also when it opens a viewer), and a Preparing card's live look at its tab is taken through it |
| `browser_vnc` | unset | Optional VNC server showing this computer's screen, as a unix socket path or a `127.0.0.1:port`; the dashboard then shows that screen live at `/browser` (see [browser setup](sources.md)) |
| `browser_novnc` | `~/.local/share/machine-browser/web` | The noVNC files the dashboard serves for `browser_vnc` (`python3 browser_viewer.py web DIR [NOVNC]` builds them) |
| `browser_lease_script` | `integrations/browser-lease.py` | Lease helper Do it runs use for the shared browser |
| `delivery_enabled` | `false` | Send cards to Telegram (leave off while you test) |
| `delivery_batch_size` | `5` | Cards per delivery pass |
| `digest_at` | `08:30` | Morning digest time; also when work held over a day off goes out |
| `currency` | `EUR` | The currency of project values on boards and lists (an ISO code, like USD or GBP) |
| `country` | from `timezone` | Two-letter country a CSV import puts local phone numbers in (+32…) and reads dates like 03/04 by |
| `import_check` | `true` | Let your model check a CSV import's columns and unclear cells; `false` keeps the guess from the headers |
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
| `place_search` | on | The Calendar's **Where** also asks Photon (photon.komoot.io, OpenStreetMap data) for addresses, with only the typed text, at most once a second. `false` keeps it to your own places |
| `enrichment` | off | Optional [organization data](../README.md#organization-data-optional-enrichment): `provider` or `url`, `headers`, `key_file`/`key_env`, `fields`, `per_day` (50); for [work contacts](intelligence.md#people-data) `people_url`, `people_fields`, `people_per_day` (50) |
| `accounts` | `[]` | Sources, below |

Every account has `id`, `service`, `transport` and `boundary` (`work:<Company>` or `personal:<name>`), plus optional `boundary_by` (`owner`: you marked it Work or Personal yourself, in the dashboard or on the setup page; leaving a Work Fleet follows it, see [Fleet](fleet.md#leaving-a-work-fleet-with-your-machine)), `scope` (`work`/`personal`, overrides the label the boundary implies), `poll_seconds` and `exclude` (noise rules, below). `config.example.json` has one complete example per built-in source; each plugin in `connectors/` (GitHub, Stripe, X, LinkedIn, RSS) shows its own at the top of its file.

**Noise rules (`exclude`).** A list of rules; an item matching all conditions of any rule is never stored, and stored copies are removed. Conditions: `channels` (Slack channel names), `bots_only`, `packages` (Android package names), `chats` (WhatsApp chat IDs), `senders` (email address substrings), `routine_recurring` (unchanged instances of recurring calendar events), `titles` (exact titles) and `text` (regexes over title and body).

**Built-in noise rules**, on top of yours (`"default_exclude": false` on an account turns them off):

- **Android:** the phone's own status notices ("… is displaying over other apps", "Charging this device via USB", USB/wireless debugging, serial console; storage and security warnings still come through). App notifications stay even if one account from that service is connected: the phone may also use personal mailboxes or other workspaces not collected here. When you connect a source that also has a phone app (Gmail, Slack, WhatsApp, …), setup asks once whether to skip that app's notifications; yes adds an `exclude` rule with its `packages` to the Android account, which you can also write yourself.
- **Calendar:** unchanged instances of recurring placeholders: Lunch, Block(ed), Busy, Focus (time), Deep work, Home, Office, Commute, Gym, Stand up / Standup / Daily standup, Happy birthday!. A moved or one-off "Lunch with Ann" still counts.

## Message protection

By default the service encrypts what messages say, from every source, in its database:
subjects, bodies, attachments' names, quoted text and other metadata of email, chats, notifications, tickets,
transcripts and calendar events, plus the text derived from them (card text, drafts and their edits, preparation
tool calls and results, Fleet and Network questions and answers, promises, the line a role was read from, and
the messages a profile or roles run reads) and the sign-ins saved with **Save for next time**. The key is
`message-content.key`, next to the
database, readable only by the service user. The dashboard, search, embeddings and reviews work as before: search
uses a keyed hash of each word.

**Back up the key with the database.** A backup of the data folder (or your home folder) holds both. Without the
key, the messages in the database and its copies can't be read by anyone, you included. `assistant doctor` and the
dashboard say when the key is missing or readable by others.

A hosting layer can keep the key off the disk: `message-content.key` is then a link to a file in memory (under
`/run`) that the host puts there at each start. Hosted Machines work this way: the control plane keeps the key,
so the disk and its backups never hold it, and deleting the Machine deletes the key, which leaves those backups'
messages unreadable. Until the key arrives the service waits, and says so; it never makes a new one.

### The key in your keyring (Linux desktop)

On a Linux desktop the key can live in your keyring (the Secret Service: GNOME Keyring, KWallet, KeePassXC) instead
of the file, so a copy of the data folder holds no key. It is opt-in:

```
assistant message-key status       # where the key is kept, and whether the keyring looks ready
assistant message-key to-keyring   # move it there
assistant message-key to-file      # move it back to message-content.key (--keep-in-keyring leaves a copy there)
```

Or set `"message_key_store": "keyring"` in config.json: the service then moves it at a start, once the keyring checks
out. It needs `secret-tool` (in libsecret; on Ubuntu `apt install libsecret-tools`) and `busctl` (systemd).

The move tests the keyring first (stores, reads back and removes a throwaway secret), stores the key, reads it back,
checks that it opens your sealed messages and saved sign-ins, and only then overwrites and removes the file.
`message-content.key` becomes a link to a file in memory (`$XDG_RUNTIME_DIR`, emptied at every reboot), and
`message-content.keyring` next to the database says which keyring item holds the key (nothing secret). At each
start the service reads the key from the keyring once and keeps it in memory. A locked keyring is never asked to
unlock: while it is locked or not running yet (at boot, before you sign in), the service waits, says so in its log
and on the dashboard's address, and starts by itself once it can read the key. It never makes a new key.

Moving to another computer works as before: an export opens everything and holds no key. A keyring without a
password (some desktops with automatic login make one) keeps its secrets unencrypted on the disk: the key is then
out of the data folder, but not encrypted at rest. `assistant message-key status` says so.

What stays readable, for support and to improve The Machine: feedback, facts and rules, skills, profile and notes,
card states and times, errors, sync health and logs. `python3 learnings.py --database ... --workspace ...` exports
exactly that, without the key.

Turning it on or off rewrites the database once at start, when no review, preparation or profile turn is running
(while one runs, the service waits and restarts itself once it's done). A copy is made first, encrypted with the
message key in a private folder next to the database; then the rewrite in one step, a check that every message opens
(if not, the copy is put back at once), and a wipe of the free space. The copy is then overwritten and removed. A
plain copy an earlier version left behind (`events.sqlite.before-protection`) is removed the same way at the next
start, once the database checks out; `assistant doctor` says when it can't be.
Installs from before it was the default are encrypted this way at their first start after the upgrade. To opt out,
set `"message_protection": false`. Exports open the messages inside the encrypted export and leave the key out; an import is encrypted again
with the new Machine's own key at its next start, so moving between your computer and hosted keeps working. The key does not stop someone with root on the machine, who can
read it; it stops the database file, its copies and tools without the key. Also sealed: a phone notification waiting
for the app (the Gateway's plugin seals it with the same key) and Network replies (one waiting to go out is sealed end
to end for the asking Machine). The live look at a preparing card's browser stays in memory only.

**Email attachments** a Do-it run saves (`assistant attachment --save`, Gmail and Outlook) are encrypted with the same
key in the job's attachments folder (`data/assistant-attachments/<job id>/` in the OpenClaw workspace). The run reads a
private copy (in the user's runtime folder, in memory, or else `open-attachments/` next to the database; mode 700)
that is removed once the run has ended; a later run asks for one with `assistant attachment --open <path>`. Drafts
attach them, sends and the dashboard read them, opened with the key; the draft store keeps its copy encrypted too.
Attachments a run saved with `gog gmail attachment` itself, and those saved before this, are found in the run's own
tool calls and encrypted in place once it has ended. Files a run makes itself (a filled-in form, a CSV, a preview)
stay as they are. Exports hold them opened, like the messages; with protection off they are opened again in place.

Not covered, because other programs own them: OpenClaw's session transcripts and caches (review and preparation turns
include message text; OpenClaw's `session.maintenance` settings, such as `pruneAfter` and `maxDiskBytes`, bound what it
keeps), WhatsApp's `wacli` store (`wacli store cleanup --days N` drops chats quiet that long) and browser profiles.

