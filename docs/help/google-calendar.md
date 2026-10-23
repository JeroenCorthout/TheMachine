---
title: Google Calendar
area: connections
summary: Your meetings, so you go in prepared and follow up. It only reads.
covers: connector:calendar
code: onboarding.py, google_api.py
site: Connect Google, Mail and Calendar, Disconnect
order: 11
---
## What it reads

Your events from 7 days back to 30 days ahead, with repeating events filled in. They show in the [Calendar](calendar.md) tab and help cards about a meeting.

## What it can do

Nothing in your calendar. It never creates, changes or answers an event.

## Connect it

On a hosted Machine it connects together with Gmail: on your setup page, under **Mail and Calendar**, press **Connect Google**.

On your own computer, run `assistant connect calendar`, or open **Connections**, then **+ Add**, and pick Google Calendar. It asks which calendar to read.

## Remove it

- On a hosted Machine: press **Disconnect** next to the address on your setup page. That removes Gmail and Calendar for that address.
- On your own computer: **Connections**, open the calendar, **Details**, then **Disconnect**.

## Uninstall it on Google

In your Google Account, open Security, then Your connections to third-party apps and services, and remove the app. See [Gmail](gmail.md).
