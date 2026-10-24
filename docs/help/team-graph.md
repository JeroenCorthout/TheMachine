---
title: Team Graph
area: fleet
summary: Experimental. Share client work with your Work Fleet, like a CRM. What goes, what never does, and how to keep something private.
covers: team-graph
code: shared_graph.py, fleet_trust.py, dashboard.html
order: 2
---
In a Work Fleet, your Machine can share your client work with your teammates' Machines, like a shared CRM. It's **Experimental**: new and still changing, so it starts off.

## Turn it on

An admin turns on **Share the Graph in this Fleet** on **What's shared**, and confirms the company may share this mail and has told its people. Everyone sees whether it's on. A Fleet that already shared before stays on. Then each person looks over what would go and taps **Start now**. Nothing goes before that.

## Your connections

**Your connections** on **What's shared** lists each one with a switch. Work ones at your Fleet's own domain start on. One at another company's domain starts off, even when it's a Work account: it says "Other company, not shared". You can turn it on. Personal ones never go. Turning one off takes back what it shared.

Inside a connection that's on, each thread is still sorted. Personal and colleague-only threads stay out. Sensitive and automated mail stays out of automatic sharing; your explicit sharing choices can override those classifications.

## Required by the Fleet

An admin can require everyone's **Work email** and **Work calendar** at the Fleet's own domain, under **Required by the Fleet**, after confirming the company has told its people. Work mail belongs to the company, like in any CRM. Those then show "Required by" your Fleet and can't be turned off. Threads you keep private stay private.

## What goes to the team

- Client threads: email with people outside your company about work, with their attachments.
- Work people, organizations and projects, with a short timeline.
- On a Team board, a project's meetings, Zoom notes and chats too, once they're sorted as client work.

## Kept out of automatic sharing

- Sensitive threads: buying or selling a company, investments, valuations, deal talks, HR, hiring and recruiters, legal, health, money, security. Not even that they exist.
- Automated mail: receipts, invoices, alerts, newsletters, and your company's own bills with its suppliers.
- Threads with only colleagues, personal mail and anything from a personal account. Personal never goes, not even when you tap Team.
- Anything you marked **Keep private**, and boards and projects set to Only you.

Each thread is judged on its own, by what it is about. Classification can be wrong; review what would go and use Keep private to correct it. Your explicit Team choice or a share with named teammates can override a sensitive classification; check the recipients before sharing.

## Share a board with chosen people

On a board's **⋯** menu, tap **Share board…** and pick teammates. Only they get its projects with their full timelines: email threads, meetings and their Zoom notes, Intercom conversations, chats, cards and moves. Sensitive ones go too, such as deals.

Personal and colleague-only threads stay out, and so do people in your Family or Friends and automated mail when only its board is shared. Connections you have not selected stay out too.

- A project added to the board later goes too. One moved off the board is taken back.
- Take someone off, or pick nobody, and it is taken back from their Machine.
- One thread can go to chosen people too: tap its label, then **Share with…**. This explicit choice can include sensitive or automated work mail. Personal and colleague-only threads cannot be shared.

## Not sure? It waits

When your Machine isn't sure, it doesn't share. Those threads wait under **Share these?** until you pick **Share with the team** or **Keep private**.

## Before the first share

Open **Fleet**, then **What's shared**. It lists the client threads that would go, by organization. Tap **Keep private** on any of them, then **Start now**.

Until you start, your Machine reminds you every few days, during your work hours. Admins see who hasn't started yet.

## What's shared

**Fleet** › **What's shared** shows everything in one place: counts per kind, every shared thread with why it went, the ones waiting under **Share these?**, and what you keep private. **Settings** has a shortcut, and so does the label on each thread.

A new client thread waits an hour before it goes, so there's time to change your mind.

## Unshare

Tap the **Team** label on a thread, then **Unshare**. Your Machine tells every teammate's Machine, and each deletes the thread, its attachments and its lines in the timeline. A Machine that was off does it when it's back. Someone may have read it already.

**Keep private** on a person, organization or project unshares all of it the same way.

Keep two threads with the same company private and your Machine asks once: always keep that company's threads private?

## Who sees it

Only your teammates' Machines. Everything is encrypted end to end, sealed for each teammate's Machine. The Machine Network only passes it on and can't read it.

## Joining and new devices

An admin lets each person into the Fleet once. After that, sharing with them needs no more OKs.

A new Machine of someone already in gets the team's Graph after 24 hours. Admins see it in the meantime and can tap **Not theirs** to keep it out.

## When someone leaves

They get nothing new from that moment. What they shared stays with the team. An admin can also **Take out** someone, and **Share again** later.

## Turn it off for everyone

An admin can turn **Share the Graph in this Fleet** off on **What's shared**. Everyone sees whether it's on. When it's off, nothing new goes.

**Turn off** keeps what went on teammates' Machines. **Turn off and remove shared data** makes every Machine delete what it got. When sharing is back on, each person looks it over again first.

## Stop sharing your own work

Turn your connections off under **Your connections**, unless the Fleet requires them. What they shared is taken back.

See also [Fleets](fleet.md) and [Security and privacy](security-and-privacy.md).
