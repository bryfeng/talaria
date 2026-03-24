"""
Talaria Pipeline Runner — Watches the board and dispatches workers at each pipeline stage.

Reads column config (worker, context_files) from board.json columns via the /api/board endpoint.
Drafts context from TALARIA_HOME/agents/ files + card spec.
Spawns the right worker (hermes / claude-code / codex).
Tracks PIDs, logs cost, advances cards.

Usage:
    python agent_watcher.py

Environment:
    TALARIA_HOME       — Path to Talaria instance home (default: ~/.talaria/talaria)
    TALARIA_PORT      — Board API port (default: 8400)
    TALARIA_WORK_DIR  — Repo working directory (default: ~/talaria)
    MAX_CONCURRENT    — Max simultaneous agents (default: 2)
    POLL_INTERVAL     — Seconds between board polls (default: 15)
"""

import json
import os
import sys
import time
import signal
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

TALARIA_HOME = Path(os.getenv("TALARIA_HOME", os.path.expanduser("~/talaria")))
TALARIA_PORT = int(os.getenv("TALARIA_PORT", "8400"))
TALARIA_WORK_DIR = os.getenv("TALARIA_WORK_DIR", os.path.expanduser("~/talaria"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "2"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
AUTO_SUMMARY = os.getenv("AUTO_SUMMARY", "").lower() in ("1", "true", "yes")

# Agent binaries
HERMES_BINARY = os.getenv("HERMES_AGENT_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/run_agent.py"))
HERMES_PYTHON = os.getenv("HERMES_VENV_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python"))
CLAUDE_CODE_BINARY = os.getenv("CLAUDE_CODE_BINARY", "claude")
CODEX_BINARY = os.getenv("CODEX_BINARY", "codex")


# ── API helpers ───────────────────────────────────────────────────────────────

def api_board() -> Optional[dict]:
    """Fetch the full board from the Talaria server."""
    try:
        with urllib.request.urlopen(f"http://localhost:{TALARIA_PORT}/api/board", timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[runner] Failed to fetch board: {e}")
        return None


def api_get(card_id: str) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"http://localhost:{TALARIA_PORT}/api/card/{card_id}", timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def api_patch(card_id: str, data: dict) -> bool:
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/card/{card_id}"
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"},
            method="PATCH")
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        print(f"[runner] Failed to PATCH card {card_id}: {e}")
        return False


def api_cost(card_id: str, agent: str, tokens: int, cost_usd: float) -> bool:
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/card/{card_id}/cost"
        body = json.dumps({
            "agent": agent,
            "tokens": tokens,
            "cost_usd": cost_usd,
            "ts": datetime.now(timezone.utc).isoformat(),
        }).encode()
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        print(f"[runner] Failed to log cost for {card_id}: {e}")
        return False


