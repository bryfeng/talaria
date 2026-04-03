"""
Talaria Pipeline Runner — Watches the board and dispatches workers at each pipeline stage.

Reads column config (worker, context_files) from board.json columns via the /api/board endpoint.
Drafts context from TALARIA_HOME/agents/ files + card spec.
Spawns the right worker (hermes / claude-code / codex).
Tracks PIDs and advances cards.

Usage:
    python agent_watcher.py

Environment:
    TALARIA_HOME       — Path to Talaria instance home (default: ~/.talaria/talaria)
    TALARIA_PORT      — Board API port (default: 8400)
    TALARIA_WORK_DIR  — Repo working directory (default: ~/talaria)
    MAX_CONCURRENT    — Max simultaneous agents (default: 2)
    POLL_INTERVAL     — Seconds between board polls (default: 15)

Lock / health files (in TALARIA_HOME):
    .watcher.lock   — Contains the PID of the running watcher.  A second instance
                      checks this file on startup and exits with a warning if the
                      recorded PID is still alive.  Stale locks (dead PID) are
                      silently cleaned up and a fresh lock is written.
    .watcher.status — JSON health file updated on startup and removed on clean
                      shutdown.  Fields: pid, started_at, host.
"""

import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
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

ARCH_REFRESH_ENABLED = os.getenv("ARCH_REFRESH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
ARCH_REFRESH_INTERVAL_SEC = int(os.getenv("ARCH_REFRESH_INTERVAL_SEC", "600"))
ARCH_DOC_MAX_AGE_SEC = int(os.getenv("ARCH_DOC_MAX_AGE_SEC", str(14 * 24 * 3600)))
ARCH_REFRESH_DONE_COOLDOWN_SEC = int(os.getenv("ARCH_REFRESH_DONE_COOLDOWN_SEC", "21600"))
ARCH_REFRESH_TITLE = "Architecture Diagram (auto-refresh)"
ARCH_REFRESH_LABEL = "system:auto-arch-refresh"
ARCH_CORE_FILES = [
    "agent_watcher.py",
    "board.json",
    "src/talaria/server.py",
    "src/talaria/board.py",
    "src/talaria/triggers.py",
    "src/talaria/cli.py",
    "src/talaria/telegram_ui.py",
]
ARCH_DOC_FILES = ["docs/architecture.md", "docs/architecture.excalidraw.json"]


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def enforce_runner_target_separation() -> None:
    """Fail fast if watcher is configured to mutate its own checkout."""
    if _is_truthy(os.getenv("TALARIA_BYPASS_ALLOWED")):
        print("[guardrail] WARNING: TALARIA_BYPASS_ALLOWED=true — separation guard bypassed.")
        return

    runner_dir = Path(__file__).resolve().parent

    target_paths = [Path(TALARIA_WORK_DIR).expanduser().resolve()]
    for cfg in [runner_dir / "talaria.config.json", TALARIA_HOME / "talaria.config.json"]:
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text())
            repos = data.get("repos", [])
            if isinstance(repos, dict):
                repos = [{"name": k, **v} for k, v in repos.items()]
            for repo in repos:
                if isinstance(repo, dict) and repo.get("path"):
                    target_paths.append(Path(str(repo["path"])).expanduser().resolve())
        except Exception:
            continue

    for target in target_paths:
        if runner_dir == target:
            raise SystemExit(
                "[guardrail] runner/target path collision detected: "
                f"{runner_dir}. Run watcher from stable clone and target a different dev repo, "
                "or set TALARIA_BYPASS_ALLOWED=true for emergency-only bypass."
            )

# Agent binaries
HERMES_BINARY = os.getenv("HERMES_AGENT_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/run_agent.py"))
HERMES_PYTHON = os.getenv("HERMES_VENV_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python"))
CLAUDE_CODE_BINARY = os.getenv("CLAUDE_CODE_BINARY", "claude")
CODEX_BINARY = os.getenv("CODEX_BINARY", "codex")

# Agent config
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "1800"))  # 30 min default (legacy)
WORKER_TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", str(AGENT_TIMEOUT)))
WORKER_NO_OUTPUT_SEC = int(os.getenv("WORKER_NO_OUTPUT_SEC", "300"))  # 5 min silent = hung
WORKER_MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "2"))

