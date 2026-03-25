"""
test_cli.py — Tests for talaria.cli command interface.

The CLI is a thin wrapper that imports from talaria.cli. We test it by
mocking the HTTP layer (urllib.request.urlopen) so we don't need a running
server.
"""

import json
import sys
from unittest.mock import patch, MagicMock

import pytest

# Import the CLI module from the installed package.
# Note: when running from ~/talaria, the thin wrapper server.py is shadowed
# by the package, but talaria.cli is the real thing.
from talaria.cli import (
    cmd_list,
    cmd_status,
    cmd_create,
    cmd_move,
    cmd_log,
    cmd_context,
    cmd_note,
    _request,
    COMMANDS,
)


# ── canned board used across tests ────────────────────────────────────────────

STANDARD_BOARD = {
    "meta": {"name": "Talaria"},
    "columns": [
        {"id": "backlog", "name": "Backlog"},
        {"id": "spec", "name": "Spec"},
        {"id": "groom", "name": "Groom"},
        {"id": "ready", "name": "Ready"},
        {"id": "in_progress", "name": "In Progress"},
        {"id": "review", "name": "Review"},
        {"id": "done", "name": "Done"},
    ],
    "cards": [
        {
            "id": "aaaa1111",
            "title": "Alpha card",
            "column": "backlog",
            "priority": "medium",
            "assignee": "alice",
            "labels": ["priority:medium"],
        },
        {
            "id": "bbbb2222",
            "title": "Beta card",
            "column": "in_progress",
            "priority": "high",
            "assignee": "",
            "labels": ["priority:high", "frontend"],
        },
        {
            "id": "cccc3333",
            "title": "Gamma card",
            "column": "done",
            "priority": "low",
            "assignee": "bob",
            "labels": ["priority:low"],
        },
    ],
}

SINGLE_CARD = {
    "id": "aaaa1111",
    "title": "Alpha card",
    "column": "backlog",
    "priority": "medium",
    "description": "The alpha card description.",
    "assignee": "alice",
    "labels": ["priority:medium"],
    "notes": [
        {"id": "n1", "text": "Working on it.", "author": "alice", "ts": "2026-03-24T00:00:00+00:00"}
    ],
    "cost_log": [
        {"agent": "hermes", "tokens": 500, "cost_usd": 0.01, "ts": "2026-03-24T00:00:00+00:00"}
    ],
    "activity": [
        {"ts": "2026-03-24T00:00:00+00:00", "action": "created", "card_id": "aaaa1111", "card_title": "Alpha card"}
    ],
}


# ── talaria list ─────────────────────────────────────────────────────────────

