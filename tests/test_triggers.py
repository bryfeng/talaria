"""
test_triggers.py — Tests for the agent_watcher trigger system.

Tests the logic in server.py's _trigger_action function and related helpers:
  _trigger_action(col, card, board) — fires side-effects on column transitions
  _queue_agent(card)               — writes card to agent_queue.json
  _notify_telegram(msg)            — non-blocking Telegram send (no-ops without creds)

We also test the agent_watcher module's own helpers.
"""

import json
import os
from unittest.mock import patch


# _trigger_action + AGENT_QUEUE come through talaria.server (re-exported from triggers).
# _queue_agent + _notify_telegram are only in talaria.triggers.
from talaria.server import _trigger_action, AGENT_QUEUE
from talaria.triggers import _queue_agent, _notify_telegram


# ── _trigger_action tests ────────────────────────────────────────────────────

class TestTriggerAction:
    def test_noop_when_trigger_is_none(self, tmp_talaria_dir):
        card = {"id": "test123", "title": "No trigger card", "column": "backlog"}
        col = {"id": "backlog", "name": "Backlog", "trigger": None}
        board = {"columns": [col]}

        # Should not raise
        _trigger_action(col, card, board)

    def test_agent_spawn_trigger_calls_queue_agent(self, tmp_talaria_dir):
        card = {"id": "test456", "title": "Spawn card", "column": "spec"}
        col = {"id": "spec", "name": "Spec", "trigger": "agent_spawn", "worker": "claude-code"}
        board = {"columns": [col]}

        with patch("talaria.triggers._queue_agent") as mock_queue, \
             patch("talaria.triggers._notify_telegram"):
            _trigger_action(col, card, board)
            mock_queue.assert_called_once_with(card)

    def test_notify_trigger_calls_notify_telegram(self, tmp_talaria_dir):
        card = {"id": "test789", "title": "Notify card", "column": "done"}
        col = {"id": "done", "name": "Done", "trigger": "notify"}
        board = {"columns": [col]}

        with patch("talaria.triggers._notify_telegram") as mock_notify:
            _trigger_action(col, card, board)
            mock_notify.assert_called_once()
            # Message should mention the card title and column
            call_args = mock_notify.call_args[0][0]
            assert "Notify card" in call_args
            assert "Done" in call_args

    def test_webhook_trigger_fires_webhook(self, tmp_talaria_dir):
        card = {"id": "testweb", "title": "Webhook card", "column": "review"}
        col = {
            "id": "review",
            "name": "Review",
            "trigger": "webhook",
            "webhook_url": "https://example.com/hook",
        }
        board = {"columns": [col]}

        with patch("talaria.server._fire_webhook") as mock_fire:
            _trigger_action(col, card, board)
            mock_fire.assert_called_once_with(
                "https://example.com/hook", card, col
            )

    def test_webhook_without_trigger_still_fires(self, tmp_talaria_dir):
        """A column without a trigger but with a webhook_url should still fire."""
        card = {"id": "testweb2", "title": "Webhook no-trigger", "column": "backlog"}
        col = {
            "id": "backlog",
            "name": "Backlog",
            "trigger": None,
            "webhook_url": "https://example.com/hook2",
        }
        board = {"columns": [col]}

        with patch("talaria.server._fire_webhook") as mock_fire:
            _trigger_action(col, card, board)
            mock_fire.assert_called_once()

    def test_github_issue_trigger(self, tmp_talaria_dir):
        card = {"id": "testgh", "title": "GH Issue card", "column": "backlog"}
        col = {
            "id": "backlog",
            "name": "Backlog",
            "trigger": "github_issue",
            "github_repo": "owner/repo",
        }
        board = {"columns": [col]}

        with patch("talaria.server._create_github_issue") as mock_gh:
            _trigger_action(col, card, board)
            mock_gh.assert_called_once_with(card, col, repo="owner/repo")

    def test_in_progress_column_calls_create_worktree(self, tmp_talaria_dir):
        """Entering in_progress should trigger worktree creation."""
        card = {"id": "testip", "title": "In progress card", "column": "in_progress"}
        col = {
            "id": "in_progress",
            "name": "In Progress",
            "trigger": "agent_spawn",
            "worker": "claude-code",
        }
        board = {"columns": [col]}

        with patch("talaria.triggers._create_worktree") as mock_wt, \
             patch("talaria.triggers._notify_telegram"):
            _trigger_action(col, card, board)
            mock_wt.assert_called_once_with(card)

    def test_done_column_calls_cleanup_worktree(self, tmp_talaria_dir):
        """Entering done should trigger worktree cleanup."""
        card = {"id": "testdone", "title": "Done card", "column": "done", "branch_name": "feat-x"}
        col = {"id": "done", "name": "Done", "trigger": "notify"}
        board = {"columns": [col]}

        with patch("talaria.triggers._cleanup_worktree") as mock_cleanup, \
             patch("talaria.triggers._notify_telegram"):
            _trigger_action(col, card, board)
            mock_cleanup.assert_called_once_with(card)


