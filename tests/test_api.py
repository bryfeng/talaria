"""
test_api.py — API endpoint tests using Flask's test client.

Tests the server.py Flask app endpoints:
  GET  /api/board
  POST /api/card
  GET  /api/card/<id>
  PATCH /api/card/<id>
  POST /api/card/<id>/note
  DELETE /api/card/<id>
  GET  /api/activity
  PATCH /api/column/<id>
"""

import json


# ── /api/board ────────────────────────────────────────────────────────────────

class TestGetBoard:
    def test_returns_200(self, app_client):
        rv = app_client.get("/api/board")
        assert rv.status_code == 200

    def test_response_shape(self, app_client):
        rv = app_client.get("/api/board")
        data = rv.get_json()
        assert "columns" in data
        assert "meta" in data
        assert isinstance(data["columns"], list)

    def test_columns_have_id_and_name(self, app_client):
        rv = app_client.get("/api/board")
        data = rv.get_json()
        for col in data["columns"]:
            assert "id" in col
            assert "name" in col

    def test_empty_board_has_no_cards(self, app_client):
        rv = app_client.get("/api/board")
        data = rv.get_json()
        assert data.get("cards") == []


# ── /api/card (POST) ─────────────────────────────────────────────────────────

class TestCreateCard:
    def test_creates_card_with_id_title_column(self, app_client):
        rv = app_client.post(
            "/api/card",
            json={"title": "My test card", "column": "backlog"},
        )
        assert rv.status_code == 201
        card = rv.get_json()
        assert "id" in card
        assert card["title"] == "My test card"
        assert card["column"] == "backlog"

    def test_default_column_is_backlog(self, app_client):
        rv = app_client.post("/api/card", json={"title": "No column card"})
        card = rv.get_json()
        assert card["column"] == "backlog"

    def test_default_priority_is_medium(self, app_client):
        rv = app_client.post("/api/card", json={"title": "Priority card"})
        card = rv.get_json()
        assert card["priority"] == "medium"

    def test_labels_include_priority_label(self, app_client):
        rv = app_client.post(
            "/api/card",
            json={"title": "Labelled card", "priority": "high"},
        )
        card = rv.get_json()
        assert "priority:high" in card["labels"]

    def test_description_is_preserved(self, app_client):
        rv = app_client.post(
            "/api/card",
            json={"title": "Desc card", "description": "A description."},
        )
        card = rv.get_json()
        assert card["description"] == "A description."

    def test_created_at_is_set(self, app_client):
        rv = app_client.post("/api/card", json={"title": "Timed card"})
        card = rv.get_json()
        assert "created_at" in card

    def test_card_saved_to_disk(self, app_client, tmp_talaria_dir):
        rv = app_client.post("/api/card", json={"title": "Disk card"})
        card = rv.get_json()
        card_file = tmp_talaria_dir["cards_dir"] / f"{card['id']}.md"
        assert card_file.exists()


# ── /api/card/<id> (GET) ─────────────────────────────────────────────────────

class TestGetCard:
    def test_get_existing_card(self, app_client):
        create = app_client.post("/api/card", json={"title": "Fetch me"})
        card_id = create.get_json()["id"]
        rv = app_client.get(f"/api/card/{card_id}")
        assert rv.status_code == 200
        assert rv.get_json()["title"] == "Fetch me"

    def test_get_nonexistent_returns_404(self, app_client):
        rv = app_client.get("/api/card/does-not-exist")
        assert rv.status_code == 404


# ── /api/card/<id> (PATCH) ────────────────────────────────────────────────────

