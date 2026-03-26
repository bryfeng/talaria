"""Tests for automatic architecture-refresh card creation in the watcher."""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import agent_watcher
from agent_watcher import PipelineRunner


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))


def test_architecture_refresh_reason_missing_docs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "src/talaria/server.py", time.time())

        reason = agent_watcher._architecture_refresh_reason(root)
        assert reason and reason.startswith("missing_doc:")


def test_architecture_refresh_reason_core_newer_than_docs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        now = time.time()

        _touch(root / "docs/architecture.md", now - 200)
        _touch(root / "docs/architecture.excalidraw.json", now - 200)
        _touch(root / "src/talaria/server.py", now - 50)

        reason = agent_watcher._architecture_refresh_reason(root)
        assert reason == "core_newer:src/talaria/server.py"


def test_architecture_refresh_reason_stale_docs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        now = time.time()

        _touch(root / "docs/architecture.md", now - (20 * 24 * 3600))
        _touch(root / "docs/architecture.excalidraw.json", now - (20 * 24 * 3600))
        _touch(root / "src/talaria/server.py", now - (30 * 24 * 3600))

        with patch.object(agent_watcher, "ARCH_DOC_MAX_AGE_SEC", 14 * 24 * 3600):
            reason = agent_watcher._architecture_refresh_reason(root, now_ts=now)

        assert reason and reason.startswith("stale_docs:")


def test_maybe_queue_architecture_refresh_creates_card_once():
    board = {"cards": []}
    runner = PipelineRunner()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        now = time.time()
        _touch(root / "docs/architecture.md", now - 200)
        _touch(root / "docs/architecture.excalidraw.json", now - 200)
        _touch(root / "src/talaria/server.py", now - 50)

        with patch.object(agent_watcher, "TALARIA_WORK_DIR", str(root)), \
             patch.object(agent_watcher, "api_create", return_value={"id": "arch123"}) as mock_create, \
             patch.object(agent_watcher, "api_note") as mock_note, \
             patch.object(agent_watcher, "notify") as mock_notify:
            runner._maybe_queue_architecture_refresh(board)

            assert mock_create.call_count == 1
            payload = mock_create.call_args[0][0]
            assert payload["title"] == agent_watcher.ARCH_REFRESH_TITLE
            assert agent_watcher.ARCH_REFRESH_LABEL in payload.get("labels", [])
            mock_note.assert_called_once()
            mock_notify.assert_called_once()


def test_maybe_queue_architecture_refresh_skips_when_existing_open_card():
    board = {
        "cards": [
            {
                "id": "c1",
                "title": "Architecture Diagram (auto-refresh)",
                "column": "ready",
                "labels": [agent_watcher.ARCH_REFRESH_LABEL],
            }
        ]
    }
    runner = PipelineRunner()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        now = time.time()
        _touch(root / "docs/architecture.md", now - 200)
        _touch(root / "docs/architecture.excalidraw.json", now - 200)
        _touch(root / "src/talaria/server.py", now - 50)

        with patch.object(agent_watcher, "TALARIA_WORK_DIR", str(root)), \
             patch.object(agent_watcher, "api_create") as mock_create:
            runner._maybe_queue_architecture_refresh(board)
            mock_create.assert_not_called()
