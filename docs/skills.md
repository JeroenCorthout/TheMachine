# Skills

A skill is a how-to your Machine's agent follows for one kind of work, for example "Research and answer customer
inquiries". It has three parts: a **name**, **when to use it** (one line), and **instructions** (steps, sources to
check, what never to do; Markdown works).

Skills are kept in **Settings > Skills** on the dashboard. You can write your own, start from the example, approve one
your Machine proposes, or install one a teammate or friend shares with you.

## How OpenClaw uses them

Skills are plain [OpenClaw skills](https://docs.openclaw.ai/tools/skills). Each skill that is on is written to
`<workspace>/skills/the-machine/<name>/SKILL.md` in your OpenClaw workspace. OpenClaw picks them up like any workspace
skill, and its agent reads one when a task matches "when to use it". New and edited skills apply from the agent's next
session; the Gateway doesn't need a restart.

The `the-machine` folder belongs to The Machine: it is rewritten from Settings > Skills, so edit skills there. Your
other skills in the workspace are left alone. If a name is already taken by one of them, The Machine's copy gets a
different folder name (`-2`), so it never replaces yours. A whole `SKILL.md` can be pasted into the instructions box:
the name and description are taken from its header.

## Instructions only

A skill can't change what your Machine may do. Only the name, when to use it and the instructions are written; any
other OpenClaw header field (metadata, environment variables, install steps, tool dispatch, allowed tools) and any
scripts are dropped. A skill runs under your Machine's own rules and approvals, like anything else the agent does:
Do it still asks you, nothing is sent without your Send, and a Fleet's rules stay as you set them.

## Import from Claude or ChatGPT

Both use the same `SKILL.md` form. **Import** under Settings > Skills lists skills to bring in; you tick which ones.

- **From Claude** or **From ChatGPT**, picked above **Find my skills**, is where it looks: that account only.
- **Find my skills** looks on this computer (Claude Code's `~/.claude/skills`, including skills and your own plugins
  synced from claude.ai, Codex's `~/.codex/skills`, and `.claude/skills` in your projects). Then your Machine reads
  the skills you made or uploaded yourself in that account, read-only, in its own browser, straight on the skills page
  and with the site's own download of each skill: on ChatGPT **Skills > Installed** (chatgpt.com/skills), on claude.ai
  **Customize > Skills**. Built-in, vendor and workspace-shared skills, custom GPTs, projects and custom instructions
  are left out. Your agent then marks a few as **Recommended**, judged against the work your Machine actually does. A
  hosted Machine has no such folders; there it only checks your account. Each place shows its own state (found,
  looking, not signed in, couldn't check), and when it's done, one line sums up how many it found and how many it
  picked.
- **Nothing found?** It says where it looked and why (for example "none you made or uploaded yourself"), and opens
  **Or bring one in yourself** with how to download a skill from that site.
- **Signed out?** If your Machine's browser is signed out of ChatGPT or claude.ai, the list shows **Sign in to ChatGPT**
  (or Claude). It opens the same sign-in dialog as for LinkedIn and X, including the one-time code the site sends; once
  you're in, it looks in that account by itself. Your agent never signs in or types a password.
- **Bring one in yourself**: upload a `.skill` or `.zip` or a folder, or paste a `SKILL.md`. In ChatGPT: **Skills >
  Installed**, a skill's **⋯** menu, **Download**. In Claude: **Customize > Skills**, open your skill and download it.

The list starts short: **Recommended**, then **Yours**. The rest waits in closed groups with counts (in your projects,
from vendors, built in), and a search box finds any of them. Each skill shows where it comes from, its scripts and other
files, and its license. What you just imported moves to **Imported**, with **View**. One you have already is under
**Already in your skills**, with **View** and, for your own, **Replace** to update it. One whose license doesn't allow
copying (some built-in Claude skills), or whose name OpenClaw already uses, is under **Can't import**, with why.
**Import selected** and **Close** stay at the bottom of the panel.

An imported skill becomes your own, instructions only, and remembers where it came from. Reference `.md` files its
instructions point to are added to them when they fit. Scripts and other files are left out, and **Read** says what was
left out; when the instructions need the scripts, the skill says so to your agent. Claude Code tool names with a plain
OpenClaw equivalent are renamed (`Bash` → `exec`); other tool-specific parts are flagged, not changed. A skill you have
already, or that OpenClaw already has by that name, isn't imported twice; **Replace** updates your own in place.

A skill that needs its scripts can instead be installed as OpenClaw's own, scripts included: **Read**, then **Install in
OpenClaw with its scripts**. Like ClawHub, that is only exactly what you read, never forced past OpenClaw's install
policy.

## Proposed by your Machine

When your agent notices the same kind of work coming back, it can propose a skill for it
(`assistant skills-propose --name ... --description ... --file -`). A proposal stays off and waits in Settings > Skills
with **Approve** and **Dismiss**; at most three wait at a time. Only your tap on the dashboard turns it on.

## From your notes

What you teach on cards (facts and draft rules, under Feedback > What I've learned) can pile up around one kind of task:
listing exchanges, support handoffs. When a review sees 5 or more notes about one such scenario (judged by meaning, while it
condenses the list), it suggests a skill on a normal card: the skill's name and when to use it, the notes it would take in,
and **Create skill**, or **Add to <skill>** when one of your own skills fits (made here, approved or imported; a teammate's
is never changed), plus **Not now**. Open **Read skill instructions and notes** on the card to review the complete proposal; Telegram links to the same card. Nothing changes before your tap. Not now isn't asked again until 3 more notes joined
the same ones. On yes the skill is written (instructions only, on, like any of yours) and the notes move into it: kept, listed
under the skill as "From N of your notes", and no longer in the list every task gets, so a task that needs them gets the
skill instead. The skill change, moved notes and completion receipt commit together; a restart retries the same offer without creating another skill or adding the instructions twice. What I've learned shows **Moved into skills** with **Put back**, which returns the notes (the skill stays).

## Sharing with your Fleet or a friend

Next to each skill, **Share** sends it over The Machine Network:

- to everyone in one of your Fleets (Work or Family), or to one teammate;
- to one friend (see [Friends](fleet.md#friends)).

The Share button appears once the Network carries skills for that Fleet or friend. Only the name, when to use it, the
instructions, a version number, a content hash and an optional note travel. Later edits don't go out by themselves:
the skill shows "Changed since you shared it", and **Share again** sends the new version.

The person you share with sees the skill under Settings > Skills, with who sent it and their note, and a **Needs you**
item. **Read** shows the instructions as text, quoted as the sender's words. Nothing is installed until they tap
**Install**; **Decline** removes it. Your Machine hears "installed" or "declined", and the skill shows who installed
it. A new version from the same person arrives as an update to read and install again; the skill they have stays as it
is until then.

What arrives is checked: it must be in its fixed form, with a hash that matches its content, from someone in that Fleet
or a friend. Anything else is declined. One person can have at most five skills waiting for you at a time, and the
Network limits how many skills one person can share per day.

Sharing a skill is free. Like every question on the Network, a shared skill is sealed for the receiving Machine and
signed with the sending Machine's own key (see [The Machine Network](network.md)), so the Network can't read or change
it and the receiver knows whose Machine sent it.

## Everything OpenClaw has

Below your own skills, Settings > Skills lists every other skill OpenClaw has, grouped as Workspace, Bundled with
OpenClaw and Other. Each shows whether it is ready or what it still needs (a command, a key, another OS). The list comes
from `openclaw skills list`, refreshes every 30 minutes and on **Check again**, and the search box filters it.

These skills are only read here, never edited. **Read** shows their `SKILL.md`. **Turn on/off** sets OpenClaw's own
`skills.entries.<name>.enabled`; the Gateway doesn't need a restart. Your agent already sees every skill that is ready,
in preparations too.

## From ClawHub

**Search ClawHub** uses `openclaw skills search`. Results are third-party: written by other people, not checked by The
Machine. **Read** shows the `SKILL.md` with notes on any scripts, install steps, keys it asks for and ClawHub's scan.
**Install** is only there after reading. It runs `openclaw skills install` for exactly that version, and only if it is
still the `SKILL.md` you read. Such a skill is not limited to instructions like your own: it is OpenClaw's, with
whatever it brings.

## There's a skill for this

When your agent reviews or prepares a card and an installed skill that isn't ready yet, or one on ClawHub, fits what
the card is about, it can add a quiet "There's a skill for this" line to the card (`skill_hint` on `suggest` or
`prepare`). It also shows under Settings > Skills. **Look** opens it; **Not now** dismisses it for good. At most three
a week, nothing is ever installed or turned on by itself.

## From the command line

- `assistant skills` lists your skills and what waits for you.
- `assistant skills-propose` proposes one (see above). Installing, importing, sharing and deciding are on the dashboard only.

### Reading a ClawHub release

The preview shows the complete SKILL.md. If its release file list cannot be fetched, try again: a missing list is not shown as “no scripts”. Installing uses OpenClaw’s normal version-pinned command after your existing Install choice. No extra approval tier is added.
