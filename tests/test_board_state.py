"""
test_board_state.py — Tests for board and card file operations (talaria.board module).

Tests the low-level I/O operations:
  - Board JSON parsing and validation
  - Card markdown file parsing (YAML frontmatter + body)
  - Card file naming conventions (cards/<id>.md)
  - Card field validation
  - board.json column structure validation
"""

import json

import pytest


# ── Module under test ─────────────────────────────────────────────────────────

from talaria.board import (
    _load_board,
    _save_board,
    _load_card,
    _save_card,
    _card_path,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def board_with_cards(tmp_path):
    """
    Creates a minimal Talaria instance on disk:
        tmp/board.json
        tmp/cards/
            testcard1.md
            testcard2.md
        tmp/logs/
    """
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    board = {
        "_schema": "Talaria board config",
        "meta": {"name": "TestBoard", "version": "1.0"},
        "columns": [
            {"id": "backlog", "name": "Backlog", "trigger": None},
            {"id": "in_progress", "name": "In Progress", "trigger": "agent_spawn", "worker": "claude-code"},
            {"id": "done", "name": "Done", "trigger": "notify"},
        ],
    }
    board_file = tmp_path / "board.json"
    board_file.write_text(json.dumps(board))

    # Create two card files
    card1 = cards_dir / "cardaaa1.md"
    card1.write_text(
        "---\n"
        "id: cardaaa1\n"
        "title: Test Card One\n"
        "column: backlog\n"
        "priority: high\n"
        "labels:\n"
        "  - priority:high\n"
        "  - frontend\n"
        "created_at: '2026-03-24T00:00:00Z'\n"
        "updated_at: '2026-03-24T00:00:00Z'\n"
        "---\n"
        "\n"
        "This is the body of card one.\n"
    )

    card2 = cards_dir / "cardbbb2.md"
    card2.write_text(
        "---\n"
        "id: cardbbb2\n"
        "title: Test Card Two\n"
        "column: in_progress\n"
        "priority: medium\n"
        "created_at: '2026-03-24T00:00:00Z'\n"
        "updated_at: '2026-03-24T00:00:00Z'\n"
        "---\n"
        "\n"
        "Body of card two.\n"
    )

    return {
        "root": tmp_path,
        "board_file": board_file,
        "cards_dir": cards_dir,
        "card1_file": card1,
        "card2_file": card2,
    }


# ── _card_path tests ─────────────────────────────────────────────────────────

class TestCardPath:
    def test_card_path_returns_correct_path(self, tmp_path):
        assert _card_path(tmp_path, "abc123") == tmp_path / "cards" / "abc123.md"

    def test_card_path_enforces_md_extension(self, tmp_path):
        path = _card_path(tmp_path, "mycard")
        assert str(path).endswith(".md")


# ── _load_board tests ─────────────────────────────────────────────────────────

class TestLoadBoard:
    def test_load_board_returns_dict(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        assert isinstance(board, dict)

    def test_board_has_columns(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        assert "columns" in board
        assert isinstance(board["columns"], list)

    def test_board_has_meta(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        assert "meta" in board
        assert board["meta"]["name"] == "TestBoard"

    def test_columns_have_id_and_name(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        for col in board["columns"]:
            assert "id" in col
            assert "name" in col

    def test_board_with_trigger_column(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        ip_col = next((c for c in board["columns"] if c["id"] == "in_progress"), None)
        assert ip_col is not None
        assert ip_col.get("trigger") == "agent_spawn"
        assert ip_col.get("worker") == "claude-code"


# ── _save_board tests ─────────────────────────────────────────────────────────

class TestSaveBoard:
    def test_save_board_writes_json(self, tmp_path):
        board_file = tmp_path / "board.json"
        board = {
            "meta": {"name": "SavedBoard"},
            "columns": [{"id": "backlog", "name": "Backlog"}],
        }
        _save_board(board_file, board)
        assert board_file.exists()

        loaded = json.loads(board_file.read_text())
        assert loaded["meta"]["name"] == "SavedBoard"

    def test_save_board_overwrites_existing(self, tmp_path):
        board_file = tmp_path / "board.json"
        board_file.write_text('{"old": "data"}')
        _save_board(board_file, {"meta": {}, "columns": []})
        loaded = json.loads(board_file.read_text())
        assert "old" not in loaded


# ── _load_card tests ─────────────────────────────────────────────────────────

class TestLoadCard:
    def test_load_card_parses_yaml_frontmatter(self, board_with_cards):
        card = _load_card(board_with_cards["cards_dir"], "cardaaa1")
        assert card["id"] == "cardaaa1"
        assert card["title"] == "Test Card One"
        assert card["column"] == "backlog"

    def test_load_card_parses_labels(self, board_with_cards):
        card = _load_card(board_with_cards["cards_dir"], "cardaaa1")
        assert "priority:high" in card.get("labels", [])
        assert "frontend" in card.get("labels", [])

    def test_load_card_includes_body(self, board_with_cards):
        card = _load_card(board_with_cards["cards_dir"], "cardaaa1")
        assert "body" in card or "description" in card
        body = card.get("body", card.get("description", ""))
        assert "body of card one" in body.lower()

    def test_load_card_missing_returns_none(self, board_with_cards):
        card = _load_card(board_with_cards["cards_dir"], "nonexistent")
        assert card is None

    def test_load_card_prioritizes_high(self, board_with_cards):
        card = _load_card(board_with_cards["cards_dir"], "cardaaa1")
        assert card["priority"] == "high"


# ── _save_card tests ─────────────────────────────────────────────────────────

class TestSaveCard:
    def test_save_card_writes_md_file(self, tmp_path):
        cards_dir = tmp_path / "cards"
        cards_dir.mkdir()

        card_data = {
            "id": "newcard1",
            "title": "New Card",
            "column": "backlog",
            "priority": "medium",
        }
        path = _save_card(cards_dir, card_data)
        assert path.exists()
        text = path.read_text()
        assert "id: newcard1" in text
        assert "title: New Card" in text

    def test_save_card_roundtrips(self, tmp_path):
        cards_dir = tmp_path / "cards"
        cards_dir.mkdir()

        card_data = {
            "id": "roundtrip1",
            "title": "Roundtrip Test",
            "column": "spec",
            "priority": "critical",
            "labels": ["bug", "urgent"],
        }
        _save_card(cards_dir, card_data)
        loaded = _load_card(cards_dir, "roundtrip1")

        assert loaded["id"] == "roundtrip1"
        assert loaded["title"] == "Roundtrip Test"
        assert loaded["column"] == "spec"
        assert loaded["priority"] == "critical"
        assert "bug" in loaded.get("labels", [])
        assert "urgent" in loaded.get("labels", [])

    def test_save_card_updates_existing(self, tmp_path):
        cards_dir = tmp_path / "cards"
        cards_dir.mkdir()
        card_path = cards_dir / "update1.md"
        card_path.write_text(
            "---\n"
            "id: update1\n"
            "title: Old Title\n"
            "column: backlog\n"
            "priority: low\n"
            "---\n"
        )

        updated = {"id": "update1", "title": "New Title", "column": "in_progress", "priority": "high"}
        _save_card(cards_dir, updated)

        loaded = _load_card(cards_dir, "update1")
        assert loaded["title"] == "New Title"
        assert loaded["column"] == "in_progress"
        assert loaded["priority"] == "high"


# ── Card file naming ──────────────────────────────────────────────────────────

class TestCardFileNaming:
    def test_cards_have_md_extension(self, board_with_cards):
        for card_file in board_with_cards["cards_dir"].iterdir():
            assert card_file.suffix == ".md"
            assert card_file.name.endswith(".md")

    def test_card_filename_matches_id(self, board_with_cards):
        # The card file name should correspond to its id field
        card1_content = board_with_cards["card1_file"].read_text()
        assert "id: cardaaa1" in card1_content

    def test_card_file_parse_tolerates_missing_optional_fields(self, tmp_path):
        cards_dir = tmp_path / "cards"
        cards_dir.mkdir()
        minimal = cards_dir / "minimal1.md"
        minimal.write_text(
            "---\n"
            "id: minimal1\n"
            "title: Minimal Card\n"
            "column: backlog\n"
            "---\n"
        )
        card = _load_card(cards_dir, "minimal1")
        assert card["id"] == "minimal1"
        assert card.get("priority") is None  # optional field


# ── board.json column structure ───────────────────────────────────────────────

class TestBoardColumnStructure:
    def test_board_has_expected_columns(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        col_ids = {c["id"] for c in board["columns"]}
        assert "backlog" in col_ids
        assert "in_progress" in col_ids
        assert "done" in col_ids

    def test_trigger_fields_are_optional(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        for col in board["columns"]:
            # trigger may be None, a string, or absent
            if "trigger" in col:
                assert isinstance(col["trigger"], (str, type(None)))

    def test_agent_spawn_trigger_has_worker(self, board_with_cards):
        board = _load_board(board_with_cards["board_file"])
        ip_col = next((c for c in board["columns"] if c.get("trigger") == "agent_spawn"), None)
        assert ip_col is not None
        assert "worker" in ip_col