class TestUpdateCard:
    def test_update_title(self, app_client):
        create = app_client.post("/api/card", json={"title": "Old title"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"title": "New title"})
        assert rv.get_json()["title"] == "New title"

    def test_update_priority(self, app_client):
        create = app_client.post("/api/card", json={"title": "Pri card"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"priority": "critical"})
        assert rv.get_json()["priority"] == "critical"

    def test_update_assignee(self, app_client):
        create = app_client.post("/api/card", json={"title": "Assign card"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"assignee": "alice"})
        assert rv.get_json()["assignee"] == "alice"

    def test_update_labels(self, app_client):
        create = app_client.post("/api/card", json={"title": "Label card"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(
            f"/api/card/{card_id}", json={"labels": ["bug", "urgent"]}
        )
        assert rv.get_json()["labels"] == ["bug", "urgent"]

    def test_update_tests_field(self, app_client):
        create = app_client.post("/api/card", json={"title": "Test card"})
        card_id = create.get_json()["id"]
        tests_cfg = {"command": "pytest", "pass_if": "exit_0"}
        rv = app_client.patch(f"/api/card/{card_id}", json={"tests": tests_cfg})
        assert rv.get_json()["tests"] == tests_cfg

    def test_move_to_column(self, app_client):
        create = app_client.post("/api/card", json={"title": "Moving card"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "spec"})
        assert rv.get_json()["column"] == "spec"

    def test_move_nonexistent_returns_404(self, app_client):
        rv = app_client.patch("/api/card/nope", json={"column": "backlog"})
        assert rv.status_code == 404

    def test_blocks_in_progress_to_review_without_runner_success(self, app_client, tmp_talaria_dir):
        board_path = tmp_talaria_dir["board_file"]
        board = json.loads(board_path.read_text())
        for col in board["columns"]:
            if col["id"] == "in_progress":
                col["auto_transition"] = {
                    "to": "review",
                    "when": "on_agent_success",
                    "require": ["rule:agent_work_done"],
                }
        board_path.write_text(json.dumps(board, indent=2))

        create = app_client.post("/api/card", json={"title": "Guarded move"})
        card_id = create.get_json()["id"]
        app_client.patch(f"/api/card/{card_id}", json={"column": "in_progress"})

        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "review"})
        assert rv.status_code == 409
        data = rv.get_json()
        assert data["from"] == "in_progress"
        assert data["to"] == "review"
        assert "agent_success" in data.get("missing_requirements", [])

    def test_allows_in_progress_to_review_with_runner_success_note(self, app_client, tmp_talaria_dir):
        board_path = tmp_talaria_dir["board_file"]
        board = json.loads(board_path.read_text())
        for col in board["columns"]:
            if col["id"] == "in_progress":
                col["auto_transition"] = {
                    "to": "review",
                    "when": "on_agent_success",
                    "require": ["rule:agent_work_done"],
                }
        board_path.write_text(json.dumps(board, indent=2))

        create = app_client.post("/api/card", json={"title": "Guarded move pass"})
        card_id = create.get_json()["id"]
        app_client.patch(f"/api/card/{card_id}", json={"column": "in_progress"})
        app_client.post(
            f"/api/card/{card_id}/note",
            json={"author": "***", "text": "[runner] Worker codex finished for card."},
        )

        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "review"})
        assert rv.status_code == 200
        assert rv.get_json()["column"] == "review"


# ── /api/card/<id>/note (POST) ────────────────────────────────────────────────

class TestAddNote:
    def test_add_note(self, app_client):
        create = app_client.post("/api/card", json={"title": "Note card"})
        card_id = create.get_json()["id"]
        rv = app_client.post(
            f"/api/card/{card_id}/note",
            json={"text": "Hello world", "author": "alice"},
        )
        assert rv.status_code == 201
        note = rv.get_json()
        assert note["text"] == "Hello world"
        assert note["author"] == "alice"
        assert "ts" in note

    def test_note_appears_in_card(self, app_client):
        create = app_client.post("/api/card", json={"title": "Note card 2"})
        card_id = create.get_json()["id"]
        app_client.post(
            f"/api/card/{card_id}/note", json={"text": "First!", "author": "bob"}
        )
        card = app_client.get(f"/api/card/{card_id}").get_json()
        assert any(n["text"] == "First!" for n in card.get("status_notes", []))


# ── /api/card/<id> (DELETE) ───────────────────────────────────────────────────

class TestDeleteCard:
    def test_delete_existing_card(self, app_client, tmp_talaria_dir):
        create = app_client.post("/api/card", json={"title": "To delete"})
        card_id = create.get_json()["id"]
        rv = app_client.delete(f"/api/card/{card_id}")
        assert rv.status_code == 200
        # Card file should be gone
        card_file = tmp_talaria_dir["cards_dir"] / f"{card_id}.md"
        assert not card_file.exists()

    def test_delete_nonexistent_returns_404(self, app_client):
        rv = app_client.delete("/api/card/no-such-card")
        assert rv.status_code == 404


