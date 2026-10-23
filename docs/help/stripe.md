---
title: Stripe
area: connections
summary: Failed payments, disputes, cancellations and big new subscriptions. It only reads.
covers: connector:stripe
code: connectors/stripe.py
site: Connect, More sources, Remove
---
## What it reads

Events on your Stripe account that may need you: failed payments, disputes, cancellations and big new subscriptions, with the customer they belong to.

## What it can do

Nothing in Stripe. It never writes, refunds or retries a payment.

## Connect it

1. In Stripe, create a restricted key with Read on Events, Customers and Account, and None on everything else.
2. Find your account ID (acct_...) under Settings, Business details.
3. On a hosted Machine: open your setup page at app.themachine.computer/setup, go to **More sources** and open Stripe. On your own computer: **Connections**, **+ Add**, Stripe, or `assistant connect stripe`.
4. Paste the account ID and the key, then press **Connect**.

## Remove it

- In the dashboard: **Connections**, open Stripe, **Details**, then **Disconnect**. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Revoke it in Stripe

In Stripe, open Developers, API keys, and delete or roll the restricted key.
