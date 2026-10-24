# Shared Graph

A Work Fleet shares its Graph. People, organizations, projects, boards and client email that one Machine knows show
up on every teammate's Machine, sealed Machine to Machine. The Network (the control plane, "CP") carries and keeps
sealed blobs it can't read. Nothing syncs with an outside CRM: everything lives in The Machine.

This page is the design, and it is built (code: `shared_graph.py`, `fleet_trust.py`; CP: `cloud/fleet_graph.py`).

## What is shared

Each thread is judged by the review, by what it is about, never by word lists. A choice made by hand always wins.

| Thread | Who reads it | What the team sees |
|---|---|---|
| **Client**: at least one person outside the company, at an organization on the Graph or with a work domain | The Fleet | The full email: body, from, to, date, and its attachments (up to 2.5 MB each; bigger ones show as "too large to share") |
| **Sensitive client**: a personal matter, health, a dispute about one person, something said to one person in confidence | Only the people on it | That it exists, in one neutral line: "Private thread with Lee, 12 Oct" |
| **Internal**: colleagues only | Only the people on it | Nothing. Never re-shared, also not by hand |
| **Personal**: a personal account, or the review says it is personal | The owner | Nothing, not even that it exists |

- Every thread on the dashboard has a small label, **Team** or **Only you**. One tap changes it. In chat the owner can
  say "keep the Globex thread private" and the assistant sets the same thing. A review can never set it by hand.
- A thread no review has judged yet is **Only you** until one does.
- **Only you by hand** shares nothing about that thread, not even the neutral line.
- Hard limits come before the review's call and before a hand choice: only accounts with a Work boundary for this
  Fleet; a thread with nobody outside the company is internal; a personal account is never read for this; an
  account at a domain none of the Fleet's members work at (another company of the owner's) never goes to it.
- Personal stays personal: once the review sorts a thread personal, a later review can't make it client or
  sensitive (not even the neutral line). Only the owner can, by hand. A thread already shared is taken back.
- People of the owner's own life never go: someone only in their Family or Friends, or someone they marked Personal.
  No record of them, and a thread with them on it stays Only you, like Keep private.

Records are shared by default when they are Work:

| Layer | What | Shared |
|---|---|---|
| Exists | "Sam knows Lee at Globex, strong, last contact 3d" | Already, through "who knows someone" |
| Record | Name, role, company, owner, project stage, next step | Yes, for Work people, organizations and projects |
| Timeline | One short line per message or meeting, written by the review | Yes, for client threads; the neutral line for sensitive ones |
| Raw mail | The email itself and its attachments | Client threads (above). Nothing else, ever |

The record's owner is the one picked by hand on it (`owner_pick`, a Work Fleet member id; `crm.py`), and otherwise
whoever in the team is closest to them. It travels only with a record that is shared anyway.

**Keep private** on a person, organization or project shares nothing about it: no record, no timeline, none of their
threads. It needs nobody's OK. Colleagues (people at the Fleet's own domains) are never shared as records. A person whose reviewed threads
are all sensitive is held back too.

**Only-you boards.** A board set to Only you (`boards.visibility`), and every project on it, is held back like Keep
private: no board, no record, no timeline, none of its threads, and a teammate's Team view never shows it. A project
can differ from its board (`projects.visibility`; empty follows the board). Projects in an Only-you board suggestion
are held back already while it waits to be saved. Switching a board or project from Team to
Only you takes back what was shared at the next publish, as Keep private does. Code that shares anything asks
`graph.visible_to_team(db, node_id)` (False for an Only-you project) or `graph.hidden_project_ids(db)` (the set of
Only-you project ids).

**A project on a Team board** shares its record (name, board, stage, why, next step, organization, people) even before
a review has judged one of its threads, so the team pipeline shows every project on it. Its threads still go only as
they are judged.

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
stays with the Fleet: their records, timeline and client mail stay on teammates' Machines, shown as "via Sam (left)".
Nothing more: no other mail of theirs is kept for the company or sealed to admins, so leaving never shares more than
was shared before. Someone who joins later gets it from an admin's Machine (catching up, below). What they got from the Fleet
is removed from their Machine when it sees it's no longer in the Fleet (as `work_removal.py` does for company data).
What they already read can't be unread.

Out of scope: a compromised Machine of a member (it sees what that member sees), a compromised admin, traffic analysis.

## Trust: confirmed members

