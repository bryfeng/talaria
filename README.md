# Talaria

<p align="center">
  <img src="logo.png" alt="Talaria" width="200"/>
</p>

[![CI](https://github.com/bryfeng/talaria/actions/workflows/test.yml/badge.svg)](https://github.com/bryfeng/talaria/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/talaria-kanban?label=PyPI)](https://pypi.org/project/talaria-kanban/)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> An AI-native kanban board where cards are autonomous coding agents.
> Drop a card in "In Progress" and a worker spawns in an isolated git worktree — implementing, testing, and advancing itself through the pipeline with zero manual intervention.

---

## Who is this for?

- **Solo devs and small teams** who want AI to handle implementation from spec to merge
- **Agentic workflows** where tickets and PRs should be the same thing
- **Builders** who want git-tracked work items with full history and auditability
- **AI tooling enthusiasts** who want first-class support for spawning Claude Code, Hermes, or Codex workers from a kanban card

---

## Comparison

| Feature | Linear | Notion | GitHub Projects | Talaria |
|---------|:------:|:------:|:---------------:|:-------:|
| Card triggers | No | No | No | **Yes** — agents auto-spawn |
| Git worktrees | No | No | No | **Yes** — isolated per card |
| CI gate in Review | No | No | No | **Yes** — per-card `tests.command` |
| Markdown source of truth | No | Partial | No | **Yes** — `cards/*.md` |
| Agent-native | No | No | No | **Yes** |
| Git-tracked work items | No | No | Yes | **Yes** |
| No database needed | No | No | No | **Yes** |

---

## Quick Start

```bash
# Install (pip installable)
pip install talaria

# Point at your project
export TALARIA_WORK_DIR=~/my-project

# Start the board + agent watcher
talaria-server &
open http://localhost:8400

# Create a card and drop it in In Progress — an agent spawns automatically
talaria create "Build user auth"
talaria move <card-id> in_progress
```

Or clone and run:

```bash
git clone https://github.com/bryfeng/talaria.git
cd talaria
pip install -e .
talaria-server &
open http://localhost:8400
```

Or with Docker:

```bash
docker-compose up
```

---

## Architecture

```
Browser / CLI
    ↓ HTTP
server.py (Flask :8400) ──reads/writes──▶ cards/*.md   ← one file per card
    ↑                                  talaria.config.json ← repo config
    │                                  board.json          ← columns + triggers
    │
    └─── polls ─── agent_watcher.py ── spawns ──▶ AI agents (Hermes, claude-code, codex)
                                    └── logs ──▶ logs/talaria.log
```

**No database. No migrations. Git-friendly.**

---

## Pipeline Columns

| Column | Trigger | Action |
|--------|---------|--------|
| Backlog | — | Default holding area |
| Spec | `agent_spawn` | Dispatches a spec-writing agent |
| Groom | `agent_spawn` | Groom/review agent — priority + labels + notes injected |
| Ready | — | Ready to pick up |
| In Progress | `agent_spawn` | Dispatches an implementation agent |
| Review | — | Human review gate + CI run (`tests.command`) |
| Done | `notify` | Telegram notification + rolling cap of 20 (oldest auto-archived) |

Add or rename columns in `board.json`. Assign triggers to automate any workflow.

Archived cards are moved to `cards/archive/` to keep Done focused on recent operational history.

## Self-hosting guardrails (lean)

Talaria enforces runner/target separation by default:
- Run orchestrator from a stable clone (example: `~/talaria-stable`)
- Target a different dev clone for mutations (example: `~/talaria-dev`)

If runner path equals target path, `talaria-server` and `agent_watcher.py` fail fast.
Emergency-only bypass: `TALARIA_BYPASS_ALLOWED=true`.

Optional local enforcement for contributors:

```bash
bash scripts/install_hooks.sh
```

This installs a `commit-msg` hook requiring commit messages to include card IDs like `[card:7ce240ee]`.
Explicit bypass tokens: `[no-card]` or `[ops]`.

---

## Features

### Agent-native kanban
Cards in Spec, Groom, and In Progress auto-spawn AI workers. Workers are configurable — defaults to Hermes Agent, swap in Claude Code or Codex by setting `worker` in `board.json`.

### Git worktree isolation
Each card gets its own git worktree and branch. Workers operate in isolation. When the card moves to Done, the branch is merged and the worktree is cleaned up automatically.

### CI gate in Review
Every card can define a `tests.command` field. When it enters Review, the CI gate runs in the card's worktree — pass and it auto-advances to Done, fail and it bounces back to In Progress.

### Real-time polling
The frontend polls every 10 seconds. Active agents show a pulsing indicator. Cost tracking (tokens + USD) is logged per run and shown on the card.

### Telegram integration
Bot commands for full board operation from Telegram. Move cards, check status, get notified when work is done.

Fresh install options:

1) Standalone Talaria Telegram UI (quickest)

```bash
# In your Talaria environment
export TELEGRAM_BOT_TOKEN=<your-bot-token>
export TALARIA_BASE_URL=http://localhost:8400
# Optional: lock the bot to specific chat IDs
export TALARIA_TELEGRAM_ALLOWED_CHATS=<chat_id_1>,<chat_id_2>

# Start Talaria server
talaria-server

# In another terminal, run Telegram UI worker
talaria-telegram-ui
```

Commands:
- /board
- /next
- /card <id>
- /create <title>
- /note <id> <text>

Inline actions:
- Move: Spec, Groom, Ready, In Progress, Review
- ✅ Done
- 📝 Note (next message becomes note text)
- 🔄 Refresh

2) Hermes gateway + Talaria API (recommended for Hermes users)