def api_note(card_id: str, text: str, author: str = "runner") -> bool:
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/card/{card_id}/note"
        body = json.dumps({"text": text, "author": author}).encode()
        req = urllib.request.urlopen(
            urllib.request.Request(url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST"),
            timeout=10)
        return True
    except Exception as e:
        print(f"[runner] Failed to add note to {card_id}: {e}")
        return False


def notify(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_HOME_CHANNEL_ID") or os.getenv("TELEGRAM_HOME_CHANNEL", "").lstrip("@")
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ── Auto-summary ──────────────────────────────────────────────────────────────

def _get_git_diff(worktree_path: str) -> str:
    """Return a compact git diff for the worktree (stat + truncated patch)."""
    try:
        for base in ("main", "master"):
            stat = subprocess.run(
                ["git", "diff", f"{base}...HEAD", "--stat"],
                cwd=worktree_path, capture_output=True, text=True, timeout=30,
            )
            if stat.returncode == 0 and stat.stdout.strip():
                patch = subprocess.run(
                    ["git", "diff", f"{base}...HEAD"],
                    cwd=worktree_path, capture_output=True, text=True, timeout=30,
                )
                diff_text = patch.stdout[:3000] if patch.returncode == 0 else ""
                return f"Stat:\n{stat.stdout.strip()}\n\nDiff:\n{diff_text}"
        # Fallback: recent commits
        log = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            cwd=worktree_path, capture_output=True, text=True, timeout=30,
        )
        if log.returncode == 0 and log.stdout.strip():
            return f"Recent commits:\n{log.stdout.strip()}"
    except Exception as e:
        return f"(could not get git info: {e})"
    return "(no git changes found)"


def generate_auto_summary(card: dict, phase: str) -> Optional[str]:
    """Call the auxiliary LLM to produce a brief summary for review or done entry.

    Returns the summary string, or None if AUTO_SUMMARY is off / no worktree /
    no API key / the call fails.
    """
    if not AUTO_SUMMARY:
        return None
    worktree_path = card.get("worktree_path")
    if not worktree_path:
        return None

    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    model = os.getenv("LLM_MODEL", "MiniMax-M2.7")

    if not api_key:
        print("[runner] AUTO_SUMMARY enabled but no MINIMAX_API_KEY / LLM_API_KEY found; skipping.")
        return None

    card_title = card.get("title", "Untitled")
    card_desc = card.get("description", "(no description)")
    git_info = _get_git_diff(worktree_path)

    if phase == "review":
        user_msg = (
            f"Card: {card_title}\n\n"
            f"Description:\n{card_desc}\n\n"
            f"Git changes:\n{git_info}\n\n"
            "Write a concise 2-3 sentence summary of what code changes were made and why. "
            "Focus on what a reviewer needs to know before approving."
        )
    else:  # done
        user_msg = (
            f"Card: {card_title}\n\n"
            f"Description:\n{card_desc}\n\n"
            "Write a concise 1-2 sentence summary of what was accomplished."
        )

    try:
        url = f"{base_url.rstrip('/')}/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": user_msg}],
            "max_tokens": 200,
        }).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[runner] Auto-summary LLM call failed: {e}")
        return None


# ── Context drafting ─────────────────────────────────────────────────────────

