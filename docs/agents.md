# Coding agents

A card can go to Claude Code or Codex. **Hand to Claude Code** and **Hand to Codex** sit with the card's other
actions when that tool is there. A small sheet opens (in the card chat, in the message box's place): **Hand to** with a
switch between the tools, one line of what it will do, chips for what it gets (the emails, the draft, `messages.md`,
links counted, never shown raw) and an optional note for the agent. **See the full job** shows the whole brief to read
or edit. It opens with the task, the card's next step (its Needs you line), then your note on the card word for word,
who it's about, what happened so far as background, the card's drafts and files, what done looks like and the limits.
Nothing goes without your **Hand off**.

The job runs in its own folder under the Machine's data folder (`agent-jobs/<job>/work`), with the card's messages in
`card/messages.md`, full text included, so it needs no mailbox of its own. A job that runs away from this computer
(Claude Code on your paired computer, Codex cloud) doesn't get that folder, so the messages are in the brief itself,
newest first, shortened to fit with a note where something is cut. The card shows its progress, then a short result with the files it made. Details stay folded away. **Ask for
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
  declined. If the job's thread is open in the Codex app, a follow-up from the card goes on in a copy of the thread
  (`thread/fork`), which shows in the app too. If even that is refused, the card says to carry on in Codex.

## On a hosted Machine

A hosted Machine has no Claude Code or Codex of yours, so it hands the job on. Set it up in **Settings > General >
Coding agents**.

- **Claude Code**: paste the link of a Claude Code session from claude.ai/code. The job goes to that session as a
  message (`claude -p <message> --cloud <session>`). That session can't reach your Machine, so it sends its result
  back through The Machine connector. Add it once in claude.ai: Settings, Connectors, Add custom connector,
  `https://app.themachine.computer/mcp`. It has one tool, `report_result`. Each job gets its own ticket, and the
  Network keeps only the ticket's hash and the sealed result until your Machine picks it up. A result that hasn't come
  back after two days shows as stopped.
- **Codex**: sign in to Codex first, on the setup page (step **Codex**): your server runs `codex login --device-auth`
  and the page shows a code to enter on OpenAI's page, on your own phone or computer. The Codex CLI keeps its sign-in
  on your server, as it always does. Then enter a Codex cloud environment id (and a branch, if not the default). The job runs as a Codex cloud task
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
