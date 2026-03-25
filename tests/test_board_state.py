"""Tests for talaria.board file I/O using current clean interfaces."""

import json
from pathlib import Path

import pytest

import talaria.board as board


@pytest.fixture
def board_fs(tmp_path, monkeypatch):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    board_file = tmp_path / "board.json"
    log_file = tmp_path / "logs" / "talaria.log"
    log_file.parent.mkdir()

    board_file.write_text(
        json.dumps(
            {
                "_schema": "Talaria board config",
                "meta": {"name": "TestBoard", "version": "1.0"},
                "columns": [
                    {"id": "backlog", "name": "Backlog", "trigger": None},
                    {"id": "in_progress", "name": "In Progress", "trigger": "agent_spawn", "worker": "claude-code"},
                    {"id": "done", "name": "Done", "trigger": "notify"},
                ],
            }
        )
    )

    monkeypatch.setattr(board, "BASE_DIR", tmp_path)
    monkeypatch.setattr(board, "CARDS_DIR", cards_dir)
    monkeypatch.setattr(board, "BOARD_FILE", board_file)
    monkeypatch.setattr(board, "LOG_FILE", log_file)

    return {"root": tmp_path, "cards": cards_dir, "board": board_file}


class TestBoardIO:
    def test_load_save_board(self, board_fs):
        data = board._load_board()
        assert data["meta"]["name"] == "TestBoard"
        data["meta"]["name"] = "Renamed"
        board._save_board(data)
        reloaded = board._load_board()
        assert reloaded["meta"]["name"] == "Renamed"


class TestCardIO:
    def test_card_path(self, board_fs):
        path = board._card_path("abc123")
        assert path == board_fs["cards"] / "abc123.md"

    def test_save_and_load_card(self, board_fs):
        card = {
            "id": "card1",
            "title": "Card One",
            "column": "backlog",
            "priority": "high",
            "labels": ["priority:high", "frontend"],
            "description": "Body text.",
        }
        board._save_card(card)
        loaded = board._load_card("card1")
        assert loaded["id"] == "card1"
        assert loaded["title"] == "Card One"
        assert loaded["priority"] == "high"
        assert "frontend" in loaded.get("labels", [])
        assert "Body text." in loaded.get("description", "")

    def test_load_missing_card_returns_none(self, board_fs):
        assert board._load_card("missing") is None

    def test_all_cards_reads_md_files(self, board_fs):
        board._save_card({"id": "a1", "title": "A", "column": "backlog"})
        board._save_card({"id": "b2", "title": "B", "column": "done"})
        cards = board._all_cards()
        ids = {c["id"] for c in cards}
        assert ids == {"a1", "b2"}


class TestFullBoard:
    def test_full_board_contains_columns_and_cards(self, board_fs):
        board._save_card({"id": "x1", "title": "X", "column": "in_progress"})
        full = board._full_board()
        assert "columns" in full
        assert "cards" in full
        assert any(c["id"] == "x1" for c in full["cards"])
