---
title: X
area: connections
summary: Your mentions, replies and direct messages on X, read in your Machine's own browser. Sends only after you approve.
covers: connector:x
code: connectors/x.py, integrations/x_inbox.py
site: Connect, More sources, Remove
---
## What it reads

Your mentions, replies and direct messages on X. It reads them in your Machine's own browser, signed in as you. It doesn't use X's developer API, marks nothing as seen, and can't read encrypted X Chat.

## What it can send

A reply or a direct message, and only after you approve it on the card.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup and go to **More sources**.
2. Open X, type your handle without the @, sign in to X in your server's browser when it asks, then press **Connect**.

On your own computer:

1. Sign in to X once in your Machine's browser (Settings, **Open the Machine’s browser ↗**).
2. In the dashboard, open **Connections**, then **+ Add**, and pick X. Or run `assistant connect x`.
3. Type your handle, and the handle the browser is normally signed in to.

If X signs your Machine out, see [Signed out of a site](signed-out.md).

## Remove it

- In the dashboard: **Connections**, open X, **Details**, then **Disconnect**. It stops reading. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Sign it out on X

In X, open Settings, Security and account access, Apps and sessions, Sessions, and log out the session of your Machine's browser.
