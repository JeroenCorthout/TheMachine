---
title: Server log
area: connections
summary: Your server's own log, so The Machine spots its own trouble. It only reads.
covers: connector:gateway
code: onboarding.py
site: More sources, Remove
---
## What it reads

The log of your OpenClaw Gateway: failed runs, refused handoffs, Telegram messages that didn't go out, and The Machine's own errors. A card tells you when something needs fixing.

## What it can do

Nothing. It only reads the log on your own server.

## Connect it

- On a hosted Machine it is on your setup page, under **More sources**, as OpenClaw Gateway log. One per server.
- On your own computer: run `assistant connect gateway`.

## Remove it

- In the dashboard: **Connections**, open it, **Details**, then **Disconnect**.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

There is nothing to uninstall anywhere else.