class TestCmdList:
    def test_list_returns_json(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_list([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["id"] == "aaaa1111"
        assert data[0]["column_name"] == "Backlog"

    def test_list_groups_by_column(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_list([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        ids = [c["id"] for c in data]
        assert "aaaa1111" in ids
        assert "bbbb2222" in ids
        assert "cccc3333" in ids

    def test_list_includes_priority_and_assignee(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_list([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        alice_card = next(c for c in data if c["id"] == "aaaa1111")
        assert alice_card["assignee"] == "alice"


# ── talaria status ───────────────────────────────────────────────────────────

class TestCmdStatus:
    def test_status_returns_active_and_backlog(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_status([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert "active" in data
        assert "backlog" in data

    def test_in_progress_is_in_active(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_status([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        active_ids = [c["id"] for c in data["active"]]
        assert "bbbb2222" in active_ids  # in_progress

    def test_backlog_cards(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = STANDARD_BOARD
            cmd_status([])

        out = capsys.readouterr()
        data = json.loads(out.out)
        backlog_ids = [c["id"] for c in data["backlog"]]
        assert "aaaa1111" in backlog_ids  # backlog
        assert "cccc3333" in backlog_ids  # done


# ── talaria create ───────────────────────────────────────────────────────────

class TestCmdCreate:
    def test_create_sends_post_to_api(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = SINGLE_CARD
            cmd_create(["Brand new card"])

        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/api/card"
        assert call_args[1]["body"]["title"] == "Brand new card"

    def test_create_prints_card_json(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = SINGLE_CARD
            cmd_create(["Alpha card"])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert data["id"] == "aaaa1111"
        assert data["title"] == "Alpha card"

    def test_create_with_priority(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "new1", "title": "High card"}
            cmd_create(["High card", "-p", "critical"])

        assert mock_req.call_args[1]["body"]["priority"] == "critical"

    def test_create_with_column(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "new2", "title": "Spec card"}
            cmd_create(["Spec card", "-c", "spec"])

        assert mock_req.call_args[1]["body"]["column"] == "spec"

    def test_create_with_labels(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "new3", "title": "Labelled card"}
            cmd_create(["Labelled card", "-l", "bug,urgent"])

        assert mock_req.call_args[1]["body"]["labels"] == ["bug", "urgent"]

    def test_create_with_description(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "new4", "title": "Described card"}
            cmd_create(["Described card", "-d", "This is the description."])

        assert mock_req.call_args[1]["body"]["description"] == "This is the description."


# ── talaria move ─────────────────────────────────────────────────────────────

class TestCmdMove:
    def test_move_calls_patch_with_column(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "aaaa1111", "column": "spec"}
            cmd_move(["aaaa1111", "spec"])

        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "PATCH"
        assert "aaaa1111" in call_args[0][1]
        assert call_args[1]["body"]["column"] == "spec"

    def test_move_requires_two_args(self, capsys):
        with pytest.raises(SystemExit):
            cmd_move(["aaaa1111"])

    def test_move_prints_updated_card(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {"id": "aaaa1111", "column": "in_progress"}
            cmd_move(["aaaa1111", "in_progress"])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert data["column"] == "in_progress"


# ── talaria log ──────────────────────────────────────────────────────────────

class TestCmdLog:
    def test_log_returns_notes_and_activity(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.side_effect = [SINGLE_CARD, SINGLE_CARD["activity"]]
            cmd_log(["aaaa1111"])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert data["id"] == "aaaa1111"
        assert "notes" in data
        assert "activity" in data
        assert len(data["notes"]) == 1

    def test_log_requires_card_id(self, capsys):
        with pytest.raises(SystemExit):
            cmd_log([])


# ── talaria context ─────────────────────────────────────────────────────────

class TestCmdContext:
    def test_context_prints_full_card(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = SINGLE_CARD
            cmd_context(["aaaa1111"])

        out = capsys.readouterr()
        data = json.loads(out.out)
        assert data["id"] == "aaaa1111"
        assert data["description"] == "The alpha card description."
        assert data["labels"] == ["priority:medium"]

    def test_context_requires_card_id(self, capsys):
        with pytest.raises(SystemExit):
            cmd_context([])


# ── talaria note ─────────────────────────────────────────────────────────────

class TestCmdNote:
    def test_note_sends_note_to_api(self, capsys):
        with patch("talaria.cli._request") as mock_req:
            mock_req.return_value = {
                "id": "n1",
                "text": "Hello note",
                "author": "hermes",
                "ts": "2026-03-24T00:00:00+00:00",
            }
            cmd_note(["aaaa1111", "Hello note"])

        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert call_args[0][0] == "POST"
        assert "aaaa1111" in call_args[0][1]
        assert call_args[1]["body"]["text"] == "Hello note"

    def test_note_requires_card_id_and_text(self, capsys):
        with pytest.raises(SystemExit):
            cmd_note(["aaaa1111"])
        with pytest.raises(SystemExit):
            cmd_note([])


# ── _request error handling ─────────────────────────────────────────────────

class TestRequestErrors:
    def test_http_error_prints_json_and_exits(self, capsys):
        import urllib.error

        body = json.dumps({"error": "Not found"}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = body

        err = urllib.error.HTTPError(
            "http://localhost:8400/api/card/no",
            404, "Not Found",
            {"Content-Type": "application/json"},
            mock_resp,
        )

        with patch("talaria.cli.urllib.request.urlopen", side_effect=err):
            with pytest.raises(SystemExit) as exc_info:
                _request("GET", "/api/card/no")
            assert exc_info.value.code == 1

        err_out = capsys.readouterr().err
        assert "Not found" in err_out

    def test_url_error_exits_with_connect_error(self, capsys):
        import urllib.error

        with patch(
            "talaria.cli.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _request("GET", "/api/board")
            assert exc_info.value.code == 1

        err_out = capsys.readouterr().err
        assert "Cannot connect" in err_out


# ── COMMANDS dict completeness ──────────────────────────────────────────────

class TestCommandsMap:
    def test_all_commands_present(self):
        assert "list" in COMMANDS
        assert "status" in COMMANDS
        assert "create" in COMMANDS
        assert "move" in COMMANDS
        assert "log" in COMMANDS
        assert "context" in COMMANDS
        assert "note" in COMMANDS

    def test_unknown_command_exits(self, capsys):
        with patch.object(sys, "argv", ["talaria", "bad-cmd"]):
            with pytest.raises(SystemExit) as exc_info:
                from talaria.cli import main
                main()
            assert exc_info.value.code == 1
