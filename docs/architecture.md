# Talaria Architecture

> Auto-generated. Regenerate by moving the Architecture Diagram card to In Progress.

---

## System Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                          TALARIA SYSTEM                                 │
│                                                                         │
│  ┌──────────────┐    HTTP/REST     ┌─────────────────────────────────┐ │
│  │   Browser    │◄────────────────►│  server.py  (Flask @ :8400)     │ │
│  │ (index.html) │                  │  GET/POST/PATCH/DELETE /api/*   │ │
│  └──────────────┘                  └──────────────┬──────────────────┘ │
│                                                    │ read/write          │
│  ┌──────────────┐    HTTP/REST                     ▼                    │
│  │ talaria_cli  │◄────────────────►  ┌─────────────────────────────┐   │
│  │  (terminal)  │                    │  File Storage (no database)  │   │
│  └──────────────┘                    │  ┌───────────┐ ┌──────────┐ │   │
│                                      │  │ board.json│ │cards/*.md│ │   │
│  ┌────────────────────────────────┐  │  │ (columns) │ │(1 per    │ │   │
│  │  agent_watcher.py              │  │  └───────────┘ │ card)    │ │   │
│  │  (Pipeline Runner)             │  │                └──────────┘ │   │
│  │  • Polls GET /api/board        │  │  ┌──────────────────────┐   │   │
│  │  • Reads column config         │  │  │ logs/talaria.log     │   │   │
│  │  • Drafts context for agents   │◄─┤  │ (append-only JSONL)  │   │   │
│  │  • Spawns worker subprocesses  │  │  └──────────────────────┘   │   │
│  │  • Monitors PIDs to completion │  └─────────────────────────────┘   │
│  │  • Runs CI gate in Review      │                                     │
│  │  • Advances cards on done      │  ┌─────────────────────────────┐   │
│  │                                │  │  Git Integration             │   │
│  │  Workers:                      │──►  • git worktree add/remove  │   │
│  │  ┌──────────┐ ┌──────────────┐ │  │  • git merge --no-ff        │   │
│  │  │ hermes   │ │ claude-code  │ │  │  • branch-per-card          │   │
│  │  └──────────┘ └──────────────┘ │  │  • multi-repo support       │   │
│  └────────────────────────────────┘  └─────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### server.py — Flask API Server

Core HTTP service. Serves the REST API and the static web UI from a single process.

| Property | Value |
|----------|-------|
| Port | 8400 (`TALARIA_PORT`) |
| Column config | `board.json` |
| Card storage | `cards/<id>.md` (one file per card) |
| Activity log | `logs/talaria.log` (append-only JSONL) |
| Static UI | `static/index.html` |

**Key Routes:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/board` | Full board state: columns + slim card list |
| `GET` | `/api/repos` | List configured repositories |
| `POST` | `/api/card` | Create card |
| `GET` | `/api/card/:id` | Get card (full, with status_notes) |
| `PATCH` | `/api/card/:id` | Update card (column, priority, labels, etc.) |
| `DELETE` | `/api/card/:id` | Delete card |
| `POST` | `/api/card/:id/note` | Add status note |
| `GET` | `/api/agent_queue` | View dispatch queue |
| `GET` | `/api/agent_queue/peek` | Preview next queued card (non-destructive) |
| `POST` | `/api/agent_queue/pop` | Remove first card from queue |
| `PATCH` | `/api/column/:id` | Update column configuration |
| `GET` | `/api/activity` | Last 50 activity log entries |
| `GET` | `/docs/<file>` | Serve docs (e.g. architecture.md) |
| `GET` | `/` | Web UI |

**On PATCH /api/card/:id column change**, `_trigger_action()` fires:
- `in_progress`: create git worktree, queue agent
- `done`: merge branch, remove worktree
- Any column with `trigger: notify/webhook/github_issue`: fire that integration

---

### agent_watcher.py — Pipeline Runner

Long-running daemon. Polls the board and drives the autonomous pipeline.

**Lifecycle:**

```
1. Poll GET /api/board every POLL_INTERVAL seconds (default 15s)
2. Find cards in columns with trigger == "agent_spawn"
3. For each eligible card (up to MAX_CONCURRENT, default 2):
   a. Read column config: worker, context_files, instructions
   b. draft_context():
        - Load TALARIA_HOME/talaria.md (project context)
        - Load each file in column.context_files[]
        - Append card title, ID, priority, labels, description
        - Append last 5 status_notes
        - Write to temp file (e.g. /tmp/talaria-<id>-<rand>.md)
   c. Worker.spawn() — start subprocess:
        hermes:      python run_agent.py --query "IMPORTANT: Read <ctx>..."
        claude-code: claude --dangerously-skip-permissions "IMPORTANT: Read <ctx>..."
        codex:       codex "IMPORTANT: Read <ctx>..."
   d. Update card agent_session_id = PID
4. Poll each active worker (os.waitpid WNOHANG) until exit
5. On worker exit → handle_worker_done():
   a. Add status note
   b. PATCH /api/card/:id {"column": next_column}
6. Review column CI gate (triggered for every card in review):
   a. Read card.tests.command (per-card field, not column config)
   b. If no tests defined → auto-advance to done
   c. Run tests.command in card's worktree
   d. exit 0 → advance to done
   e. non-zero → move back to in_progress + add failure note
```

**Environment Variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `TALARIA_PORT` | 8400 | Server port |
| `TALARIA_HOME` | `~/.talaria/talaria` | Path to context guide files |
| `TALARIA_WORK_DIR` | `~` | Working directory for spawned agents |
| `TALARIA_MAX_CONCURRENT` | 2 | Max simultaneous worker processes |
| `POLL_INTERVAL` | 15 | Seconds between board polls |
| `CLAUDE_CODE_BINARY` | `claude` | Claude Code CLI path |
| `HERMES_AGENT_PATH` | `~/.hermes/hermes-agent/run_agent.py` | Hermes binary |
| `HERMES_VENV_PATH` | `~/.hermes/hermes-agent/.venv/bin/python` | Python for hermes |
| `CODEX_BINARY` | `codex` | Codex CLI path |
| `TELEGRAM_BOT_TOKEN` | — | Telegram notifications |
| `TELEGRAM_HOME_CHANNEL_ID` | — | Telegram chat ID |
| `GITHUB_TOKEN` | — | GitHub API token (for `github_issue` trigger) |
| `GITHUB_REPO` | — | Default repo for `github_issue` trigger (`owner/repo`) |

---

### talaria_cli.py — Terminal Interface

Thin HTTP client for terminal-first workflows and agent scripting. Uses only `urllib` (no extra deps).

```bash
talaria list                     # Cards grouped by column (JSON)
talaria create <title>           # New card in backlog
talaria move <card-id> <col>    # Move card (triggers watcher)
talaria log <card-id>            # Activity + status notes
talaria context <card-id>        # Full card data (for agent prompts)
talaria note <card-id> <text>    # Add status note
```

All output is JSON:

```bash
talaria context 9f875537 | jq '.description'
talaria list | jq '.[] | select(.column == "review")'
```

---

### static/index.html — Web UI

Single-page kanban. Polls `/api/board` for state.

- Drag-and-drop cards between columns
- Click card → modal with full description and notes
- Keyboard shortcuts: `N` = new card, `Esc` = close modal
- Activity log panel

---

## Data Model

### board.json — Column Configuration

Source of truth for board structure. **Does not store cards** (cards live in `cards/*.md`).

```json
{
  "_schema": "Talaria board config — columns only",
  "meta": { "name": "Talaria", "version": "1.0" },
  "columns": [
    { "id": "backlog",     "name": "Backlog",      "trigger": null },
    {
      "id": "spec",        "name": "Spec",          "trigger": "agent_spawn",
      "worker": "claude-code",
      "context_files": ["talaria.md", "spec-guide.md"],
      "instructions": "Write a comprehensive spec..."
    },
    { "id": "groom",       "name": "Groom",         "trigger": "agent_spawn",
      "worker": "claude-code",
      "context_files": ["talaria.md", "groom-guide.md"] },
    { "id": "ready",       "name": "Ready",          "trigger": null },
    { "id": "in_progress", "name": "In Progress",    "trigger": "agent_spawn",
      "worker": "claude-code",
      "context_files": ["talaria.md", "coding-guide.md"] },
    { "id": "review",      "name": "Review",         "trigger": null },
    { "id": "done",        "name": "Done",           "trigger": "notify" }
  ]
}
```

Column fields:

| Field | Purpose |
|-------|---------|
| `id` | Unique column identifier |
| `name` | Display name |
| `trigger` | Automation: `null`, `agent_spawn`, `notify`, `webhook`, `github_issue` |
| `worker` | Worker type for `agent_spawn`: `claude-code`, `hermes`, `codex` |
| `context_files` | Files to load from `TALARIA_HOME` into agent context |
| `instructions` | Optional extra instructions injected into agent context |
| `webhook_url` | URL for `webhook` trigger |
| `github_repo` | Repo for `github_issue` trigger |

### cards/<id>.md — Card File

One Markdown file per card. YAML frontmatter + description + log section.

```markdown
---
id: 9f875537
title: Pipeline runner — column config to context drafting
column: done
priority: high
labels: [priority:high, core, backend]
created_at: '2026-03-22T04:45:04Z'
updated_at: '2026-03-23T00:19:18Z'
base_branch: main
branch_name: 9f875537-pipeline-runner
worktree_path: ~/talaria/9f875537-pipeline-runner
agent_session_id: null
repo: talaria
tests:
  command: pytest
  pass_if: exit_0
status_notes:
  - { ts: '2026-03-23T00:01:44Z', author: runner, text: Worker finished. Elapsed: 84s. }
---

Card description / task spec goes here.

## Log

[2026-03-23 00:00:20] **runner**: claude-code spawned (PID 58564)
[2026-03-23 00:01:44] **runner**: Worker finished. Elapsed: 84s.
```

### logs/talaria.log — Activity Log

Append-only JSONL. One JSON object per line.

```json
{"ts": "2026-03-23T00:39:49Z", "action": "moved", "card_id": "06a31581",
 "card_title": "Worktree management", "from_column": "groom", "to_column": "ready"}
```

Actions: `created`, `updated`, `moved`, `deleted`, `trigger_fired`

---

## Trigger System

When a card enters a column, `_trigger_action()` in server.py fires based on the column's `trigger` field:

| Trigger | Handler | Action |
|---------|---------|--------|
| `null` | — | Nothing |
| `agent_spawn` | `agent_watcher.py` | Queue card; watcher spawns worker |
| `notify` | `_notify_telegram()` | Send Telegram message |
| `webhook` | `_fire_webhook()` | POST card JSON to `webhook_url` |
| `github_issue` | `_create_github_issue()` | Open GitHub issue |

---

## Data Flows

### Card Creation

```
User (Browser or CLI)
    │ POST /api/card
    ▼
server.py: generate UUID, save cards/<id>.md
    │ log "created" → logs/talaria.log
    ▼
Web UI refreshes on next poll
```

### Column Transition (e.g. ready → in_progress)

```
User drags card (or CLI move / agent PATCH)
    │ PATCH /api/card/:id {"column": "in_progress"}
    ▼
server.py:
    ├─ Update column in cards/<id>.md
    ├─ _create_worktree(): git worktree add <id>-<slug> -b <branch>
    ├─ _queue_agent(card) → append to agent_queue.json
    └─ Log "moved" → talaria.log

agent_watcher (next poll cycle):
    ├─ Finds card in "in_progress" (agent_spawn trigger)
    ├─ draft_context() → /tmp/talaria-<id>-<rand>.md
    ├─ Worker.spawn() → subprocess PID
    └─ PATCH card: agent_session_id = PID

Worker runs (reads context file, executes task):
    ├─ POST /api/card/:id/note  {"text": "what I did", "author": "hermes"}
    └─ PATCH /api/card/:id      {"column": "review"}

agent_watcher (worker exit detected):
    ├─ Add "Worker finished" status note
    └─ Advance card to next column

Review CI gate (agent_watcher polls review column each cycle):
    ├─ Read card.tests (per-card field, not column config)
    ├─ No tests defined → auto-advance to "done"
    ├─ exit 0  → PATCH column: "done" + notify
    └─ non-zero → PATCH column: "in_progress" + add failure note

Card reaches "done":
    └─ _cleanup_worktree():
           git merge --no-ff <branch>
           git worktree remove --force
           git branch -d <branch>
           Clear worktree_path, branch_name, agent_session_id
```

---

## Git Integration

### Branch-Per-Card Worktrees

When a card enters **In Progress**, a git worktree is created:

```bash
git worktree add <worktree_path> -b <card_id>-<slug> <base_branch>
# e.g. git worktree add ./9f875537-pipeline-runner -b 9f875537-pipeline-runner main
```

- `worktree_path` and `branch_name` stored in card frontmatter
- Worker spawned with `cwd` set to worktree directory
- Worktree gives each card an isolated working copy

When card reaches **Done**:

```bash
git merge --no-ff <branch_name> -m "Merge <card_id>: <title>"
git worktree remove --force <worktree_path>
git branch -d <branch_name>
```

### Multi-Repo Support

`talaria.config.json` maps repo names to local paths:

```json
{
  "repos": [
    { "name": "talaria", "path": "~/talaria" },
    { "name": "api",     "path": "/Users/bryanfeng/myapi" }
  ]
}
```

Set `repo: api` in a card's frontmatter to have its worktree created in `/Users/bryanfeng/myapi`.

---

## File Structure

```
talaria/
├── server.py                 # Flask REST API + static file serving
├── agent_watcher.py          # Pipeline runner / agent orchestrator
├── talaria_cli.py            # Terminal CLI (talaria list/move/note/...)
├── board.json                # Column config — source of truth
├── talaria.config.json       # Repos, worker paths, integrations
├── requirements.txt          # Python deps (flask, watchdog, pyyaml)
├── Dockerfile
├── docker-compose.yml
├── static/
│   └── index.html            # Single-page kanban UI
├── cards/
│   └── <id>.md               # One file per card
├── logs/
│   └── talaria.log           # Append-only JSONL activity log
└── docs/
    ├── architecture.md       # This file
    └── architecture.excalidraw.json
```

Context/guide files live in `TALARIA_HOME` (default `~/.talaria/talaria`):

```
~/.talaria/talaria/
├── talaria.md        # Project context — always injected first
├── spec-guide.md     # Instructions for Spec agents
├── groom-guide.md    # Instructions for Groom agents
└── coding-guide.md   # Instructions for In Progress agents
```

---

## Deployment

**Local:**
```bash
pip install -r requirements.txt
python server.py           # Terminal 1 — API + UI at :8400
python agent_watcher.py    # Terminal 2 — pipeline runner
```

**Docker:**
```bash
docker-compose up
```

**Public (ngrok):**
```bash
python server.py &
ngrok http 8400
# share https://<id>.ngrok.io with remote agents
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| No database | JSON + Markdown is git-friendly, auditable, zero-dependency |
| One `.md` file per card | Enables `git diff`, `git blame`, readable history |
| Polling over push | Decouples agent lifecycle from HTTP; simpler failure model |
| Temp file context injection | Agents receive full structured context without HTTP overhead |
| `os.waitpid(WNOHANG)` | Avoids zombie processes; handles both child and non-child PIDs |
| Branch-per-card worktrees | Agents work in isolation; merges are clean and auditable |
| Column-level trigger config | Board behavior driven by `board.json` — no code changes needed |
| CI gate in Review | Per-card `tests.command` runs in worktree; auto-advances or retries |

---

*Last regenerated: 2026-03-24. Update by moving the Architecture Diagram card to In Progress.*
