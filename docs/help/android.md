---
title: Android notifications
area: connections
summary: The notifications on your Android phone, through the OpenClaw app. It only reads.
covers: connector:android
code: onboarding.py, android-notifications.mjs
site: Show a setup code, Approve, More sources, Remove
---
## What it reads

The notifications that come in on your Android phone, through the OpenClaw app. One-time codes are left out, and so are WhatsApp, Telegram, Signal and Discord notifications.

## What it can do

Nothing on your phone. It only reads notifications.

## Connect it

On a hosted Machine:

1. Install the OpenClaw app on your phone.
2. On your setup page, go to **More sources**, open Android notifications and press **Show a setup code**.
3. Scan it in the OpenClaw app, then press **Approve** for the pairing.
4. In the OpenClaw app, turn on Forward Notification Events, and allow notification access when Android asks.

On your own computer, pair the phone with your OpenClaw Gateway, then run `assistant connect android`.

## Remove it

- In the dashboard: **Connections**, open Android, **Details**, then **Disconnect**.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Remove it on the phone

Turn off notification access for the OpenClaw app in Android's settings, or unpair the phone in the OpenClaw app.
