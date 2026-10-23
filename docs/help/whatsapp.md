---
title: WhatsApp
area: connections
summary: Your WhatsApp chats, read as a linked device. Replies only after you approve.
covers: connector:whatsapp
code: onboarding.py, next_actions.py
site: Link WhatsApp, More sources, Remove
---
## What it reads

Your WhatsApp chats, as a linked device of your phone's WhatsApp, like WhatsApp on a computer. When you connect it reads the last 7 days.

## What it can send

A text into a chat that already exists, only after you approve it on the card.

If a card came in by email but you talk to that person on WhatsApp, say so in the card's chat. The Machine finds your existing chat with them and switches the draft to WhatsApp, for you to review and send. It never starts a new chat.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup, go to **More sources** and open WhatsApp.
2. Press **Link WhatsApp**.
3. On your phone, in WhatsApp, open Settings, Linked devices, Link a device, and scan the code. A pairing code by phone number works too.

On your own computer, run `assistant connect whatsapp` and scan the code it shows in the terminal.

## Remove it

- On a hosted Machine: press **Remove** next to it on your setup page. Your server unlinks itself and deletes what it kept for WhatsApp.
- On your own computer: **Connections**, open WhatsApp, **Details**, then **Disconnect**. Then unlink it on your phone too.

## Unlink it in WhatsApp

On your phone, in WhatsApp, open Settings, Linked devices, tap your Machine and log out.
