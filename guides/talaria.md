# Talaria — Project Context

You are working on **Talaria**, an AI-native kanban board where cards are autonomous coding agents.

## What Talaria Is

- Self-hosted kanban with first-class AI agent orchestration
- Named after Hermes' winged sandals — swift, crossing between worlds
- No database: cards are Markdown files, board config is JSON, log is JSONL
- Git-friendly: branch-per-card worktrees, `git diff`-readable history

## Architecture

```
Browser / CLI → server.py (Flask :8400) → cards/*.md, board.json, logs/talaria.log
                     ↓ polls
              agent_watcher.py → spawns AI agents (Hermes, claude-code, codex)
```

## Key Files

| File | Purpose |
|------|---------|
| `server.py` | Flask REST API + static UI |
| `agent_watcher.py` | Pipeline runner; polls board, spawns agents, handles CI gate |
| `talaria_cli.py` | Terminal CLI (list, create, move, log, context) |
| `board.json` | Column config: IDs, names, triggers, worker assignment |
| `talaria.config.json` | Repo paths, worker binary paths, integrations |
| `cards/<id>.md` | One file per card: YAML frontmatter + description + log |
| `logs/talaria.log` | Append-only JSONL activity log |

## Standard Column Pipeline

`backlog → spec → groom → ready → in_progress → review → done`

- **Spec** (trigger: `agent_spawn`): Agent writes SPEC.md section in the card
- **Groom** (trigger: `agent_spawn`): Agent reviews spec for completeness and edge cases
- **In Progress** (trigger: `agent_spawn`): Agent implements the feature
- **Review** (trigger: `null`): Human or CI gate; runs `card.tests.command` in worktree

## Context Files

When a card enters a trigger column, the watcher loads context from `TALARIA_HOME` (default `~/.talaria/talaria`):

- `talaria.md` — always injected first (this file)
- `spec-guide.md` — loaded for Spec column agents
- `groom-guide.md` — loaded for Groom column agents
- `coding-guide.md` — loaded for In Progress column agents
- `review-guide.md` — loaded for Review column agents

## Key Commands

```bash
# Start the board
python server.py

# Start the agent watcher (pipeline runner)
python agent_watcher.py

# CLI
talaria list                          # All cards, JSON
talaria context <card-id>             # Full card data (for agent prompts)
talaria move <card-id> <column>       # Move card (triggers watcher)
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TALARIA_PORT` | 8400 | HTTP server port |
| `TALARIA_HOME` | `~/.talaria/talaria` | Path to context guide files |
| `TALARIA_WORK_DIR` | `~` | Working directory for agents |
| `TALARIA_MAX_CONCURRENT` | 2 | Max simultaneous workers |
| `POLL_INTERVAL` | 15 | Seconds between board polls |
| `HERMES_AGENT_PATH` | `~/.hermes/hermes-agent/run_agent.py` | Hermes binary |
| `HERMES_VENV_PATH` | `~/.hermes/hermes-agent/.venv/bin/python` | Python for Hermes |
| `TELEGRAM_BOT_TOKEN` | — | Telegram notifications |
| `TELEGRAM_HOME_CHANNEL_ID` | — | Telegram chat ID |
