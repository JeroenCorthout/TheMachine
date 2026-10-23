---
title: Intercom
area: connections
summary: Customer conversations in your Intercom workspace. It only reads conversations.
covers: connector:intercom
code: connectors/intercom.py, onboarding.py
site: Sign in with Intercom, Or paste an access token, Connect, More sources, Remove
---
## What it reads

Customer conversations in your Intercom workspace: the last 7 days when you connect, then new ones as they come in. It also reads which workspace and admin the sign-in belongs to.

## What it can do

It only reads conversations. It never replies, assigns, closes or tags. Anything other than reading is refused before the token is even used.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup and go to **More sources**.
2. Open Intercom and press **Sign in with Intercom**.
3. Approve The Machine in Intercom. It is made by Salesflare.

If the sign-in doesn't suit your workspace, open **Or paste an access token** instead, paste a token from Intercom's Developer Hub and press **Connect**.

On your own computer:

1. In Intercom's Developer Hub, open your app, then Authentication, and copy its access token.
2. In the dashboard, open **Connections**, then **+ Add**, and pick Intercom. Or run `assistant connect intercom`.
3. Paste the token. A token from another workspace becomes a second Intercom account.

Intercom always counts as work, for the company its workspace names.

## Remove it

- In the dashboard: **Connections**, open Intercom, **Details**, then **Disconnect**. It stops reading. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Uninstall it on Intercom

- Signed in with Intercom: in Intercom, open Settings, Apps, Installed apps, and remove The Machine.
- A pasted token: delete or rotate it in the Developer Hub, or delete the app you made it in.
