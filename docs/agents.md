# Coding agents

A card can go to Claude Code or Codex. **Hand to Claude Code** and **Hand to Codex** sit with the card's other
actions when that tool is there. You read and edit the brief first: the task, the card's drafts, links and files, and
what done looks like. Nothing goes without your tap.

The job runs in its own folder under the Machine's data folder (`agent-jobs/<job>/work`), with the card's messages in
`card/`. The card shows its progress, then a short result with the files it made. Details stay folded away. **Ask for
changes** sends a follow-up to the same job. Cancel stops it.

A job never sends anything for you. If it rewrites a message (`draft.md`), that text becomes the card's draft, and you
still send it with the card's own Send.

The CLIs keep their own sign-ins. The Machine never reads, copies or stores a Claude or ChatGPT credential.

## On your own computer

- **Claude Code** runs as a background session (`claude --bg`, named after the card) with Remote Control on. It is in
  `claude agents`, at claude.ai/code and in the Claude apps, where you can read it and keep talking to it. **Open in
  Claude** goes there. Claude Code asks once whether to trust the jobs folder: run `claude` in it and accept. Until
  then, jobs run as `claude -p` and only show on the card.
- **Codex** runs through the Codex app's own app server, so the job is a thread in the Codex app (named after the
  card), also from your phone through the app's remote access. **Open in Codex** opens it. Without the Codex app it
  uses `codex exec`, and the job only shows on the card. Nothing is approved for you: a command that needs approval is
  declined.

## On a hosted Machine

A hosted Machine has no Claude Code or Codex of yours, so it hands the job on. Set it up in **Settings > General >
Coding agents**.

- **Claude Code**: paste the link of a Claude Code session from claude.ai/code. The job goes to that session as a
  message (`claude -p <message> --cloud <session>`). That session can't reach your Machine, so it sends its result
  back through The Machine connector. Add it once in claude.ai: Settings, Connectors, Add custom connector,
  `https://app.themachine.computer/mcp`. It has one tool, `report_result`. Each job gets its own ticket, and the
  Network keeps only the ticket's hash and the sealed result until your Machine picks it up. A result that hasn't come
  back after two days shows as stopped.
- **Codex**: enter a Codex cloud environment id (and a branch, if not the default). The job runs as a Codex cloud task
  (`codex cloud exec`). The card shows its status and then its changes, and **Open in Codex cloud** goes to the task.

## Settings

`agents` in config.json, all optional:

```json
{"agents": {"claude": {"command": "claude", "config_dir": "~/.claude-other", "background": true},
            "codex": {"command": "codex", "app_server": true},
            "max_parallel": 2, "timeout_minutes": 30}}
```

`config_dir` points the jobs' Claude Code at another config folder, such as another account, and only for jobs.
`"claude": false` or `"codex": false` hides that action. `"agents": false` turns both off.