def draft_context(col_config: dict, card: dict, home: Path) -> str:
    """Build the context file fed to a worker.

    Reads TALARIA_HOME/talaria.md (always first), then each file in
    context_files, then appends the card spec at the end.
    """
    lines = []
    lines.append(f"# Talaria — Worker Context")
    lines.append(f"# Card: {card['title']} [#{card['id']}]")
    lines.append(f"# Column: {col_config['id']} ({col_config['name']})")
    lines.append(f"# Worker: {col_config.get('worker', 'hermes')}")
    lines.append(f"# Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # Always load talaria.md first
    root = home / "talaria.md"
    if root.exists():
        lines.append("## Project Context")
        lines.append(root.read_text())
        lines.append("")

    # Then load additional context files
    for ctx_file in col_config.get("context_files", []):
        if ctx_file == "talaria.md":
            continue  # already loaded
        path = home / ctx_file
        if path.exists():
            lines.append(f"## {ctx_file}")
            lines.append(path.read_text())
            lines.append("")
        else:
            lines.append(f"## {ctx_file} [NOT FOUND: {path}]")
            lines.append("")
    # Card spec — always included
    lines.append("## Card Spec")
    lines.append(f"**Title:** {card.get('title', 'Untitled')}")
    lines.append(f"**ID:** {card['id']}")
    lines.append(f"**Priority:** {card.get('priority', 'none')}")
    labels = card.get('labels', [])
    if labels:
        lines.append(f"**Labels:** {', '.join(labels)}")
    lines.append("")

    desc = card.get("description", "")
    if desc:
        lines.append(desc)
    else:
        lines.append("(No description provided)")

    # Append existing status notes for context
    notes = card.get("status_notes", [])
    if notes:
        lines.append("")
        lines.append("## Status Notes")
        for note in notes[-5:]:  # last 5 notes to keep context lean
            author = note.get("author", "unknown")
            text = note.get("text", "")
            ts = note.get("ts", "")[:10]
            lines.append(f"- **{author}** [{ts}]: {text[:200]}")

    # Instructions from column config
    if col_config.get("instructions"):
        lines.append("")
        lines.append(f"## Instructions")
        lines.append(col_config["instructions"])

    return "\n".join(lines)


# ── Worker dispatch ───────────────────────────────────────────────────────────

class Worker:
    def __init__(self, card_id: str, col_config: dict, card: dict, context: str):
        self.card_id = card_id
        self.col_config = col_config
        self.card = card
        self.context = context
        self.pid: Optional[int] = None
        self.started_at: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        # Use the card's worktree path (set when entering In Progress) so agents
        # run inside the correct repo's worktree for multi-repo cards.
        worktree = card.get("worktree_path")
        self.work_dir: str = worktree if worktree and Path(worktree).exists() else TALARIA_WORK_DIR

    @property
    def worker_type(self) -> str:
        return self.col_config.get("worker", "hermes")

    def _write_context(self) -> Path:
        """Write context to a temp file readable by the worker."""
        fd, path = tempfile.mkstemp(suffix=".md", prefix=f"talaria-{self.card_id}-")
        with os.fdopen(fd, "w") as f:
            f.write(self.context)
        return Path(path)

    def _spawn_hermes(self, ctx_path: Path, goal: str) -> int:
        cmd = [
            HERMES_PYTHON, HERMES_BINARY,
            "--query", goal,
            "--model", os.getenv("LLM_MODEL", "MiniMax-M2.7"),
            "--base-url", os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        ]
        proc = subprocess.Popen(cmd, cwd=self.work_dir, env=self._env())
        return proc.pid

    def _env(self) -> dict:
        """Get env dict for subprocess. Inherits parent environment as-is."""
        return os.environ.copy()

    def _spawn_claude_code(self, ctx_path: Path, goal: str) -> int:
        cmd = [CLAUDE_CODE_BINARY, "--dangerously-skip-permissions", "--print", goal]
        proc = subprocess.Popen(cmd, cwd=self.work_dir, env=self._env())
        return proc.pid

    def _spawn_codex(self, ctx_path: Path, goal: str) -> int:
        cmd = [CODEX_BINARY, goal]
        proc = subprocess.Popen(cmd, cwd=self.work_dir, env=self._env())
        return proc.pid

    def spawn(self) -> bool:
        """Spawn the worker process. Returns True if successful."""
        ctx_path = self._write_context()
        self.started_at = datetime.now(timezone.utc).isoformat()

        # Build goal: include context path + instruction to read it
        goal = (
            f"IMPORTANT: Read the file at {ctx_path} carefully before starting.\n"
            f"It contains your full task context, instructions, and the card spec.\n"
            f"Complete the task described in that file, then:\n"
            f"  1. Add a completion note to the Talaria card via the API:\n"
            f"     curl -X POST http://localhost:{TALARIA_PORT}/api/card/{self.card_id}/note \\\n"
            f"       -H 'Content-Type: application/json' \\\n"
            f"       -d '{{\"text\": \"<what you did>\", \"author\": \"hermes\"}}'\n"
            f"  2. Move the card to 'review' via:\n"
            f"     curl -X PATCH http://localhost:{TALARIA_PORT}/api/card/{self.card_id} \\\n"
            f"       -H 'Content-Type: application/json' \\\n"
            f"       -d '{{\"column\": \"review\"}}'\n"
        )

        try:
            if self.worker_type == "claude-code":
                self.pid = self._spawn_claude_code(ctx_path, goal)
            elif self.worker_type == "codex":
                self.pid = self._spawn_codex(ctx_path, goal)
            else:
                self.pid = self._spawn_hermes(ctx_path, goal)

            self.process = _ProcessWrapper(self.pid)
            print(f"[runner] Spawned {self.worker_type} for card {self.card_id}, PID {self.pid}")
            return True
        except FileNotFoundError:
            print(f"[runner] Binary not found for worker type: {self.worker_type}")
            return False
        except Exception as e:
            print(f"[runner] Failed to spawn {self.worker_type}: {e}")
            return False

    def is_done(self) -> bool:
        """Check if the worker process has exited."""
        if self.process is None:
            return True
        return self.process.poll() is not None

    def cleanup(self):
        """Called when the worker is done. Logs completion."""
        if self.process is None:
            return
        returncode = self.process.poll()
        elapsed = 0
        if self.started_at:
            try:
                started = datetime.fromisoformat(self.started_at)
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            except Exception:
                pass

        print(f"[runner] Worker {self.worker_type} for {self.card_id} done. "
              f"PID {self.pid}, exit={returncode}, elapsed={elapsed:.0f}s")


class _ProcessWrapper:
    """Wrapper for a subprocess.Popen that works even if the process isn't our child."""
    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        """Return exit code if process has exited, None if still running.
        
        Uses waitpid with WNOHANG to avoid leaving zombies — unlike os.kill(pid, 0)
        which only checks if the PID is in the process table (zombies still count as alive).
        """
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == 0:
                return None  # still running
            # Normal exit: status & 0x7F == 0, signald exit: otherwise
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                return -os.WTERMSIG(status)
            return status
        except ChildProcessError:
            return 0  # already reaped / doesn't exist

    def kill(self):
        try:
            os.kill(self.pid, 9)
        except ProcessLookupError:
            pass


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def _run_ci_tests(card_id: str, tests: dict) -> tuple:
    """Run the CI test command for a card. Returns (passed: bool, output: str)."""
    command = tests.get("command", "")
    pass_if = tests.get("pass_if", "exit_0")

    if not command:
        return True, "(no command configured)"

    print(f"[runner] Running CI tests for card {card_id}: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr).strip()
        passed = result.returncode == 0 if pass_if == "exit_0" else result.returncode == 0
        print(f"[runner] CI tests for {card_id}: exit={result.returncode}, passed={passed}")
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Test command timed out after 300 seconds."
    except Exception as e:
        return False, f"Test command failed to run: {e}"


def handle_worker_done(worker: Worker, success: bool = True):
    """Called when a worker finishes. Updates the card and advances the pipeline."""
    worker.cleanup()

    card_id = worker.card_id

    # Log completion note
    elapsed = 0
    if worker.started_at:
        try:
            started = datetime.fromisoformat(worker.started_at)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        except Exception:
            pass

    note = f"[runner] Worker {worker.worker_type} finished for card #{card_id}. "
    note += f"Elapsed: {elapsed:.0f}s."
    api_note(card_id, note, author="runner")

    # Log cost entry (tokens/cost populated by agent itself if known; runner logs 0 as a timestamp marker)
    api_cost(card_id, agent=worker.worker_type, tokens=0, cost_usd=0.0)

    # Advance to next column
    next_col = _get_next_column(worker.col_config["id"])
    if not next_col:
        return

    # CI gate: when advancing to review, run tests if configured
    if next_col == "review":
        card = api_get(card_id)
        tests = card.get("tests") if card else None
        if tests and isinstance(tests, dict) and tests.get("command"):
            api_patch(card_id, {"column": "review"})
            api_note(card_id, "Moved to review by pipeline runner.", author="runner")
            notify(f"🔬 Card #{card_id} entered *review* — running CI tests...")

            passed, output = _run_ci_tests(card_id, tests)

            if passed:
                api_patch(card_id, {"column": "done"})
                api_note(card_id, f"[CI] Tests passed. Auto-advanced to done.\n```\n{output[:2000]}\n```", author="runner")
                notify(f"✅ Card #{card_id} CI passed — moved to *done*")
            else:
                api_patch(card_id, {"column": "in_progress"})
                api_note(card_id, f"[CI] Tests failed. Moved back to in_progress.\n```\n{output[:2000]}\n```", author="runner")
                notify(f"❌ Card #{card_id} CI failed — moved back to *in_progress*")
            return

    api_patch(card_id, {"column": next_col})
    api_note(card_id, f"Moved to {next_col} by pipeline runner.", author="runner")
    notify(f"✅ Card #{card_id} moved to *{next_col}*")


def _get_next_column(current_col: str) -> Optional[str]:
    """Map column flow. Groom → Ready, In Progress → Review, Review → Done."""
    flow = {
        "spec": "groom",
        "groom": "ready",
        "in_progress": "review",
        "review": "done",
    }
    return flow.get(current_col)


# ── Main loop ────────────────────────────────────────────────────────────────

class PipelineRunner:
    def __init__(self):
        self.active_workers: dict[str, Worker] = {}  # card_id → Worker
        self.last_poll = 0

    def _dispatch_card(self, card: dict, col_config: dict):
        """Dispatch a worker for a card in a trigger column."""
        card_id = card["id"]

        # Skip if already being worked
        if card_id in self.active_workers:
            return

        # Skip if card already has an agent running
        if card.get("agent_session_id"):
            # Check if PID is still alive
            pid_str = card["agent_session_id"]
            try:
                pid = int(pid_str)
                try:
                    result = os.waitpid(pid, os.WNOHANG)
                    if result[0] == 0:
                        self.active_workers[card_id] = Worker(card_id, col_config, card, "")
                        self.active_workers[card_id].pid = pid
                        return  # still running
                except ChildProcessError:
                    pass  # PID dead / doesn't exist — continue to dispatch
            except ValueError:
                pass  # invalid PID string — continue to dispatch

        print(f"[runner] Dispatching {col_config.get('worker', 'hermes')} for card {card_id}: {card['title']}")

        # Draft context
        context = draft_context(col_config, card, TALARIA_HOME)
        worker = Worker(card_id, col_config, card, context)

        if not worker.spawn():
            api_note(card_id, f"[runner] Failed to spawn worker: {worker.worker_type}. Check binary paths.", author="runner")
            return

        self.active_workers[card_id] = worker

        # Update card with PID
        api_patch(card_id, {"agent_session_id": str(worker.pid)})
        api_note(card_id, f"[runner] {worker.worker_type} spawned (PID {worker.pid}) at {worker.started_at}", author="runner")
        notify(f"🤖 *{worker.worker_type}* dispatched: *{card['title']}*\nCard: #{card_id}")

    def _check_workers(self):
        """Poll active workers, handle completions."""
        done = []
        for card_id, worker in self.active_workers.items():
            if worker.is_done():
                handle_worker_done(worker)
                done.append(card_id)

        for card_id in done:
            del self.active_workers[card_id]

    def _run_done_summary(self, card: dict):
        """Append an [auto-summary] note when a card enters Done, if not already present."""
        if not AUTO_SUMMARY or not card.get("worktree_path"):
            return
        notes = card.get("status_notes", [])
        if any("[auto-summary]" in n.get("text", "") for n in notes):
            return  # already summarized
        summary = generate_auto_summary(card, "done")
        if summary:
            api_note(card["id"], f"[auto-summary] {summary}", author="runner")

    def _run_review_gate(self, card: dict):
        """Run tests for a card in Review. Advance to Done on pass, back to In Progress on fail."""
        card_id = card["id"]

        # Auto-summary on review entry (only once — skip if note already exists)
        if AUTO_SUMMARY and card.get("worktree_path"):
            notes = card.get("status_notes", [])
            if not any("[auto-summary]" in n.get("text", "") for n in notes):
                summary = generate_auto_summary(card, "review")
                if summary:
                    api_note(card_id, f"[auto-summary] {summary}", author="runner")

        tests = card.get("tests")

        if not tests:
            # No tests defined — auto-advance to Done
            api_patch(card_id, {"column": "done"})
            api_note(card_id, "[review-gate] No tests defined — auto-advancing to Done.", author="runner")
            notify(f"✅ Card #{card_id} passed review (no tests defined)")
            return

        command = tests.get("command")
        pass_if = tests.get("pass_if", "exit_0")

        if not command:
            api_note(card_id, "[review-gate] tests.command is missing — cannot run.", author="runner")
            return

        # Run the test command in the card's worktree or TALARIA_WORK_DIR
        worktree = card.get("worktree_path") or TALARIA_WORK_DIR
        worktree_path = Path(worktree) if worktree else Path(TALARIA_WORK_DIR)

        print(f"[runner] Running review tests for {card_id}: {command}")
        api_note(card_id, f"[review-gate] Running tests: `{command}`", author="runner")

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
                timeout=300,
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            api_note(card_id, f"[review-gate] TIMEOUT — test command exceeded 300s.", author="runner")
            api_patch(card_id, {"column": "in_progress"})
            notify(f"❌ Card #{card_id} review timed out (300s) — back to In Progress.")
            return
        except Exception as e:
            api_note(card_id, f"[review-gate] ERROR running tests: {e}", author="runner")
            api_patch(card_id, {"column": "in_progress"})
            notify(f"❌ Card #{card_id} review error: {e} — back to In Progress.")
            return

        passed = (exit_code == 0) if pass_if == "exit_0" else False
        output = (result.stdout + "\n" + result.stderr).strip()

        if passed:
            summary = output[:500] if output else "(no output)"
            api_note(card_id, f"[review-gate] ✅ Tests passed (exit {exit_code}). Output: {summary}", author="runner")
            api_patch(card_id, {"column": "done"})
            notify(f"✅ Card #{card_id} passed review — tests passed, moved to Done.")
        else:
            summary = output[:1000] if output else f"(exit {exit_code})"
            api_note(card_id, f"[review-gate] ❌ Tests FAILED (exit {exit_code}). Output:\n{output[:2000]}", author="runner")
            api_patch(card_id, {"column": "in_progress"})
            notify(f"❌ Card #{card_id} tests failed — back to In Progress. Output:\n{summary[:500]}")

    def run(self, poll_interval: int = POLL_INTERVAL, max_concurrent: int = MAX_CONCURRENT):
        print(f"[runner] Talaria Pipeline Runner started")
        print(f"[runner] TALARIA_HOME: {TALARIA_HOME}")
        print(f"[runner] TALARIA_WORK_DIR: {TALARIA_WORK_DIR}")
        print(f"[runner] Max concurrent: {max_concurrent}, poll interval: {poll_interval}s")
        print(f"[runner] Workers: hermes ({HERMES_BINARY}), claude-code ({CLAUDE_CODE_BINARY}), codex ({CODEX_BINARY})")

        running = True
        def signal_handler(sig, frame):
            nonlocal running
            print("[runner] Shutting down...")
            running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while running:
            # Check workers first
            self._check_workers()

            # Then try to dispatch new work
            board = api_board()
            if board:
                columns = {c["id"]: c for c in board.get("columns", [])}
                cards = board.get("cards", [])

                active_count = len(self.active_workers)

                for card in cards:
                    if active_count >= max_concurrent:
                        break

                    col_id = card.get("column")
                    col_config = columns.get(col_id, {})

                    # Review column: run CI gate if tests are defined
                    if col_id == "review":
                        self._run_review_gate(card)
                        continue

                    # Done column: append auto-summary if not already present
                    if col_id == "done":
                        self._run_done_summary(card)
                        continue

                    if col_config.get("trigger") == "agent_spawn":
                        self._dispatch_card(card, col_config)

            # Sleep between polls
            for _ in range(poll_interval):
                if not running:
                    break
                time.sleep(1)

        # Drain active workers on shutdown
        print(f"[runner] Waiting for {len(self.active_workers)} active workers to finish...")
        while self.active_workers:
            self._check_workers()
            time.sleep(1)
        print("[runner] Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Talaria Pipeline Runner")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL)
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT)
    args = parser.parse_args()

    runner = PipelineRunner()
    runner.run(poll_interval=args.poll_interval, max_concurrent=args.max_concurrent)
