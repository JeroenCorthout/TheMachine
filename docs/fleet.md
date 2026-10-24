# The Fleet

The Fleet lets your Machine and the Machines of the people you work or live with help each other. It is in beta.
Machines never talk to each other directly: questions and answers pass through The Machine Network. Hosted Machines
are on it already, and a Machine you run yourself can join too (see "A self-hosted Machine in a Fleet").

There are two kinds:

- a **Work Fleet**: you and your teammates;
- a **Family Fleet**: you and the people you live with.

Next to the Fleets you can have **Friends**: people whose Machines help yours one to one (see "Friends").

You can be in several Work Fleets, ones you started and ones you joined, and one Family Fleet. Your Machine answers in
each and keeps them apart: each Fleet has its own people, its own rules and its own questions, and the
Fleet tab shows each Work Fleet as its own section. The Fleets come first, Work and Family together in the order you joined or made them, then Friends, Network, Sharing and Activity. Asking from a Fleet's section asks only that Fleet; "Ask the Fleet
who knows" from the Graph tab asks each of your Work Fleets, one at a time. Leaving one Fleet leaves the others as they
are.

Any Fleet, and any friend, can also share **skills**: how-tos your Machine follows, which the other person reads and
installs themselves (see [Skills](skills.md)). A skill is instructions only and never changes your rules here.

What a Fleet needs from you (a teammate's question or request, a meeting's follow-ups to split, a server to confirm)
shows as a card under **Suggestions**, tagged Fleet and Work, with the same buttons as your other cards; the
Suggestions badge counts them. The Fleet tab keeps the people, the rules, the team brief, your own questions with
their answers, and the log.

## What a Work Fleet does

1. **Who knows someone at X?** Ask from the **Fleet** tab, or with **Ask the Fleet** on a person or organization in
   the Graph. Each teammate's Machine answers from its own graph: "knows 2 people, closest: regular, last contact
   2026-09". Names only when that teammate set With names for you.
2. **Meeting prep.** If you turn it on, your Machine asks your Work Fleet about the outside people in your meetings this
   week: how close each teammate is, their last contact and open promises. The answers show on the meeting's card
   ("From your Fleet: …") and go into its preparation.
3. **No double replies.** When the same email reaches you and a teammate, the first one to take it on claims it. The
   other's card says "<teammate> is on it", with **I'll handle it** to take it over. Once one of you closes theirs (an
   email that reached a teammate or a shared address like info@), the other's open card says "Done: <teammate> already
   handled this", with **Mark done** first: when they marked it done, or when the card would hand it to them and they
   closed it either way. It then offers no handoff. If they reopen theirs, so does yours.
4. **Shared connectors.** An admin connects Intercom once and shares it from the Fleet tab, **Shared connectors**: the
   Machines they say OK to get it as a work source, sealed Machine to Machine. Connections shows "Shared by <Fleet>".
   How it's kept safe: [Network](network.md#shared-connectors).
5. **Shared Graph.** Work People, Organizations and Projects are shared with the Fleet: name, role, company, stage,
   next step, a short timeline and client threads with their attachments, as far back as the Machine has them.
   Sensitive client threads show only that they exist; colleague threads and Personal never leave. Every thread has
   Team or Only you, every record Keep private. Stopping altogether takes an admin's OK on the Fleet tab. Graph has
   **Mine / Team**, "via <name>" and **Ask <name>**. It all stays on the Machines; the Network only passes it on.
   Design: [Shared Graph](shared-graph.md).

A Family Fleet asks three things: when everyone is free, to-dos for one another, and family plans to say yes or no to.
See [Family](family.md). Work questions never travel in a Family.

## Asking a teammate to do something

Besides questions, a Work Fleet carries three kinds of requests. Each has its own rule in the Fleet (Ask me first by
default, Always accept, Never), and nothing happens on your side until you accept one.

