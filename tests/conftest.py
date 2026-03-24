"""
conftest.py — pytest fixtures for the Talaria test suite.

Provides:
  tmp_talaria_dir  — a temporary directory with cards/, board.json, logs/ subdirs
                     (patches server globals so tests run in isolation)
  app_client       — Flask test client for the Flask app
  server_module    — the actual talaria.server module (for direct access to helpers)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

# Import the real talaria.server module (not the thin root wrapper).
# When the package is installed (`pip install -e .`), this imports
# src/talaria/server.py via the talaria package.
import talaria.server as talaria_server


# ── Standard board.json used across fixtures ──────────────────────────────────

STANDARD_BOARD = {
    "_schema": "Talaria board config",
    "meta": {
        "name": "Talaria",
        "version": "1.0",
        "created": "2026-03-21T00:00:00Z",
    },
    "columns": [
        {"id": "backlog", "name": "Backlog", "trigger": None},
        {"id": "spec", "name": "Spec", "trigger": "agent_spawn", "worker": "claude-code"},
        {"id": "groom", "name": "Groom", "trigger": "agent_spawn", "worker": "claude-code"},
        {"id": "ready", "name": "Ready", "trigger": None},
        {
            "id": "in_progress",
            "name": "In Progress",
            "trigger": "agent_spawn",
            "worker": "claude-code",
        },
        {"id": "review", "name": "Review", "trigger": None},
        {"id": "done", "name": "Done", "trigger": "notify"},
    ],
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_talaria_dir(monkeypatch):
    """
    Creates a temp directory that looks like a Talaria instance:
        <tmpdir>/cards/
        <tmpdir>/board.json
        <tmpdir>/logs/

    Patches server.py's module globals so all file I/O happens inside the
    temp directory instead of the real ~/.talaria/talaria.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cards_dir = tmp / "cards"
        board_file = tmp / "board.json"
        logs_dir = tmp / "logs"
        agent_queue = tmp / "agent_queue.json"

        cards_dir.mkdir()
        logs_dir.mkdir()

        # Write default board.json
        board_file.write_text(json.dumps(STANDARD_BOARD, indent=2))

        # Patch server.py globals
        monkeypatch.setattr(talaria_server, "CARDS_DIR", cards_dir)
        monkeypatch.setattr(talaria_server, "BOARD_FILE", board_file)
        monkeypatch.setattr(talaria_server, "LOG_FILE", logs_dir / "talaria.log")
        monkeypatch.setattr(talaria_server, "AGENT_QUEUE", agent_queue)
        monkeypatch.setattr(talaria_server, "BASE_DIR", tmp)

        yield {
            "root": tmp,
            "cards_dir": cards_dir,
            "board_file": board_file,
            "logs_dir": logs_dir,
            "agent_queue": agent_queue,
        }


@pytest.fixture
def app_client(tmp_talaria_dir):
    """
    Returns a Flask test client configured against the real server.py app.
    All routes operate inside tmp_talaria_dir.
    """
    talaria_server.app.config["TESTING"] = True
    with talaria_server.app.test_client() as client:
        yield client
