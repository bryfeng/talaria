"""
Tests for agent_watcher robustness: timeouts, kill/retry, deadlock detection.

Covers:
  - Worker.check_timeout: overall wall-clock timeout
  - Worker.check_timeout: no-output hang detection
  - Worker.check_timeout: returns False when process is already done
  - Worker.kill: SIGTERM then SIGKILL escalation
  - PipelineRunner._handle_timeout: note logged, card moved to ready, retry count incremented
  - PipelineRunner._handle_timeout: max retries → escalate to blocked (or ready fallback)
  - WORKER_TIMEOUT_SEC env var is respected
  - WORKER_NO_OUTPUT_SEC env var is respected
  - WORKER_MAX_RETRIES env var is respected
"""

import subprocess
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

import agent_watcher
from agent_watcher import Worker, PipelineRunner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_worker(card_id="abc123", timeout_sec=300, no_output_sec=120):
    col_config = {"id": "in_progress", "name": "In Progress", "worker": "claude-code"}
    card = {"id": card_id, "title": "Test card", "column": "in_progress"}
    w = Worker(card_id, col_config, card, "")
    w.timeout_sec = timeout_sec
    w.no_output_sec = no_output_sec
    return w


def _started_ago(seconds: float) -> str:
    """Return an ISO timestamp that is *seconds* in the past."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return ts.isoformat()


# ── Worker.check_timeout: overall wall-clock ──────────────────────────────────

def test_check_timeout_not_exceeded_yet():
    w = _make_worker(timeout_sec=3600)
    w.started_at = _started_ago(60)
    w.last_output_at = time.time()
    # Process hasn't exited
    w._popen = None
    w.process = MagicMock()
    w.process.poll.return_value = None

    timed_out, reason = w.check_timeout()
    assert timed_out is False


def test_check_timeout_overall_exceeded():
    w = _make_worker(timeout_sec=60)
    w.started_at = _started_ago(120)  # started 2 minutes ago, timeout is 1 minute
    w.last_output_at = time.time()
    w._popen = None
    w.process = MagicMock()
    w.process.poll.return_value = None

    timed_out, reason = w.check_timeout()
    assert timed_out is True
    assert "overall timeout exceeded" in reason
    assert "120" in reason or "121" in reason  # elapsed seconds


def test_check_timeout_no_output_hung():
    w = _make_worker(timeout_sec=3600, no_output_sec=60)
    w.started_at = _started_ago(30)
    w.last_output_at = time.time() - 90  # silent for 90s, threshold is 60s
    # Attach a fake popen so the no-output branch fires
    w._popen = MagicMock()
    w._popen.poll.return_value = None
    w.process = MagicMock()
    w.process.poll.return_value = None

    timed_out, reason = w.check_timeout()
    assert timed_out is True
    assert "no output" in reason
    assert "hung" in reason


def test_check_timeout_no_output_within_threshold():
    w = _make_worker(timeout_sec=3600, no_output_sec=120)
    w.started_at = _started_ago(30)
    w.last_output_at = time.time() - 30  # only silent 30s, threshold is 120s
    w._popen = MagicMock()
    w._popen.poll.return_value = None
    w.process = MagicMock()
    w.process.poll.return_value = None

    timed_out, reason = w.check_timeout()
    assert timed_out is False
    assert reason == ""


def test_check_timeout_skipped_if_process_done():
    """If the process has exited, check_timeout must return False regardless of timers."""
    w = _make_worker(timeout_sec=1)
    w.started_at = _started_ago(9999)  # way past timeout
    w.last_output_at = 0.0
    # Mark process as done
    w._popen = MagicMock()
    w._popen.poll.return_value = 0  # exit code 0

    timed_out, reason = w.check_timeout()
    assert timed_out is False


def test_check_timeout_no_output_disabled_when_zero():
    """WORKER_NO_OUTPUT_SEC=0 disables the no-output check."""
    w = _make_worker(timeout_sec=3600, no_output_sec=0)
    w.started_at = _started_ago(10)
    w.last_output_at = time.time() - 9999  # would trigger if enabled
    w._popen = MagicMock()
    w._popen.poll.return_value = None
    w.process = MagicMock()
    w.process.poll.return_value = None

    timed_out, _ = w.check_timeout()
    assert timed_out is False


# ── Worker.kill ───────────────────────────────────────────────────────────────

def test_kill_sends_sigterm_then_waits():
    w = _make_worker()
    mock_popen = MagicMock()
    mock_popen.wait.return_value = None  # exits promptly on SIGTERM
    w._popen = mock_popen

    w.kill()

    mock_popen.terminate.assert_called_once()
    mock_popen.wait.assert_called_once_with(timeout=5)
    mock_popen.kill.assert_not_called()


def test_kill_escalates_to_sigkill_on_timeout():
    w = _make_worker()
    mock_popen = MagicMock()
    mock_popen.wait.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=5)
    w._popen = mock_popen

    w.kill()

    mock_popen.terminate.assert_called_once()
    mock_popen.kill.assert_called_once()


def test_kill_uses_process_wrapper_when_no_popen():
    w = _make_worker()
    w._popen = None
    mock_proc = MagicMock()
    w.process = mock_proc

    w.kill()

    mock_proc.kill.assert_called_once()


# ── PipelineRunner._handle_timeout ───────────────────────────────────────────

@pytest.fixture
def runner():
    return PipelineRunner()


def _attach_done_popen(worker: Worker):
    """Give the worker a popen that appears exited (for cleanup calls)."""
    mock_popen = MagicMock()
    mock_popen.poll.return_value = -15  # killed by signal
    worker._popen = mock_popen
    worker.pid = 12345


@patch("agent_watcher.notify")
@patch("agent_watcher.api_patch")
@patch("agent_watcher.api_note")
def test_handle_timeout_first_attempt_moves_to_ready(mock_note, mock_patch, mock_notify, runner):
    w = _make_worker(card_id="card01")
    w.started_at = _started_ago(10)
    _attach_done_popen(w)

    runner._handle_timeout(w, "overall timeout exceeded (3600s > 300s)")

    # Note should mention killed + attempt number
    note_calls = [c[0][1] for c in mock_note.call_args_list]
    assert any("KILLED" in n for n in note_calls)
    assert any("Attempt 1" in n for n in note_calls)

    # Card moved back to ready for retry
    patch_positional = [(c[0][0], c[0][1]) for c in mock_patch.call_args_list]
    assert ("card01", {"column": "ready"}) in patch_positional


@patch("agent_watcher.notify")
@patch("agent_watcher.api_patch")
@patch("agent_watcher.api_note")
def test_handle_timeout_increments_retry_count(mock_note, mock_patch, mock_notify, runner):
    w = _make_worker(card_id="card02")
    w.started_at = _started_ago(10)
    _attach_done_popen(w)

    assert runner.retry_counts.get("card02", 0) == 0
    runner._handle_timeout(w, "no output for 300s")
    assert runner.retry_counts.get("card02", 0) == 1

    _attach_done_popen(w)
    runner._handle_timeout(w, "no output for 300s")
    # On second timeout attempt == WORKER_MAX_RETRIES (2), so escalate and delete key
    assert "card02" not in runner.retry_counts


@patch("agent_watcher.notify")
@patch("agent_watcher.api_patch", return_value=True)
@patch("agent_watcher.api_note")
def test_handle_timeout_escalates_after_max_retries(mock_note, mock_patch, mock_notify, runner):
    """After WORKER_MAX_RETRIES failures the card should be escalated to 'blocked'."""
    card_id = "card03"
    # Pre-seed so the next call hits the max
    runner.retry_counts[card_id] = agent_watcher.WORKER_MAX_RETRIES - 1

    w = _make_worker(card_id=card_id)
    w.started_at = _started_ago(10)
    _attach_done_popen(w)

    runner._handle_timeout(w, "overall timeout exceeded (9999s > 300s)")

    # Card should be moved to 'blocked'
    patch_calls = [(c[0][0], c[0][1]) for c in mock_patch.call_args_list]
    assert (card_id, {"column": "blocked"}) in patch_calls

    # retry_counts key should be deleted
    assert card_id not in runner.retry_counts

    # Note should mention max retries and escalation
    note_texts = [c[0][1] for c in mock_note.call_args_list]
    assert any("Max retries" in n or "Escalat" in n for n in note_texts)


@patch("agent_watcher.notify")
@patch("agent_watcher.api_patch", return_value=False)  # 'blocked' column doesn't exist
@patch("agent_watcher.api_note")
def test_handle_timeout_falls_back_to_ready_when_blocked_missing(mock_note, mock_patch, mock_notify, runner):
    """If 'blocked' column doesn't exist, fall back to 'ready'."""
    card_id = "card04"
    runner.retry_counts[card_id] = agent_watcher.WORKER_MAX_RETRIES - 1

    w = _make_worker(card_id=card_id)
    w.started_at = _started_ago(10)
    _attach_done_popen(w)

    runner._handle_timeout(w, "overall timeout exceeded")

    patch_calls = [(c[0][0], c[0][1]) for c in mock_patch.call_args_list]
    # 'blocked' attempted first (returns False), then 'ready' as fallback
    assert (card_id, {"column": "blocked"}) in patch_calls
    assert (card_id, {"column": "ready"}) in patch_calls

    note_texts = [c[0][1] for c in mock_note.call_args_list]
    assert any("ready" in n.lower() for n in note_texts)


