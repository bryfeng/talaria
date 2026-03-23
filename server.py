"""
Talaria — Lightweight kanban for agentic team coordination.
"""

import json
import os
import re
import subprocess
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, request

BASE_DIR = Path(__file__).parent
KANBAN_FILE = BASE_DIR / "kanban.json"
LOG_FILE = BASE_DIR / "logs" / "talaria.log"
AGENT_QUEUE = BASE_DIR / "agent_queue.json"
AGENT_QUEUE_LOCK = threading.Lock()

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
app.config["JSON_SORT_KEYS"] = False


# ── helpers ────────────────────────────────────────────────────────────────────

def _load():
    with open(KANBAN_FILE) as f:
        return json.load(f)

def _save(data):
    with open(KANBAN_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _log(action: str, card: dict, from_col: str = None, to_col: str = None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "card_id": card.get("id"),
        "card_title": card.get("title"),
        "from_column": from_col,
        "to_column": to_col,
    }
    data = _load()
    data.setdefault("activity_log", []).insert(0, entry)
    data["activity_log"] = data["activity_log"][:500]   # cap at 500 entries
    _save(data)
    _append_log(entry)

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
        # Check if branch already exists
        existing = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        if existing.stdout.strip():
            # Branch exists — attach worktree to it
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
        # Merge branch into main
        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name,
             "-m", f"Merge {branch_name} (talaria #{card['id']})"],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[talaria] Merge conflict for {branch_name}: {result.stderr}")
            return  # Leave worktree in place for manual resolution

        # Remove worktree
        if worktree_path:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=str(BASE_DIR), capture_output=True, text=True,
            )

        # Delete the branch
        subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=str(BASE_DIR), capture_output=True, text=True,
        )
        print(f"[talaria] Worktree cleaned up: {branch_name}")
    except Exception as e:
        print(f"[talaria] Worktree cleanup error for {card.get('id')}: {e}")


def _trigger_action(column: dict, card: dict, data: dict):
    """Fire side-effects when a card enters a trigger column."""
    trigger = column.get("trigger")
    col_id = column["id"]
    col_name = column["name"]

    # Worktree lifecycle management
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

    # Fire webhook as side-effect on any column that has webhook_url set
    # (even alongside other trigger types)
    if trigger != "webhook":
        webhook_url = column.get("webhook_url")
        if webhook_url:
            _fire_webhook(webhook_url, card, column)

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
    token = os.getenv("TELEGRAM_BOT_TOKEN")
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


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "static", "index.html")

@app.route("/api/board")
def get_board():
    return jsonify(_load())

@app.route("/api/card", methods=["POST"])
def create_card():
    data = _load()
    body = request.json

    priority = body.get("priority", "medium")
    labels = body.get("labels", [])
    # Auto-tag priority as a label for filtering
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
    data["cards"].append(card)
    _save(data)
    _log("created", card)
    return jsonify(card), 201

@app.route("/api/card/<card_id>", methods=["GET"])
def get_card(card_id):
    data = _load()
    card = next((c for c in data["cards"] if c["id"] == card_id), None)
    if not card:
        return jsonify({"error": "Not found"}), 404
    return jsonify(card)

@app.route("/api/card/<card_id>", methods=["PATCH"])
def update_card(card_id):
    data = _load()
    idx = next((i for i, c in enumerate(data["cards"]) if c["id"] == card_id), None)
    if idx is None:
        return jsonify({"error": "Not found"}), 404

    card = data["cards"][idx]
    old_col = card["column"]
    body = request.json

    # Apply updates
    for key in ("title", "description", "priority", "assignee", "labels", "agent_session_id",
                "base_branch", "worktree_path", "branch_name"):
        if key in body:
            card[key] = body[key]

    # Column change → trigger logic
    if "column" in body and body["column"] != old_col:
        card["column"] = body["column"]
        col = next((c for c in data["columns"] if c["id"] == body["column"]), None)
        _log("moved", card, from_col=old_col, to_col=body["column"])
        if col:
            _trigger_action(col, card, data)
    else:
        _log("updated", card)

    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return jsonify(card)

@app.route("/api/card/<card_id>", methods=["DELETE"])
def delete_card(card_id):
    data = _load()
    idx = next((i for i, c in enumerate(data["cards"]) if c["id"] == card_id), None)
    if idx is None:
        return jsonify({"error": "Not found"}), 404
    card = data["cards"].pop(idx)
    _save(data)
    _log("deleted", card)
    return jsonify({"ok": True})

@app.route("/api/card/<card_id>/note", methods=["POST"])
def add_note(card_id):
    data = _load()
    idx = next((i for i, c in enumerate(data["cards"]) if c["id"] == card_id), None)
    if idx is None:
        return jsonify({"error": "Not found"}), 404
    body = request.json
    note = {
        "id": str(uuid.uuid4())[:8],
        "text": body.get("text", ""),
        "author": body.get("author", "user"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data["cards"][idx].setdefault("status_notes", []).append(note)
    data["cards"][idx]["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
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
    data = _load()
    col = next((c for c in data["columns"] if c["id"] == col_id), None)
    if not col:
        return jsonify({"error": "Not found"}), 404
    body = request.json
    for key in ("trigger", "webhook_url", "worker", "context_files", "instructions"):
        if key in body:
            if body[key] is None and key in col:
                del col[key]
            elif body[key] is not None:
                col[key] = body[key]
    _save(data)
    return jsonify(col)


@app.route("/api/activity")
def get_activity():
    data = _load()
    return jsonify(data.get("activity_log", [])[:50])


# ── static files ─────────────────────────────────────────────────────────────

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR / "static", filename)


if __name__ == "__main__":
    port = int(os.getenv("TALARIA_PORT", os.getenv("KANBAN_PORT", 8400)))
    print(f"🗂  Talaria running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
