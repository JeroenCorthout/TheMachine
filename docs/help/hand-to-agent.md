---
title: Hand to Claude Code or Codex
area: cards
summary: Send a coding card to Claude Code or Codex. The result comes back on the card.
covers: hand-to-agent
code: agent_jobs.py, agent_remote.py, dashboard.html
order: 5
---
## Hand off a card

1. On the card, open **More** and pick **Hand to Codex** or **Hand to Claude Code**. It shows when that tool is set up.
2. Check the summary and what it gets, add a note if you like, and press **Hand off**. **See the full job** shows the whole brief.

The card then shows that it's working, and the result when it's done, with **Show changes**. **Change** asks it for changes; **Hand off again** starts over.

A job never sends anything for you.

## Set it up

In **Settings**, **General**, under **Coding agents**:

- **Claude Code session**: the address of a Claude Code session.
- **Codex cloud environment**: its environment id.
- **Runs on**, and optionally a **Branch**.

Claude sends its result back through The Machine connector. Add it once in claude.ai, Settings, Connectors, Add custom connector, with the address shown there. For Codex on a hosted Machine, sign in under Codex on your setup page first.

Coding agents shows on a hosted Machine and on setups that run jobs elsewhere. More in the [agents guide](https://github.com/Salesflare/TheMachine/blob/main/docs/agents.md).
