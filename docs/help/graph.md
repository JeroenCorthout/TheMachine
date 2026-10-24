---
title: The Graph
area: graph
summary: The people, organizations and projects in your messages, kept up to date by themselves.
covers: tab:people
code: graph.py, crm.py, crm_import.py, crm_fields.py, enrichment.py, dashboard.html
order: 1
---
Your Machine builds a map of who you deal with and what's going on, from your messages, meetings and decisions. Nothing to set up, and it stays on your Machine.

## People

Everyone you're in touch with, closest first. A person's page starts with their photo, name, title, organization and place, and one line on how you're in touch ("Talk daily · last Fri, Oct 23 · next meeting Tue, Oct 27"). Below that:

- **Contact**: their phones and emails. Tap one to call or write, or **Copy** it.
- **About you**: what they are to you, Work or Personal contact, and your Family or Fleet, with **See in Fleet**.
- **People** and **Projects** they're part of, **Open between you** (promises both ways, with **Done** and **Drop**), and their **Timeline**.

Someone's addresses and chats are joined by themselves when it's clear they're one person, like a work email with their own address in CC.

The **Work** and **Personal** tabs list only those contacts. **Download CSV**, under the order menu, saves the tab you're on as a spreadsheet, also for Organizations and Projects.

### Timeline

Every person and organization page has one, newest first: email, meetings and their Zoom summaries, Intercom conversations (open or closed, with **Open in Intercom**), WhatsApp, Slack, LinkedIn and X chats, cards, promises and project moves. The chips on top show one source at a time. **Show more** loads older ones.

An organization's timeline has its people's messages and the Intercom conversations from its domain. Your timelines are yours: the team's Graph shows only what your sharing rules allow.

## Filter and sort

**Filter**, next to the tabs on People, Organizations and Projects, narrows the list by owner, tag, board, stage, organization, value and expected close date. People and organizations match through their open projects.

The filters you picked show above the list, with **Clear**. The order menu also sorts by **Value** and **Close date**. Your Machine does the filtering, so long lists stay fast. On a phone the filters open as a sheet.

## Owner

In a Work Fleet, each person, organization and project has an owner: **Owner** under About you on its page. It's you, unless you pick someone from your Work Fleet. **Mine**, next to Filter and on a board, shows only yours. A board card shows its owner's name. Each change shows in its timeline. A record shared with your team carries its owner with it, under the same sharing rules as the rest.

## Fields and tags

**Edit fields** under Fields on a person, organization or project fills in your own fields. **New field** adds one for all of that kind: text, number, date or a choice from a list. **Remove** takes a field away for all of them.

**+ Tag** adds a tag, like VIP or partner; **×** takes it off. Tags show in the lists and **Filter** finds them. A record shared with your team carries its fields and tags with it.

## Import a spreadsheet

**Import CSV…**, under the order menu of People, Organizations or Projects, adds a spreadsheet saved as CSV. Your Machine guesses which column is which; change any of them.

The preview shows each row as new, matching one already in your Graph, or skipped, with why. A match is the same email for a person (else the same name at the same organization), the same website or name for an organization, and the same name for a project.

**Import** adds the new ones and fills in the matches; an empty cell changes nothing. **Undo** right after, or **Undo last import** under the order menu, takes the last import back. The file stays on your Machine.

## One organization, several websites

**⋯**, **Same organization as…** on an organization's page makes it part of another one, with its people, projects and notes. **Add a website…** adds one more of its websites, like its .io next to its .com: people with an address there join it, and its details are looked up for every website.

They're listed under **Websites**, each with **Split off** to undo it. Organizations that look like doubles show under **Same organization?**: **Merge** or **Not the same**.

## Add someone or an organization

**New person** at the top of People, **New organization** at the top of Organizations, or **+** in the top bar on a phone. Give a name, and for a person an email and organization if you have them; for an organization, its website. When someone like it is already in your Graph, the form shows them first, so you can open that one instead. **Add** opens the new page.

Someone you add stays, also with no messages yet. Their first message joins them. An organization you added can be deleted from its **⋯** menu while nobody is in it.

## Notes

**Edit** on a person's or organization's page has a **Notes** field, for anything you want to remember. Notes show on the page and stay through merges.

## Edit someone

**Edit**, at the top of their page, changes their name, title, organization, location, LinkedIn, phones and emails, what they are to you, and Work or Personal. **Save** keeps it, **Cancel** leaves it as it was.

Your edits stay, also when their details are looked up again or a review reads something else, and through **Merge** and **Undo**. Empty a field to use the looked-up value again. An empty name brings back the one from their messages.

