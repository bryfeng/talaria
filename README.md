# Talaria

<p align="center">
  <img src="logo.png" alt="Talaria" width="200"/>
</p>

> Lightweight kanban for agentic team coordination.
> Named after the winged sandals of Hermes — swift, free, crossing between worlds.

A self-hosted kanban board with first-class support for AI agent orchestration. Use it as a personal task tracker, a coordination layer for autonomous agents, or a lightweight Linear alternative.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the board
python server.py

# 3. Open in browser
open http://localhost:8400

# 4. (Optional) Start the agent watcher — spawns agents for cards in trigger columns
python agent_watcher.py
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
server.py (Flask) ──reads/writes──▶ cards/*.md   ← card content (one file per card)
    ↑                                  talaria.config.json ← project config
    │                                  board.json          ← columns + metadata only
    │                                  agent_queue.json     ← agent dispatch queue
    │
    └─── polls ─── agent_watcher.py ── spawns ───▶ AI agents (Hermes, claude-code, etc.)
                                    └── logs ──▶ logs/talaria.log
```

**Data model:**
- `board.json` — columns + board metadata only. Cards and activity log live elsewhere.
- `cards/*.md` — one Markdown file per card. Source of truth for card content.
- `logs/talaria.log` — append-only activity log (JSONL).
- `agent_queue.json` — pipeline queue used by agent_watcher.py.

No database. No migrations. Git-friendly.

---

## Column Triggers

| Column | Trigger | Action |
|--------|---------|--------|
| Backlog | — | Default holding area |
| Spec | `agent_spawn` | Dispatches a spec-writing agent |
| Groom | `agent_spawn` | Dispatches a groom/review agent |
| Ready | — | Ready to pick up |
| In Progress | `agent_spawn` | Dispatches an implementation agent |
| Review | — | Human review gate |
| Done | `notify` | Telegram notification |

Add or rename columns in `board.json`. Assign triggers per-column to automate workflows.

---

## API

```
GET    /api/board              — Full board state (columns + slim cards)
POST   /api/card               — Create card
GET    /api/card/:id           — Get card
PATCH  /api/card/:id           — Update card (column, priority, labels, etc.)
DELETE /api/card/:id           — Delete card
POST   /api/card/:id/note      — Add a status note
GET    /api/activity            — Recent activity log (last 50 entries from logs/talaria.log)
GET    /api/agent_queue         — View agent dispatch queue
POST   /api/agent_queue/pop    — Pop next card from agent queue
```

**Example — create a card that will dispatch an agent:**

```bash
curl -X POST http://localhost:8400/api/card \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Research competitor pricing",
    "description": "Use web search to find pricing pages for the top 5 competitors",
    "column": "in_progress",
    "priority": "high"
  }'
```

---

## Agent Configuration

The watcher spawns AI subagents (defaults to Hermes Agent) for cards in trigger columns. Configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TALARIA_PORT` | `8400` | HTTP server port |
| `TALARIA_HOME` | `talaria/` | Path to Talaria directory |
| `TALARIA_WORK_DIR` | `~` | Working directory for agents |
| `TALARIA_MAX_CONCURRENT` | `2` | Max agents running simultaneously |
| `HERMES_AGENT_PATH` | `~/.hermes/hermes-agent/run_agent.py` | Path to agent binary |
| `HERMES_VENV_PATH` | `~/.hermes/hermes-agent/.venv/bin/python` | Python interpreter |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_HOME_CHANNEL_ID` | — | Telegram chat ID for notifications |

---

## CLI

A CLI is available for terminal-first workflows and agent scripting:

```bash
python talaria_cli.py <command> [args]
```

Or symlink it for convenience:

```bash
ln -s $(pwd)/talaria_cli.py /usr/local/bin/talaria
talaria list
```

### Commands

| Command | Description |
|---------|-------------|
| `talaria list` | List all cards grouped by column |
| `talaria create <title>` | Create a new card in backlog |
| `talaria move <card-id> <column>` | Move a card to a column (e.g. `in_progress`, `done`) |
| `talaria log <card-id>` | Show activity log and status notes for a card |
| `talaria context <card-id>` | Show full card data (all fields — useful for agents) |

All output is JSON for easy piping and scripting:

```bash
# Move card to In Progress — triggers agent_watcher.py
talaria move 9f875537 in_progress

# Get the card's full context for an agent prompt
talaria context 9f875537 | jq '.description'

# List all cards in Review
talaria list | jq '.[] | select(.column == "review")'
```

The CLI reads `TALARIA_PORT` (default: 8400) to find the server. Ensure `server.py` is running before using CLI commands.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `N` | New card (when no input focused) |
| `Esc` | Close modals |

---

## Extending Triggers

The `trigger` field on a column can be extended in `server.py`:

```python
if trigger == "agent_spawn":
    _queue_agent(card)
elif trigger == "notify":
    _notify_telegram(...)
elif trigger == "webhook":
    _call_webhook(card)
```

Add your own handler functions to automate any workflows — Slack messages, GitHub issues, cron jobs, etc.

---

## Deploying with Ngrok

To expose Talaria publicly (e.g. for webhook integrations or external agent access):

```bash
# Start the board
python server.py

# In another terminal, expose via ngrok
ngrok http 8400
```

Or run both together:

```bash
ngrok http 8400 --log=stdout > /dev/null &
python server.py
```

---

## License

MIT
