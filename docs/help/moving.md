---
title: Move to your own computer
area: getting-started
summary: Export everything from a hosted Machine in one encrypted file and import it on your own computer.
covers: moving
code: moving.py, cli.py
site: Export everything, Delete it now, Download
order: 5
---
## Export

1. On your account page, open Data and press **Export everything**.
2. Optionally set a passphrase to take your keys along. Without one, the export gets a code and those sources need connecting again.
3. Press **Download**. The file comes straight from your server, which deletes it after 24 hours, or at once with **Delete it now**.

The file is always encrypted. Google always needs a new sign-in on the other side. Your model sign-in and chat history stay behind.

## Import

On your own computer, [install The Machine](self-hosted-setup.md), then run `assistant import` with the file.

Then cancel your hosted plan if you no longer need it: see [Cancel or delete](cancel-and-delete.md).
