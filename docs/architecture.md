# Architecture and reviews

[Documentation index](../README.md). Current reference; dated repair evidence lives in [History](history.md).

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
| `retention.py` | Nightly housekeeping: old sync history and preparation logs go, free space goes back to the disk, local database copies rotate |
| `openclaw-plugin/` | `personal-assistant-bridge`: the `assistant_signals` tool, Telegram buttons and replies, Android feed capture, review/preparation guards |
| `*.mjs` | Small Node helpers that call the OpenClaw Gateway (`openclaw-root.mjs` finds the installed package) |
| `integrations/browser-lease.py` | Shares the OpenClaw browser between concurrent Do it runs |
| `browser_viewer.py`, `deploy/browser/` | Optional: a browser of its own on a virtual display that you watch and use from yours (noVNC, 127.0.0.1 only), and bringing a tab to the front on a desktop |
| `mail_attachments.py` | Email attachments a preparation saves: encrypted on disk with the message key, a private readable copy for the run, encrypted in place after runs that saved them with `gog` |
| `browser_login.py` | The sign-in dialog: fills a sign-in page in the assistant's browser with what you type on the dashboard, and keeps saved sign-ins encrypted |
| `integrations/browser_cdp.py` | Talks to a browser's DevTools address directly, for the LinkedIn and X readers when one is set |
| `AGENTS.md`, `PREPARATION.md` | Instructions the plugin gives the agent for reviews and for Do it runs (templates; see below) |
| `source-notes/` | How to read each connector, one file per service; a review or Do it run gets only the notes for the sources this Machine has connected |
| `install.sh`, `installer.py` | Install, upgrade and uninstall |
| `config.example.json` | Every configuration key with example values |
| `embeddings.py` | Optional related signals by meaning, through a local embedding server |
| `graph.py` | People and projects: a living graph rebuilt from signals, card decisions, project labels and merges |
| `crm.py`, `crm_import.py`, `crm_fixes.py` | The Graph as a CRM: owners, change history, filters and sorts done on the Machine, merged organizations with several websites, and CSV import with undo, easy fixes shown before and after, and a check by the owner's model (an `import` agent run that sees headers, a few values and unclear cells) |
| `crm_fields.py` | Custom fields per record type (text, number, date, choice; values sealed at rest) and tags, kept through rebuilds and merges |
| `deploy/` | Example systemd units (the service, an optional local embedding server) and Caddyfile |
| `test_*.py`, `test_*.mjs` | Offline tests |

Python's standard library and SQLite are enough: no database server, queue, web framework or extra scheduler. Runtime data lives in `~/.local/share/personal-assistant/`: `config.json`, `events.sqlite`, tokens and receipts.

**Data model.** The main tables are `events` (with an `event_search` full-text index), `checkpoints`, `sync_activity`, `analyses` (reviews), `review_runs`, `suggestions`, `decisions`, `deliveries` (the Telegram outbox), `preparations` and `action_jobs` (approved sends). Provider IDs deduplicate events per account; a changed event gets a new version and becomes reviewable again. Each source page commits its events and next cursor in one transaction, so a crash replays the page instead of losing or duplicating it.

**Boundaries.** Every account has an explicit ID and `boundary` (`work:Acme`, `personal:phone`, `owner:system`…). Every read names its accounts explicitly; app names and chat labels never decide which company an item belongs to.

### On the device

The dashboard opens from the device and then refreshes. Until the first real data is known it shows a quiet outline of the cards (as many as last time, never their content); "All clear" shows only when the Machine itself says there are no cards, never from a kept copy.

