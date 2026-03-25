"""
test_telegram_ui.py — Unit tests for Talaria Telegram UI.

Tests the pure logic functions that don't require network access:
  move_keyboard()      — inline keyboard structure
  format_board()       — board summary text
  card_text()          — card detail text
  pick_next_card()     — selects highest-priority Ready/Backlog card
  parse_command()      — splits /cmd args
  handle_message()     — command routing + note flow state
  handle_callback()    — inline button dispatch
"""

from unittest.mock import MagicMock, call, patch

import pytest

import talaria.telegram_ui as ui


# ── move_keyboard ─────────────────────────────────────────────────────────────

class TestMoveKeyboard:
    def test_returns_inline_keyboard(self):
        kb = ui.move_keyboard("abc123")
        assert "inline_keyboard" in kb

    def test_move_buttons_use_callback_schema(self):
        kb = ui.move_keyboard("abc123")
        rows = kb["inline_keyboard"]
        all_btns = [btn for row in rows for btn in row]
        move_btns = [b for b in all_btns if b["callback_data"].startswith("talaria:move:")]
        assert len(move_btns) >= 5  # spec/groom/ready/in_progress/review

    def test_done_button_present(self):
        kb = ui.move_keyboard("abc123")
        all_btns = [btn for row in kb["inline_keyboard"] for btn in row]
        done_btns = [b for b in all_btns if b["callback_data"] == "talaria:done:abc123:"]
        assert len(done_btns) == 1

    def test_note_button_present(self):
        kb = ui.move_keyboard("abc123")
        all_btns = [btn for row in kb["inline_keyboard"] for btn in row]
        note_btns = [b for b in all_btns if b["callback_data"] == "talaria:note:abc123:"]
        assert len(note_btns) == 1

    def test_callback_data_within_64_bytes(self):
        """Telegram enforces a 64-byte limit on callback_data."""
        kb = ui.move_keyboard("abc12345")
        for row in kb["inline_keyboard"]:
            for btn in row:
                assert len(btn["callback_data"].encode()) <= 64


# ── format_board ──────────────────────────────────────────────────────────────

class TestFormatBoard:
    def _board(self, cards):
        return {"cards": cards, "columns": []}

    def test_shows_column_counts(self):
        board = self._board([
            {"id": "a1", "title": "Card A", "column": "ready"},
            {"id": "a2", "title": "Card B", "column": "backlog"},
            {"id": "a3", "title": "Card C", "column": "backlog"},
        ])
        text = ui.format_board(board)
        assert "Ready 1" in text
        assert "Backlog 2" in text

    def test_shows_ready_preview(self):
        board = self._board([{"id": "r1", "title": "Ready Card", "column": "ready"}])
        text = ui.format_board(board)
        assert "Ready Card" in text

    def test_shows_backlog_preview(self):
        board = self._board([{"id": "b1", "title": "Backlog Card", "column": "backlog"}])
        text = ui.format_board(board)
        assert "Backlog Card" in text

    def test_empty_board(self):
        text = ui.format_board({"cards": [], "columns": []})
        assert "0" in text


# ── pick_next_card ────────────────────────────────────────────────────────────

class TestPickNextCard:
    def test_prefers_ready_over_backlog(self):
        board = {
            "cards": [
                {"id": "b1", "column": "backlog", "priority": "high", "created_at": "2026-01-01"},
                {"id": "r1", "column": "ready", "priority": "low", "created_at": "2026-01-02"},
            ]
        }
        card = ui.pick_next_card(board)
        assert card["id"] == "r1"

    def test_uses_backlog_when_no_ready(self):
        board = {
            "cards": [
                {"id": "b1", "column": "backlog", "priority": "medium", "created_at": "2026-01-01"},
            ]
        }
        assert ui.pick_next_card(board)["id"] == "b1"

    def test_returns_none_when_empty(self):
        assert ui.pick_next_card({"cards": []}) is None

    def test_sorts_by_priority(self):
        board = {
            "cards": [
                {"id": "low", "column": "ready", "priority": "low", "created_at": "2026-01-01"},
                {"id": "hi", "column": "ready", "priority": "high", "created_at": "2026-01-02"},
            ]
        }
        assert ui.pick_next_card(board)["id"] == "hi"


# ── parse_command ─────────────────────────────────────────────────────────────

class TestParseCommand:
    def test_splits_cmd_and_args(self):
        cmd, args = ui.parse_command("/card abc123")
        assert cmd == "/card"
        assert args == "abc123"

    def test_no_args(self):
        cmd, args = ui.parse_command("/board")
        assert cmd == "/board"
        assert args == ""

    def test_empty_text(self):
        cmd, args = ui.parse_command("")
        assert cmd == ""
        assert args == ""

    def test_multi_word_args(self):
        cmd, args = ui.parse_command("/create My feature card")
        assert cmd == "/create"
        assert args == "My feature card"


# ── handle_message ────────────────────────────────────────────────────────────

