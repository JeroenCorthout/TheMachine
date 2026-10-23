---
title: Slack
area: connections
summary: The channels and direct messages you're in. Sends a direct message only after you approve it.
covers: connector:slack
code: assistant.py, next_actions.py, onboarding.py
site: Connect, More sources, Remove
---
## What it reads

The conversations you're a member of: channels, private channels, direct messages and group messages, the last 7 days when you connect, then everything new. It never joins a channel or opens a conversation by itself.

## What it can send

A direct message to one person, as you, and only after you approve it on the card. It never posts in a channel.

## Connect it

Slack is read through Maton, which holds the Slack sign-in.

1. Sign in at maton.ai, connect Slack there, and copy your Maton API key from Home.
2. On a hosted Machine: open your setup page at app.themachine.computer/setup, go to **More sources**, open Slack, paste the key and press **Connect**.
3. On your own computer: run `assistant connect slack`, or **Connections**, **+ Add**, Slack.

## Remove it

- In the dashboard: **Connections**, open Slack, **Details**, then **Disconnect**. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Uninstall it on Slack

Remove the Slack connection in Maton, and delete the Maton API key you gave. A Slack admin can also remove the app from the workspace, under Manage apps.