# Prompt patterns that need auto-confirmation (y\n)
_PROMPT_PATTERNS = [
    re.compile(r'\[y/n\]\s*$', re.IGNORECASE),
    re.compile(r'\?\[y/n\]\s*$', re.IGNORECASE),
    re.compile(r'continue\?.*\[y/n\]', re.IGNORECASE),
    re.compile(r'do you want to proceed', re.IGNORECASE),
    re.compile(r'are you sure.*\(yes/no\)', re.IGNORECASE),
    re.compile(r'allow.*\?$', re.IGNORECASE),
]


# ── Single-instance lock ──────────────────────────────────────────────────────

_LOCK_FILE = TALARIA_HOME / ".watcher.lock"
_STATUS_FILE = TALARIA_HOME / ".watcher.status"


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a running process."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user


def acquire_watcher_lock() -> None:
    """Acquire the single-instance lock or exit with a warning.

    Reads .watcher.lock; if the recorded PID is still alive the process
    exits immediately.  Stale locks (dead PID) are cleaned up automatically.
    """
    if _LOCK_FILE.exists():
        try:
            existing_pid = int(_LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None

        if existing_pid and _pid_alive(existing_pid):
            print(
                f"[runner] ERROR: Another watcher is already running (PID {existing_pid}).\n"
                f"[runner] Lock file: {_LOCK_FILE}\n"
                f"[runner] To stop it: kill {existing_pid}\n"
                f"[runner] To force-start: delete {_LOCK_FILE} and retry."
            )
            raise SystemExit(1)

        # Stale lock — clean up silently
        print(f"[runner] Stale lock found (PID {existing_pid} is gone). Removing.")
        _LOCK_FILE.unlink(missing_ok=True)
        _STATUS_FILE.unlink(missing_ok=True)

    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.write_text(str(os.getpid()))


def write_status_file() -> None:
    """Write health/status JSON for external monitoring tools."""
    _STATUS_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
    }))


def release_watcher_lock() -> None:
    """Remove lock and status files on clean shutdown."""
    _LOCK_FILE.unlink(missing_ok=True)
    _STATUS_FILE.unlink(missing_ok=True)


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


def api_note(card_id: str, text: str, author: str = "runner") -> bool:
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/card/{card_id}/note"
        body = json.dumps({"text": text, "author": author}).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST"),
            timeout=10)
        return True
    except Exception as e:
        print(f"[runner] Failed to add note to {card_id}: {e}")
        return False


def api_create(data: dict) -> Optional[dict]:
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/card"
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[runner] Failed to create card: {e}")
        return None


def api_compact_queue() -> Optional[dict]:
    """Request queue compaction so stale entries don't loop forever."""
    try:
        url = f"http://localhost:{TALARIA_PORT}/api/agent_queue/compact"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[runner] Failed to compact agent queue: {e}")
        return None


def _find_open_arch_refresh_card(cards: list[dict]) -> Optional[dict]:
    for card in cards:
        if card.get("column") == "done":
            continue
        labels = card.get("labels", []) or []
        if ARCH_REFRESH_LABEL in labels:
            return card
        if card.get("title") == ARCH_REFRESH_TITLE:
            return card
    return None


def _parse_iso_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        norm = value.replace("Z", "+00:00")
        return datetime.fromisoformat(norm).timestamp()
    except Exception:
        return None


def _extract_arch_refresh_reason(card: dict) -> Optional[str]:
    text = card.get("description") or ""
    m = re.search(r"Auto-detected reason:\s*(.+)", text)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def _find_recent_done_arch_refresh_card(cards: list[dict], reason: str, now_ts: Optional[float] = None) -> Optional[dict]:
    if ARCH_REFRESH_DONE_COOLDOWN_SEC <= 0:
        return None

    now_ts = now_ts or time.time()
    for card in cards:
        if card.get("column") != "done":
            continue
        labels = card.get("labels", []) or []
        if ARCH_REFRESH_LABEL not in labels and card.get("title") != ARCH_REFRESH_TITLE:
            continue
        if _extract_arch_refresh_reason(card) != reason:
            continue

        ts = _parse_iso_ts(card.get("updated_at")) or _parse_iso_ts(card.get("created_at"))
        if ts is None:
            continue

        if now_ts - ts <= ARCH_REFRESH_DONE_COOLDOWN_SEC:
            return card
    return None


