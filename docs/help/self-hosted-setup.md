---
title: Run it on your own computer
area: getting-started
summary: Install the open-source app on Linux, macOS or Windows. Free, with your own model and your own data.
covers: self-hosted
code: install.sh, install.ps1, docs/getting-started.md
order: 2
---
The Machine is open source (MIT). On your own computer it is free, and your data stays there.

## What it needs

- OpenClaw 2026.10.5 or newer. The installer offers to install it.
- Python 3.12 or newer, and Node.js 24.16 or newer.
- Tailscale, to reach your dashboard from your phone.
- Your own user account, not root.

## Install it

On Linux or macOS (macOS is in beta):

1. `git clone https://github.com/Salesflare/TheMachine.git ~/.openclaw/workspace/tools/personal-assistant`
2. `cd ~/.openclaw/workspace/tools/personal-assistant && ./install.sh`

On Windows (beta), it runs inside WSL2. In PowerShell, run `irm https://raw.githubusercontent.com/Salesflare/TheMachine/main/install.ps1 | iex`. It sets up WSL2 with Ubuntu and everything the app needs, then runs `./install.sh`.

The installer finds your accounts, connects them one by one and shows a preview of last week before anything turns on.

## After installing

- Add sources with `assistant connect`, or **Connections**, then **+ Add**, in the dashboard.
- Check everything with `assistant doctor`. It prints a fix for each problem.
- Updates come as signed releases.

The full guide is in the repository: [Getting started](https://github.com/Salesflare/TheMachine/blob/main/docs/getting-started.md).
