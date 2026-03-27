#!/usr/bin/env python3
"""
Talaria Telegram UI (MVP)

Telegram-native control surface for Talaria using inline keyboards.

Commands:
  /board                Show column counts + quick backlog/ready preview
  /next                 Show top card from ready, else backlog
  /card <id>            Show card context + inline move buttons
  /find <query>         Find cards by id/title/description/labels
  /move <id> <column>   Move card by text command (fallback when callbacks fail)
  /create <title>       Create backlog card
  /note <id> <text>     Add note to card
  /history [filters]    Query done/archive history graph
  /help                 Show help

Environment:
  TELEGRAM_BOT_TOKEN                 Required
  TALARIA_BASE_URL                   Default: http://localhost:8400
  TALARIA_TELEGRAM_ALLOWED_CHATS     Optional comma-separated chat IDs allowlist
  TALARIA_TELEGRAM_AGENT_FEEDBACK    off|important|all (default: important)
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TALARIA_BASE_URL = os.getenv("TALARIA_BASE_URL", "http://localhost:8400").rstrip("/")
ALLOWED_CHATS_RAW = os.getenv("TALARIA_TELEGRAM_ALLOWED_CHATS", "").strip()
ALLOWED_CHATS = {x.strip() for x in ALLOWED_CHATS_RAW.split(",") if x.strip()}
FEEDBACK_MODE = (os.getenv("TALARIA_TELEGRAM_AGENT_FEEDBACK", "important") or "important").strip().lower()
if FEEDBACK_MODE not in {"off", "important", "all"}:
    FEEDBACK_MODE = "important"

COLUMNS = ["backlog", "spec", "groom", "ready", "in_progress", "review", "done"]
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Per-user note state: chat_id (str) -> card_id waiting for note text
_note_state: dict[str, str] = {}


def _tg_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def tg_send(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _tg_api("sendMessage", payload)


def tg_edit(chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _tg_api("editMessageText", payload)


def tg_answer_callback(callback_id: str, text: str = "") -> None:
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    _tg_api("answerCallbackQuery", payload)


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int, detail: Any):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API {method} {path} failed ({status_code}): {detail}")


def api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = TALARIA_BASE_URL + path
    if method == "GET":
        r = requests.get(url, timeout=20)
    elif method == "POST":
        r = requests.post(url, json=body or {}, timeout=20)
    elif method == "PATCH":
        r = requests.patch(url, json=body or {}, timeout=20)
    elif method == "DELETE":
        r = requests.delete(url, timeout=20)
    else:
        raise ValueError(f"Unsupported method: {method}")

    if r.status_code >= 400:
        detail: Any
        try:
            detail = r.json()
        except Exception:
            detail = r.text or f"HTTP {r.status_code}"
        raise ApiError(method, path, r.status_code, detail)

    if r.text:
        return r.json()
    return None


def format_board(board: dict[str, Any]) -> str:
    cards = board.get("cards", [])
    counts = {c: 0 for c in COLUMNS}
    for card in cards:
        col = card.get("column", "backlog")
        counts[col] = counts.get(col, 0) + 1

    ready = [c for c in cards if c.get("column") == "ready"]
    review = [c for c in cards if c.get("column") == "review"]
    backlog = [c for c in cards if c.get("column") == "backlog"]

    lines = [
        "Talaria board",
        f"Backlog {counts.get('backlog',0)} | Spec {counts.get('spec',0)} | Groom {counts.get('groom',0)}",
        f"Ready {counts.get('ready',0)} | In Progress {counts.get('in_progress',0)} | Review {counts.get('review',0)} | Done {counts.get('done',0)}",
    ]

    if ready:
        lines.append("\nReady:")
        for c in ready[:3]:
            lines.append(f"- {c['id']} {c['title']}")

    if review:
        lines.append("\nReview:")
        for c in review[:3]:
            lines.append(f"- {c['id']} {c['title']}")

    if backlog:
        lines.append("\nBacklog (top 3):")
        for c in backlog[:3]:
            lines.append(f"- {c['id']} {c['title']}")

    return "\n".join(lines)


def board_keyboard(board: dict[str, Any]) -> dict[str, Any]:
    cards = board.get("cards", [])
    ready = [c for c in cards if c.get("column") == "ready"]
    review = [c for c in cards if c.get("column") == "review"]

    row1 = [{"text": "⏭ Next", "callback_data": "talaria:board:next"}]
    row2 = []
    if ready:
        row2.append({"text": f"🟢 Ready {ready[0]['id']}", "callback_data": f"talaria:open:{ready[0]['id']}"})
    if review:
        row2.append({"text": f"🟣 Review {review[0]['id']}", "callback_data": f"talaria:open:{review[0]['id']}"})

    rows = [row1]
    if row2:
        rows.append(row2)
    rows.append([{"text": "🔄 Refresh", "callback_data": "talaria:board:refresh"}])
    return {"inline_keyboard": rows}


def _matches_query(card: dict[str, Any], q: str) -> bool:
    ql = q.lower()
    hay = " ".join(
        [
            str(card.get("id", "")),
            str(card.get("title", "")),
            str(card.get("description", "")),
            " ".join(card.get("labels", []) or []),
        ]
    ).lower()
    return ql in hay


def find_cards(board: dict[str, Any], query: str, limit: int = 8) -> list[dict[str, Any]]:
    cards = board.get("cards", [])
    q = (query or "").strip()
    if not q:
        return []

    matches = [c for c in cards if _matches_query(c, q)]

    def score(card: dict[str, Any]) -> tuple[int, int, str]:
        ql = q.lower()
        cid = str(card.get("id", "")).lower()
        title = str(card.get("title", "")).lower()
        exact = 0 if ql == cid or ql == title else 1
        starts = 0 if cid.startswith(ql) or title.startswith(ql) else 1
        return (exact, starts, card.get("updated_at", card.get("created_at", "")))

    matches.sort(key=score)
    return matches[:limit]


def find_keyboard(cards: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for c in cards[:8]:
        rows.append([
            {
                "text": f"Open {c['id']}",
                "callback_data": f"talaria:open:{c['id']}",
            }
        ])
    return {"inline_keyboard": rows}


def card_text(card: dict[str, Any]) -> str:
    labels = ", ".join(card.get("labels", [])[:4])
    desc = (card.get("description") or "").strip()
    if len(desc) > 280:
        desc = desc[:280] + "..."
    return (
        f"{card.get('title','Untitled')}\n"
        f"ID: {card.get('id')}\n"
        f"Column: {card.get('column')}\n"
        f"Priority: {card.get('priority','medium')}\n"
        f"Labels: {labels or '-'}\n"
        + (f"\n{desc}" if desc else "")
    )


def move_keyboard(card_id: str) -> dict[str, Any]:
    rows = [
        [
            {"text": "Spec", "callback_data": f"talaria:move:{card_id}:spec"},
            {"text": "Groom", "callback_data": f"talaria:move:{card_id}:groom"},
            {"text": "Ready", "callback_data": f"talaria:move:{card_id}:ready"},
        ],
        [
            {"text": "In Progress", "callback_data": f"talaria:move:{card_id}:in_progress"},
            {"text": "Review", "callback_data": f"talaria:move:{card_id}:review"},
        ],
        [
            {"text": "✅ Done", "callback_data": f"talaria:done:{card_id}:"},
            {"text": "📝 Note", "callback_data": f"talaria:note:{card_id}:"},
            {"text": "🔄 Refresh", "callback_data": f"talaria:open:{card_id}"},
        ],
    ]
    return {"inline_keyboard": rows}


def pick_next_card(board: dict[str, Any]) -> dict[str, Any] | None:
    cards = board.get("cards", [])
    ready = [c for c in cards if c.get("column") == "ready"]
    backlog = [c for c in cards if c.get("column") == "backlog"]
    pool = ready if ready else backlog
    if not pool:
        return None

    def sort_key(card: dict[str, Any]):
        p = card.get("priority", "medium")
        return (PRIORITY_RANK.get(p, 99), card.get("created_at", ""))

    pool.sort(key=sort_key)
    return pool[0]


def parse_command(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    if not text:
        return "", ""
    if " " in text:
        cmd, args = text.split(" ", 1)
    else:
        cmd, args = text, ""
    return cmd.lower(), args.strip()


def _chat_allowed(chat_id: str) -> bool:
    if not ALLOWED_CHATS:
        return True
    return str(chat_id) in ALLOWED_CHATS


def _feedback_enabled(event: str) -> bool:
    if FEEDBACK_MODE == "off":
        return False
    if FEEDBACK_MODE == "all":
        return True
    return event in {"done", "error"}


def _send_feedback(chat_id: str, event: str, card: dict[str, Any] | None = None, error: Exception | None = None) -> None:
    if not _feedback_enabled(event):
        return

    try:
        if event == "done" and card:
            board = api("GET", "/api/board")
            done_count = sum(1 for c in board.get("cards", []) if c.get("column") == "done")
            tg_send(
                chat_id,
                f"Moved {card.get('id')} to Done. Done now: {done_count}/20. Next: /next",
            )
            return

        if event == "move" and card:
            tg_send(chat_id, f"Moved {card.get('id')} to {card.get('column')}.")
            return

        if event == "open" and card:
            tg_send(chat_id, f"Refreshed {card.get('id')} ({card.get('column')}).")
            return

        if event == "error":
            detail = str(error) if error else "Unknown error"
            tg_send(chat_id, f"Talaria action failed: {detail}")
            return
    except Exception:
        # Never let UX feedback break callback actions
        return


def handle_message(msg: dict[str, Any]) -> None:
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if not chat_id or not _chat_allowed(chat_id):
        return

    text = msg.get("text", "")

    # Conversational note flow: waiting for note text after pressing 📝 Note
    if chat_id in _note_state and not text.startswith("/"):
        card_id = _note_state.pop(chat_id)
        try:
            api("POST", f"/api/card/{card_id}/note", {"text": text, "author": "telegram"})
            tg_send(chat_id, f"Note added to {card_id}.")
        except Exception as e:
            tg_send(chat_id, f"Failed to add note: {e}")
        return

    cmd, args = parse_command(text)

    if cmd in ("/help", "/start"):
        tg_send(
            chat_id,
            "Talaria Telegram UI\n"
            "Commands:\n"
            "/board\n"
            "/next\n"
            "/card <id>\n"
            "/find <query>\n"
            "/move <id> <column>\n"
            "/create <title>\n"
            "/note <id> <text>\n"
            "/history [q] [domain=..] [component=..] [type=..] [release=..]",
        )
        return

    if cmd == "/board":
        board = api("GET", "/api/board")
        tg_send(chat_id, format_board(board), reply_markup=board_keyboard(board))
        return

    if cmd == "/next":
        board = api("GET", "/api/board")
        card = pick_next_card(board)
        if not card:
            tg_send(chat_id, "No cards in Ready or Backlog.")
            return
        full = api("GET", f"/api/card/{card['id']}")
        tg_send(chat_id, card_text(full), reply_markup=move_keyboard(full["id"]))
        return

    if cmd == "/card":
        if not args:
            tg_send(chat_id, "Usage: /card <id>")
            return
        try:
            full = api("GET", f"/api/card/{args}")
        except Exception as e:
            tg_send(chat_id, f"Card not found: {args} ({e})")
            return
        tg_send(chat_id, card_text(full), reply_markup=move_keyboard(full["id"]))
        return

    if cmd == "/find":
        if not args:
            tg_send(chat_id, "Usage: /find <query>")
            return
        board = api("GET", "/api/board")
        matches = find_cards(board, args, limit=8)
        if not matches:
            tg_send(chat_id, f"No cards match: {args}")
            return

        lines = [f"Matches for '{args}':"]
        for c in matches:
            lines.append(f"- {c.get('id')} [{c.get('column')}] {c.get('title')}")
        tg_send(chat_id, "\n".join(lines), reply_markup=find_keyboard(matches))
        return

    if cmd == "/move":
        parts = args.split()
        if len(parts) != 2:
            tg_send(chat_id, "Usage: /move <id> <column>")
            return
        card_id, column = parts[0], parts[1].strip().lower()
        if column not in COLUMNS:
            tg_send(chat_id, f"Invalid column '{column}'. Valid: {', '.join(COLUMNS)}")
            return
        card = api("PATCH", f"/api/card/{card_id}", {"column": column})
        tg_send(chat_id, card_text(card), reply_markup=move_keyboard(card_id))
        return

    if cmd == "/history":
        params = []
        free = []
        for token in args.split():
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in {"domain", "component", "type", "release", "limit"} and v:
                    params.append((k, v))
            else:
                free.append(token)

        if free:
            params.append(("q", " ".join(free)))

        query = ""
        if params:
            from urllib.parse import urlencode

            query = "?" + urlencode(params)

        rows = api("GET", f"/api/history{query}")
        if not rows:
            tg_send(chat_id, "No history matches.")
            return

        lines = ["History (top matches):"]
        for row in rows[:8]:
            dom = ",".join(row.get("domains", [])[:2])
            comp = ",".join(row.get("components", [])[:2])
            lines.append(
                f"- {row.get('card_id')} {row.get('title')} [{row.get('type')}] d={dom} c={comp}"
            )
        tg_send(chat_id, "\n".join(lines))
        return

    if cmd == "/create":
        if not args:
            tg_send(chat_id, "Usage: /create <title>")
            return
        card = api("POST", "/api/card", {"title": args, "column": "backlog"})
        tg_send(chat_id, f"Created {card['id']} in backlog:\n{card['title']}")
        return

    if cmd == "/note":
        parts = args.split(" ", 1)
        if len(parts) < 2:
            tg_send(chat_id, "Usage: /note <id> <text>")
            return
        card_id, note_text = parts[0], parts[1]
        api("POST", f"/api/card/{card_id}/note", {"text": note_text, "author": "telegram"})
        tg_send(chat_id, f"Added note to {card_id}.")
        return


def _gate_failure_text(err: ApiError | Exception) -> str:
    if isinstance(err, ApiError) and isinstance(err.detail, dict):
        missing = err.detail.get("missing")
        if isinstance(missing, list) and missing:
            return "Done blocked. Missing requirements: " + ", ".join(str(x) for x in missing)
        for key in ("error", "message", "detail"):
            val = err.detail.get(key)
            if isinstance(val, str) and val.strip():
                return f"Done blocked: {val.strip()}"
    return f"Done blocked: {err}"


def handle_callback(cb: dict[str, Any]) -> None:
    cb_id = cb.get("id")
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    message_id = msg.get("message_id")

    if not chat_id or not _chat_allowed(chat_id):
        return

    try:
        parts = data.split(":")
        if len(parts) < 2 or parts[0] != "talaria":
            tg_answer_callback(cb_id, "Unknown action")
            return

        action = parts[1]

        if action == "board":
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "next":
                board = api("GET", "/api/board")
                card = pick_next_card(board)
                if not card:
                    tg_answer_callback(cb_id, "No cards in Ready/Backlog")
                    return
                full = api("GET", f"/api/card/{card['id']}")
                tg_answer_callback(cb_id, "Opened next")
                tg_edit(chat_id, message_id, card_text(full), reply_markup=move_keyboard(full["id"]))
                return

            if sub == "refresh":
                board = api("GET", "/api/board")
                tg_answer_callback(cb_id, "Refreshed")
                tg_edit(chat_id, message_id, format_board(board), reply_markup=board_keyboard(board))
                return

            tg_answer_callback(cb_id, "Unsupported board action")
            return

        if len(parts) < 3:
            tg_answer_callback(cb_id, "Unknown action")
            return

        card_id = parts[2]

        if action == "move" and len(parts) >= 4:
            column = parts[3]
            card = api("PATCH", f"/api/card/{card_id}", {"column": column})
            tg_answer_callback(cb_id, f"Moved to {column}")
            tg_edit(chat_id, message_id, card_text(card), reply_markup=move_keyboard(card_id))
            _send_feedback(chat_id, "move", card=card)
            return

        if action == "done":
            try:
                card = api("PATCH", f"/api/card/{card_id}", {"column": "done"})
            except Exception as e:
                msg_text = _gate_failure_text(e)
                tg_answer_callback(cb_id, "Done blocked")
                tg_send(chat_id, msg_text)
                _send_feedback(chat_id, "error", error=e)
                return
            tg_answer_callback(cb_id, "Done! ✅")
            tg_edit(chat_id, message_id, card_text(card), reply_markup=move_keyboard(card_id))
            _send_feedback(chat_id, "done", card=card)
            return

        if action == "note":
            _note_state[chat_id] = card_id
            tg_answer_callback(cb_id, "Send your note as the next message")
            tg_send(chat_id, f"Type your note for card {card_id}:")
            return

        if action == "open":
            card = api("GET", f"/api/card/{card_id}")
            tg_answer_callback(cb_id, "Refreshed")
            tg_edit(chat_id, message_id, card_text(card), reply_markup=move_keyboard(card_id))
            _send_feedback(chat_id, "open", card=card)
            return

        tg_answer_callback(cb_id, "Unsupported action")
    except Exception as e:
        try:
            tg_answer_callback(cb_id, f"Error: {e}")
        except Exception:
            pass
        _send_feedback(chat_id, "error", error=e)


def run_loop() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    print("[talaria-telegram] starting poll loop")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )
            r.raise_for_status()
            data = r.json()
            updates = data.get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    handle_message(upd["message"])
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"])
        except KeyboardInterrupt:
            print("\n[talaria-telegram] stopped")
            return
        except Exception as e:
            print(f"[talaria-telegram] loop error: {e}")
            time.sleep(2)


def main() -> None:
    run_loop()


if __name__ == "__main__":
    main()
