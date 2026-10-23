---
title: Trello
area: connections
summary: Card changes, comments and activity on one Trello board. It only reads.
covers: connector:trello
code: connectors/trello.py
site: Connect, More sources, Remove
---
## What it reads

Card changes, comments and activity on one Trello board per connection. It never marks a board as viewed.

## What it can do

Nothing in Trello. It only reads.

## Connect it

1. At trello.com/power-ups/admin, create a Power-Up with any name.
2. Open its API key page and copy the API key (not the secret).
3. Click the Token link next to it, allow read access and copy the token.
4. On a hosted Machine: open your setup page at app.themachine.computer/setup, go to **More sources** and open Trello. On your own computer: **Connections**, **+ Add**, Trello, or `assistant connect trello`.
5. Give the board's address, the API key and the token, then press **Connect**.

## Remove it

- In the dashboard: **Connections**, open the board, **Details**, then **Disconnect**. What it already brought in stays.
- On a hosted Machine you can also press **Remove** next to it on your setup page.

## Revoke it in Trello

Revoke the token in your Trello account settings, under Applications, or delete the Power-Up.