# ── _queue_agent tests ───────────────────────────────────────────────────────

class TestQueueAgent:
    def test_writes_to_agent_queue(self, tmp_talaria_dir):
        card = {"id": "queue01", "title": "Queue me"}
        _queue_agent(card)

        assert AGENT_QUEUE.exists()
        queue = json.loads(AGENT_QUEUE.read_text())
        assert len(queue) == 1
        assert queue[0]["card"]["id"] == "queue01"
        assert "queued_at" in queue[0]

    def test_multiple_cards_append(self, tmp_talaria_dir):
        _queue_agent({"id": "q1", "title": "Card 1"})
        _queue_agent({"id": "q2", "title": "Card 2"})

        queue = json.loads(AGENT_QUEUE.read_text())
        assert len(queue) == 2
        assert [q["card"]["id"] for q in queue] == ["q1", "q2"]


# ── _notify_telegram tests ───────────────────────────────────────────────────

class TestNotifyTelegram:
    def test_noops_without_credentials(self, tmp_talaria_dir):
        """When TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL_ID are absent,
        _notify_telegram should silently return without raising."""
        with patch.dict(os.environ, {}, clear=True):
            _notify_telegram("Test message")  # should not raise

    def test_sends_when_credentials_present(self, tmp_talaria_dir):
        import os as _os
        with patch.dict(_os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_HOME_CHANNEL_ID": "987654321",
        }):
            with patch("talaria.server.urllib.request.urlopen") as mock_urlopen:
                _notify_telegram("Hello from tests")
                # Non-blocking thread — give it a moment to fire
                import time; time.sleep(0.1)
                mock_urlopen.assert_called_once()


# ── agent_watcher module helpers ─────────────────────────────────────────────
# These test the agent_watcher module's HTTP client functions (api_board, api_get, api_cost).
# We mock urllib.request.urlopen to avoid spawning real servers.
# The Flask test client (app_client) handles server-side testing.

class TestAgentWatcherHelpers:
    def test_api_board_returns_board_dict(self, tmp_talaria_dir, monkeypatch):
        """agent_watcher.api_board() parses the board response correctly."""
        from unittest.mock import MagicMock
        from agent_watcher import api_board

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "columns": [{"id": "backlog", "name": "Backlog"}],
            "meta": {"name": "Test"}
        }).encode()

        with patch("agent_watcher.urllib.request.urlopen", return_value=mock_response):
            board = api_board()
            assert board is not None
            assert "columns" in board
            assert board["meta"]["name"] == "Test"

    def test_api_get_returns_card(self, tmp_talaria_dir, monkeypatch):
        """agent_watcher.api_get() parses the card response correctly."""
        from unittest.mock import MagicMock
        from agent_watcher import api_get

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "id": "card123",
            "title": "AW test",
            "column": "backlog"
        }).encode()

        with patch("agent_watcher.urllib.request.urlopen", return_value=mock_response):
            card = api_get("card123")
            assert card is not None
            assert card["id"] == "card123"
            assert card["title"] == "AW test"

    def test_api_cost_posts_to_endpoint(self, tmp_talaria_dir, monkeypatch):
        """agent_watcher.api_cost() POSTs to the cost endpoint."""
        from unittest.mock import MagicMock, call
        from agent_watcher import api_cost

        mock_response = MagicMock()
        mock_urlopen = MagicMock(return_value=mock_response)

        with patch("agent_watcher.urllib.request.urlopen", mock_urlopen):
            ok = api_cost("card456", "hermes", 1000, 0.05)
            assert ok is True
            # Verify it was called
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args[0][0]
            assert "card456" in call_args.full_url
            assert "cost" in call_args.full_url