# ── /api/activity ─────────────────────────────────────────────────────────────

class TestActivity:
    def test_returns_list(self, app_client):
        rv = app_client.get("/api/activity")
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), list)


class TestHistory:
    def test_history_returns_done_cards(self, app_client):
        create = app_client.post(
            "/api/card",
            json={
                "title": "History card",
                "labels": ["feature", "domain:telegram", "component:telegram_ui"],
            },
        )
        card_id = create.get_json()["id"]
        app_client.patch(f"/api/card/{card_id}", json={"column": "done"})

        rv = app_client.get("/api/history")
        assert rv.status_code == 200
        rows = rv.get_json()
        assert any(r.get("card_id") == card_id for r in rows)

    def test_history_filters_by_domain(self, app_client):
        c1 = app_client.post(
            "/api/card",
            json={"title": "Telegram feature", "labels": ["feature", "domain:telegram", "component:telegram_ui"]},
        ).get_json()["id"]
        c2 = app_client.post(
            "/api/card",
            json={"title": "Watcher fix", "labels": ["bugfix", "domain:watcher", "component:agent_watcher"]},
        ).get_json()["id"]
        app_client.patch(f"/api/card/{c1}", json={"column": "done"})
        app_client.patch(f"/api/card/{c2}", json={"column": "done"})

        rv = app_client.get("/api/history?domain=telegram")
        rows = rv.get_json()
        assert rows
        assert all("telegram" in r.get("domains", []) for r in rows)


class TestQueueAndReleaseCut:
    def test_compact_agent_queue_drops_stale_and_dedupes(self, app_client, tmp_talaria_dir):
        create = app_client.post("/api/card", json={"title": "Queue target"})
        card_id = create.get_json()["id"]

        tmp_talaria_dir["agent_queue"].write_text(
            json.dumps(
                [
                    {"card": {"id": card_id, "column": "backlog"}, "queued_at": "2026-01-01T00:00:00+00:00"},
                    {"card": {"id": card_id, "column": "backlog"}, "queued_at": "2026-01-01T00:00:01+00:00"},
                    {"card": {"id": "missing", "column": "backlog"}, "queued_at": "2026-01-01T00:00:02+00:00"},
                ],
                indent=2,
            )
        )

        rv = app_client.post("/api/agent_queue/compact", json={})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["before"] == 3
        assert data["after"] == 1
        assert data["dropped"]["missing_card"] == 1
        assert data["dropped"]["deduped"] == 1

    def test_release_cut_archives_done_and_stamps_release(self, app_client, tmp_talaria_dir):
        c1 = app_client.post("/api/card", json={"title": "Ship one"}).get_json()["id"]
        c2 = app_client.post("/api/card", json={"title": "Ship two"}).get_json()["id"]
        app_client.patch(f"/api/card/{c1}", json={"column": "done"})
        app_client.patch(f"/api/card/{c2}", json={"column": "done"})

        rv = app_client.post("/api/release/cut", json={"release": "v0.2.0"})
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["release"] == "v0.2.0"
        assert body["archived_count"] == 2
        assert set(body["archived_ids"]) == {c1, c2}

        board = app_client.get("/api/board").get_json()
        done_cards = [c for c in board.get("cards", []) if c.get("column") == "done"]
        assert done_cards == []

        graph_file = tmp_talaria_dir["archive_dir"] / "graph.jsonl"
        rows = [json.loads(line) for line in graph_file.read_text().splitlines() if line.strip()]
        assert rows
        assert all(row.get("release") == "v0.2.0" for row in rows)

    def test_release_cut_requires_release_tag(self, app_client):
        rv = app_client.post("/api/release/cut", json={})
        assert rv.status_code == 400
        assert "release" in rv.get_json().get("error", "")


# ── Column trigger integration ─────────────────────────────────────────────────

