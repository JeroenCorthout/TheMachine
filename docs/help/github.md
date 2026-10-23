---
title: GitHub
area: connections
summary: Review requests, mentions, assignments and failed CI runs. It only reads.
covers: connector:github
code: connectors/github.py, onboarding.py
site: Sign in with GitHub, More sources, Remove
---
## What it reads

Your GitHub notifications: review requests, mentions, assignments, replies and failed CI runs. It never marks a notification as read.

## What it can do

Nothing on GitHub. Every call it makes is a read.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup and go to **More sources**.
2. Open GitHub and press **Sign in with GitHub**.
3. Type the code it shows at GitHub, on your phone or computer.

On your own computer:

1. Sign in to the GitHub CLI first: `gh auth login`.
2. In the dashboard, open **Connections**, then **+ Add**, and pick GitHub. Or run `assistant connect github`.
3. Confirm your GitHub login. The Machine checks it matches on every sync.

## Remove it

- In the dashboard: **Connections**, open GitHub, **Details**, then **Disconnect**. It stops reading. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Uninstall it on GitHub

Revoke the GitHub CLI's access in GitHub, under Settings, Applications, Authorized OAuth Apps. On your own computer, `gh auth logout` also signs the CLI out.