Shared connectors (#68) confirm admins by Machine keys: the owner's root, then admins vouched for by a confirmed admin.
The Shared Graph adds one statement kind to `fleet_trust.py`:

- `member`: `{t: member, fleet, member, key, by}`, signed by a confirmed admin's Machine after their owner's OK
  ("Share the team's Graph with <name>'s Machine?", with their key's fingerprint). Confirmed admins are confirmed
  members.
- `optout`: `{t: optout, fleet, member, key, by, asked, at}`, signed by a confirmed admin's Machine after their owner's
  OK on a member's ask to stop sharing (below). It counts only from an admin other than that member, for their current
  key and for that very ask.

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
- `team_files(fleet, member, tag, name, mime, size, data, at)`: their attachments.
- `team_asks(fleet, member, off, asked, at)`: members' asks to stop sharing, for an admin's OK.
- `share_out(fleet, kind, key, hash)`: what this Machine published (records, and each thread and file that went), to
  send only changes and to go on where a backfill stopped.

With message protection on, teammates' records, mail, files and the review's lines are sealed at rest with the
message key, like the owner's own mail (`message_seal.DERIVED`).
- `fleet_meta` keys: graph keys by version, sequence numbers, cursors.

A record (JSON, inside a blob):

```
person:  {r, t: person, name, name_hand, role, role_hand, org: {name, d}, ids: [hash], d, score, last, timeline: [{at, line}]}
org:     {r, t: org, name, role, role_hand, d, score, last}
project: {r, t: project, name, board, stages, stage, stage_hand, stage_at, why, next, org, people: [hash], score, last, timeline}
thread:  {tag, subject, with, at, private: true}             (sensitive: the neutral line only)
mail:    {tag, subject, messages: [{at, from, to, cc, body, attachments: [{name, size, mime, file | too_large}]}]}
file:    {name, mime, data}                                   (one attachment; `file` above is its tag)
ask:     {off, asked}                                         (a member asks to stop sharing, or not any more)
```

`r` and `tag` are HMAC(publisher secret, local id): stable per publisher, meaningless to others. `ids` and `d` are
HMAC(Fleet graph key, address or domain), under each of the newest few key versions the publisher holds, so two
teammates still match while a new key version reaches everyone. `score` is the publisher's closeness (the Graph's
strength).

## Sync protocol

A **blob** has a header in the clear and an encrypted body:

```
header = {v: 1, fleet, from, id, seq, kind, at, to: [member ids], kv, tag?}
kind   = key | snapshot | delta | mail | file | ask
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
  replaces it; one with an empty ciphertext removes it. A receiver drops mail whose tag isn't in the publisher's
  latest snapshot.
- **file**: one attachment of a shared client thread, by `tag`, up to 2.5 MB. Only for whole client threads: a
  sensitive or private thread's files are never even read. Gone (an empty blob) when its thread stops being shared.
- **ask**: a member's ask to stop sharing, or that they withdrew it. Every member gets it sealed; only admins' Machines
  act on it.
- **want**: a Machine asks its teammates for what it missed (catching up, below).

**History and batches.** At the start a Machine shares all the Work history it has, not only recent mail, through the
same labels: a history pass (one model turn at a time, a batch of threads per turn) judges older Work threads no
review saw. Threads go the newest first, at most 100 blobs an hour and 1,800 a day per Machine (under the CP's 120
and 2,000). `share_out` keeps what went, so a backfill that stopped (a limit, the Machine off) goes on from there
next round. Someone who joins later gets what went before by catching up.

Each Machine publishes every few minutes when something changed and fetches with a cursor. A receiver opens a blob
only when: the sender is a confirmed member, the signature checks with their pinned key, the wrap opens, its `h`
matches, the seq is newer than the last one taken from them, and the header's fleet and `to` include it (or a confirmed
teammate passed it on, for a catch-up). A blob from
someone not confirmed yet (their admin's OK is still on its way) waits in `team_pending` and is tried again on each
round for 7 days. The CP's inbox reports the newest wrap cursor (`graph=`), so a Machine fetches only when there is
something new for it.

**The Network as a mailbox.** Everything stays on the Machines: each keeps its own copy of the team's Graph. The CP
only passes blobs on, as the Network does with asks. A receiver acks what it took, and the CP deletes its wrap; a blob
goes once every Machine it was sealed for took it, and anything nobody took goes after 30 days. There is no storage
cap, only a limit per blob (4 MB) and on what a Fleet has in flight (500 MB), so a Machine that is off for long can't
fill the CP: at that limit the oldest of whoever has the most waiting goes first. The CP checks what it can without
reading: the sender is a Work member, `to` and the wraps name current members, `key` blobs come from an admin, sizes
and rates are within limits.

**Catching up.** Each Machine keeps the blobs it has as their publishers signed them (`team_blobs`: the last
snapshot and the deltas since, and the newest of each thread and file), so it can pass them on. A Machine that was
away longer than 30 days, or one just confirmed, sends a sealed `want`: which records it has of each teammate and how
far back it got their threads. Any online teammate's Machine answers from its own copies: each publisher's records
first, then threads and files, the newest first, a batch a round, going on where it stopped. A blob passed on is the
publisher's original, still checked against their signature; only its key is sealed again, for the one who asked.
What someone who left shared is passed on only by an admin's Machine. Two teammates answering the same want is fine:
the CP skips a blob already waiting for that Machine, and the receiver drops what it has.

Catch-up sends a long snapshot/delta chain in ordered prefixes within each round's budget, retaining the last sent
sequence before continuing. Signed empty mail/file blobs are retained as removal markers: a later relay of older
content cannot restore a removed copy, and an offline teammate receives the removal during catch-up. Temporary
attachment-listing failures leave the thread unfinished for retry; files already shared are not downloaded twice.

**Matching and conflicts.** A receiver matches teammates' people by hashed address, organizations by hashed domain,
projects by board and name. For each field of a matched record the value set by hand wins; otherwise the value from
whoever is closest (highest score) wins. Stages are facts with dates: the newest move by hand wins, else the newest
move a message showed. The Mine view never changes: Team is a view over Mine plus what teammates shared.

## Shared Boards

Boards with the same name become one team pipeline. A tile is a project; its stage moves come from anyone's mail (the
review on each Machine moves its own copy, which it publishes). Each tile has an **owner**: the teammate closest to
it. **Ask <name>** on a tile, person or organization starts a handoff to that teammate (the Fleet's handoff,
`fleet_requests.py`), never anything to someone outside the Fleet.

## What members and admins see

| | Member | Admin | CP |
|---|---|---|---|
| Records and timelines of Work | Yes | Yes | No (sealed) |
| Client threads | Yes | Yes | No (sealed) |
| Sensitive client threads | That they exist | That they exist | No |
| Internal, Personal, Keep private, Only-you boards | No | No | No |
| Who publishes, when, how much | Yes | Yes | Yes (metadata) |
| A leaver's shared work | Yes, stays | Yes, stays | No |
| A leaver's other work mail | No | No | No |
| Asks to stop sharing | Their own | Yes, to approve | That a blob of kind ask went |

## Dashboard

- **Mine / Team** on People, Organizations and Projects. Mine is the Graph as before. Team adds teammates' records.
- A quiet **via Sam** under a record a teammate shared ("via Sam and Kim", "via Sam (left)").
- **Ask Sam** on a teammate's record or tile.
- **Team / Only you** on each thread on a record's page; **Keep private** on a record.
- The Fleet tab, in the Team Graph box: "Share the team's Graph with <name>'s Machine?" and "<name> asks to stop
  sharing their Work with the team." (Approve / Not now) for admins, and for everyone their own sharing: on, asked an
  admin, or off, with one button (Stop sharing my Work, Keep sharing, Share again).
- On a teammate's client thread, its attachments open from the dashboard (served as downloads, never run).

## Stopping

Like in any CRM, nobody switches sharing off on their own. A member taps **Stop sharing my Work**; their Machine tells
the admins (an `ask` blob) and keeps sharing. An admin's owner sees the ask on the Fleet tab and approves it on their
own Machine, which signs an `optout` statement. Once the member's Machine sees it, it publishes nothing new. What it
shared before stays with the Fleet. **Share again** turns it back on at once, with no OK needed. A Network that
forges, drops or replays these can at most delay a stop: it can't make one, because only a confirmed admin's key
signs it.

## Migration

1. Machines on this version publish their root (owner) and their keys, as with shared connectors.
2. An admin's Machine asks once per member: "Share the team's Graph with <name>'s Machine?". Until then nothing goes
   to them.
3. Sharing starts as soon as a Machine is confirmed and an admin has OK'd a teammate's Machine. Until then the Team view says "Sharing starts once you OK a teammate's Machine." (or "once an admin OKs") and "Nothing from the team yet."; after, "Work is shared with <Fleet>. Personal never."
4. Past threads: all the Work history the Machine has, judged by the history pass, in batches (above).
5. Machines that haven't updated publish nothing and get nothing; "who knows someone" keeps working.
6. Nothing is imported from or written to another CRM.

## Decided

1. No admin escrow. When someone leaves, the Fleet keeps exactly what they shared, nothing more.
2. Stopping takes an admin's OK, like in any CRM. Keep private on a record needs none.
3. Attachments go with a shared client thread, sealed the same way, up to 2.5 MB each. In a Careful Fleet (security level) they don't: the thread lists them as not shared.
4. History: as far back as the Machine has Work mail, through the same labels, in resumable batches.
5. No look-first delay: sharing starts once a Machine is confirmed.
6. Everything stays on the Machines. The CP is a mailbox: a blob goes once every Machine took it, or after 30 days.
   Machines catch each other up.
