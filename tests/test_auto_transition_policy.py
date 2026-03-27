from unittest.mock import MagicMock, patch

import agent_watcher


def test_get_auto_transition_uses_explicit_policy():
    col = {
        "id": "ready",
        "auto_transition": {
            "to": "in_progress",
            "when": "on_rule_pass",
            "require": ["label:auto-next"],
            "on_fail": "ready",
        },
    }
    p = agent_watcher._get_auto_transition(col)
    assert p["to"] == "in_progress"
    assert p["when"] == "on_rule_pass"
    assert p["require"] == ["label:auto-next"]
    assert p["on_fail"] == "ready"


def test_get_auto_transition_falls_back_to_legacy_mapping():
    p = agent_watcher._get_auto_transition({"id": "spec"})
    assert p == {"to": "groom", "when": "on_agent_success"}


def test_requirements_pass_field_and_label():
    card = {
        "description": "has body",
        "labels": ["auto-next", "feature"],
        "tests": {"command": "pytest -q"},
    }
    assert agent_watcher._requirements_pass(card, ["description"])
    assert agent_watcher._requirements_pass(card, ["field:description"])
    assert agent_watcher._requirements_pass(card, ["label:auto-next"])
    assert not agent_watcher._requirements_pass(card, ["label:missing"])
    assert not agent_watcher._requirements_pass(card, ["field:acceptance_criteria"])


def test_groom_decomposition_rule_non_high_scope_passes():
    card = {
        "id": "c1",
        "labels": ["priority:medium", "component:api"],
    }
    assert agent_watcher._requirements_pass(card, ["rule:groom_decomposition"])


def test_groom_decomposition_rule_high_scope_requires_children_and_marker():
    high_scope = {
        "id": "c2",
        "labels": ["scope:large", "component:api", "component:watcher", "component:telegram"],
    }
    assert not agent_watcher._requirements_pass(high_scope, ["rule:groom_decomposition"])

    high_scope_ok = {
        "id": "c3",
        "labels": [
            "scope:large",
            "component:api",
            "component:watcher",
            "component:telegram",
            "child:abc12345",
            "child:def67890",
            "decomposed",
        ],
    }
    assert agent_watcher._requirements_pass(high_scope_ok, ["rule:groom_decomposition"])


def test_unknown_rule_fails_requirements():
    card = {"id": "c4", "labels": []}
    assert not agent_watcher._requirements_pass(card, ["rule:unknown_rule"])


@patch("agent_watcher.api_get", return_value={"id": "abc123"})
@patch("agent_watcher.api_patch", return_value=True)
@patch("agent_watcher.notify")
@patch("agent_watcher.api_cost")
@patch("agent_watcher.api_note")
def test_handle_worker_done_moves_using_policy(mock_note, mock_cost, mock_notify, mock_patch, mock_get):
    worker = MagicMock()
    worker.card_id = "abc123"
    worker.worker_type = "claude-code"
    worker.started_at = None
    worker.col_config = {
        "id": "spec",
        "auto_transition": {"to": "groom", "when": "on_agent_success"},
    }

    agent_watcher.handle_worker_done(worker)

    assert ("abc123", {"column": "groom"}) in [
        (c[0][0], c[0][1]) for c in mock_patch.call_args_list
    ]


@patch("agent_watcher.api_get", return_value={"id": "abc124", "labels": []})
@patch("agent_watcher.api_patch", return_value=True)
@patch("agent_watcher.notify")
@patch("agent_watcher.api_cost")
@patch("agent_watcher.api_note")
def test_handle_worker_done_on_fail_when_requirements_missing(mock_note, mock_cost, mock_notify, mock_patch, mock_get):
    worker = MagicMock()
    worker.card_id = "abc124"
    worker.worker_type = "claude-code"
    worker.started_at = None
    worker.col_config = {
        "id": "groom",
        "auto_transition": {
            "to": "ready",
            "when": "on_agent_success",
            "require": ["label:auto-next"],
            "on_fail": "ready",
        },
    }

    agent_watcher.handle_worker_done(worker)

    assert ("abc124", {"column": "ready"}) in [
        (c[0][0], c[0][1]) for c in mock_patch.call_args_list
    ]