class TestHandleMessage:
    def _msg(self, text, chat_id="42"):
        return {"chat": {"id": int(chat_id)}, "text": text}

    def test_board_command(self):
        with patch.object(ui, "api", return_value={"cards": [], "columns": []}) as mock_api, \
             patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("/board"))
            mock_api.assert_called_once_with("GET", "/api/board")
            mock_send.assert_called_once()

    def test_next_no_cards(self):
        with patch.object(ui, "api", return_value={"cards": [], "columns": []}) as mock_api, \
             patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("/next"))
            mock_send.assert_called_once()
            assert "No cards" in mock_send.call_args[0][1]

    def test_card_command(self):
        fake_card = {"id": "ab12", "title": "T", "column": "backlog", "priority": "medium", "labels": [], "description": ""}
        with patch.object(ui, "api", return_value=fake_card), \
             patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("/card ab12"))
            mock_send.assert_called_once()

    def test_card_missing_id(self):
        with patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("/card"))
            assert "Usage" in mock_send.call_args[0][1]

    def test_create_command(self):
        fake_card = {"id": "xy99", "title": "New thing", "column": "backlog"}
        with patch.object(ui, "api", return_value=fake_card) as mock_api, \
             patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("/create New thing"))
            mock_api.assert_called_with("POST", "/api/card", {"title": "New thing", "column": "backlog"})
            assert "xy99" in mock_send.call_args[0][1]

    def test_note_conversational_flow(self):
        """After pressing 📝 Note, next non-command message posts the note."""
        chat_id = "77"
        card_id = "cd56"
        # Seed state as if user pressed Note button
        ui._note_state[chat_id] = card_id

        with patch.object(ui, "api") as mock_api, \
             patch.object(ui, "tg_send") as mock_send:
            ui.handle_message(self._msg("Great progress!", chat_id=chat_id))
            mock_api.assert_called_once_with(
                "POST", f"/api/card/{card_id}/note",
                {"text": "Great progress!", "author": "telegram"},
            )
            assert chat_id not in ui._note_state  # state cleared

    def test_note_state_not_consumed_by_commands(self):
        """A slash command while in note-waiting state is routed normally."""
        chat_id = "88"
        ui._note_state[chat_id] = "anycard"
        with patch.object(ui, "api", return_value={"cards": [], "columns": []}) as mock_api, \
             patch.object(ui, "tg_send"):
            ui.handle_message(self._msg("/board", chat_id=chat_id))
        # note state should still be there (command didn't consume it)
        assert chat_id in ui._note_state
        ui._note_state.pop(chat_id, None)  # cleanup


# ── handle_callback ───────────────────────────────────────────────────────────

class TestHandleCallback:
    def _cb(self, data, chat_id="42", msg_id=1):
        return {
            "id": "cbid",
            "data": data,
            "message": {"chat": {"id": int(chat_id)}, "message_id": msg_id},
        }

    def test_move_action(self):
        fake_card = {"id": "aa11", "title": "T", "column": "ready", "priority": "medium", "labels": [], "description": ""}
        with patch.object(ui, "api", return_value=fake_card) as mock_api, \
             patch.object(ui, "tg_answer_callback") as mock_ans, \
             patch.object(ui, "tg_edit"):
            ui.handle_callback(self._cb("talaria:move:aa11:ready"))
            mock_api.assert_called_once_with("PATCH", "/api/card/aa11", {"column": "ready"})
            mock_ans.assert_called_once()
            assert "ready" in mock_ans.call_args[0][1]

    def test_done_action(self):
        fake_card = {"id": "bb22", "title": "T", "column": "done", "priority": "medium", "labels": [], "description": ""}
        with patch.object(ui, "api", return_value=fake_card) as mock_api, \
             patch.object(ui, "tg_answer_callback") as mock_ans, \
             patch.object(ui, "tg_edit"):
            ui.handle_callback(self._cb("talaria:done:bb22:"))
            mock_api.assert_called_once_with("PATCH", "/api/card/bb22", {"column": "done"})
            mock_ans.assert_called_once()

    def test_note_action_sets_state(self):
        chat_id = "99"
        with patch.object(ui, "tg_answer_callback"), \
             patch.object(ui, "tg_send"):
            ui.handle_callback(self._cb("talaria:note:cc33:", chat_id=chat_id))
        assert ui._note_state.get(chat_id) == "cc33"
        ui._note_state.pop(chat_id, None)

    def test_open_action_refreshes(self):
        fake_card = {"id": "dd44", "title": "T", "column": "spec", "priority": "medium", "labels": [], "description": ""}
        with patch.object(ui, "api", return_value=fake_card) as mock_api, \
             patch.object(ui, "tg_answer_callback"), \
             patch.object(ui, "tg_edit") as mock_edit:
            ui.handle_callback(self._cb("talaria:open:dd44"))
            mock_api.assert_called_once_with("GET", "/api/card/dd44")
            mock_edit.assert_called_once()

    def test_unknown_prefix_answered(self):
        with patch.object(ui, "tg_answer_callback") as mock_ans:
            ui.handle_callback(self._cb("other:action:id"))
            mock_ans.assert_called_once()

    def test_api_error_answered(self):
        with patch.object(ui, "api", side_effect=RuntimeError("boom")), \
             patch.object(ui, "tg_answer_callback") as mock_ans:
            ui.handle_callback(self._cb("talaria:move:ee55:spec"))
            mock_ans.assert_called_once()
