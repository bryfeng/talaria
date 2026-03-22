"""
Talaria — Lightweight kanban for agentic team coordination.
"""

import json
import os
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

def _trigger_action(column: dict, card: dict, data: dict):
    """Fire side-effects when a card enters a trigger column."""
    trigger = column.get("trigger")
    if not trigger:
        return

    col_id = column["id"]
    col_name = column["name"]

    if trigger == "agent_spawn":
        _queue_agent(card)
        _notify_telegram(f"🤖 Agent dispatched: *{card['title']}* moved to *{col_name}*")
    elif trigger == "notify":
        _notify_telegram(f"📋 Card moved to *{col_name}*: *{card['title']}*")

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
    for key in ("title", "description", "priority", "assignee", "labels", "agent_session_id"):
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