## Same person?

When two people might be one, their page asks if they are the same person, and People lists them under **Same person?**. **Merge** joins them, with all their messages, notes, roles and projects. **Not the same** keeps them apart for good.

**Merge all** merges every pair in the list at once, except the ones you marked **Not the same**. **Undo** right after splits them all again.

A joined person's page lists why under **Joined from**. **Undo** splits them again.

To join two people yourself, open one of them, then **⋯**, **Same person as…** and pick the other.

## Job title and photo

People with a company address get a job title, photo, LinkedIn and city, looked up like [organization details](organization-data.md). Only their name and company domain are sent on a hosted Machine. Turn it off in **Settings**, under Security: **Look up work contacts**.

Your connections fill in more as they run, with what they already read and nothing extra asked: a title, phone and time zone from Slack, a bio, place and website from X, a headline and photo from LinkedIn, and job titles from Google contacts and Salesflare. What someone says about themselves comes first. Hover over the line under their name to see where it came from. It all stays on your Machine.

WhatsApp pictures come through your always-on WhatsApp sync, when it allows them: up to 50 a day, only for people already in your Graph. Nothing is sent to them.

## Delete someone permanently

On their page, open the ⋯ menu and pick **Delete permanently…**, then **Delete**. It removes them and what your Machine knows about them: their entry, addresses, looked-up details, the messages only between the two of you, and notes about them. Cards that involve them stay, with their name taken out. They won't be added again. It can't be undone.

## Who's who

Roles your Machine read in your messages, waiting for your word. **✓ Right** confirms one; **Edit** opens their page in Edit. It learns from your edits. This shows only when roles are waiting.

## In a Work Fleet

Client work is shared with your teammates' Machines, and theirs shows in yours. Each thread shows **Team** or **Only you**. See [Team Graph](team-graph.md).

## Organizations

Companies, from the addresses and names in your messages. See [Organization details](organization-data.md).

An organization's page is built like a person's: its logo, name and what it does, one line on how you're in touch, then **Contact** (its website and LinkedIn), **About you** (what it is to you), the people and projects there and its **Timeline**. **Edit** at the top changes its details and what it is to you. **Ask the Fleet who knows** it finds a way in through your Fleet.

Mail from email warm-up tools is kept out, under "Hidden as warm-up", with **Undo** per domain.

## Projects

Named by your Machine from what your messages are about. Signals that look like they belong show as "probably part of it". Renaming a project to another project's name joins the two.

Or run them by hand, like deals in a CRM:

- **New project**: at the top of a board, **+ Add** under a stage, **New project** at the top of the list, **+** in the top bar on a phone, or from a person's or organization's page. Give it a name, a board and stage, an organization (its website or name), a value, an expected close date, people and notes. Projects already in your Graph with a name like it show first.
- **Edit** on a project's page, or **Edit** in a card's **⋯** on the board (or press E on a card). It changes all of the above.
- **Won** or **Lost**, with why. Won moves it to the board's last stage, Lost to Stalled or declined. **Reopen** undoes it.
- **Archive** takes it off the board and lists; a new message about it brings it back. **Delete** removes it for good; its messages and people stay.
- Move a card by dragging it, or with **⋯**, **Move to** on a phone. Your move stands until a newer message moves it.

### Boards

Projects of one kind in stages, like an evaluation pipeline. **⋯** on a board: **Edit board** (name, stages, which projects belong), **Open on this board** and **New board**. Reviews keep adding projects that fit and place them in a stage. When they aren't sure, the board shows "2 projects could belong here": **Add** or **Leave out**.

Tick a few, or **Select all**, to do them at once, and **Undo** right after if you change your mind. A project that bundles several counterparts becomes one project per counterpart when you add it.

**Pipeline**, at the top of a board, adds up the value of its open projects per stage, and what's expected to close per month from their close dates. Won, lost and stalled projects aren't open; what you won this month shows apart. Values are in euros unless your Machine is set to another currency. **Mine** counts only yours.

A new board, or a suggested board you save, looks through your whole history once. It finds projects and organizations that fit, makes one project per organization and puts each in a stage. The header says "Looking through your history…" while it works, then how many it added and how many wait for you to review. An Only-you board keeps what it finds to you.

## A card about someone

**New card** in the **⋯** menu of a person, organization or project starts a card about them.

## What it knows about someone

Under Known about, each fact has a menu:

- **Forget** deletes it everywhere.
- Not about them only takes it off that page.
