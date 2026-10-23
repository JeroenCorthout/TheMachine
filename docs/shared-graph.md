# Shared Graph

A Work Fleet shares its Graph. People, organisations, projects, boards and client email that one Machine knows show
up on every teammate's Machine, sealed Machine to Machine. The Network (the control plane, "CP") carries and keeps
sealed blobs it can't read. Nothing syncs with an outside CRM: everything lives in The Machine.

This page is the design. Phase 1 is built (code: `shared_graph.py`, `fleet_trust.py`; CP: `cloud/fleet_graph.py`).
Phase 2 is a design only, waiting for a decision.

## What is shared

Each thread is judged by the review, by what it is about, never by word lists. A choice made by hand always wins.

| Thread | Who reads it | What the team sees |
|---|---|---|
| **Client**: at least one person outside the company, at an organisation on the Graph or with a work domain | The Fleet | The full email: body, from, to, date, attachment names and sizes |
| **Sensitive client**: a personal matter, health, a dispute about one person, something said to one person in confidence | Only the people on it | That it exists, in one neutral line: "Private thread with Lee, 12 Oct" |
| **Internal**: colleagues only | Only the people on it | Nothing. Never re-shared, also not by hand |
| **Personal**: a personal account, or the review says it is personal | The owner | Nothing, not even that it exists |

- Every thread on the dashboard has a small label, **Team** or **Only you**. One tap changes it. In chat the owner can
  say "keep the Globex thread private" and the assistant sets the same thing. A review can never set it by hand.
- A thread no review has judged yet is **Only you** until one does.
- **Only you by hand** shares nothing about that thread, not even the neutral line.
- Hard limits come before the review's call and before a hand choice: only accounts with a Work boundary for this
  Fleet; a thread with nobody outside the company is internal; a personal account is never read for this.

Records are shared by default when they are Work:

| Layer | What | Shared |
|---|---|---|
| Exists | "Sam knows Lee at Globex, strong, last contact 3d" | Already, through "who knows someone" |
| Record | Name, role, company, owner, project stage, next step | Yes, for Work people, organisations and projects |
| Timeline | One short line per message or meeting, written by the review | Yes, for client threads; the neutral line for sensitive ones |
| Raw mail | The email itself | Client threads (above). Explicit sharing of other threads is Phase 2 |