If you're running Hermes Telegram gateway, enable Talaria routing in Hermes and point it at Talaria:

```bash
export TALARIA_BASE_URL=http://localhost:8400
export TALARIA_TELEGRAM_UI_ENABLED=true
# Optional allowlist (recommended for OSS deployments)
export TALARIA_TELEGRAM_ALLOWED_CHATS=<chat_id_1>,<chat_id_2>
```

Behavior in Hermes Telegram:
- /board, /next, /card, /create, /note are routed API-first to Talaria
- talaria:* inline callbacks are handled natively
- If Talaria is offline, users get an explicit error (no silent local-file fallback)

### CLI
Terminal-first interface for scripting and agent workflows:

```bash
talaria list                      # Cards grouped by column (JSON)
talaria create "Build login"       # New card in backlog
talaria move <id> in_progress     # Triggers agent_spawn
talaria log <id>                  # Activity log + status notes
talaria context <id>              # Full card data (for agent prompts)
talaria note <id> "fixed the bug" # Add status note
```

All output is JSON — pipe to `jq` for scripting.

---

## API

```
GET    /api/board           — Full board state (columns + cards)
POST   /api/card            — Create card
GET    /api/card/:id        — Get card
PATCH  /api/card/:id        — Update card (column, priority, labels, etc.)
DELETE /api/card/:id        — Delete card
POST   /api/card/:id/note   — Add status note
GET    /api/activity        — Recent activity log
GET    /api/agent_queue     — View agent dispatch queue
POST   /api/agent_queue/pop — Pop next card from queue
```

Example:

```bash
curl -X POST http://localhost:8400/api/card \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Research competitor pricing",
    "description": "Use web search to find pricing for the top 5 competitors",
    "column": "in_progress",
    "priority": "high"
  }'
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TALARIA_PORT` | `8400` | HTTP server port |
| `TALARIA_HOME` | `talaria/` | Path to Talaria directory |
| `TALARIA_WORK_DIR` | `~` | Working directory for agents |
| `TALARIA_MAX_CONCURRENT` | `2` | Max simultaneous agents |
| `HERMES_AGENT_PATH` | `~/.hermes/hermes-agent/run_agent.py` | Hermes binary path |
| `HERMES_VENV_PATH` | `~/.hermes/hermes-agent/.venv/bin/python` | Python interpreter |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_HOME_CHANNEL_ID` | — | Telegram chat ID |
| `TALARIA_BASE_URL` | `http://localhost:8400` | API base URL used by Telegram UI/Hermes integration |
| `TALARIA_TELEGRAM_ALLOWED_CHATS` | — | Comma-separated Telegram chat ID allowlist |
| `TALARIA_TELEGRAM_UI_ENABLED` | `false` | Enable Talaria slash/callback routing inside Hermes gateway |

---

## Links

- [Architecture](docs/architecture.md)
- [Changelog](CHANGELOG.md)
- [GitHub](https://github.com/bryfeng/talaria)

---

## License

MIT — Bryan Feng 2026
