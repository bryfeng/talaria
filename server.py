"""
Talaria — Lightweight kanban for agentic team coordination.
"""

import json
import os
import re
import subprocess
import uuid
import threading
import yaml
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, request

BASE_DIR = Path(__file__).parent
CARDS_DIR = BASE_DIR / "cards"
BOARD_FILE = BASE_DIR / "board.json"
LOG_FILE = BASE_DIR / "logs" / "talaria.log"
AGENT_QUEUE = BASE_DIR / "agent_queue.json"
AGENT_QUEUE_LOCK = threading.Lock()

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
app.config["JSON_SORT_KEYS"] = False


# ── card file I/O ──────────────────────────────────────────────────────────────

def _card_path(card_id: str) -> Path:
    return CARDS_DIR / f"{card_id}.md"


def _card_from_md(text: str) -> dict:
    """Parse YAML frontmatter + description + log from a card .md file."""
    if not text.strip():
        return {}

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_raw, rest = parts[1], parts[2]
            fm = yaml.safe_load(fm_raw) or {}
        else:
            fm = {}
            rest = text
    else:
        fm = {}
        rest = text

    rest = rest.strip()

    # Split description from ## Log section
    log_marker = rest.find("## Log")
    if log_marker != -1:
        description = rest[:log_marker].strip()
        log_text = rest[log_marker + len("## Log"):].strip()
    else:
        description = rest
        log_text = ""

    card = dict(fm)
    card["description"] = description

    # Parse log entries back into status_notes format
    status_notes = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: [2026-03-23 00:41:35] **author**: text
        m = re.match(r"\[([^\]]+)\]\s+\*\*([^*]+)\*\*:\s*(.*)", line)
        if m:
            ts_display, author, text = m.group(1), m.group(2), m.group(3)
            # Try to reconstruct ISO ts from display format
            try:
                dt = datetime.strptime(ts_display, "%Y-%m-%d %H:%M:%S")
                ts = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except Exception:
                ts = ts_display
            status_notes.append({
                "ts": ts,
                "author": author,
                "text": text,
            })
    card["status_notes"] = status_notes

    return card