- **The page.** `sw.js` keeps a copy of the dashboard page (the app shell) and the brand images, and opens the app from them. The page never holds the CSRF token (the server leaves it out, and the service worker takes out one an older page still had): the page gets it from `api/session` (a change waits for it). After opening, the service worker asks for the page again with its ETag (a short 304 when nothing changed). A new release drops the copy before the page reloads.
- **The data.** The last cards (`api/status`), the open card's chat and the Graph's overview and the first page of each list are kept in IndexedDB, encrypted with AES-GCM under a non-extractable key that never leaves that browser's IndexedDB, each row bound to its name and owner. Keys that look like secrets (`csrf`, `token`, `*_token`, `password`, `*_key` and the like) are removed first. Rows older than 7 days are dropped, one row is at most 3 MB and all of them at most 8 MB (oldest first).
- **Compressed.** The page, `sw.js`, `api/status` and the other data answers go compressed (Brotli when the Python module is there, else zstd on Python 3.14+, else gzip; the browser's `Accept-Encoding` decides), with `Vary: Accept-Encoding` and an ETag per coding that still gives a 304. Only answers without a secret are compressed: a secret compressed next to text an outsider can influence (an email's words on a card) could be guessed from the size (BREACH). So the page carries no CSRF token, and `api/session`, file downloads, the browser view and streams go as they are. `api/status` also leaves out what the cards never read (a finished review's signal list, saved pre-close state).
- **Whose.** The page (and `api/session`) carries `cache_scope`, an HMAC of the Machine's token and its owner that says nothing about either. A different scope (another owner, or a new Machine) wipes what the device kept.
- **Wiped** on Sign out (before the sign-out request goes), when the session is gone (the sign-in gate's redirect or a 401/403 on `api/status` or `api/session`, also seen by the service worker), and on another owner. Nothing is kept inside a frame (the site's demo).
- **Stale.** A kept copy shown while the Machine can't be reached says so at the foot of the sidebar: "Offline · updated 10:42".

The copy protects against someone reading the browser's files (a lost laptop's disk, a backup). Anyone using the unlocked browser profile itself sees what the dashboard shows anyway, so on a shared computer sign out when done.


## Reviews

The service starts reviews itself (`review_worker.enabled`). When at least `min_signals` wait, or the oldest has waited `max_wait_minutes`, it starts one isolated turn of your main agent, `agent:main:assistant-review-signals-<run>`, with message delivery off. A newly connected mailbox (Gmail or Outlook, on a new install or added later) does not wait for that: its first review starts as soon as its first import of the last 7 days is in, as one review (none starts halfway through the import, and never later than `max_wait_minutes`); the dashboard says "First review starts when your mail is in" meanwhile. The plugin adds `USER.md` and `AGENTS.md` to that turn. The agent claims up to 120 signals, reads them through the `assistant_signals` tool, checks decisions, earlier suggestions and related evidence, saves suggestions with `when`/`due` timing, and finishes the analysis. One run at a time with a gap between runs; failed runs back off. The dashboard (Connections, Signal reviews) shows a running review at the top: how far it is (12 of 40 signals), from which accounts, and its latest step from its own tool calls ("Reading 20 emails", "Looking up Globex", "Suggesting: …", "3 suggestions so far"; earlier steps fold away; never message text or search words), updated every few seconds; then when the next one starts. Finished runs move to Recent runs. Unfinished claims transfer safely to the next run, and the service never restarts the Gateway.

**One card per matter.** A new signal about something that already has a card updates that card instead of starting another one. A later message in the same email or Slack thread finds the thread's card by itself; across threads the review judges sameness by meaning (the same people and the same ask or commitment) and passes `card_id`. When open cards share people or the project with a new suggestion, `suggest` asks the review which card it is (`card_id`) or whether it's a different matter (`separate:true`) before saving. An open card gets the new evidence, title and body; a snoozed one, or one waiting for a reply, comes back to Outstanding with one new notification. A done or dismissed card reopens only when the review says it needs you again (`reopen:true`); otherwise the message is kept on it as evidence. Each update is in the card's history, and the old key keeps pointing at the card. A title The Machine wrote stays current: the review update or a preparation result gives it a new one when the situation changed (not for a mere rewording). A title you chose yourself (`title_source` `user`) is never replaced.

**Things that slipped.** Each review prompt also gets up to five "Possibly slipped" lines of each kind: your own sent emails whose new text asks something (a ? in a link doesn't count) and whose thread has had nothing since, 4-21 days later, but only in threads the other person started or to people who had written to you before you started the thread, so your outreach never shows up; and questions addressed directly to you (at most two To recipients, not Gmail's Updates/Promotions/Social/Forums categories, from someone you have emailed before) with no reply from you in the thread after 2-14 days. Three or more emails sent the same day with the same subject pattern count as outreach too and are left out. Anything already on a card is skipped. The `slipped` table records which review got each one; it's offered again only if that review didn't save within six hours. The review checks for an answer elsewhere before making a card.

### Promises

