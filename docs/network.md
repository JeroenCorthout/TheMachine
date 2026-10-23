# The Machine Network

Machines talk to each other (Fleets, Friends) through The Machine Network. This page says what the Network can see,
how messages stay reliable, and where it's going.

## What the Network sees

- **End-to-end encryption.** Your Machine makes its own keys (X25519 to receive, Ed25519 to sign) and keeps the
  private ones in `network-keys.json` next to its database, readable only by you. It publishes the public keys to the
  Network directory. Questions and answers are sealed for the receiving Machine and signed by the sending one, bound to
  who sends what to whom, so the Network can't read them, change them or replay them elsewhere.
- **Key-change warnings.** Your Machine pins a teammate's or friend's keys the first time it sees them. If they change
  (a reinstall, a new computer), the Fleet tab shows a warning with the old and new fingerprints. If their keys
  disappear, nothing is sent to them until they're back.
- **Local-first.** History stays on your Machine. The Network keeps a sealed message only until it's delivered and
  answered, and clears answers once your Machine acknowledges them.
- **What isn't sealed:** who is in which Fleet or friends group, who asks whom, the kind of question, its status and
  times. The Network needs those to route messages.
- **Handling claims** (who of you handles an email several teammates got) are hashes of the Fleet's secret and the
  email's Message-ID. The claim payload contains no Message-ID, recipient address or email content. The Network
  hands out the secret and knows it: equal hashes reveal matching claims, and it can test a candidate Message-ID
  obtained elsewhere. Claims are therefore not end-to-end encrypted. The secret cannot open sealed questions or
  sign as a Machine; those keys never leave your Machine. A secret unknown to the Network is on the roadmap.
- **Finding people you know** (docs/fleet.md) is opt-in on both sides. Contacts are looked up blinded (private set
  intersection), so the Network never sees your address book. Being findable covers your account email and the
  addresses of the mail accounts your Machine connected, each of which you can turn off; the Network keeps those only
  blinded and learns which addresses matched people who chose to be found.
- **Your own addresses.** A self-hosted Machine tells the Network the mail addresses of its work accounts (so teammates'
  Machines know who knows whom) and its dashboard's https address when you set `dashboard_host`, so your Account page
  can link to your dashboard. Without `dashboard_host` it sends no dashboard address.
- **Your account in Settings.** A paired Machine asks the Network for your account in short (`GET /api/fleet/account`:
  your email, your Fleets as counts, and links to the account's pages), at most once a minute, and only for its own
  token's owner. Its buttons are plain links: no token on your Machine opens a signed-in account page.
- **Moving your Machine.** `assistant export --keys` carries `network-keys.json` (encrypted with your passphrase) and
  `assistant import` puts it back, so your teammates and friends see the same Machine. An export without keys leaves
  them out: the import makes a new Network identity, and your teammates and friends see a key-change notice once.
- **Sealed only.** The Network carries nothing else: a question or answer that isn't sealed is refused there and
  dropped by your Machine. A teammate or friend whose Machine has no keys yet (one from before end-to-end encryption)
  can't be asked until it updates; asking everyone skips it, asking that person by name says so.

The sealing needs nothing beyond Python: it uses the `cryptography` package when it's installed (fast), and otherwise
its own implementation of the same standard algorithms.

## Reliability

- Every question has an id; a retry never asks twice, and your Machine never acts on the same question twice.
- An answer the Network couldn't take (a restart, your connection down) waits in an outbox on your Machine and is
  sent again with backoff, for up to 3 days.
- A question the other Machine hasn't picked up yet shows as **Not delivered yet** on the Fleet tab.
- Your Machine can use more than one Network endpoint (`network_urls` in `config.json`) and switches to the next one
  when one is away.
- **Two nodes in two clouds.** The Network runs on a main node and a standby node with another provider
  (`network2.themachine.computer`), both built in. When the main node is away your Machine uses the standby, which
  carries sealed questions and answers only (pairing, keys, claims, finding people and the directory wait for the main
  node and never go to the standby; your Machine keeps its directory from before). Nothing is lost or done twice: a question moves to the standby only when
  the main node surely didn't take it, an answer goes to the node that has its question (or waits in the outbox until
  that node is back), your Machine looks into both mailboxes, and a question that came through both is taken once.
  After 5 minutes it tries the main node first again.

## Addresses

The Network lives at `network.themachine.computer`. Its old address, `relay.themachine.computer`, keeps working and is
tried when the new one can't be reached, so Machines from before the rename carry on. The standby node is
`network2.themachine.computer`. Older names keep working too:
`Relay` in code, `--relay` on `fleet-pair` (now `--network`; both are ignored).

## Roadmap

| Phase | What | State |
|---|---|---|
| 1 | Fleets and Friends through the Network, approvals, rules, an activity log on each Machine | Done |
| 2 | End-to-end encryption, key pinning and key-change warnings | Done |
| 3 | Outbox with retries, once-only handling, "Not delivered yet", endpoint failover | Done |
| 4 | A second Network node on another cloud provider, with failover drills | Done |
| 5 | Finding people you know (with permission), useful-connection suggestions, task-driven email invites | In progress |
| 6 | Other AI agents on the Network through a published protocol | Later |

## Retry and identity guarantees

Private Network keys are written atomically on first use; concurrent starts use one pair, and a malformed key file is reported instead of silently rotating identity. Plain exports omit these keys even at a custom configured path; encrypted `--keys` exports carry them (also from a custom path) and the import restores them next to the database. Outbound reply intent is committed before delivery and retained after interruption. A stale flush cannot erase a newer queued reply. Family requests keep their request identity after a failed response and obtain a new one only once the local receipt is saved.

Import restores external Network keys into the new data folder even when their old path still exists. If a credential has the same filename, the identity goes in `network-identity/` instead; neither overwrites the other. Plain imports do not point back at the exporting Machine’s external identity.

## Restart-safe node ownership

Request-to-node identity is saved before a question is sent and kept through restarts and local receipt commits. An ambiguous connection failure stays on that node; a definite refused connection may use the next one. Duplicate deliveries keep one owner choice and receive the same final answer on both nodes. Discovery and membership operations stay on the main node. Replay bindings, duplicate mappings and saved reply receipts expire after 30 days in the local private database. No extra approval tier is added.

### Interrupted discovery

A blinded non-match is checked. A positive match is checked only after its name lookup returns and is saved. Rate-limited, interrupted and over-cap matches stay eligible for the next incremental lookup; the daily refresh timestamp is not advanced over unfinished name refreshes. Lookups remain bounded and do not resend a friend request.
