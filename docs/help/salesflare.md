---
title: Salesflare CRM
area: connections
summary: Accounts, contacts and deals, so it knows who matters. It reads with your API key.
covers: connector:salesflare
code: assistant.py, onboarding.py
site: More sources, Connect, Remove
---
## What it reads

Your accounts, contacts and deals in Salesflare, and the account feeds: colleagues' emails that don't involve you, and meetings with notes.

## What it can change

Nothing, unless you set `join_pipelines` in your Machine's settings file. Then it adds you to accounts in those pipelines by itself. It is empty by default.

## Connect it

1. In Salesflare, open Settings, API keys, and create a key.
2. On a hosted Machine: open your setup page at app.themachine.computer/setup, go to **More sources**, open Salesflare CRM, paste the key and press **Connect**.
3. On your own computer: run `assistant connect crm`, or **Connections**, **+ Add**, Salesflare CRM.

## Remove it

- In the dashboard: **Connections**, open Salesflare, **Details**, then **Disconnect**. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Revoke it in Salesflare

Delete the API key in Salesflare, under Settings, API keys.
