#!/opt/homebrew/bin/python3.12
"""
Talaria — Lightweight kanban for agentic team coordination.
"""

__version__ = "0.1.0"

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from talaria.board import (
    BASE_DIR,
    LOG_FILE,
    _load_board,
    _save_board,
    _load_card,
    _save_card,
    _card_path,
    _full_board,
    _get_repos,
    _log,
    _archive_excess_done_cards,
)
from talaria.triggers import (
    _trigger_action,
    AGENT_QUEUE,
    AGENT_QUEUE_LOCK,
)
from talaria.guardrails import enforce_runner_target_separation

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
app.config["JSON_SORT_KEYS"] = False
logger = logging.getLogger(__name__)


# ── API ─────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "static", "index.html")


@app.route("/api/board")
def get_board():
    return jsonify(_full_board())


@app.route("/api/repos")
def get_repos():
    """Return repos list from talaria.config.json."""
    return jsonify(_get_repos())


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
        "repo": body.get("repo") or None,
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
                "base_branch", "worktree_path", "branch_name", "cost_log", "repo", "tests"):
        if key in body:
            card[key] = body[key]

    moved_to = None

    # Column change → trigger logic
    if "column" in body and body["column"] != old_col:
        moved_to = body["column"]
        card["column"] = moved_to
        col = next((c for c in board["columns"] if c["id"] == moved_to), None)
        _log("moved", card, from_col=old_col, to_col=moved_to)
        if col:
            try:
                _trigger_action(col, card, board)
            except Exception as e:
                logger.exception("Trigger action failed for %s: %s", card_id, e)
    else:
        _log("updated", card)

    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_card(card)

    if moved_to == "done":
        _archive_excess_done_cards()

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


@app.route("/api/card/<card_id>/cost", methods=["POST"])
def add_cost(card_id):
    card = _load_card(card_id)
    if not card:
        return jsonify({"error": "Not found"}), 404
    body = request.json
    entry = {
        "agent": body.get("agent", "unknown"),
        "tokens": body.get("tokens", 0),
        "cost_usd": body.get("cost_usd", 0.0),
        "ts": body.get("ts", datetime.now(timezone.utc).isoformat()),
    }
    card.setdefault("cost_log", []).append(entry)
    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_card(card)
    return jsonify(entry), 201


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
    for key in ("trigger", "webhook_url", "webhook_headers", "github_repo", "worker", "context_files", "instructions"):
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


@app.route("/api/arch/meta")
def arch_meta():
    """Return last-modified timestamps for architecture docs (for auto-refresh polling)."""
    docs_dir = BASE_DIR / "docs"
    result = {}
    for filename in ["architecture.md", "architecture.excalidraw.json"]:
        path = docs_dir / filename
        if path.exists():
            result[filename] = {"exists": True, "mtime": path.stat().st_mtime}
        else:
            result[filename] = {"exists": False}
    return jsonify(result)


# ── static files ───────────────────────────────────────────────────────────────

@app.route("/docs/<path:filename>")
def docs_files(filename):
    """Serve architecture docs and diagrams."""
    return send_from_directory(BASE_DIR / "docs", filename)


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR / "static", filename)


def main():
    """Entry point for talaria-server CLI."""
    runner_dir = BASE_DIR
    target_paths = [
        os.path.expanduser(repo.get("path", ""))
        for repo in _get_repos()
        if isinstance(repo, dict)
    ]
    enforce_runner_target_separation(
        runner_dir=runner_dir,
        target_paths=[Path(p) for p in target_paths if p],
    )

    port = int(os.getenv("TALARIA_PORT", os.getenv("KANBAN_PORT", 8400)))
    print(f"🗂  Talaria running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=True)


if __name__ == "__main__":
    main()
