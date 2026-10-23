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

Everyone you're in touch with, closest first. A person's page shows how often you talk, their organisation and projects, **Open between you** (promises both ways, with **Done** and **Drop**), and their recent messages.

- **Edit**: say what they are to you, like "accountant".
- **Rename**: give them the name you use. An empty name brings back the one from their messages.
- Someone's addresses and chats are joined by themselves when it's clear they're one person, like a work email with their own address in CC.

## Same person?

When two people might be one, their page asks if they are the same person, and People lists them under **Same person?**. **Merge** joins them, with all their messages, notes, roles and projects. **Not the same** keeps them apart for good.

**Merge all** merges every pair in the list at once, except the ones you marked **Not the same**. **Undo** right after splits them all again.

A joined person's page lists why under **Joined from**. **Undo** splits them again.

## Job title and photo

People with a company address get a job title, photo, LinkedIn and city, looked up like [organisation details](organisation-data.md). Only their name and company domain are sent on a hosted Machine. Turn it off in **Settings**, under Graph: **Look up work contacts**.

Your connections fill in more as they run, with what they already read and nothing extra asked: a title, phone and time zone from Slack, a bio, place and website from X, a headline and photo from LinkedIn, and job titles from Google contacts and Salesflare. What someone says about themselves comes first. Hover over the line under their name to see where it came from. It all stays on your Machine.

## Delete someone permanently

On their page, open the ⋯ menu and pick **Delete permanently…**, then **Delete**. It removes them and what your Machine knows about them: their entry, addresses, looked-up details, the messages only between the two of you, and notes about them. Cards that involve them stay, with their name taken out. They won't be added again. It can't be undone.

## Who's who

Roles your Machine read in your messages, waiting for your word. **✓ Right** confirms one; **Edit** changes it. It learns from your edits. This shows only when roles are waiting.

## Organisations

Companies, from the addresses and names in your messages. See [Organisation details](organisation-data.md).

Mail from email warm-up tools is kept out, under "Hidden as warm-up", with **Undo** per domain.

## Projects

Named by your Machine from what your messages are about. Signals that look like they belong show as "probably part of it". **Rename** a project; renaming it to another project's name joins the two.

## What it knows about someone

Under Known about, each fact has a menu:

- **Forget** deletes it everywhere.
- Not about them only takes it off that page.
