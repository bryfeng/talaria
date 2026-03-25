"""
test_api.py — API endpoint tests using Flask's test client.

Tests the server.py Flask app endpoints:
  GET  /api/board
  POST /api/card
  GET  /api/card/<id>
  PATCH /api/card/<id>
  POST /api/card/<id>/note
  POST /api/card/<id>/cost
  DELETE /api/card/<id>
  GET  /api/activity
  PATCH /api/column/<id>
"""




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


# ── /api/card/<id>/cost (POST) ────────────────────────────────────────────────

class TestAddCost:
    def test_append_cost_log_entry(self, app_client):
        create = app_client.post("/api/card", json={"title": "Cost card"})
        card_id = create.get_json()["id"]
        rv = app_client.post(
            f"/api/card/{card_id}/cost",
            json={
                "agent": "hermes",
                "tokens": 12345,
                "cost_usd": 0.25,
                "ts": "2026-03-24T00:00:00+00:00",
            },
        )
        assert rv.status_code == 201
        entry = rv.get_json()
        assert entry["agent"] == "hermes"
        assert entry["tokens"] == 12345
        assert entry["cost_usd"] == 0.25

    def test_cost_log_accumulates(self, app_client):
        create = app_client.post("/api/card", json={"title": "Multi cost"})
        card_id = create.get_json()["id"]
        app_client.post(
            f"/api/card/{card_id}/cost",
            json={"agent": "hermes", "tokens": 100, "cost_usd": 0.01},
        )
        app_client.post(
            f"/api/card/{card_id}/cost",
            json={"agent": "claude-code", "tokens": 200, "cost_usd": 0.02},
        )
        card = app_client.get(f"/api/card/{card_id}").get_json()
        assert len(card["cost_log"]) == 2


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
