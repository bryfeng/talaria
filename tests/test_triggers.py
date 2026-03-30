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
            ok = api_note("card456", "done", author="runner")
            assert ok is True
            req = mock_urlopen.call_args[0][0]
            assert "card456" in req.full_url
            assert req.full_url.endswith("/note")
