# Adding a source

A source (connector) reads one account of one service and stores what it finds as **events**. Reviews then decide what deserves a card. A new connector must not add AI interpretation or unrequested writes: no replies or marking as read. The existing opt-in Salesflare account-join exception is not permission to add writes to another source.

There are two ways to add one:

- **A connector plugin**: one Python file in `connectors/`, named after its service. No changes to `assistant.py`. This is the way for most sources. [`connectors/rss.py`](../connectors/rss.py) is a complete example; [`github.py`](../connectors/github.py) (a CLI) and [`stripe.py`](../connectors/stripe.py) (an API with a key, filtering what's worth storing) are two more, [`trello.py`](../connectors/trello.py) pages an API with cursors and an overlap window, [`intercom.py`](../connectors/intercom.py) reads a search API in frozen windows and allows only its read routes, and [`x.py`](../connectors/x.py) and [`linkedin.py`](../connectors/linkedin.py) read through the signed-in OpenClaw browser.
- **A built-in source**: a branch in `sync_source` in `assistant.py`, for sources that need deeper integration (realtime threads, special review handling).

This guide covers the plugin.

## 1. The account in `config.json`

```json
{"id": "work-feed", "service": "rss", "transport": "http", "boundary": "work:Acme",
 "url": "https://example.com/feed.xml", "poll_seconds": 1800}
```

- `id` is the account's own ID. Its checkpoint, health and events are keyed by it.
- `service` picks the connector: `rss` loads `connectors/rss.py`.
- `boundary` (`work:<Company>` or `personal:<name>`) keeps signals apart in reviews and on the dashboard; only `work:` ones count as work (work cards wait for a work day). In `SETUP`, a bare `work` makes `assistant connect` ask which company.
- Any other keys (`url` here) are yours to read in the connector. Keep secrets out of `config.json`: name a credential (`"credential": "stripe"`) and read it with `helpers.credential`.

## 2. The module

```python
KINDS = {'rss.item': 'feed'}          # event kind -> short label for cards and reviews

def sync(store, config, account, helpers):
    state = store.checkpoint(account['id']) or {}
    ...                                # read new items since state
    store.commit_page(account['id'], state, events)
    return {'mode': 'incremental', 'events': len(events)}
```

The service calls `sync` every `poll_seconds` (default 300) in the account's own thread. `helpers` has:

| Helper | Use |
|---|---|
| `helpers.SourceError(category, status=None, retry_after=None)` | Raise on any failure. `category` is a short label like `rss_http`; it's shown on the dashboard, so never put message content or secrets in it. `retry_after` (seconds) delays the next try. |
| `helpers.iso(timestamp)` | A Unix timestamp as the UTC ISO string `occurred_at` expects. |
| `helpers.now()` | The current time as that string. |
| `helpers.local_tz()` | The owner's time zone (config `timezone`), for things like active hours. |
| `helpers.credential(config, name)` | The secret named `name` in `config['credentials']` (or systemd's `LoadCredential`), as text. Use it for API keys instead of reading files yourself. |

### Setup: `assistant connect <service>`

Setup verifies a real read before saving the account configuration. A plugin can define `check(config, account, helpers)`, returning `None` on success or raising `helpers.SourceError` with a content-free error label. The check must not write source data or advance checkpoints. Existing `Source.verify()` or `Source.profile()` methods are also supported. Without a supported read check, setup reports that verification is unavailable rather than calling the account connected. A source that answers it is busy or rate limited (`x_browser_busy`, `linkedin_browser_busy`, `x_rate_limited`) is saved anyway with a note; the first sync reads it.

Declare `SETUP` so the owner can add an account without editing JSON:

```python
SETUP = {'label': 'RSS feed',                          # name in prompts, doctor and next steps
         'account': {'id': 'feed', 'service': 'rss', 'transport': 'http', 'boundary': 'work', 'poll_seconds': 1800},
         'what': 'Posts from the feeds you follow.',     # one line: what it reads, shown next to the source in setup screens
         'note': 'Shown before the questions, e.g. where to find a key. Give full https:// links; setup pages make them clickable.',
         'ask': [('url', 'Feed URL (RSS or Atom)')],     # (key, prompt[, default]); defaults can use '{other_key}'
         'detect': {},                                   # key -> command whose output is the default, e.g. ['gh', 'api', 'user', '--jq', '.login']
         'secret': None,                                 # a prompt here asks for a key without echo into the `credential` file (mode 600)
         'hints': {},                                    # error prefix -> plain fix, e.g. {'myapi:HTTP_401': 'MyAPI rejected the key'}
         'browser': None,                                # for sources read through the shared browser: its site ('linkedin', 'x'); an error ending in _signed_out then shows 'Signed out' with a way to sign in again
         'family': None,                                 # e.g. 'MYAPI_': offer keys named MYAPI_…KEY/TOKEN the owner already saved
         'maton': None}                                  # truthy offers reading through Maton instead; the connection is found with the key ('maton_app' names the Maton app if not the service)
```

`assistant connect` adds the account from `account`, asks each `ask` key (empty answers stop it), stores the secret, verifies a real read, saves `config.json`, restarts the service and shows the first sync. With `credential_variable` in `account`, the secret is written as `VARIABLE=value`, and other lines in that file stay as they were. When the owner already has an account of this service, the new one starts from it instead of `account`, without the identity keys (`id`, `email`, `handle`, `login`, `url`, `connection_id`, … and your first `ask` key). `assistant doctor` treats a missing `ask` key or credential file as not set up. A test checks every connector has a complete `SETUP`, including its one-line `what` (short enough to fit a tile in two lines). A built-in source's line is in `WHAT` in `onboarding.py`. The Machine reports each line with its sources, so the dashboard's Add tab and a hosted setup page show it under the name.

## 3. Events

Use this common event shape; optional metadata is source-specific. Check parsing/validation in the store rather than assuming every provider payload already has this shape:

| Field | Meaning |
|---|---|
| `account_id` | `account['id']`. `commit_page` refuses events for another account. |
| `source_id` | Stable ID of the item in the source. The same item must always get the same ID; hash it if it's long or contains personal data. |
| `kind` | `<service>.<type>`, e.g. `rss.item`. Add it to `KINDS`. |
| `occurred_at` | When it happened, UTC ISO. |
| `title` | A short line (max 1000 chars): subject, chat name, item title. |
| `body` | The text (max 40000 chars). Plain text; strip HTML. Omit one-time codes and passwords. |
| `metadata` | JSON with what reviews need: `source_url` (an https link to open the original, shown as Open), sender, thread or conversation IDs, `from_me` for the owner's own messages. |
| `deleted` | `True` when the item was removed at the source. |

Message text goes in `title`, `body` and metadata only. With [message protection](configuration.md#message-protection) on, `commit_page` seals all three; only the lookup keys `thread_id`, `from`, `to`, `labels`, `from_me`, `user_id`, `channel_id`, `chat_id`, `conversation_id` and `notification_key_hash` stay readable, so keep text out of those, `source_id`, `kind` and your cursor. `MESSAGE_SEAL_AUDIT=1` makes `commit_page` refuse an event that does (`test_message_seal.py` runs every connector suite that way). Store only through `commit_page`, never with your own SQL.

When an item changes, send it again with the same `source_id`: the service stores a new version and reviews see it again. Unchanged items are skipped by content hash, so rereading is cheap and safe.

## 4. Checkpoints

`store.commit_page(account_id, state, events)` stores the events **and** the new `state` in one transaction. If the process dies mid-read, the next run starts from the old state and rereads the same page; nothing is lost or doubled.

- Put your cursor in `state`: a page token, a `since` time, an ETag. It's JSON, read back with `store.checkpoint(account_id)`.
- For a long backlog, commit page by page, each with the cursor of the next page.
- When the provider explicitly rejects an expired cursor, record that expiry and replay the supported bounded window (`config.get('bootstrap_days', 7)`) with deduplication. Do not clear a valid retained continuation because a request timed out or was rate limited; disclose any unrecoverable coverage gap.
- On the first run, read only that recent window, not years of history.

## 5. Dashboard and noise rules

- The account appears under Sources with its health, last read and errors. It gets a generic icon unless you add one to `ICONS` in `dashboard.html`.
- `exclude` rules (`senders`, `titles`, `text`) work on plugin events too. See Noise rules in the README.
- Every event opens in the dashboard's reader with its title, sender (`sender_display` or `from`, "You" when `from_me`) and body, so a readable plain-text `body` is all it needs.
- To put the people in your events on the Graph tab, add a branch for your `kind` to `touches()` in [`graph.py`](../graph.py): it returns who took part, with a stable identifier (`email:`, `wa:`, or your own prefix), a name and their role (you wrote to them, they wrote to you, a meeting, or just present). Without it the source works as before; it just adds no people.

## 6. Tests

Test with saved fixtures, not live accounts. [`test_connectors.py`](../test_connectors.py) shows the pattern for `rss.py`: it fakes the HTTP response and checks

- the events and their fields,
- that a second run with the same feed adds nothing,
- that `304 Not Modified` keeps the checkpoint,
- that errors raise `SourceError` with a safe label.

After fixture tests, the owner can run `assistant connect <service>`: setup verifies the account, restarts the service and shows its first read. A busy source remains explicitly unverified until that read succeeds. Check it later with `assistant sources`, `assistant doctor` or on the dashboard.

## Checklist

- [ ] Read-only credential for exactly this account, with the least access that works.
- [ ] Stable `source_id`; `kind` listed in `KINDS`.
- [ ] Message text only in `title`, `body` and metadata, never in ids, lookup keys or the cursor.
- [ ] Events and cursor committed together; replays don't duplicate.
- [ ] Failures raise `SourceError` with a content-free label.
- [ ] Fixture tests for parsing, replays, expired cursors and errors.
- [ ] `SETUP` with what `assistant connect` should ask, plus a supported read-only setup check.
