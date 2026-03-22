# Talaria

> Lightweight kanban for agentic team coordination.
> Named after the winged sandals of Hermes — swift, free, crossing between worlds.

A self-hosted kanban board with first-class support for AI agent orchestration. Use it as a personal task tracker, a coordination layer for autonomous agents, or a lightweight Linear alternative.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the board
python server.py

# 3. Open in browser
open http://localhost:8400

# 4. (Optional) Start the agent watcher — spawns agents for cards in "In Progress"
python agent_watcher.py
```

Or with Docker:

```bash
docker-compose up
```

## Architecture

```
Browser (drag & drop)
       ↓ HTTP
server.py (Flask)
       ↓ reads/writes
talaria.json (source of truth)
       ↓ triggers
agent_queue.json ← agent_watcher.py reads & dispatches agents
```

The board is a single JSON file. No database. No migration scripts. Works offline.

## Column Triggers

| Column | Trigger | Action |
|--------|---------|--------|
| Backlog | — | Default holding area |
| Ready | — | Ready to pick up |
| In Progress | `agent_spawn` | Dispatches an AI subagent |
| Review | `notify` | Telegram notification |
| Blocked | `notify` | Telegram notification |
| Done | `notify` | Telegram notification |

Add or rename columns in `talaria.json`. Assign triggers per-column to automate workflows.

## API

```
GET    /api/board              — Full board state
POST   /api/card               — Create card
GET    /api/card/:id           — Get card
PATCH  /api/card/:id           — Update card (column, priority, labels, etc.)
DELETE /api/card/:id           — Delete card
POST   /api/card/:id/note      — Add a status note
GET    /api/agent_queue        — See queued agent tasks
POST   /api/agent_queue/pop    — Remove first item (after dispatch)
GET    /api/activity           — Recent activity log
```

Example — create a card that will dispatch an agent:

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

## Agent Configuration

The watcher spawns AI subagents (defaults to Hermes Agent) for cards in trigger columns. Configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TALARIA_PORT` | `8400` | HTTP server port |
| `TALARIA_WORK_DIR` | `~` | Working directory for agents |
| `TALARIA_MAX_CONCURRENT` | `2` | Max agents running simultaneously |
| `HERMES_AGENT_PATH` | `~/.hermes/hermes-agent/run_agent.py` | Path to agent binary |
| `HERMES_VENV_PATH` | `~/.hermes/hermes-agent/venv/bin/python` | Python interpreter |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_HOME_CHANNEL_ID` | — | Telegram chat ID for notifications |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `N` | New card (when no input focused) |
| `Esc` | Close modals |

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

Add your own handler functions to automate any workflow — Slack messages, GitHub issues, cron jobs, etc.

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

## License

MIT
