# Bring your own model

The Machine contains no model and no model settings. Everything that needs a model goes through your own OpenClaw agent: reviews, Do it preparations and profile/role drafting (the native Telegram Change prompt is transport work, not a reasoning call). Pick and configure the model in OpenClaw, and The Machine uses it.

## Which agent and model it uses

Reviews and preparations run as isolated sessions of your **main** OpenClaw agent:

- `agent:main:assistant-review-signals-<run>` for reviews
- `agent:main:assistant-review-preparation-<job>` for Do it

They resolve their model the same way your chats do: `agents.defaults.model.primary`, then `agents.defaults.model.fallbacks` in order (or a per-agent `agents.entries.main.model`). See OpenClaw's [Models](https://docs.openclaw.ai/concepts/models) and [Model providers](https://docs.openclaw.ai/concepts/model-providers) docs.

Inspect the current model with `openclaw models status`, and available configured models with `openclaw models list`. To choose another, use `openclaw models set <model-id-or-alias>` with a value that installation actually lists. Inspect fallback commands with `openclaw models fallbacks --help`; keep existing fallbacks unless you intend to change them. Do not copy a model name from an old screenshot or assume a model available on one machine is available on another.

To use your own Claude plan (Pro, Max or Team), install Claude Code with Anthropic's installer and sign in with `claude auth login`. That adds Claude's models: `openclaw models list` shows them as `claude-cli/*`, and OpenClaw runs them through Claude Code without reading its sign-in. Pick one as main model or fallback like any other; your current main model stays until you do. (`openclaw models auth login --provider anthropic --method cli --set-default` also makes a Claude model the main model; it needs a terminal.) Usage counts against your plan, and with a Claude model as main model Settings → Model says "Uses your Claude plan". Sign out with `claude auth logout`.

The thinking level (`agents.defaults.thinkingDefault`) applies to fallbacks too: OpenClaw passes it on to whichever model answers, mapped to the nearest level that model supports. Claude Code gets it as its `--effort`. Set `params.thinking` on one model's entry in `agents.defaults.models` to give that model its own level.

The Assistant has no separate model switch. A change applies to new OpenClaw turns; it does not change an already-running turn. No Assistant-service restart is needed just to choose a model.

## A provider of your own

Configure a custom provider in OpenClaw, not in the Assistant. OpenClaw supports local and hosted providers, including suitable Azure deployments, API gateways, Ollama and compatible local servers. API type, deployment name, authentication headers and secret format depend on that provider and your OpenClaw version. Use that version's provider documentation and `openclaw models --help` rather than treating a generic JSON example as a working configuration. Keep credentials in OpenClaw's supported protected secret/auth stores, never in a public repository.

After setup, verify the provider in `openclaw models status` and test one small owner-requested task before relying on it for unattended work. Set the intended main model explicitly, and retain intentional account boundaries and fallbacks.

## What the model has to be good at

| Job | Needs |
|---|---|
| **Reviews** | Long context: up to 120 signals per run, read in pages through the `assistant_signals` tool. Reliable tool calls and judgement: most signals deserve no card. |
| **Do it** | Agentic work: many tool calls, the browser, files and commands, then a short result and a correct `next_actions` JSON (for example an email draft with exact recipients). |
| **Drafts** | Writing in your voice, per channel. The service gives each draft your few lines for that channel (read from your own messages), your own recent messages there and your edits of earlier drafts on it, so drafts improve as you edit them. |

In practice: use a current frontier model with strong tool use as the primary. Small or older models tend to suggest too much, skip pages of a review, or save malformed next actions. The service rejects invalid ones and the run then records a blocker instead of sending anything, but you'll see more "Needs your help" cards.

## Local and self-hosted models

Any OpenClaw provider works, including Ollama, LM Studio, vLLM or LiteLLM in front of your own GPUs. Points to check:

- **Context window:** at least 128K tokens is comfortable for reviews. With less, filter noisy sources and check actual context usage. `review_worker.min_signals` changes when a review starts, not its claim limit; it does not reduce the up-to-120 batch cap.
- **Tool calling:** the model must support function/tool calls through your provider. Test with one Do it on a simple card before turning on prepare-ahead.
- **Speed:** a preparation may make dozens of calls. The default runtime allowance is 25 minutes plus ten minutes of outage grace; a slow local model may hit that on bigger tasks.

A mixed setup works well: a frontier model as `primary`, a local model as the last fallback, so reviews keep going during a provider outage.

## Privacy and cost

- Collected content stays in your SQLite database, but reviews and preparations send the signals they read to your model provider. A local reasoning model keeps inference local, but connected services, chosen enrichment (organisation domains; for work contacts their name and company domain, or the address your service asks for), Fleet/Friends summaries and Telegram/push delivery still use the network.
- To have someone forgotten, **Delete permanently** on their page removes them and what is tied to them, and keeps them from being added again ([Deleting someone](intelligence.md#deleting-someone)).
- Each review reads up to 120 signals; each Do it reads the card's full evidence. The Friday recap reports the week's token usage, so you can see what a setup costs before committing to it.
- If your provider reports usage limits (a ChatGPT sign-in has a 5-hour and a weekly window, depending on the plan), **Settings → Model** shows the model in use, how much of each window is used and when it resets. It asks OpenClaw's running Gateway (`usage.status`) every five minutes and a minute after a review run ends, and keeps only the percentages and reset times; reading it never costs model usage. `openclaw status --usage` shows the same. When a review run stops on the limit, Connections says so next to the run, with when it lifts.
- ChatGPT plans can come with **usage limit resets**, which start the limits over. OpenClaw does not report them, so The Machine asks the Codex CLI's own app-server (`codex app-server`, no model call) when the Codex CLI on this computer is signed in to the same ChatGPT account (same email and plan). Then Settings shows how many you have and **Use a reset**, after a confirm. Otherwise (also on a hosted Machine, where only OpenClaw is signed in) it shows no count, only **See resets in ChatGPT**, which opens ChatGPT's Codex usage page (`chatgpt.com/codex/cloud/settings/usage`); on a Team or Business plan, pick that workspace there. Set `"codex": false` in config.json to never ask the Codex CLI, or `"codex": "/path/to/codex"` for another binary.
- Prepare-ahead (cards that arrive already drafted) spends tokens on cards you may dismiss. Turn it off with `"prepare_ahead": false`, or cap it with `"prepare_ahead_per_day"` (default 10) in `config.json`.

## Known limitation

The Machine always uses your **main** agent. Using a separate agent (and so a separate model) for The Machine only is not supported yet; it would mean making the session key prefix configurable in `preparation-run.mjs` and the review runner. Contributions welcome.
