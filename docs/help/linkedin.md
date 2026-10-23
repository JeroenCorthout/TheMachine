---
title: LinkedIn
area: connections
summary: Your LinkedIn messages, read in your Machine's own signed-in browser. Replies only after you approve.
covers: connector:linkedin
code: connectors/linkedin.py, integrations/linkedin_inbox.py
site: Connect, More sources, Remove
---
## What it reads

Your LinkedIn messages. It reads them in a tab of your Machine's own browser, signed in as you, now and then during waking hours. It uses no API key and marks nothing as read.

## What it can send

A reply to a message, and only after you approve it on the card. Until then it only drafts.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup and go to **More sources**.
2. Open LinkedIn, sign in to LinkedIn in your server's browser when it asks, then press **Connect**.

On your own computer:

1. Sign in to LinkedIn once in your Machine's browser (Settings, **Open the Machine’s browser ↗**).
2. In the dashboard, open **Connections**, then **+ Add**, and pick LinkedIn. Or run `assistant connect linkedin`.

A second LinkedIn account gets a browser of its own. If LinkedIn signs your Machine out, see [Signed out of a site](signed-out.md).

## Remove it

- In the dashboard: **Connections**, open LinkedIn, **Details**, then **Disconnect**. It stops reading. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Sign it out on LinkedIn

In LinkedIn, open Settings, Sign in and security, Where you're signed in, and sign out the session of your Machine's browser.
