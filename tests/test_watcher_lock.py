"""
Tests for agent_watcher single-instance lock + startup guard.

Covers:
  - _pid_alive: live vs dead PID detection
  - acquire_watcher_lock: normal acquisition, second-instance exit, stale lock cleanup
  - write_status_file: correct JSON fields
  - release_watcher_lock: removes lock + status files
"""

import json
import os

import pytest

import agent_watcher


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Redirect lock/status files to a temp directory."""
    lock = tmp_path / ".watcher.lock"
    status = tmp_path / ".watcher.status"
    monkeypatch.setattr(agent_watcher, "_LOCK_FILE", lock)
    monkeypatch.setattr(agent_watcher, "_STATUS_FILE", status)
    return tmp_path


# ── _pid_alive ────────────────────────────────────────────────────────────────

def test_pid_alive_current_process():
    assert agent_watcher._pid_alive(os.getpid()) is True


def test_pid_alive_dead_pid():
    # Use a very large PID that is almost certainly not running.
    # We look for one that os.kill raises ProcessLookupError for.
    for candidate in range(99990, 99999):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            assert agent_watcher._pid_alive(candidate) is False
            return
        except PermissionError:
            continue  # owned by another user, skip
    pytest.skip("Could not find a dead PID in the test range")


# ── acquire_watcher_lock ──────────────────────────────────────────────────────

def test_acquire_writes_lock(tmp_home):
    lock = agent_watcher._LOCK_FILE
    assert not lock.exists()

    agent_watcher.acquire_watcher_lock()
    try:
        assert lock.exists()
        assert int(lock.read_text().strip()) == os.getpid()
    finally:
        agent_watcher.release_watcher_lock()


def test_acquire_exits_if_live_instance(tmp_home):
    lock = agent_watcher._LOCK_FILE
    # Write current PID as if another instance is running.
    lock.write_text(str(os.getpid()))

    with pytest.raises(SystemExit) as exc:
        agent_watcher.acquire_watcher_lock()

    assert exc.value.code == 1
    # Clean up so other tests aren't affected.
    lock.unlink(missing_ok=True)


def test_acquire_cleans_stale_lock(tmp_home, capsys):
    lock = agent_watcher._LOCK_FILE
    status = agent_watcher._STATUS_FILE

    # Find a dead PID.
    dead_pid = None
    for candidate in range(99990, 99999):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            dead_pid = candidate
            break
        except PermissionError:
            continue

    if dead_pid is None:
        pytest.skip("Could not find a dead PID for stale-lock test")

    # Plant a stale lock + status file.
    lock.write_text(str(dead_pid))
    status.write_text('{"pid": %d}' % dead_pid)

    agent_watcher.acquire_watcher_lock()
    try:
        captured = capsys.readouterr()
        assert "Stale lock" in captured.out
        assert int(lock.read_text().strip()) == os.getpid()
    finally:
        agent_watcher.release_watcher_lock()


def test_acquire_tolerates_corrupt_lock(tmp_home):
    lock = agent_watcher._LOCK_FILE
    lock.write_text("not-a-pid")

    # Should not raise; corrupt lock is treated as stale.
    agent_watcher.acquire_watcher_lock()
    try:
        assert int(lock.read_text().strip()) == os.getpid()
    finally:
        agent_watcher.release_watcher_lock()


# ── write_status_file ─────────────────────────────────────────────────────────

def test_write_status_file_fields(tmp_home):
    agent_watcher.write_status_file()
    try:
        status = agent_watcher._STATUS_FILE
        assert status.exists()
        data = json.loads(status.read_text())
        assert data["pid"] == os.getpid()
        assert "started_at" in data
        assert "host" in data
    finally:
        agent_watcher._STATUS_FILE.unlink(missing_ok=True)


# ── release_watcher_lock ──────────────────────────────────────────────────────

def test_release_removes_files(tmp_home):
    lock = agent_watcher._LOCK_FILE
    status = agent_watcher._STATUS_FILE

    lock.write_text(str(os.getpid()))
    status.write_text('{"pid": %d}' % os.getpid())

    agent_watcher.release_watcher_lock()

    assert not lock.exists()
    assert not status.exists()


def test_release_is_idempotent(tmp_home):
    # Should not raise even if files are already gone.
    agent_watcher.release_watcher_lock()
    agent_watcher.release_watcher_lock()