**Keep private** on a person, organisation or project shares nothing about it: no record, no timeline, none of their
threads. Colleagues (people at the Fleet's own domains) are never shared as records. A person whose reviewed threads
are all sensitive is held back too.

The weekly recap says, in one line: "Shared with Acme: 214 threads, 31 people, 6 projects. 4 held back." with a
link to review the held-back ones.

## Threat model

**A compromised CP.** It can drop, delay or reorder what it carries, and see metadata: who is in the Fleet, who
publishes, when, how big, to whom, and how many threads each Machine shares. It can't:

- read: every blob is encrypted with a fresh key that is sealed (X25519) to each recipient Machine's pinned key;
- forge or change: every blob is signed (Ed25519) by the publishing Machine, and the seal binds the sender, the
  recipient, the kind and the blob id;
- replay an old blob as new: sequence numbers per publisher, checked by every receiver;
- add a member to get data sealed for it: a Machine seals only to members **confirmed by admin keys** (below). The CP
  can remove someone from the directory (they stop getting new data), never add one;
- enumerate contacts: addresses inside records are keyed hashes (HMAC-SHA256 with the Fleet graph key, which the CP
  never has). The CP never sees them anyway; teammates without that contact can't read the address back either.

**A malicious member.** They can read what the Fleet shares with them: that's the point of sharing. They can't:

- write as someone else: a record carries its publisher, checked against the signature;
- overwrite others' fields: each Machine publishes only its own records; a conflict is resolved on each receiver by
  closeness, and a field set by hand stays the hand value;
- move a teammate's tile on that teammate's Machine: a stage move by hand from a teammate shows as theirs on the Team
  board and never changes the receiver's own Mine board;
- get Personal data, Keep private records, sensitive or internal threads: those are never put in a blob;
- ask for more than the Fleet gets: there is no query; every Machine pushes what it shares.

A malicious admin can confirm a fake member. That is the trust root: admins are trusted, as in shared connectors.

**Someone leaving.** The directory drops them at once and every Machine stops sealing for them. Within a round the
Fleet graph key rotates, so blobs after the leave are sealed and hashed with a key they never had. What they shared
stays with the Fleet: their records, timeline and client mail stay on teammates' Machines and in the CP replica, shown
as "via Sam (left)". An admin's Machine re-seals those blobs for people who join later. What they got from the Fleet
is removed from their Machine when it sees it's no longer in the Fleet (as `work_removal.py` does for company data).
What they already read can't be unread.

Out of scope: a compromised Machine of a member (it sees what that member sees), a compromised admin, traffic analysis.

## Trust: confirmed members

Shared connectors (#68) confirm admins by Machine keys: the owner's root, then admins vouched for by a confirmed admin.
The Shared Graph adds one statement kind to `fleet_trust.py`:

- `member`: `{t: member, fleet, member, key, by}`, signed by a confirmed admin's Machine after their owner's OK
  ("Share the team's Graph with <name>'s Machine?", with their key's fingerprint). Confirmed admins are confirmed
  members.

A member is confirmed while they are in the directory now, with the key a confirmed admin vouched for. Pins are kept
between rounds, so a member vouched for by an admin who later left stays confirmed. A key change drops them until an
admin says OK again. A Machine seals only to confirmed members and opens only blobs from confirmed members.

## Data model

On each Machine, next to the Graph:

- `share_threads(thread, verdict, line, with_name, hand, hand_at, event_id, account_id, at)`: the review's verdict per
  thread (client, sensitive, internal, personal), its one line, and the owner's hand choice (team or private).
- `share_private(node_id, at)`: Keep private.
- `team_records(fleet, member, rid, kind, body, at)`: what teammates shared, opened, one row per record.
- `team_mail(fleet, member, tag, body, at)`: client threads teammates shared, opened.
- `share_out(fleet, ...)`: what this Machine published last (record hashes, threads), to send only changes.
- `fleet_meta` keys: graph keys by version, sequence numbers, cursors.

A record (JSON, inside a blob):

```
person:  {r, t: person, name, name_hand, role, role_hand, org: {name, d}, ids: [hash], d, score, last, timeline: [{at, line}]}
org:     {r, t: org, name, role, role_hand, d, score, last}
project: {r, t: project, name, board, stages, stage, stage_hand, stage_at, why, next, org, people: [hash], score, last, timeline}
thread:  {tag, subject, with, at, private: true}             (sensitive: the neutral line only)
mail:    {tag, subject, messages: [{at, from, to, cc, body, attachments: [{name, size}]}]}
```

`r` and `tag` are HMAC(publisher secret, local id): stable per publisher, meaningless to others. `ids` and `d` are
HMAC(Fleet graph key, address or domain), under each of the newest few key versions the publisher holds, so two
teammates still match while a new key version reaches everyone. `score` is the publisher's closeness (the Graph's
strength).

## Sync protocol

A **blob** has a header in the clear and an encrypted body:

```
header = {v: 1, fleet, from, id, seq, kind, at, to: [member ids], kv, tag?}
kind   = key | snapshot | delta | mail
body   = AEAD(DK, json)                  DK: 32 fresh random bytes per blob
         HKDF-SHA256 -> encryption and MAC keys, HMAC-SHA256 counter stream, MAC over header + nonce + ciphertext
sig    = Ed25519(publisher, "tm-shared-graph/1" + header + sha256(ciphertext))
wraps  = {member: seal({dk, h}, publisher, member, {p: graph, k: kind, f: from, t: member, r: id})}
```

The seal is the Network's own (`network_crypto.seal`): sealed for one Machine, signed by the sender, bound to who
sends what to whom. `h` is the hash of header and ciphertext, so a wrap can't be moved onto another blob.

- **key**: from a confirmed admin, the Fleet graph key `{kv, key}`. The first confirmed admin Machine that finds none
  makes one. Any confirmed admin re-seals it for a newly confirmed member and makes a new version when someone it was
  sealed for left. Receivers keep every version they get and match with all of them; publishers use the newest.
- **snapshot**: all of a Machine's shared records and the tags of its shared threads. Replaces what came before from
  that Machine. Sent when the recipients or the key change, and daily.
- **delta**: changed and removed records since the last blob, with `prev` (the previous seq). A receiver that missed
  one waits for the next snapshot.
- **mail**: one client thread, or one sensitive thread's neutral line, by `tag`. A newer blob with the same tag
  replaces it on the CP; one with an empty ciphertext removes it (the CP deletes such tombstones after 30 days). A
  receiver drops mail whose tag isn't in the publisher's latest snapshot.

Each Machine publishes every few minutes when something changed and fetches with a cursor. A receiver opens a blob
only when: the sender is a confirmed member, the signature checks with their pinned key, the wrap opens, its `h`
matches, the seq is newer than the last one taken from them, and the header's fleet and `to` include it. A blob from
someone not confirmed yet (their admin's OK is still on its way) waits in `team_pending` and is tried again on each
round for 7 days. The CP's inbox reports the newest wrap cursor (`graph=`), so a Machine fetches only when there is
something new for it.

**The CP replica.** The CP keeps every current blob (latest snapshot, its deltas, current mail, keys) for as long as
the Fleet exists, including blobs of members who left. A Machine that was off, reinstalled or new catches up from it
when the publisher is offline. The CP checks what it can without reading: the sender is a Work member, `to` and the
wraps name current members, `key` blobs come from an admin, sizes and rates are within limits.

**Matching and conflicts.** A receiver matches teammates' people by hashed address, organisations by hashed domain,
projects by board and name. For each field of a matched record the value set by hand wins; otherwise the value from
whoever is closest (highest score) wins. Stages are facts with dates: the newest move by hand wins, else the newest
move a message showed. The Mine view never changes: Team is a view over Mine plus what teammates shared.

## Shared Boards

Boards with the same name become one team pipeline. A tile is a project; its stage moves come from anyone's mail (the
review on each Machine moves its own copy, which it publishes). Each tile has an **owner**: the teammate closest to
it. **Ask <name>** on a tile, person or organisation starts a handoff to that teammate (the Fleet's handoff,
`fleet_requests.py`), never anything to someone outside the Fleet.

## What members and admins see

| | Member | Admin | CP |
|---|---|---|---|
| Records and timelines of Work | Yes | Yes | No (sealed) |
| Client threads | Yes | Yes | No (sealed) |
| Sensitive client threads | That they exist | That they exist | No |
| Internal, Personal, Keep private | No | No | No |
| Who publishes, when, how much | Yes | Yes | Yes (metadata) |
| A leaver's shared work | Yes, stays | Yes, stays | Sealed blobs |
| A leaver's other work mail | No | Phase 2: escrow | No |

## Dashboard

- **Mine / Team** on People, Organisations and Projects. Mine is the Graph as before. Team adds teammates' records.
- A quiet **via Sam** under a record a teammate shared ("via Sam and Kim", "via Sam (left)").
- **Ask Sam** on a teammate's record or tile.
- **Team / Only you** on each thread on a record's page; **Keep private** on a record.
- The Fleet tab: who is confirmed, "Share the team's Graph with <name>'s Machine?" for admins.

## Migration

1. Machines on this version publish their root (owner) and their keys, as with shared connectors.
2. An admin's Machine asks once per member: "Share the team's Graph with <name>'s Machine?". Until then nothing goes
   to them.
3. The owner of each Machine sees a card once: "Work is shared with <Fleet> from <day>. Personal never." Nothing is
   published for 24 hours, or until they tap Share now, so they can mark things Keep private first.
4. Past threads: only those the review judges from then on, plus the last 90 days of threads a review already saw.
5. Machines that haven't updated publish nothing and get nothing; "who knows someone" keeps working.
6. Nothing is imported from or written to another CRM.

## Phase 2 (design only)

**Admin escrow on leave.** Work email is company property. When someone leaves, their work-account mail stays
available to the Fleet's admins, sealed to admin keys, never readable by the CP.

- An **escrow key** (X25519) per Fleet. Its private half is sealed to each confirmed admin's Machine by an admin; it
  rotates when an admin leaves.
- **Continuous**, not at leave time: each Machine seals its work-account mail (not client threads already shared, not
  Personal) to the escrow public key in daily blobs, kept by the CP for 400 days. A leaver's Machine may be wiped or
  self-hosted and gone; continuous escrow doesn't depend on it.
- **Opening** needs the person to have left, and is logged in the Fleet's log for every admin. Two admins, where
  there are two, approve it.
- Sensitive threads go too (company mail), opened only through that flow.

**Explicit thread sharing.** Share any work thread the hard limits allow, to the Fleet or to one teammate, as a mail
blob sealed only to them. Stop sharing removes it from the CP and asks their Machines to delete it.

## Open decisions

1. Escrow: continuous (above, more storage, works when a Machine is gone) or only at leave time.
2. Escrow opening: one admin, or two when there are two. And whether the person who left is told.
3. Whether a member can turn the Shared Graph off for their Machine, or only an admin for the Fleet.
4. Attachments: names and sizes only (now), or the files too, sealed the same way.
5. How far back client mail goes on the first share: 90 days now.