class TestColumnTriggers:
    def test_moving_to_in_progress_column_does_not_error(self, app_client):
        """Moving to a trigger column should not raise; trigger functions are
        called but may be no-ops when external deps (git worktree) are absent."""
        create = app_client.post("/api/card", json={"title": "Trigger test"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "in_progress"})
        assert rv.status_code == 200
        assert rv.get_json()["column"] == "in_progress"

    def test_moving_to_done_column_does_not_error(self, app_client):
        create = app_client.post("/api/card", json={"title": "Done trigger"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "done"})
        assert rv.status_code == 200
        assert rv.get_json()["column"] == "done"

    def test_blocks_review_to_done_without_review_pass_signal(self, app_client, tmp_talaria_dir):
        board_path = tmp_talaria_dir["board_file"]
        board = json.loads(board_path.read_text())
        for col in board["columns"]:
            if col["id"] == "review":
                col["auto_transition"] = {
                    "to": "done",
                    "when": "on_checks_pass",
                    "require": ["rule:review_passed"],
                }
        board_path.write_text(json.dumps(board, indent=2))

        create = app_client.post("/api/card", json={"title": "Review guard"})
        card_id = create.get_json()["id"]
        app_client.patch(f"/api/card/{card_id}", json={"column": "review"})

        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "done"})
        assert rv.status_code == 409
        assert "checks_passed" in rv.get_json().get("missing_requirements", [])

    def test_allows_review_to_done_with_review_pass_signal(self, app_client, tmp_talaria_dir):
        board_path = tmp_talaria_dir["board_file"]
        board = json.loads(board_path.read_text())
        for col in board["columns"]:
            if col["id"] == "review":
                col["auto_transition"] = {
                    "to": "done",
                    "when": "on_checks_pass",
                    "require": ["rule:review_passed"],
                }
        board_path.write_text(json.dumps(board, indent=2))

        create = app_client.post("/api/card", json={"title": "Review guard pass"})
        card_id = create.get_json()["id"]
        app_client.patch(f"/api/card/{card_id}", json={"column": "review"})
        app_client.post(
            f"/api/card/{card_id}/note",
            json={"author": "***", "text": "[review-gate] passed: tests command succeeded"},
        )

        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "done"})
        assert rv.status_code == 200
        assert rv.get_json()["column"] == "done"

    def test_done_column_archives_oldest_when_over_cap(self, app_client, tmp_talaria_dir):
        created_ids = []
        for i in range(21):
            create = app_client.post("/api/card", json={"title": f"Done {i:02d}"})
            card_id = create.get_json()["id"]
            created_ids.append(card_id)
            rv = app_client.patch(f"/api/card/{card_id}", json={"column": "done"})
            assert rv.status_code == 200

        board = app_client.get("/api/board").get_json()
        done_cards = [c for c in board.get("cards", []) if c.get("column") == "done"]
        assert len(done_cards) == 20

        archived = list(tmp_talaria_dir["archive_dir"].glob("*.md"))
        assert len(archived) == 1
        # First completed card should be archived first.
        assert archived[0].stem.startswith(created_ids[0])

        graph_file = tmp_talaria_dir["archive_dir"] / "graph.jsonl"
        assert graph_file.exists()
        rows = [line for line in graph_file.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        assert created_ids[0] in rows[0]

    def test_agent_spawn_trigger_column_accepts_card(self, app_client):
        """Cards can be moved to agent_spawn trigger columns (spec, groom)."""
        create = app_client.post("/api/card", json={"title": "Spawn test"})
        card_id = create.get_json()["id"]
        rv = app_client.patch(f"/api/card/{card_id}", json={"column": "spec"})
        assert rv.status_code == 200
        assert rv.get_json()["column"] == "spec"


# ── /api/column/<id> (PATCH) ──────────────────────────────────────────────────

class TestUpdateColumn:
    def test_update_column_trigger(self, app_client):
        rv = app_client.patch("/api/column/backlog", json={"trigger": "notify"})
        assert rv.status_code == 200
        assert rv.get_json()["trigger"] == "notify"

    def test_update_column_webhook_url(self, app_client):
        rv = app_client.patch(
            "/api/column/done",
            json={"webhook_url": "https://example.com/hook"},
        )
        assert rv.status_code == 200
        assert rv.get_json()["webhook_url"] == "https://example.com/hook"

    def test_update_nonexistent_column_returns_404(self, app_client):
        rv = app_client.patch("/api/column/no-such-col", json={"trigger": "notify"})
        assert rv.status_code == 404
