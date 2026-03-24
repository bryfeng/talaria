#!/usr/bin/env python3
"""
talaria — CLI for interacting with the Talaria kanban board.

Commands:
  talaria list                  List all cards (grouped by column)
  talaria create <title>        Create a new card in backlog
  talaria move <card-id> <col>  Move a card to a column
  talaria log <card-id>         Show activity log / notes for a card
  talaria context <card-id>     Show full card context (for agents)
  talaria note <card-id> <text> Add a status note to a card
"""

import json
import sys
import os
import urllib.request
import urllib.error

PORT = int(os.getenv("TALARIA_PORT", os.getenv("KANBAN_PORT", 8400)))
BASE_URL = f"http://localhost:{PORT}"


def _request(method: str, path: str, body=None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        print(json.dumps({"error": err, "status": e.code}), file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"error": f"Cannot connect to Talaria at {BASE_URL}: {e.reason}"}), file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    board = _request("GET", "/api/board")
    columns = {c["id"]: c["name"] for c in board.get("columns", [])}
    cards = board.get("cards", [])

    # Group by column
    grouped = {}
    for card in cards:
        col = card["column"]
        grouped.setdefault(col, []).append(card)

    output = []
    for col_id, col_name in columns.items():
        col_cards = grouped.get(col_id, [])
        for card in col_cards:
            output.append({
                "id": card["id"],
                "title": card["title"],
                "column": col_id,
                "column_name": col_name,
                "priority": card.get("priority", ""),
                "assignee": card.get("assignee", ""),
                "labels": card.get("labels", []),
            })

    print(json.dumps(output, indent=2))


def cmd_create(args):
    if not args:
        print(json.dumps({"error": "Usage: talaria create <title>"}), file=sys.stderr)
        sys.exit(1)
    title = " ".join(args)
    card = _request("POST", "/api/card", {"title": title})
    print(json.dumps(card, indent=2))


def cmd_move(args):
    if len(args) < 2:
        print(json.dumps({"error": "Usage: talaria move <card-id> <column>"}), file=sys.stderr)
        sys.exit(1)
    card_id, column = args[0], args[1]
    card = _request("PATCH", f"/api/card/{card_id}", {"column": column})
    print(json.dumps(card, indent=2))


def cmd_log(args):
    if not args:
        print(json.dumps({"error": "Usage: talaria log <card-id>"}), file=sys.stderr)
        sys.exit(1)
    card_id = args[0]
    card = _request("GET", f"/api/card/{card_id}")

    # Also fetch activity log filtered to this card
    activity = _request("GET", "/api/activity")
    card_activity = [e for e in activity if e.get("card_id") == card_id]

    output = {
        "id": card["id"],
        "title": card["title"],
        "column": card["column"],
        "notes": card.get("status_notes", []),
        "activity": card_activity,
    }
    print(json.dumps(output, indent=2))


def cmd_context(args):
    if not args:
        print(json.dumps({"error": "Usage: talaria context <card-id>"}), file=sys.stderr)
        sys.exit(1)
    card_id = args[0]
    card = _request("GET", f"/api/card/{card_id}")
    print(json.dumps(card, indent=2))


def cmd_note(args):
    if len(args) < 2:
        print(json.dumps({"error": "Usage: talaria note <card-id> <text>"}), file=sys.stderr)
        sys.exit(1)
    card_id = args[0]
    text = " ".join(args[1:])
    note = _request("POST", f"/api/card/{card_id}/note", {"text": text, "author": "hermes"})
    print(json.dumps(note))


COMMANDS = {
    "list": cmd_list,
    "create": cmd_create,
    "move": cmd_move,
    "log": cmd_log,
    "context": cmd_context,
    "note": cmd_note,
}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    cmd = args[0]
    if cmd not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {cmd}", "commands": list(COMMANDS.keys())}), file=sys.stderr)
        sys.exit(1)

    COMMANDS[cmd](args[1:])


if __name__ == "__main__":
    main()
