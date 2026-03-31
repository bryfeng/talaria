"""Tests for talaria.triggers and agent_watcher helper APIs."""

import json
import os
from unittest.mock import MagicMock, patch

import talaria.triggers as triggers


class TestTriggerAction:
    def test_noop_when_trigger_is_none(self, tmp_talaria_dir):
        card = {"id": "test123", "title": "No trigger card", "column": "backlog"}
        col = {"id": "backlog", "name": "Backlog", "trigger": None}
        board = {"columns": [col]}
        triggers._trigger_action(col, card, board)

    def test_agent_spawn_trigger_calls_queue_agent(self, tmp_talaria_dir):
        card = {"id": "test456", "title": "Spawn card", "column": "spec"}
        col = {"id": "spec", "name": "Spec", "trigger": "agent_spawn", "worker": "claude-code"}
        board = {"columns": [col]}
        with patch("talaria.triggers._queue_agent") as mock_queue, patch("talaria.triggers._notify_telegram"):
            triggers._trigger_action(col, card, board)
            mock_queue.assert_called_once_with(card)

    def test_webhook_trigger_fires_webhook(self, tmp_talaria_dir):
        card = {"id": "testweb", "title": "Webhook card", "column": "review"}
        col = {"id": "review", "name": "Review", "trigger": "webhook", "webhook_url": "https://example.com/hook"}
        board = {"columns": [col]}
        with patch("talaria.triggers._fire_webhook") as mock_fire:
            triggers._trigger_action(col, card, board)
            mock_fire.assert_called_once_with("https://example.com/hook", card, col)

    def test_github_issue_trigger_calls_creator(self, tmp_talaria_dir):
        card = {"id": "testgh", "title": "GH Issue card", "column": "backlog"}
        col = {"id": "backlog", "name": "Backlog", "trigger": "github_issue", "github_repo": "owner/repo"}
        board = {"columns": [col]}
        with patch("talaria.triggers._create_github_issue") as mock_gh:
            triggers._trigger_action(col, card, board)
            mock_gh.assert_called_once_with(card, col, repo="owner/repo")


class TestQueueAgent:
    def test_writes_to_agent_queue(self, tmp_talaria_dir):
        triggers._queue_agent({"id": "queue01", "title": "Queue me"})
        queue_path = tmp_talaria_dir["agent_queue"]
        assert queue_path.exists()
        queue = json.loads(queue_path.read_text())
        assert len(queue) == 1
        assert queue[0]["card"]["id"] == "queue01"

    def test_multiple_cards_append(self, tmp_talaria_dir):
        triggers._queue_agent({"id": "q1", "title": "Card 1"})
        triggers._queue_agent({"id": "q2", "title": "Card 2"})
        queue = json.loads(tmp_talaria_dir["agent_queue"].read_text())
        assert [q["card"]["id"] for q in queue] == ["q1", "q2"]

    def test_compact_queue_drops_missing_and_dedupes(self, tmp_talaria_dir):
        # Create one live card file by moving a card through API helper path.
        from talaria.board import _save_card

        _save_card({"id": "live1", "title": "Live", "column": "backlog", "created_at": "2026-01-01T00:00:00+00:00"})
        tmp_talaria_dir["agent_queue"].write_text(
            json.dumps(
                [
                    {"card": {"id": "live1", "column": "backlog"}, "queued_at": "2026-01-01T00:00:00+00:00"},
                    {"card": {"id": "live1", "column": "backlog"}, "queued_at": "2026-01-01T00:00:01+00:00"},
                    {"card": {"id": "gone", "column": "backlog"}, "queued_at": "2026-01-01T00:00:02+00:00"},
                ],
                indent=2,
            )
        )

        result = triggers._compact_agent_queue()
        assert result["before"] == 3
        assert result["after"] == 1
        assert result["dropped"]["missing_card"] == 1
        assert result["dropped"]["deduped"] == 1


class TestNotifyTelegram:
    def test_noops_without_credentials(self, tmp_talaria_dir):
        with patch.dict(os.environ, {}, clear=True):
            triggers._notify_telegram("hello")  # should not raise


class TestAgentWatcherHelpers:
    def test_api_board_returns_board_dict(self, tmp_talaria_dir):
        from agent_watcher import api_board

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"columns": [{"id": "backlog", "name": "Backlog"}], "meta": {"name": "Test"}}).encode()
        cm = MagicMock()
        cm.__enter__.return_value = mock_response
        cm.__exit__.return_value = False

        with patch("agent_watcher.urllib.request.urlopen", return_value=cm):
            board = api_board()
            assert board is not None
            assert board["meta"]["name"] == "Test"

    def test_api_get_returns_card(self, tmp_talaria_dir):
        from agent_watcher import api_get

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"id": "card123", "title": "AW test", "column": "backlog"}).encode()
        cm = MagicMock()
        cm.__enter__.return_value = mock_response
        cm.__exit__.return_value = False

        with patch("agent_watcher.urllib.request.urlopen", return_value=cm):
            card = api_get("card123")
            assert card is not None
            assert card["id"] == "card123"

    def test_api_note_posts_to_endpoint(self, tmp_talaria_dir):
        from agent_watcher import api_note

        cm = MagicMock()
        cm.__enter__.return_value = MagicMock()
        cm.__exit__.return_value = False

        with patch("agent_watcher.urllib.request.urlopen", return_value=cm) as mock_urlopen:
            ok = api_note("card456", "done", author="***")
            assert ok is True
            req = mock_urlopen.call_args[0][0]
            assert "card456" in req.full_url
            assert req.full_url.endswith("/note")

    def test_api_compact_queue_calls_compact_endpoint(self, tmp_talaria_dir):
        from agent_watcher import api_compact_queue

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"before": 3, "after": 1}).encode()
        cm = MagicMock()
        cm.__enter__.return_value = mock_response
        cm.__exit__.return_value = False

        with patch("agent_watcher.urllib.request.urlopen", return_value=cm) as mock_urlopen:
            result = api_compact_queue()
            assert result == {"before": 3, "after": 1}
            req = mock_urlopen.call_args[0][0]
            assert req.full_url.endswith("/api/agent_queue/compact")
            assert req.get_method() == "POST"