Reviews record every promise they find in a message, with `promise`, instead of making a card: one you made in something you sent (`mine`), or one someone made to you (`theirs`, in their message or in your ask they took on), with who, what and a date when one was said. Most are never spelled out and look different per person, language and relationship, so the review judges what a message means in its conversation (reading the request in `earlier_in_conversation`), with examples as a guide rather than words to look for. Each goes into the `promises` table, once per message, person, direction and distinct promised action. A plan made together (their "let's do it Sunday" and your yes) reads as a promise both ways: while one with that person for the same action is open, the second is not recorded again, and the open one becomes yours, marked as made together. The same open promise stored twice from before is kept once. Nothing shows up on Telegram yet.

- **When it's checked:** yours on the morning it's due (at `digest_at`, never sooner than three hours after it was recorded), theirs the morning after their date. Without a date: yours after 3 days, theirs after a week.
- **Closing by itself:** someone else's promise made by email closes when that sender replies in the original thread (sender-only fallback if no thread was recorded). Chats run on, so a later chat message is no sign it was kept: those stay open for the review or the check. Their new message is reviewed as usual, so a delay can become a new promise. Every review prompt lists the open promises, and the review closes one (`kept` or `dropped`, with the reason) or moves its date when a later message shows it.
- **The card:** a promise still open when it's checked becomes a card (`promise:<id>`), prepared ahead under the usual limits: for yours the thing you promised, for theirs a short nudge. If they answer while the card is on its way, it closes. At most 5 such cards a day. With reviews off (a new install, its first look included) a due promise waits: no card and nothing prepared until reviews are on.
- **Where you see them:** the dashboard's Suggestions tab has a **Promises** list (You promised, Promised to you) with Done and Drop for open ones. Every promise link (that list, the Calendar, Open between you on a person, organization or project page) opens the promise's own page (`#promise/<id>`, read from `GET /api/promise?id=`): its state, the message it came from, the people, organization and project of that message, its card as a link, and a history from `promise_log` (kinds and dates only, never message text). Change date there is `promise_due`, owner only. Also, `assistant promises` lists them, and the weekly recap counts kept and open ones.

Your decisions and written reasons are summarized into every review prompt, and lasting facts you state are saved (`remember_fact`) and listed, removable, on the dashboard. How you write is kept **per channel**: a short block (up to five lines) each for Email, Slack, WhatsApp, LinkedIn, X and any other channel, plus Everywhere, under Settings → **How you write**, where you can edit or remove them. A voice study reads a sample of your own sent messages per channel (a few dozen emails across languages) and writes each block: greetings and sign-offs per language, length and structure, tone per kind of person, habits, with your style notes worked in. It runs once on its own and again every 30 days, and never touches a block you wrote yourself. A style note that holds beyond one card (length, greeting, sign-off, language, capitals, punctuation, links) is worked into that channel's block instead of piling up next to it, and a review compares drafts you edited, or answered yourself in the same thread or chat instead of tapping Send, with what you wrote, and folds lasting habits into that block. Such an update keeps the rest of the block: it can't shrink it to one note. Every preparation gets all the blocks (its card's channel marked), your own recent messages on that channel within the card's source accounts (the same conversation first) as examples, and your earlier edits on that channel within the same source accounts. How a kind of task is done (what to include, where things go) stays a **draft rule**. A preparation doesn't get everything learned: while the list is short it gets all of it, after that the facts and draft rules learned on that card plus the ones closest in meaning to it (with [local embeddings](intelligence.md#related-signals-optional-local-embeddings) on, else the newest). Now and then a review condenses the list: notes that say the same thing or contradict each other become one (the newer wins), a note about how you write on one channel moves into that channel's block, and the originals stay behind it. Feedback → **What I've learned** lists it newest first, grouped as How you write, Facts and How to do things, with "Merged from N notes" and **Undo merge** on each condensed one. You can edit or remove each fact and rule there. A card's chat shows what its notes taught, read from the database (not from the reply): a quiet **Saved to what I’ve learned** line under the reply, with **Edit** and **Undo**,. Five or more notes about one kind of task can become a skill, on a card you decide ([skills](skills.md#from-your-notes)). Each Do it result says whether it's finished or what it needs from you, and the run sorts your Change notes (style, missing fact, keep going, new ask). The Feedback tab shows the split, the hit rate per day or week, and markers where the rules changed.
