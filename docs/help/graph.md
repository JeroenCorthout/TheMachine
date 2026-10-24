---
title: The Graph
area: graph
summary: The people, organisations and projects in your messages, kept up to date by themselves.
covers: tab:people
code: graph.py, enrichment.py, dashboard.html
order: 1
---
Your Machine builds a map of who you deal with and what's going on, from your messages, meetings and decisions. Nothing to set up, and it stays on your Machine.

## People

Everyone you're in touch with, closest first. A person's page starts with their photo, name, title, organisation and place, and one line on how you're in touch ("Talk daily · last Fri, Oct 23 · next meeting Tue, Oct 27"). Below that:

- **Contact**: their phones and emails. Tap one to call or write, or **Copy** it.
- **About you**: what they are to you, Work or Personal contact, and your Family or Fleet, with **See in Fleet**.
- **People** and **Projects** they're part of, **Open between you** (promises both ways, with **Done** and **Drop**), and their recent messages.

Someone's addresses and chats are joined by themselves when it's clear they're one person, like a work email with their own address in CC.

## Edit someone

**Edit**, at the top of their page, changes their name, title, organisation, location, LinkedIn, phones and emails, what they are to you, and Work or Personal. **Save** keeps it, **Cancel** leaves it as it was.

Your edits stay, also when their details are looked up again or a review reads something else, and through **Merge** and **Undo**. Empty a field to use the looked-up value again. An empty name brings back the one from their messages.

## Same person?

When two people might be one, their page asks if they are the same person, and People lists them under **Same person?**. **Merge** joins them, with all their messages, notes, roles and projects. **Not the same** keeps them apart for good.

**Merge all** merges every pair in the list at once, except the ones you marked **Not the same**. **Undo** right after splits them all again.

A joined person's page lists why under **Joined from**. **Undo** splits them again.

## Job title and photo

People with a company address get a job title, photo, LinkedIn and city, looked up like [organisation details](organisation-data.md). Only their name and company domain are sent on a hosted Machine. Turn it off in **Settings**, under Graph: **Look up work contacts**.

Your connections fill in more as they run, with what they already read and nothing extra asked: a title, phone and time zone from Slack, a bio, place and website from X, a headline and photo from LinkedIn, and job titles from Google contacts and Salesflare. What someone says about themselves comes first. Hover over the line under their name to see where it came from. It all stays on your Machine.

WhatsApp pictures come through your always-on WhatsApp sync, when it allows them: up to 50 a day, only for people already in your Graph. Nothing is sent to them.

## Delete someone permanently

On their page, open the ⋯ menu and pick **Delete permanently…**, then **Delete**. It removes them and what your Machine knows about them: their entry, addresses, looked-up details, the messages only between the two of you, and notes about them. Cards that involve them stay, with their name taken out. They won't be added again. It can't be undone.

## Who's who

Roles your Machine read in your messages, waiting for your word. **✓ Right** confirms one; **Edit** opens their page in Edit. It learns from your edits. This shows only when roles are waiting.

## Organisations

Companies, from the addresses and names in your messages. See [Organisation details](organisation-data.md).

An organisation's page is built like a person's: its logo, name and what it does, one line on how you're in touch, then **Contact** (its website and LinkedIn), **About you** (what it is to you) and the people and projects there. **Edit** at the top changes its details and what it is to you. **Ask the Fleet who knows** it finds a way in through your Fleet.

Mail from email warm-up tools is kept out, under "Hidden as warm-up", with **Undo** per domain.

## Projects

Named by your Machine from what your messages are about. Signals that look like they belong show as "probably part of it". Renaming a project to another project's name joins the two.

Or run them by hand, like deals in a CRM:

- **New project**: at the top of a board, **+ Add** under a stage, in the list, or from a person's or organisation's page. Give it a name, a board and stage, an organisation, a value, people and notes.
- **Edit** on a project's page, or **Edit** in a card's **⋯** on the board (or press E on a card). It changes all of the above.
- **Won** or **Lost**, with why. Won moves it to the board's last stage, Lost to Stalled or declined. **Reopen** undoes it.
- **Archive** takes it off the board and lists; a new message about it brings it back. **Delete** removes it for good; its messages and people stay.
- Move a card by dragging it, or with **⋯**, **Move to** on a phone. Your move stands until a newer message moves it.

### Boards

Projects of one kind in stages, like an evaluation pipeline. **⋯** on a board: **Edit board** (name, stages, which projects belong), **Open on this board** and **New board**. Reviews keep adding projects that fit and place them in a stage. When they aren't sure, the board shows "2 projects could belong here": **Add** or **Leave out**.

A new board, or a suggested board you save, looks through your whole history once. It finds projects and organisations that fit, makes one project per organisation and puts each in a stage. The header says "Looking through your history…" while it works, then how many it added and how many wait for you to review. An Only-you board keeps what it finds to you.

## What it knows about someone

Under Known about, each fact has a menu:

- **Forget** deletes it everywhere.
- Not about them only takes it off that page.
