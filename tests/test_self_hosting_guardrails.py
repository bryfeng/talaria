"""Tests for self-hosting enforcement guardrails."""

from pathlib import Path

import pytest

from talaria.guardrails import enforce_runner_target_separation
import agent_watcher


def test_guardrail_raises_when_runner_equals_target(monkeypatch, tmp_path):
    runner = tmp_path / "runner"
    runner.mkdir()
    target = runner

    monkeypatch.delenv("TALARIA_BYPASS_ALLOWED", raising=False)
    with pytest.raises(RuntimeError):
        enforce_runner_target_separation(runner, [target])


def test_guardrail_bypass_allows_same_path(monkeypatch, tmp_path):
    runner = tmp_path / "runner"
    runner.mkdir()

    monkeypatch.setenv("TALARIA_BYPASS_ALLOWED", "true")
    enforce_runner_target_separation(runner, [runner])


def test_watcher_guardrail_blocks_self_target_via_work_dir(monkeypatch):
    runner_dir = Path(agent_watcher.__file__).resolve().parent
    monkeypatch.delenv("TALARIA_BYPASS_ALLOWED", raising=False)
    monkeypatch.setattr(agent_watcher, "TALARIA_WORK_DIR", str(runner_dir))

    with pytest.raises(SystemExit):
        agent_watcher.enforce_runner_target_separation()


def test_watcher_guardrail_blocks_self_target_via_config(monkeypatch, tmp_path):
    runner_dir = Path(agent_watcher.__file__).resolve().parent
    cfg_dir = tmp_path / "home"
    cfg_dir.mkdir()
    (cfg_dir / "talaria.config.json").write_text(
        '{"repos": [{"name": "talaria", "path": "%s"}]}' % str(runner_dir)
    )

    monkeypatch.delenv("TALARIA_BYPASS_ALLOWED", raising=False)
    monkeypatch.setattr(agent_watcher, "TALARIA_WORK_DIR", str(tmp_path / "other"))
    monkeypatch.setattr(agent_watcher, "TALARIA_HOME", cfg_dir)

    with pytest.raises(SystemExit):
        agent_watcher.enforce_runner_target_separation()