def _arch_refresh_card_payload(reason: str) -> dict:
    description = (
        "Regenerate architecture documentation because source architecture changed or docs became stale.\n\n"
        "Tasks:\n"
        "1) Run talaria-architecture skill to regenerate docs/architecture.md.\n"
        "2) Update docs/architecture.excalidraw.json to match current flows.\n"
        "3) Include note with changed components + validation steps.\n\n"
        f"Auto-detected reason: {reason}"
    )
    return {
        "title": ARCH_REFRESH_TITLE,
        "description": description,
        "column": "ready",
        "priority": "medium",
        "labels": [
            "type:docs",
            "domain:architecture",
            "component:architecture-diagram",
            ARCH_REFRESH_LABEL,
            "auto-next",
        ],
    }


def _architecture_refresh_reason(repo_root: Path, now_ts: Optional[float] = None) -> Optional[str]:
    now_ts = now_ts or time.time()

    doc_paths = [repo_root / rel for rel in ARCH_DOC_FILES]
    for p in doc_paths:
        if not p.exists():
            return f"missing_doc:{p.relative_to(repo_root)}"

    doc_mtime = min(p.stat().st_mtime for p in doc_paths)

    newest_core_mtime = 0.0
    newest_core_rel = None
    for rel in ARCH_CORE_FILES:
        path = repo_root / rel
        if not path.exists():
            continue
        mtime = path.stat().st_mtime
        if mtime > newest_core_mtime:
            newest_core_mtime = mtime
            newest_core_rel = rel

    if newest_core_mtime > doc_mtime:
        return f"core_newer:{newest_core_rel}"

    age_sec = now_ts - max(p.stat().st_mtime for p in doc_paths)
    if age_sec > ARCH_DOC_MAX_AGE_SEC:
        days = int(age_sec // 86400)
        return f"stale_docs:{days}d"

    return None


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
    lines.append("# Talaria — Worker Context")
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
        lines.append("## Instructions")
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
        self._popen: Optional[subprocess.Popen] = None  # actual Popen for newly spawned workers
        self._log_file = None
        self._output_thread: Optional[threading.Thread] = None
        self._stop_output = threading.Event()
        self._timed_out = False
        self.last_output_at: float = 0.0
        self.timeout_sec: int = WORKER_TIMEOUT_SEC
        self.no_output_sec: int = WORKER_NO_OUTPUT_SEC
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

    def _spawn_hermes(self, ctx_path: Path, goal: str) -> subprocess.Popen:
        cmd = [
            HERMES_PYTHON, HERMES_BINARY,
            "--query", goal,
            "--model", os.getenv("LLM_MODEL", "MiniMax-M2.7"),
            "--base-url", os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        ]
        return subprocess.Popen(cmd, cwd=self.work_dir, env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def _env(self) -> dict:
        """Get env dict for subprocess. Inherits parent environment as-is."""
        return os.environ.copy()

    def _spawn_claude_code(self, ctx_path: Path, goal: str) -> subprocess.Popen:
        col_id = self.col_config.get("id", "")
        # --print for read-only stages (spec, groom); full execution for implementation
        if col_id in ("spec", "groom"):
            cmd = [CLAUDE_CODE_BINARY, "--dangerously-skip-permissions", "--print", goal]
        else:
            cmd = [CLAUDE_CODE_BINARY, "--dangerously-skip-permissions", goal]
        return subprocess.Popen(cmd, cwd=self.work_dir, env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def _spawn_codex(self, ctx_path: Path, goal: str) -> subprocess.Popen:
        cmd = [CODEX_BINARY, goal]
        return subprocess.Popen(cmd, cwd=self.work_dir, env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

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
                self._popen = self._spawn_claude_code(ctx_path, goal)
            elif self.worker_type == "codex":
                self._popen = self._spawn_codex(ctx_path, goal)
            else:
                self._popen = self._spawn_hermes(ctx_path, goal)

            self.pid = self._popen.pid
            self.process = _ProcessWrapper(self.pid)
            self.last_output_at = time.time()
            self._start_output_thread()
            print(f"[runner] Spawned {self.worker_type} for card {self.card_id}, PID {self.pid}")
            return True
        except FileNotFoundError:
            print(f"[runner] Binary not found for worker type: {self.worker_type}")
            return False
        except Exception as e:
            print(f"[runner] Failed to spawn {self.worker_type}: {e}")
            return False

    def _start_output_thread(self):
        """Start a background thread that reads subprocess stdout and updates last_output_at."""
        def reader():
            try:
                if self._popen and self._popen.stdout:
                    for line in self._popen.stdout:
                        if self._stop_output.is_set():
                            break
                        self.last_output_at = time.time()
                        print(f"[{self.worker_type}/{self.card_id}] {line.rstrip()}")
            except Exception:
                pass
        self._output_thread = threading.Thread(target=reader, daemon=True, name=f"output-{self.card_id}")
        self._output_thread.start()

    def check_timeout(self) -> tuple:
        """Return (is_timed_out: bool, reason: str). Checks both overall timeout and no-output hang."""
        if self.is_done():
            return False, ""
        now = time.time()
        if self.started_at:
            try:
                started_ts = datetime.fromisoformat(self.started_at).timestamp()
                elapsed = now - started_ts
                if elapsed > self.timeout_sec:
                    return True, f"overall timeout exceeded ({int(elapsed)}s > {self.timeout_sec}s)"
            except Exception:
                pass
        if self._popen and self.last_output_at > 0 and self.no_output_sec > 0:
            silent_for = now - self.last_output_at
            if silent_for > self.no_output_sec:
                return True, f"no output for {int(silent_for)}s (hung, threshold={self.no_output_sec}s)"
        return False, ""

    def kill(self):
        """Terminate the worker process (SIGTERM then SIGKILL)."""
        self._stop_output.set()
        if self._popen:
            try:
                self._popen.terminate()
            except Exception:
                pass
            try:
                self._popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self._popen.kill()
                except Exception:
                    pass
        elif self.process:
            self.process.kill()

    def is_done(self) -> bool:
        """Check if the worker process has exited."""
        if self._popen is not None:
            return self._popen.poll() is not None
        if self.process is None:
            return True
        return self.process.poll() is not None

    def cleanup(self):
        """Called when the worker is done. Logs completion."""
        self._stop_output.set()
        rc_source = self._popen if self._popen is not None else self.process
        if rc_source is None:
            return
        returncode = rc_source.poll()
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


def _legacy_auto_transition(col_id: str) -> Optional[dict]:
    """Backward-compatible transition defaults when board.json has no policy."""
    mapping = {
        "spec": {"to": "groom", "when": "on_agent_success"},
        "groom": {"to": "ready", "when": "on_agent_success"},
        "in_progress": {"to": "review", "when": "on_agent_success"},
        "review": {"to": "done", "when": "on_checks_pass"},
    }
    return mapping.get(col_id)


def _get_auto_transition(col_config: dict) -> Optional[dict]:
    """Return normalized auto-transition policy from a column config."""
    policy = col_config.get("auto_transition")
    if isinstance(policy, dict) and policy.get("to"):
        return {
            "to": policy.get("to"),
            "when": policy.get("when", "on_agent_success"),
            "require": policy.get("require", []) or [],
            "on_fail": policy.get("on_fail"),
        }
    return _legacy_auto_transition(col_config.get("id", ""))


def _count_label_prefix(labels: list[str], prefix: str) -> int:
    return sum(1 for label in labels if isinstance(label, str) and label.startswith(prefix))


def _is_high_scope_card(labels: list[str]) -> bool:
    """Heuristic for cards that must be decomposed in Groom.

    High-scope signals:
      - label: scope:large
      - label: subsystems:<N> where N > 2
      - more than 2 component:* labels
    """
    if "scope:large" in labels:
        return True

    for label in labels:
        if not isinstance(label, str) or not label.startswith("subsystems:"):
            continue
        raw = label.split(":", 1)[1].strip().rstrip("+")
        try:
            if int(raw) > 2:
                return True
        except ValueError:
            continue

    return _count_label_prefix(labels, "component:") > 2


def _groom_decomposition_pass(card: dict) -> bool:
    """Require decomposition metadata for high-scope cards.

    Passes by default for normal-scope cards.
    For high-scope cards, require:
      - at least two child:* labels
      - explicit decomposed marker via label 'decomposed' or 'split:done'
    """
    labels = card.get("labels", []) or []
    if not _is_high_scope_card(labels):
        return True

    child_count = _count_label_prefix(labels, "child:")
    has_decomposed_marker = ("decomposed" in labels) or ("split:done" in labels)
    return child_count >= 2 and has_decomposed_marker


def _has_review_pass_note(card: dict) -> bool:
    """Return True when card has evidence of review gate pass."""
    notes = card.get("status_notes", []) or []
    for n in notes:
        text = n.get("text", "") if isinstance(n, dict) else str(n)
        norm = text.lower()
        if "[review-gate]" in norm and ("pass" in norm or "passed" in norm):
            return True
    return False


def _requirements_pass(card: dict, requirements: list[str]) -> bool:
    """Validate lightweight field/label/rule requirements.

    Requirement forms:
      - "field_name" or "field:field_name"
      - "label:some-label"
      - "rule:groom_decomposition"
    """
    for req in requirements:
        req = (req or "").strip()
        if not req:
            continue

        if req.startswith("label:"):
            want = req.split(":", 1)[1].strip()
            labels = card.get("labels", []) or []
            if want not in labels:
                return False
            continue

        if req.startswith("rule:"):
            rule_name = req.split(":", 1)[1].strip()
            if rule_name == "groom_decomposition":
                if not _groom_decomposition_pass(card):
                    return False
                continue
            if rule_name == "agent_work_done":
                notes = card.get("status_notes", []) or []
                has_finish = False
                for n in notes:
                    text = n.get("text", "") if isinstance(n, dict) else str(n)
                    norm = text.lower()
                    if "[runner]" in norm and "finished" in norm:
                        has_finish = True
                        break
                if not has_finish:
                    return False
                continue
            if rule_name == "review_passed":
                if not _has_review_pass_note(card):
                    return False
                continue
            return False

        field = req.split(":", 1)[1].strip() if req.startswith("field:") else req
        val = card.get(field)
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        if isinstance(val, (list, dict)) and not val:
            return False
    return True


def handle_worker_done(worker: Worker, success: bool = True):
    """Called when a worker finishes. Updates the card and advances by policy."""
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

    policy = _get_auto_transition(worker.col_config or {})
    if not policy or policy.get("when") != "on_agent_success":
        return

    card = api_get(card_id) or {"id": card_id}
    requirements = policy.get("require", []) or []
    if not _requirements_pass(card, requirements):
        on_fail = policy.get("on_fail")
        api_note(
            card_id,
            f"[runner] Transition requirements failed for {worker.col_config.get('id')} → {policy.get('to')}: {requirements}",
            author="runner",
        )
        if on_fail:
            api_patch(card_id, {"column": on_fail})
        return

    to_col = policy.get("to")
    if not to_col:
        return

    api_patch(card_id, {"column": to_col})
    api_note(card_id, f"Moved to {to_col} by pipeline runner policy.", author="runner")
    notify(f"✅ Card #{card_id} moved to *{to_col}*")


# ── Main loop ────────────────────────────────────────────────────────────────

class PipelineRunner:
    def __init__(self):
        self.active_workers: dict[str, Worker] = {}  # card_id → Worker
        self.retry_counts: dict[str, int] = {}  # card_id → retry count
        self.last_poll = 0
        self.last_arch_refresh_check = 0.0

    def _handle_timeout(self, worker: Worker, reason: str):
        """Kill a timed-out/hung worker, log the failure, and retry or escalate."""
        card_id = worker.card_id
        worker.kill()
        worker.cleanup()

        retry_count = self.retry_counts.get(card_id, 0) + 1
        self.retry_counts[card_id] = retry_count

        failure_note = (
            f"[runner] Worker {worker.worker_type} KILLED — {reason}. "
            f"Attempt {retry_count}/{WORKER_MAX_RETRIES}."
        )
        api_note(card_id, failure_note, author="runner")
        print(f"[runner] {failure_note}")

        if retry_count >= WORKER_MAX_RETRIES:
            escalate_note = (
                f"[runner] Max retries ({WORKER_MAX_RETRIES}) exceeded. "
                f"Escalating card to 'blocked'. Last failure: {reason}"
            )
            moved = api_patch(card_id, {"column": "blocked"})
            if not moved:
                # 'blocked' column may not exist; fall back to 'ready' with a strong note
                api_patch(card_id, {"column": "ready"})
                escalate_note += " (could not find 'blocked' column — returned to 'ready')"
            api_note(card_id, escalate_note, author="runner")
            notify(f"🚨 Card #{card_id} ESCALATED after {retry_count} failed attempts: {reason}")
            del self.retry_counts[card_id]
        else:
            api_patch(card_id, {"column": "ready"})
            retry_note = (
                f"[runner] Card moved back to 'ready' for retry "
                f"{retry_count}/{WORKER_MAX_RETRIES}. Failure: {reason}"
            )
            api_note(card_id, retry_note, author="runner")
            notify(f"⚠️ Card #{card_id} timed out ({reason}) — retry {retry_count}/{WORKER_MAX_RETRIES}")

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
        """Poll active workers, handle completions and timeouts."""
        done = []
        for card_id, worker in self.active_workers.items():
            timed_out, reason = worker.check_timeout()
            if timed_out:
                print(f"[runner] Worker for {card_id} timed out: {reason}")
                self._handle_timeout(worker, reason)
                done.append(card_id)
            elif worker.is_done():
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

    def _maybe_queue_architecture_refresh(self, board: dict) -> None:
        """Create an architecture-refresh card when docs are stale or out-of-date."""
        if not ARCH_REFRESH_ENABLED:
            return

        repo_root = Path(TALARIA_WORK_DIR).expanduser()
        if not repo_root.exists():
            return

        reason = _architecture_refresh_reason(repo_root)
        if not reason:
            return

        cards = board.get("cards", []) or []
        existing = _find_open_arch_refresh_card(cards)
        if existing:
            return

        recent_done = _find_recent_done_arch_refresh_card(cards, reason)
        if recent_done:
            return

        created = api_create(_arch_refresh_card_payload(reason))
        if not created:
            return

        cid = created.get("id", "?")
        api_note(cid, f"[system] Auto-created architecture refresh card ({reason}).", author="***")
        notify(f"🏗️ Auto-created architecture refresh card #{cid} ({reason}).")

    def _run_review_gate(self, card: dict, col_config: Optional[dict] = None):
        """Run checks/tests for a card in Review. Advance on pass, fallback on fail."""
        card_id = card["id"]
        col_config = col_config or {"id": "review"}
        policy = _get_auto_transition(col_config) or {"to": "done", "when": "on_checks_pass", "require": [], "on_fail": "in_progress"}

        # Auto-summary on review entry (only once — skip if note already exists)
        if AUTO_SUMMARY and card.get("worktree_path"):
            notes = card.get("status_notes", [])
            if not any("[auto-summary]" in n.get("text", "") for n in notes):
                summary = generate_auto_summary(card, "review")
                if summary:
                    api_note(card_id, f"[auto-summary] {summary}", author="runner")

        requirements = policy.get("require", []) or []
        if not _requirements_pass(card, requirements):
            on_fail = policy.get("on_fail") or "in_progress"
            api_note(card_id, f"[review-gate] requirements failed: {requirements}", author="runner")
            if on_fail:
                api_patch(card_id, {"column": on_fail})
            return

        tests = card.get("tests")

        if not tests:
            # No tests defined — treat as review pass evidence first, then transition.
            # This aligns with server-side on_checks_pass policy, which requires a
            # [review-gate] pass note before review -> done is accepted.
            to_col = policy.get("to", "done")

            if not _has_review_pass_note(card):
                api_note(card_id, "[review-gate] passed: no tests defined", author="***")

            moved = api_patch(card_id, {"column": to_col})
            if moved:
                api_note(card_id, f"[review-gate] No tests defined — moved to {to_col}.", author="***")
                notify(f"✅ Card #{card_id} passed review (no tests defined)")
            else:
                api_note(card_id, f"[review-gate] No tests defined, but move to {to_col} was blocked.", author="***")
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
            on_fail = policy.get("on_fail") or "in_progress"
            api_note(card_id, "[review-gate] TIMEOUT — test command exceeded 300s.", author="runner")
            if on_fail:
                api_patch(card_id, {"column": on_fail})
            notify(f"❌ Card #{card_id} review timed out (300s) — back to {on_fail}.")
            return
        except Exception as e:
            on_fail = policy.get("on_fail") or "in_progress"
            api_note(card_id, f"[review-gate] ERROR running tests: {e}", author="runner")
            if on_fail:
                api_patch(card_id, {"column": on_fail})
            notify(f"❌ Card #{card_id} review error: {e} — back to {on_fail}.")
            return

        passed = (exit_code == 0) if pass_if == "exit_0" else False
        output = (result.stdout + "\n" + result.stderr).strip()

        if passed:
            summary = output[:500] if output else "(no output)"
            to_col = policy.get("to", "done")
            api_note(card_id, f"[review-gate] ✅ Tests passed (exit {exit_code}). Output: {summary}", author="runner")
            api_patch(card_id, {"column": to_col})
            notify(f"✅ Card #{card_id} passed review — tests passed, moved to {to_col}.")
        else:
            summary = output[:1000] if output else f"(exit {exit_code})"
            on_fail = policy.get("on_fail") or "in_progress"
            api_note(card_id, f"[review-gate] ❌ Tests FAILED (exit {exit_code}). Output:\n{output[:2000]}", author="runner")
            if on_fail:
                api_patch(card_id, {"column": on_fail})
            notify(f"❌ Card #{card_id} tests failed — back to {on_fail}. Output:\n{summary[:500]}")

    def run(self, poll_interval: int = POLL_INTERVAL, max_concurrent: int = MAX_CONCURRENT):
        acquire_watcher_lock()
        write_status_file()

        print("[runner] Talaria Pipeline Runner started")
        print(f"[runner] PID: {os.getpid()}, lock: {_LOCK_FILE}, status: {_STATUS_FILE}")
        print(f"[runner] TALARIA_HOME: {TALARIA_HOME}")
        print(f"[runner] TALARIA_WORK_DIR: {TALARIA_WORK_DIR}")
        print(f"[runner] Max concurrent: {max_concurrent}, poll interval: {poll_interval}s")
        print(f"[runner] Worker timeout: {WORKER_TIMEOUT_SEC}s, no-output timeout: {WORKER_NO_OUTPUT_SEC}s, max retries: {WORKER_MAX_RETRIES}")
        print(f"[runner] Arch refresh: enabled={ARCH_REFRESH_ENABLED}, interval={ARCH_REFRESH_INTERVAL_SEC}s, max_age={ARCH_DOC_MAX_AGE_SEC}s")
        print(f"[runner] Workers: hermes ({HERMES_BINARY}), claude-code ({CLAUDE_CODE_BINARY}), codex ({CODEX_BINARY})")

        compacted = api_compact_queue()
        if compacted:
            print(
                "[runner] Queue compacted on startup: "
                f"before={compacted.get('before')} after={compacted.get('after')} dropped={compacted.get('dropped')}"
            )

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

                now = time.time()
                if now - self.last_arch_refresh_check >= max(ARCH_REFRESH_INTERVAL_SEC, 1):
                    self._maybe_queue_architecture_refresh(board)
                    self.last_arch_refresh_check = now

                active_count = len(self.active_workers)

                for card in cards:
                    if active_count >= max_concurrent:
                        break

                    col_id = card.get("column")
                    col_config = columns.get(col_id, {})

                    # Review column: run checks/tests policy
                    if col_id == "review":
                        self._run_review_gate(card, col_config)
                        continue

                    # Done column: append auto-summary if not already present
                    if col_id == "done":
                        self._run_done_summary(card)
                        continue

                    # Worker-driven columns advance on on_agent_success.
                    if col_config.get("trigger") == "agent_spawn":
                        self._dispatch_card(card, col_config)
                        continue

                    # Rule-based columns advance on on_rule_pass.
                    policy = _get_auto_transition(col_config)
                    if policy and policy.get("when") == "on_rule_pass":
                        reqs = policy.get("require", []) or []
                        if _requirements_pass(card, reqs):
                            to_col = policy.get("to")
                            if to_col:
                                api_patch(card["id"], {"column": to_col})
                                api_note(card["id"], f"Moved to {to_col} by rule policy.", author="runner")
                                notify(f"➡️ Card #{card['id']} auto-moved to *{to_col}*")

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

        release_watcher_lock()
        print("[runner] Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Talaria Pipeline Runner")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL)
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT)
    args = parser.parse_args()

    enforce_runner_target_separation()
    runner = PipelineRunner()
    runner.run(poll_interval=args.poll_interval, max_concurrent=args.max_concurrent)
