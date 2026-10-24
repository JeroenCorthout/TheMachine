---
title: Zoom
area: connections
summary: The AI Companion summaries and transcripts of meetings you host. It only reads.
covers: connector:zoom
code: connectors/zoom.py, meeting_notes.py, onboarding.py
site: Connect Zoom, More sources, Remove, Sign in with Zoom
---
## What it reads

The AI Companion summary of each Zoom meeting you host, and its transcript when Zoom has one: the last 7 days when you connect, then new ones as they come in. It also reads your Zoom name and email, to know whose meetings they are.

Summaries need a paid Zoom plan, AI Companion turned on during the meeting, and no end-to-end encryption.

## What it does with them

Each summary is matched to the meeting in your calendar. Your reviews then use it for that meeting's follow-ups, and a Fleet follow-up split comes filled in with who does what. You check and edit it first; nothing is sent until you send it.

## What it can do

It only reads. It never creates, changes or deletes anything in Zoom, and it only sees your own meetings. Your Zoom sign-in is kept on your own Machine.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup and go to **More sources**.
2. Open Zoom, pick work or personal, and press **Connect Zoom**.
3. Sign in to Zoom and allow The Machine. It is made by Salesflare.

On your own computer, once it's [paired with your account](fleet.md): open **Connections**, then **+ Add**, pick Zoom and press **Sign in with Zoom**.

## Remove it

- In the dashboard: **Connections**, open Zoom, **Details**, then **Disconnect**. It stops reading. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Uninstall it on Zoom

Sign in at marketplace.zoom.us, open Manage, then Added Apps, and remove The Machine. It can no longer read anything. What it already brought in stays on your Machine until you delete it: see [Cancel and delete](cancel-and-delete.md).