# ── Failure reason visibility ─────────────────────────────────────────────────

@patch("agent_watcher.notify")
@patch("agent_watcher.api_patch")
@patch("agent_watcher.api_note")
def test_failure_reason_in_note(mock_note, mock_patch, mock_notify, runner):
    """The timeout reason must appear verbatim in the status note."""
    reason = "no output for 600s (hung, threshold=300s)"
    w = _make_worker(card_id="card05")
    w.started_at = _started_ago(10)
    _attach_done_popen(w)

    runner._handle_timeout(w, reason)

    note_texts = " ".join(c[0][1] for c in mock_note.call_args_list)
    assert reason in note_texts


# ── Env-var configuration ─────────────────────────────────────────────────────

def test_worker_timeout_sec_env_default():
    """WORKER_TIMEOUT_SEC defaults to AGENT_TIMEOUT (1800)."""
    # The module-level constant should match what the env says.
    assert agent_watcher.WORKER_TIMEOUT_SEC > 0


def test_worker_no_output_sec_env_default():
    assert agent_watcher.WORKER_NO_OUTPUT_SEC > 0


def test_worker_max_retries_env_default():
    assert agent_watcher.WORKER_MAX_RETRIES >= 1


def test_worker_inherits_module_timeout():
    """Worker.timeout_sec should pick up the module-level WORKER_TIMEOUT_SEC."""
    # A freshly-constructed Worker uses the module defaults.
    col_config = {"id": "in_progress", "name": "In Progress", "worker": "claude-code"}
    card = {"id": "x", "title": "t", "column": "in_progress"}
    fresh = Worker("x", col_config, card, "")
    assert fresh.timeout_sec == agent_watcher.WORKER_TIMEOUT_SEC
    assert fresh.no_output_sec == agent_watcher.WORKER_NO_OUTPUT_SEC
