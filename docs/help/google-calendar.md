---
title: Google Calendar
area: connections
summary: Your meetings, so you go in prepared and follow up. It changes nothing unless you tap.
covers: connector:calendar
code: onboarding.py, google_api.py
site: Connect Google, Mail and Calendar, Disconnect, Sign in with Google
order: 11
---
## What it reads

Your events from 7 days back to 30 days ahead, with repeating events filled in. They show in the [Calendar](calendar.md) tab and help cards about a meeting.

## What it can do

Nothing on its own. It never invites anyone or answers an invitation.

If the sign-in may write calendars, you can add, change or delete an event without guests, on your own calendar or a shared one you may edit. Every change is your tap: in the [Calendar](calendar.md), or on a card that offers it. A sign-in from **Connect Google** only reads.

## Connect it

On a hosted Machine it connects together with Gmail: on your setup page, under **Mail and Calendar**, press **Connect Google**.

On your own computer, once it's [paired with your account](fleet.md): open **Connections**, then **+ Add**, pick Google Calendar and press **Sign in with Google**. It connects together with Gmail.

With your own app: run `assistant connect calendar`, or open **Use your own app** there. It asks which calendar to read.

## Remove it

- On a hosted Machine: press **Disconnect** next to the address on your setup page. That removes Gmail and Calendar for that address.
- On your own computer: **Connections**, open the calendar, **Details**, then **Disconnect**.

## Uninstall it on Google

In your Google Account, open Security, then Your connections to third-party apps and services, and remove the app. See [Gmail](gmail.md).
