---
title: Do it, and what gets sent
area: cards
summary: Do it prepares the work. Nothing is sent, paid or changed until you approve that step.
covers: do-it
code: dashboard.html, next_actions.py, assistant.py
order: 2
---
## What Do it does

**Do it** asks your Machine to prepare the card: draft the reply, look things up, fill in a form. It works with your own accounts and its own browser, as you would.

## Nothing goes out before your yes

- A reply comes back as a draft. **Review and send** opens a preview; it only goes out when you press **Send now**.
- **Edit** in the preview changes the text first.
- A step that commits something, like submit, pay or book, stops and waits for your confirm.
- In card chat, a draft is never sent in the same message that wrote it.

What it can send depends on the source: an email, a Slack direct message, a WhatsApp, LinkedIn or X message. Each connection's article says what it can send.

## When it can't

- "This can't be sent from here." means you copy the draft with **Copy draft** and send it yourself.
- **Needs your help** means it asks you something: **Answer** it. **Stuck** means it stopped without a question; the card says why.
- **Try again** runs it once more. **Change** asks for something else.
