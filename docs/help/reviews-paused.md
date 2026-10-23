---
title: No new cards
area: troubleshooting
summary: Reviews are off, waiting, or stopped on your model's usage limit. How to tell and what to do.
covers: 
code: dashboard.html, assistant.py
order: 3
---
Cards come from reviews: your Machine reads what came in, in small batches, within about an hour.

## Reviews are off

The top of Suggestions says "Reviews are off, so no suggestions arrive yet." On a hosted Machine, press **Turn on reviews**. On your own computer, run `./install.sh --activate`.

## Your model's limit

When your AI plan's limit is reached, reviews wait until it lifts. **Connections**, then **Reviews**, shows it next to the run, with when it resets. **Settings**, **Model** shows your limits. See [Model](model.md).

Nothing is lost while it waits: what came in is reviewed once it can.

## A run failed

**Recent runs** under **Reviews** shows each run. A failed batch is tried again by itself.