def _card_to_md(card: dict) -> str:
    """Serialize a card dict back to .md format with YAML frontmatter."""
    # Fields that go into frontmatter
    fm_keys = [
        "id", "title", "column", "priority", "assignee", "labels",
        "created_at", "updated_at", "worktree_path", "branch_name",
        "agent_session_id", "base_branch", "cost_log", "github_issue",
    ]
    fm = {}
    for k in fm_keys:
        v = card.get(k)
        if v is not None and v != [] and v != "":
            fm[k] = v

    lines = []
    lines.append("---")
    lines.append(yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip())
    lines.append("---")
    lines.append("")

    desc = card.get("description", "").strip()
    if desc:
        lines.append(desc)
        lines.append("")
    lines.append("")
    lines.append("## Log")
    lines.append("")

    for note in card.get("status_notes", []):
        ts = note.get("ts", "")
        author = note.get("author", "user")
        text = note.get("text", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_display = ts
        lines.append(f"[{ts_display}] **{author}**: {text}")

    return "\n".join(lines)


def _load_card(card_id: str) -> dict:
    """Load a single card from its .md file."""
    path = _card_path(card_id)
    if not path.exists():
        return None
    return _card_from_md(path.read_text())


def _save_card(card: dict) -> None:
    """Write a card dict to its .md file."""
    CARDS_DIR.mkdir(exist_ok=True)
    path = _card_path(card["id"])
    path.write_text(_card_to_md(card))


def _all_cards() -> list:
    """Load all cards from cards/*.md files."""
    CARDS_DIR.mkdir(exist_ok=True)
    cards = []
    for path in sorted(CARDS_DIR.glob("*.md")):
        card_id = path.stem
        card = _card_from_md(path.read_text())
        card["id"] = card_id  # ensure id matches filename
        cards.append(card)
    return cards


# ── board file I/O ─────────────────────────────────────────────────────────────

def _load_board() -> dict:
    """Load board config (meta + columns) from board.json."""
    with open(BOARD_FILE) as f:
        return json.load(f)


def _save_board(data: dict) -> None:
    """Save board config to board.json."""
    with open(BOARD_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _full_board() -> dict:
    """Return the full board response: meta + columns + all cards."""
    board = _load_board()
    board["cards"] = _all_cards()
    return board


# ── helpers ────────────────────────────────────────────────────────────────────

def _log(action: str, card: dict, from_col: str = None, to_col: str = None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "card_id": card.get("id"),
        "card_title": card.get("title"),
        "from_column": from_col,
        "to_column": to_col,
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def _append_log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def _slugify(text: str) -> str:
    """Convert text to a lowercase hyphen-separated slug (max 40 chars)."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:40]


def _create_worktree(card: dict) -> None:
    """Create a git worktree when a card enters In Progress."""
    card_id = card["id"]
    slug = _slugify(card.get("title", card_id))
    branch_name = f"{card_id}-{slug}"
    worktree_path = BASE_DIR / f"{card_id}-{slug}"
    base_branch = card.get("base_branch", "main")

    try:
        existing = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        if existing.stdout.strip():
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=str(BASE_DIR), check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base_branch],
                cwd=str(BASE_DIR), check=True, capture_output=True, text=True,
            )
        card["worktree_path"] = str(worktree_path)
        card["branch_name"] = branch_name
        print(f"[talaria] Worktree created: {worktree_path} (branch: {branch_name})")
    except subprocess.CalledProcessError as e:
        print(f"[talaria] Worktree creation failed for {card_id}: {e.stderr}")
    except Exception as e:
        print(f"[talaria] Worktree creation error for {card_id}: {e}")


def _cleanup_worktree(card: dict) -> None:
    """Merge branch to main and delete worktree when a card enters Done."""
    branch_name = card.get("branch_name")
    worktree_path = card.get("worktree_path")
    if not branch_name:
        return

    try:
        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name,
             "-m", f"Merge {branch_name} (talaria #{card['id']})"],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[talaria] Merge conflict for {branch_name}: {result.stderr}")
            return

        if worktree_path:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=str(BASE_DIR), capture_output=True, text=True,
            )

        subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        print(f"[talaria] Worktree cleaned up: {branch_name}")
    except Exception as e:
        print(f"[talaria] Worktree cleanup error for {card.get('id')}: {e}")


def _trigger_action(column: dict, card: dict, board: dict):
    """Fire side-effects when a card enters a trigger column."""
    trigger = column.get("trigger")
    col_id = column["id"]
    col_name = column["name"]

    if col_id == "in_progress":
        _create_worktree(card)
    elif col_id == "done":
        _cleanup_worktree(card)

    if not trigger:
        return

    if trigger == "agent_spawn":
        _queue_agent(card)
        _notify_telegram(f"🤖 Agent dispatched: *{card['title']}* moved to *{col_name}*")
    elif trigger == "notify":
        _notify_telegram(f"📋 Card moved to *{col_name}*: *{card['title']}*")
    elif trigger == "webhook":
        webhook_url = column.get("webhook_url")
        if webhook_url:
            _fire_webhook(webhook_url, card, column)
        else:
            print(f"[talaria] webhook trigger on column '{col_id}' but no webhook_url configured")
    elif trigger == "github_issue":
        _create_github_issue(card, column, repo=column.get("github_repo"))

    # Fire webhook as side-effect on any column that has webhook_url set
    if trigger != "webhook":
        webhook_url = column.get("webhook_url")
        if webhook_url:
            _fire_webhook(webhook_url, card, column)

    # Fire GitHub issue as side-effect on any column that has github_repo set
    if trigger != "github_issue":
        github_repo = column.get("github_repo")
        if github_repo:
            _create_github_issue(card, column, repo=github_repo)

    _log("trigger_fired", card, to_col=col_id)


def _queue_agent(card: dict):
    """Write card to agent queue so a watcher/cron can dispatch an agent."""
    with AGENT_QUEUE_LOCK:
        queue = []
        if AGENT_QUEUE.exists():
            with open(AGENT_QUEUE) as f:
                queue = json.load(f)
        queue.append({
            "card": card,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        })
        with open(AGENT_QUEUE, "w") as f:
            json.dump(queue, f, indent=2)

def _notify_telegram(msg: str):
    """Send a Telegram message via bot API."""
    token=os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_HOME_CHANNEL_ID") or os.getenv("TELEGRAM_HOME_CHANNEL", "").lstrip("@")
    if not token or not chat_id:
        return
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[talaria] Telegram notify failed: {e}")


def _fire_webhook(url: str, card: dict, column: dict):
    """POST card data to a webhook URL when a card enters a column."""
    import urllib.request
    payload = {
        "event": "card.moved",
        "column": {"id": column["id"], "name": column["name"]},
        "card": card,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[talaria] Webhook fired: {url}")
    except Exception as e:
        print(f"[talaria] Webhook failed ({url}): {e}")


def _create_github_issue(card: dict, column: dict, repo: str = None):
    """Create a GitHub issue when a card enters a column.

    Reads GITHUB_TOKEN and GITHUB_REPO from the environment (or accepts an
    explicit repo override from the column config).
    """
    import urllib.request
    token=os.getenv("GITHUB_TOKEN")
    repo = repo or os.getenv("GITHUB_REPO")
    if not token or not repo:
        print("[talaria] GitHub issue skipped: GITHUB_TOKEN or GITHUB_REPO not set")
        return

    title = card.get("title", "Untitled")
    description = card.get("description", "")
    col_name = column.get("name", column.get("id", ""))
    body_lines = []
    if description:
        body_lines.append(description)
        body_lines.append("")
    body_lines.append(f"**Talaria card:** `{card['id']}` → *{col_name}*")

    payload = {
        "title": title,
        "body": "\n".join(body_lines),
    }
    labels = [l for l in card.get("labels", []) if not l.startswith("priority:")]
    if labels:
        payload["labels"] = labels

    url = f"https://api.github.com/repos/{repo}/issues"
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"[talaria] GitHub issue created: {result.get('html_url')}")
    except Exception as e:
        print(f"[talaria] GitHub issue creation failed: {e}")


# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "static", "index.html")

@app.route("/api/board")
def get_board():
    return jsonify(_full_board())

@app.route("/api/card", methods=["POST"])
def create_card():
    body = request.json

    priority = body.get("priority", "medium")
    labels = body.get("labels", [])
    if priority not in labels:
        labels = [f"priority:{priority}"] + labels

    card = {
        "id": str(uuid.uuid4())[:8],
        "title": body.get("title", "Untitled"),
        "description": body.get("description", ""),
        "column": body.get("column", "backlog"),
        "priority": priority,
        "assignee": body.get("assignee", ""),
        "labels": labels,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "agent_session_id": None,
        "status_notes": [],
    }
    _save_card(card)
    _log("created", card)
    return jsonify(card), 201

@app.route("/api/card/<card_id>", methods=["GET"])
def get_card(card_id):
    card = _load_card(card_id)
    if not card:
        return jsonify({"error": "Not found"}), 404
    return jsonify(card)

@app.route("/api/card/<card_id>", methods=["PATCH"])
def update_card(card_id):
    card = _load_card(card_id)
    if not card:
        return jsonify({"error": "Not found"}), 404

    old_col = card["column"]
    body = request.json
    board = _load_board()

    # Apply updates
    for key in ("title", "description", "priority", "assignee", "labels", "agent_session_id",
                "base_branch", "worktree_path", "branch_name"):
        if key in body:
            card[key] = body[key]

    # Column change → trigger logic
    if "column" in body and body["column"] != old_col:
        card["column"] = body["column"]
        col = next((c for c in board["columns"] if c["id"] == body["column"]), None)
        _log("moved", card, from_col=old_col, to_col=body["column"])
        if col:
            _trigger_action(col, card, board)
    else:
        _log("updated", card)

    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_card(card)
    return jsonify(card)

@app.route("/api/card/<card_id>", methods=["DELETE"])
def delete_card(card_id):
    path = _card_path(card_id)
    if not path.exists():
        return jsonify({"error": "Not found"}), 404
    card = _load_card(card_id)
    path.unlink()
    _log("deleted", card)
    return jsonify({"ok": True})

@app.route("/api/card/<card_id>/note", methods=["POST"])
def add_note(card_id):
    card = _load_card(card_id)
    if not card:
        return jsonify({"error": "Not found"}), 404
    body = request.json
    note = {
        "id": str(uuid.uuid4())[:8],
        "text": body.get("text", ""),
        "author": body.get("author", "user"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    card.setdefault("status_notes", []).append(note)
    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_card(card)
    return jsonify(note), 201

@app.route("/api/agent_queue")
def get_agent_queue():
    with AGENT_QUEUE_LOCK:
        if AGENT_QUEUE.exists():
            with open(AGENT_QUEUE) as f:
                return jsonify(json.load(f))
        return jsonify([])

@app.route("/api/agent_queue/peek")
def peek_agent_queue():
    """Pop the oldest card without removing it (for preview before dispatch)."""
    with AGENT_QUEUE_LOCK:
        if AGENT_QUEUE.exists():
            with open(AGENT_QUEUE) as f:
                queue = json.load(f)
            if queue:
                return jsonify(queue[0])
        return jsonify(None)

@app.route("/api/agent_queue/pop", methods=["POST"])
def pop_agent_queue():
    """Remove the first item from the queue (call after agent is dispatched)."""
    with AGENT_QUEUE_LOCK:
        if not AGENT_QUEUE.exists():
            return jsonify({"ok": True})
        with open(AGENT_QUEUE) as f:
            queue = json.load(f)
        if queue:
            queue.pop(0)
        with open(AGENT_QUEUE, "w") as f:
            json.dump(queue, f, indent=2)
        return jsonify({"ok": True})

@app.route("/api/column/<col_id>", methods=["PATCH"])
def update_column(col_id):
    """Update column configuration (e.g. webhook_url, trigger)."""
    board = _load_board()
    col = next((c for c in board["columns"] if c["id"] == col_id), None)
    if not col:
        return jsonify({"error": "Not found"}), 404
    body = request.json
    for key in ("trigger", "webhook_url", "github_repo", "worker", "context_files", "instructions"):
        if key in body:
            if body[key] is None and key in col:
                del col[key]
            elif body[key] is not None:
                col[key] = body[key]
    _save_board(board)
    return jsonify(col)


@app.route("/api/activity")
def get_activity():
    """Return recent activity log entries (from LOG_FILE)."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        return jsonify([])
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return jsonify(entries[:50])


# ── static files ──────────────────────────────────────────────────────────────

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR / "static", filename)


if __name__ == "__main__":
    port = int(os.getenv("TALARIA_PORT", os.getenv("KANBAN_PORT", 8400)))
    print(f"🗂  Talaria running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