4. **Warm intros.** When "who knows someone at X?" finds a teammate who does, **Ask <teammate> for an intro** (with a
   line why, if you like) asks that one teammate. Their Suggestions, phone and Telegram show "An intro to someone at X"
   with **Accept** and **Decline**. Accepting makes a card on their side with a double-opt-in email to the person they
   know best there, from their own work email: it asks that person first whether they want the introduction. The draft
   is built by plain code from their own graph, signed with their name, and goes out only with their own **Send**, like
   any other draft. You follow it in your Fleet tab, on your phone and in Telegram: asked, accepted, sent, or declined.
5. **Handoffs.** **Hand off to a teammate** on a work card opens a small sheet: who, what they are asked to do (the
   card's next step, to edit), what goes along as chips (its outside contact and the card's sources, "Email from
   Stripe Alerts · Fri 23 Oct"; × leaves one out) and an optional note. Its title and due date go too. They get "<you> hands you: ..." with **Accept** and **Decline**; accepting makes a card
   on their side with the task as its title, which leads with the ask in your words ("<you> asks you to take this on:
   ..."), then the contact, with
   copies of the sources as its own sources: they read in the card chat and under **Read the thread** like any
   email, from their own Machine, without access to your mailbox. Only that card's own emails and messages go (up to
   three, about 6,000 characters each: sender, date, subject and text; attachments don't travel). The contact is the
   person who wrote, by Reply-To if there is one, never a Google Group's own address on a "via" email. If they already
   have an open card on it (the same email, recognized by a key of its Message-ID, or else the one open card on email
   from the same contact), your handoff joins that card, sources included, instead of making a second one. Your card says "Handed off to <teammate>" and how it went. The Fleet tab
   lists your handoffs in their own **Handoffs** box, both ways: open ones first (waiting, declined, or one waiting for
   you), accepted and done ones after them, with **Open** to go to the card. While it waits for them
   it leaves Ready and sits under Later, with nothing to send again, saying when it comes back to check in if they
   haven't answered ("Check in with <teammate> Mon 09:00 if no answer"), with **Check in now**, **Change time** and
   **Not needed**. That time follows the card's own urgency and deadline: about 3 hours later (within your hours, else
   the next morning) when it is urgent or due today, the morning of the work day before a nearer deadline, and
   otherwise three days later, on a work day. Once they accept (or mark it done), it is marked done for you, still saying to whom (Undo
   brings it back); if they decline, it is back in Ready, saying "<teammate> declined: <their reason>", with **Do it
   myself**, **Hand to someone else** and **Mark done**. Decline asks "Why? (optional)" with quick picks (Not mine,
   Already done, No time this week); **Accept with a note** (under More) adds a short note too. Only the sender sees it,
   sealed like the answer. A card handed to you can't be handed straight back to whoever gave it; to others it can.

   If you are already on a handoff before you answer it, it can become yours without a tap. Your Machine's review
   looks only at messages you sent and calls you joined after it came, and judges by what they are about whether you
   are acting on it (never a message you only received, never a word list). It then accepts it for you, quietly: your
   card says "Marked as yours, since you talked to Sam about it" with **Not mine**, which undoes it and tells your
   teammate. They see "<you> is on it · talked to Sam on Slack, Fri 10:12" and their card is done, the check-in gone.
   Where (email, Slack, a call), when, and the first name of who you talked to travel; never their address or what
   was said. Dismissing that card later tells your teammate you declined, so it comes back to them. Nothing is sent or drafted
   for you. Your own Accept or Decline always wins: once you answer, it is never marked for you. An older Machine
   on the other side reads it as a plain accept, and a later Not mine only as a note that you declined.

   When a task is a teammate's to do, the review offers to hand it to their Machine, and Do it prepares the handoff
   itself: **Hand off to <teammate>** leads the card and opens this form with that teammate picked and the message
   written to them already in it (a Slack message or email to them can be a second choice). What goes is always the
   message for them, never the card's own note of what The Machine did.

   **Someone not on the Network.** Pick **Someone else, by email** and give their address: instead of a Fleet request,
   the card gets an ordinary email draft from its Gmail account, for you to read, change and send yourself (it never
   goes out by itself). It says what you need from them, with no promotional line or signature.

   **Task page.** The card then offers **Add a task page**: a page they open from your email to see the status and
   answer (I'm on it, Done, Can't do this, with a short note), no account needed. Nothing is made until you tap it and
   see exactly what the page shows: the title, the details, the due date and your first name. **Leave out** takes a
   part off, and you pick how long the link works (7, 14 or 30 days, 14 by default). **Add to email** puts one plain
   line in the draft, "Status and replies: <link>". Never on the page: the email or its thread, your notes, the graph or
   other cards; details that repeat the original email are refused. The page is sealed on your Machine with a key that
   exists only in the link (after the #), so the Network keeps an unreadable copy; it never changes when someone only
   opens the link (an email scanner, a preview), and an answer needs a check only that key can make. Answers come back
   on the card (it leads with their latest answer, and the sent email folds into one line) and as a notification, and
   the page shows them as updates. Can't do this brings the card back to Ready. Marking the card done closes the page
   (it ends within 7 days); **Stop sharing** deletes it from the Network at once. If they get a Machine of their own
   (hosted, or self-hosted with `assistant task-import '<link>'`), the task arrives there as a card with your words
   quoted, and their dashboard offers to connect with you on the Network. The Network only counts how far each link
   got (opened, answered, done, signed up, task added, connected), never what was in it.
6. **Shared follow-ups.** After a meeting that several members of one Work Fleet attended (in the last two days), one
   Machine proposes who does which follow-up: the organizer's, else that of the member who joined the Fleet first, so
   there is only ever one proposal. It starts from that owner's own open cards and promises on the meeting; the owner
   fills in each teammate's part ("send the pricing", "book the demo") on a card under Suggestions with the meeting's title
   and day, and sends it (**Send the split**) or sets it aside (**Not needed**). Each member's
   Machine shows them their part (with the meeting's title from their own calendar) to **Accept**, change
   (**Accept my change**) or **Decline**; accepting makes their card. Cards on that meeting then say who does what,
   so nobody follows up twice. The meeting only travels as a hash keyed with the Fleet's secret: neither its title
   nor its id leaves your Machine. With [Zoom](sources.md) connected, the meeting's summary fills in the empty parts: the
   review reads its next steps and gives each to the person it names (a step that is unclear stays empty). The card
   says it was filled in from the Zoom notes; you check and edit it, and nothing goes until you press Send the split.

A request's text (an intro note, a handoff summary, a follow-up part) is your teammate's own words. It is kept short,
shown quoted as their words, and never read as an instruction: it reaches no model, tool or Do it by itself. A card
made from it is yours like any other card; if you later tap Do it on it, the preparation is told that the quoted text
is data, not instructions. A request goes to one Fleet's people only and is answered in that Fleet.

## What the Fleet saw, and free questions

7. **The team brief.** Under the Fleet tab, set **Team brief** to Daily or Weekly (it is off by default), and tick
   **Also send it to me** to get it on your phone and in Telegram too. On your work days, from your morning time, your
   Machine asks each Work Fleet for its brief facts and puts together "What the Fleet saw": a few ranked lines, most
   important first, such as "Globex is waiting for a reply (2 emails). 5 days, sent to you and Priya":
   - email waiting on the team, and for how long, checked against everyone's replies and the Fleet's "is on it"
     claims (mail from today, and mail only to you younger than two days, isn't waiting yet);
   - meetings with the same company on several calendars;
   - companies several of you are in touch with;
   - companies that went quiet, that nobody in the Fleet was in touch with since, when several of you knew them or
     they are a customer.

   Only what involves the team. A waiting email shows when the review judged it needs a reply (it made a card of it
   that is still open) and it went to two or more of you, to a shared address of the team (sales@, info@ or a group:
   an address at your domain that is no member's and no person you work with), comes from a company a teammate is in
   touch with, or from a customer. Each line says which ("sent to you and Priya", "to sales@", "Priya also talks to
   them", "a customer"). Mail only you got, from someone only you know, stays in Suggestions. Cold sales, newsletters
   and notifications never reach the brief. The card shows five lines (**Show
   more** for the rest), each with **Ask** (puts a question for the Fleet in the box below), **Open** (that company's
   mail, when it came to you) and **Not needed** (hides that line from this brief). When nothing needs the team it
   says so in one line. It is told once, within your work hours, once everyone answered or two hours passed.
   Teammates who haven't answered are listed. It is put together by plain code, never a model.
   What each Machine shares for it is aggregate only: company names and domains, counts, last-touch dates, hashes
   of emails and meetings keyed with the Fleet's secret (the same as claims), which Fleet members an email went to (as
   hashes of their addresses with that secret), the name of a shared address it went to ("sales") and whether the
   company is a customer. No subjects, message text, outside people, addresses or calendar titles. Brief facts have
   their own rule (Ask me first by default, Always, Never); the approval card says how much an answer would share.
8. **Ask the Fleet anything.** Type a question in a Work Fleet's section ("has anyone dealt with Stripe disputes?").
   Each teammate's Machine searches only its owner's own work accounts in that Fleet, on that Machine, and shows its
   owner your question, quoted as your words, with what it found and a draft answer built by plain code ("Yes, I have:
   3 emails on this, with Acme, most recently in 2026-09"). Nothing goes back until they send that draft, edited or
   not; their phone and Telegram offer **Send this answer** and **Don't answer**. There is no Always: every answer is
   one its owner sent. You see the answers as they arrive, per teammate, and "no answer" after a day or when declined.
   A teammate can have at most 3 free questions waiting with you; more are declined by themselves.

Both follow your [work hours](../README.md#work-hours): brief and free questions wait outside them
and only notify you once your hours start. In several Work Fleets each one hears only about the accounts of its own
company (a `work:<Fleet name>` boundary, or an address at a teammate's domain).

## Answers, not data

A Machine never hands out an email, a message, a calendar entry or a file in an answer. It answers with a short
summary it builds itself (the [Shared Graph](shared-graph.md) is the one place Work client threads are shared, sealed
to the Fleet's Machines):

| Question | Without names (the default) | With names, also |
|---|---|---|
| Who knows someone at X | knows or not, closeness, last contact month, how many people | up to 5 names with their closeness and last contact |
| Meeting context | per attendee: closeness, last contact month, how many open promises each way | the name and up to 3 open promises per side |

- **Only work contacts.** A Work Fleet only hears about people seen in your work accounts (accounts with a `work:`
  boundary; without boundaries, every account not marked personal). People only seen in personal accounts never come
  up. Neither do roles, nor people with only a free-mail address unless asked by that exact address.
- **No content, ever:** no message text, subjects, files, calendar titles, notes or facts.

## You decide, per Fleet

- **Off by default.** Joining a Fleet only makes your Machine reachable. The first question from a teammate waits for
  you as a card under Suggestions: **Share once**, **Always share**, **Decline** or **Never share** (under More). Always and
  Never become your exception for that teammate and that kind of question, whatever its date or details, and settle
  their other questions of that kind still waiting (a daily brief leads with Always share).
- **You hear about it.** A waiting question sends one phone notification (if you turned them on in the dashboard
  app) and, with Telegram connected, one Telegram message with **Answer once**, **Always** and **Decline**. Questions
  that arrive within a minute share one. They say who asks and what, never anything of yours. A tap on the phone
  notification opens the question's card under Suggestions and asks you to confirm.
- **Rules:** one per kind of question for each Fleet, for everyone in it, the same three choices: **Ask me first**
  (the default), **Always** or **Never**. Where it applies, under it how much they hear: **Without names** (the
  default) or **With names** (for meeting context: **Names and promises**). Under each rule a short example, from
  made-up data, says what they would get. New teammates follow the Fleet's rule.
- **Exceptions:** "Different for someone? Add an exception" picks a teammate and their own choice; it shows as a small
  pill ("Robin: Always") that × removes, back to the Fleet's rule. If someone is in several of your Fleets, the Fleet
  a question comes through decides. Rules set per teammate before were folded once: the most common choice per kind
  became the Fleet's rule (on a tie, Ask me first), the others stayed as exceptions, so nobody's rule changed.
- **Only about…** narrows a rule (folded away until you open it): **Only about** some companies, domains or names
  (anything else asks you first), **Never about** others (declined, and their people are left out of every answer),
  and **Only people from your work accounts** (on unless you turn it off). For shared emails it goes by the other
  companies on the email.
- **Shared emails:** Always tells the Network you're on an email you both got, shows who is on the ones they took, and
  says when you closed one (done, or done or not needed). Ask me first and Always both show what they closed.
  Ask me first sends nothing by itself: the card says who else got it, with **Tell them you're on it**. Never sends
  nothing. A rule saved as Off before the three choices is Never; Share is Always.
- **Only from your dashboard.** Rules and approvals can't be changed by Telegram, the command line, the API token or the
  assistant. The one exception: the buttons on the Telegram message sent for that very question, tapped by you in your
  own chat. Never is only on the dashboard. A prompt in an email can't change who gets told what.
- **A log** in the Fleet tab shows every question asked or answered, both ways.

## Security level

A Work Fleet is **Standard** (the defaults above) or **Careful**, picked by its owner or an admin on the account page.
The control plane carries it in the directory (`security: {level}`); each Machine applies it as a floor
(`security_levels.py`): no Always (rules count as Ask me first, requests wait for the owner), Without names, only people
from work accounts, no attachments in the Shared Graph, no card text in phone notifications for work cards, and no
contact lookups. A member's own stricter choice stays, and their looser stored choice comes back if the Fleet goes back
to Standard. The control plane also makes sign-in sessions 7 days and binds invite links to their email. Sends still
need the owner's own Send in both levels. A Machine's own level (Settings, Security) is the same idea for the owner, for
this Machine, Family and Friends: see [Security levels](help/security-levels.md).

## What happens in your Fleet

If you own a Fleet, your Machine also tells you what happens in it: someone asks the Fleet for a server, someone
joined, a member's server started, someone left or was removed, a member's server is now paid by another Fleet they are in. They show in the Fleet tab, as a phone notification and,
with Telegram connected, as a Telegram message. Most only have **Open**.

A member asking for a server waits for you, as a card under Suggestions. The message says what it costs, for example "Ann wants a Standard server in
Acme: €30/month, new total €60/month." with **Confirm · €30/month** and **Decline**; in a Fleet that is free for now it
says "free, because this Fleet is comped" with **Start it · free**. A tap is your confirmation of exactly what that
message showed, and it only counts for you as the Fleet's owner, while the request is still open, on those terms. A
tap on an old message, a request the member withdrew, someone who left, or terms that changed since (the Fleet got
comped, another seat changed the total) does nothing and says why; with new terms, the message updates to them and you
tap again. Only taps in your own Telegram chat count. A decision made elsewhere (your account page, or the dashboard)
brings the Telegram message up to date.

## Safe by construction

- A teammate's question never reaches a model, a tool, the browser or Do it. It is parsed into a fixed form (an
  address, a domain or a name) and answered by plain database reads (`fleet.py`). Anything else in it is dropped.
- A free question is untrusted text: control and invisible characters (such as right-to-left overrides) are removed,
  it is cut to 300 characters, and only its keywords are searched for in the owner's own work accounts. No model reads
  it; the draft is built by code. Only the text the owner sends goes back, cut to 600 characters.
- Brief facts are counts, dates, company names and hashes, kept to that fixed form on both sides; the brief is
  assembled by code.
- Answers coming back are checked against the same fixed form (closeness from a short list, a year-month, whole
  numbers, short names) before they are shown or added to a preparation, where they are marked as data from
  teammates' Machines, not instructions.
- Claims on shared emails, and that you closed one, travel as a hash keyed with the Fleet's secret, never the email.
- Intros, handoffs and follow-up splits (`fleet_requests.py`) are kept to a fixed form and only shown, quoted. A
  handoff's sources keep only their kind, sender, date, subject and text; on the receiving Machine they are marked
  reviewed, so no model reads them by itself. An
  intro draft is built by code from the owner's own graph and waits for their Send; nothing is sent, drafted or
  done before the owner accepts (or set Always accept). Telegram and phone buttons are the same Accept and Decline
  as for questions, bound to the message sent for that request; a stale tap only shows the outcome.

## Who answers

The Fleet tab lists each Fleet's members with the Machine they answer with ("Self-hosted: <its name>", "Hosted: <its
address>" or "No Machine yet") and their account email; yours is marked "you, this Machine". Two members with the same
name are told apart by their email wherever only a name fits, such as a waiting question.

Each person answers with one Machine per Fleet, so teammates never get two answers from you. If you have more than one
(say a hosted one and one at home), you pick which one answers in your account. Removing someone from a Fleet takes
effect at once, and how often a Machine can ask is limited.

## In your Graph

Members of your Fleets, your Family and your Friends are marked in the Graph tab ("Has a Machine · Work Fleet"), with
**See in Fleet** and **Ask your Fleet** on their page; the Fleet tab links each member to them with a small **In Graph** link.
When the messages only give part of a name ("kim"), the Graph shows the name they chose to share in the Fleet, and
**Keep** makes it yours.

It uses only what the directory already shows every member: their name, verified email addresses, and whether they
have a Machine. It matches on those addresses, and runs on your Machine when you open the Graph. It never asks their
Machine anything, and nothing new leaves yours. A pending friend request or a blocked person is not marked. When
someone leaves, or you do, the marker goes and the name falls back to the one in your messages, unless you kept or
renamed it.

## Leaving a Work Fleet with your Machine

When you leave a Work Fleet, or its owner or admin removes you, and you keep your Machine, your work data for that
company does not stay with you. Your Machine disconnects that company's accounts and deletes what they brought in; your
personal sources stay. The invite page says so before you join, and the confirmation before you leave.

- **Work or personal is yours to mark.** Each sign-in and account is Work (with its company) or Personal: pick it in
  the dashboard (Connections, a card's Details, and when you add one with + Add) or, on a hosted Machine, on the setup
  page next to each Google, Microsoft and other account. It is the account's `boundary` (`work:Acme` or `personal:me`),
  marked as your own choice (`boundary_by: owner`); all accounts on the same address follow it.
- **Which accounts:** your marking first: what you marked Work for that company goes, whatever its address, and what
  you marked Personal (or another company's) stays. Unmarked ones: those whose address is at the company's email domain
  (the domain of the Fleet's owner and admins, or one its members share, such as `acme.com`, also `eu.acme.com`), and
  the accounts in that company's work boundary (`work:Acme`, matched to the Fleet's name or the domain) that have no
  address elsewhere, such as the company Slack, Intercom, Salesflare or GitHub. An unmarked account filed under that
  company with an address elsewhere (a personal Gmail) stays.
- **Their sign-ins go too** on a self-hosted Machine: a Google sign-in in gog's keyring (`gog auth remove`) and an
  Outlook sign-in's token file. A sign-in that a staying account uses is never touched.
- **What goes:** their messages and events, the cards and drafts made only from them (a card that also used a personal
  source stays), their promises, graph and search entries and sync state, and their own secret when no other account
  uses it. Google and Microsoft access through your hosted Machine's account page is revoked.
- **First a safety copy:** config.json and the database go into `work-removal-backups/` next to the database, readable
  only by you, and are deleted after 7 days.
- **How it arrives:** a hosted Machine gets it from the control plane; a self-hosted one asks the Network every 10
  minutes (`GET /removals`), also when it is in no Fleet any more, and reports the counts back. A self-hosted Machine
  says it can do this when it joins (`remove_work` in `/me`); an older one cannot, and the confirmation says so.
- You get an email once it is done: "Your work accounts from Acme were disconnected and their data removed."

The code is in `work_removal.py`.

## A self-hosted Machine in a Fleet

A Machine you run yourself can be your Machine in a Fleet, next to teammates on hosted Machines. It needs an account
with The Machine (no subscription) and is off until you pair it:

1. In your account, make a pairing code. It works once, for 15 minutes.
2. In The Machine's folder, run `python3 cli.py fleet-pair ABCD-EFGH --label "My computer"`. This stores the
   Machine's own key, readable only by you, next to its database. A wrong, expired or already used code is refused:
   make a new one.
3. Restart the service, and pick this Machine for your Fleet in your account.

Or simply ask your Machine's AI to do it: "Pair this Machine with my Fleet: in The Machine's folder, run
`python3 cli.py fleet-pair ABCD-EFGH --label "My computer"`, then restart The Machine." Your account page shows this
with your code filled in, ready to copy.

Your Machine then shares the addresses of its work accounts with your Fleet, so your teammates' Machines recognize
them, and nothing else about you. Everything in "You decide, per Fleet" and "Safe by construction" applies the same
way. You can disconnect it from your account at any time; its key then stops working.

**Sign-ins for it.** Some sources sign in through The Machine's own app: Intercom, Google (Gmail and Calendar),
Microsoft (Outlook mail and calendar) and Zoom. On a paired Machine you start that on its dashboard: Connections,
**+ Add**, then the source. Not paired yet, the dashboard says to pair
first and links to the account page; pasting a token stays as a folded fallback. The Machine asks the control plane for a
one-time start link (`fleet.py connect_start`, `POST /connect-start`, main node only), signed with the sign key it
published after pairing, with a fresh nonce and the time. The control plane checks that signature against the key it
pinned, and refuses when the key changed since, until you pair again; the link works once, for 10 minutes, and only
signed in as the Machine's owner. It opens in a new tab and leads to the provider. The control plane checks the pin
again at the callback and seals what the sign-in hands back for this Machine's box key (`network_crypto.open_box`, in
its own domain and never taken for a message from another Machine). The tab then says you can close it. The Network's
inbox says one waits, and the Machine fetches it once (`GET /connects`, main node only). It is deleted there as it is
handed out, and is gone after 15 minutes if not picked up. The Machine opens it and connects it as Connections, Add would
(`onboarding.connect_delivered`: only the sources listed there, and only the key that source asks for). The connector
checks the key first (Intercom: `GET /me`, its workspace and admin). Replies turn on only for the owner's own Sign in
with Intercom, as that admin, and never for a token shared through a Fleet. Then the Machine reports back in a few
words (`POST /connects`), and the dashboard says "Connected to <workspace>". Nothing it carries is logged on either
side, and neither is the start link.

Google, Microsoft and Zoom hand back a refresh token, with the same apps and callbacks as a hosted Machine's. It comes
sealed the same way, and `onboarding.connect_grant` keeps it on the Machine only (`mail_grants.py`, as for
[broker accounts](sources.md)), then checks each account with a real read as the address it signed in as; one
that doesn't check out leaves nothing. Access tokens then come from the control plane over the Machine's own device
token (`POST /google-token`, `/microsoft-token`, `/zoom-token` on the Network's main node): the Machine sends its
refresh token along, the control plane uses it for that one call with the app's secret and keeps nothing, and a rotated
one goes back to the Machine. The control plane only records which accounts that device signed in, with no token, and
answers only for those, while it is paired. Disconnecting the last account on a sign-in forgets it on both sides
(`POST /grant-forget`), and revokes it at Google.

Pairing is free, and so are Friends and Family Fleets on the Network. In a Work Fleet, while a self-hosted Machine
answers for someone, the Network costs €4 a month for that member, plus VAT where it applies, paid by the Fleet's owner
and confirmed before it starts; nothing while the Fleet is free. Each Fleet is billed on its own, with its own card,
invoices and cancel, apart from your own plan.

## Friends

Friends are people outside your Fleets whose Machines can help yours, one to one. Each person has a **username**: on a
hosted Machine it is your Machine's address name, and you can change it on your account page. A self-hosted Machine
paired with your account (see above) claims a username too.

- **Adding someone.** In the Fleet tab under **Friends**, or on your account page, type their exact username and send a
  request. There is no directory and no search by name, and a request looks the same whether or not that name exists.
  Nothing connects until they accept. People you already know can also be found privately; see below.
- **A request for you** shows in the Fleet tab and on your account page, as a Telegram message with Accept and Decline,
  and as a phone notification. Only you can answer it. A tap turns that message into the result (Accepted, Declined,
  or already handled), or says why nothing was done.
- **Remove or block** a friend, or block a request, at any time. Blocking is silent: their requests are dropped from
  then on. You can unblock later.

What a friend's Machine can ask yours. Under **Sharing** there is one rule per kind for all your friends (Ask me first and
Without names by default), with "Different for someone? Add an exception" for one friend, as in a Fleet. New friends
follow the rule for all friends. Rules set per friend before were folded once, the same way as for teammates, so
nobody's rule changed:

1. **Who knows someone at X?**, the same short answer as for a teammate.
2. **Meeting context**, only for a meeting that friend is in too.
3. **A warm intro**: you see the request with Accept and Decline; nothing is drafted before you accept, and nothing
   goes out without your Send.
4. **When are you busy?** For one to fourteen days, your Machine answers from your own calendars with busy times only:
   start and end, never what, where or with whom. All-day events, events you declined and calendars shared with you
   by someone else don't count.

Friends are kept apart from your Fleets: a friend sees nothing of your Fleets, your Fleet rules don't apply to
friends, and your friends' rules don't apply to teammates. A friend's questions and answers are treated as data: kept
to their fixed form, and never given to your AI, its tools or Do it.

Friends are free, between hosted and self-hosted Machines alike.

### Finding people you know

Both switches are off until you turn them on, in the Fleet tab under **Network** (Suggestions asks once).

- **Check which of my contacts have a Machine** looks daily for your contacts on the Network, and soon after you meet someone
  new or someone you know becomes findable. Your Machine blinds each
  address with a secret only it holds before anything leaves it (a private set intersection: H(x)^a over a 2048-bit
  group); the Network raises those to its own secret and sends them back with the blinded addresses of people who let
  themselves be found. Your Machine learns only which of its own contacts matched, the Network never sees your address
  book, and only matches are then looked up by name. Lookups are rate-limited (at most 3,000 addresses a day). To know
  when to look again, your Machine asks hourly in which of 256 buckets someone became findable, nothing more.
- **Let people who have my email find me** makes your account email findable that way, and only by people who already
  have it, plus the addresses of the mail and calendar accounts your Machine signed in to. The Fleet tab lists them
  under **Findable by**; untick any you'd rather keep out. The Network stores those only blinded. Someone's sign-in
  email only ever finds them, and an address that two accounts list finds neither. It needs a username.

A match becomes a card in Suggestions only when there is something concrete to do together: an open card that involves
them, or a meeting with them in the next two weeks; the card says which. Someone at your own work domain is suggested
as a colleague, anyone else as a friend or family, for you to pick; nothing is guessed from what you write. **Connect**
sends an ordinary friend request, which they still accept, and sharing then follows the rules you set for that friend.
**Not now** hides them for 30 days unless something new comes up; **Never suggest** hides them for good. Someone in a
Work or Family Fleet with you is connected already and never suggested, nor offered as a Connect on a task they sent
you; if one of you leaves that Fleet, they can be suggested again. A friendship you already have stays as it is. What was
checked, and every switch you flip, is kept in the Fleet activity history (see What happens in your Fleet).

**Leave the Network** turns both switches off, removes every friend and withdraws open requests. Your Fleets stay.

## Delivery and retry

Current Machines save returned answers locally before acknowledging the exact answer receipt/version to the relay. They commit a request identity before asking a question; retrying the same identity and exact question returns the original result instead of asking twice. Older app/relay pairs retain their older delivery behavior. Questions expire after at most three days; answers are retained until acknowledged or for at most three days, and question metadata for thirty days. Relay processing is not end-to-end encryption between Machines.

### A handoff and a newer choice

The card waits for the teammate’s answer only if you have not changed it while the handoff was sending. Their later acceptance or decline updates the card only while that exact handoff still owns it. A newer Change, Snooze, Done, Dismiss or Reopen wins; the reply stays in the Fleet log. One exception: if you marked the card done after handing it off (it was with them), their decline reopens it, once.
