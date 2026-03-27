"""
conftest.py — pytest fixtures for the Talaria test suite.

Provides:
  tmp_talaria_dir  — a temporary directory with cards/, board.json, logs/
  app_client       — Flask test client bound to the real talaria.server app
"""

import json
import tempfile
from pathlib import Path

import pytest

import talaria.board as talaria_board
import talaria.server as talaria_server
import talaria.triggers as talaria_triggers


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


@pytest.fixture
def tmp_talaria_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cards_dir = tmp / "cards"
        board_file = tmp / "board.json"
        logs_dir = tmp / "logs"
        log_file = logs_dir / "talaria.log"
        agent_queue = tmp / "agent_queue.json"
        archive_dir = cards_dir / "archive"

        cards_dir.mkdir()
        logs_dir.mkdir()
        board_file.write_text(json.dumps(STANDARD_BOARD, indent=2))

        # Patch board module paths (source of truth for file I/O helpers)
        monkeypatch.setattr(talaria_board, "BASE_DIR", tmp)
        monkeypatch.setattr(talaria_board, "CARDS_DIR", cards_dir)
        monkeypatch.setattr(talaria_board, "ARCHIVE_DIR", archive_dir)
        monkeypatch.setattr(talaria_board, "BOARD_FILE", board_file)
        monkeypatch.setattr(talaria_board, "LOG_FILE", log_file)

        # Patch server/triggers copies imported at module import time
        monkeypatch.setattr(talaria_server, "BASE_DIR", tmp)
        monkeypatch.setattr(talaria_server, "LOG_FILE", log_file)
        monkeypatch.setattr(talaria_server, "AGENT_QUEUE", agent_queue)

        monkeypatch.setattr(talaria_triggers, "AGENT_QUEUE", agent_queue)

        yield {
            "root": tmp,
            "cards_dir": cards_dir,
            "archive_dir": archive_dir,
            "board_file": board_file,
            "logs_dir": logs_dir,
            "log_file": log_file,
            "agent_queue": agent_queue,
        }


@pytest.fixture
def app_client(tmp_talaria_dir):
    talaria_server.app.config["TESTING"] = True
    with talaria_server.app.test_client() as client:
        yield client
