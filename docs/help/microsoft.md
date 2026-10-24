---
title: Microsoft Outlook and Calendar
area: connections
summary: Outlook mail and meetings, for Microsoft 365 and Outlook.com. Sends an email only after you approve it.
covers: connector:outlook, connector:outlook_calendar
code: ms_graph.py, onboarding.py
site: Connect Microsoft, Copy link for your admin, I'm the admin: approve now, Disconnect, Sign in with Microsoft, Use your own app
order: 12
---
## What it reads

- **Outlook**: all your mail, the last 7 days when you connect, then everything new.
- **Outlook Calendar**: your meetings, read only.

## What it can send and change

- An email from a card, only after you approve it in the preview.
- When you snooze a card about an email, the thread's messages move to a Snoozed folder in Outlook and come back when the card does.

The calendar is never changed.

## Connect it

On a hosted Machine:

1. Open your setup page at app.themachine.computer/setup.
2. Press **Connect Microsoft** and sign in.
3. If Microsoft asks for admin approval, press **Copy link for your admin**, or **I'm the admin: approve now** if that's you.

On your own computer, once it's [paired with your account](fleet.md): open **Connections**, then **+ Add**, pick Outlook and press **Sign in with Microsoft**. Mail and calendar connect together.

With your own app: open **Use your own app** there, or run `assistant connect outlook` or `assistant connect outlook_calendar`. It uses your own app registration in Microsoft Entra and a sign-in code. It asks whether to also send what you approve.

## Remove it

- On a hosted Machine: press **Disconnect** next to the account on your setup page. In the dashboard, **Reconnect or disconnect ↗** on its card takes you there.
- On your own computer: **Connections**, open the account, **Details**, then **Disconnect**.

## Uninstall it on Microsoft

Microsoft has no way for us to revoke a sign-in, so remove it there as well:

- Work or school account: myapps.microsoft.com
- Personal account: account.live.com/consent/Manage
