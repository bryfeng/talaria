"""
Talaria triggers.py — Trigger system and side-effects.

Handles worktree creation, GitHub integration, Telegram notifications,
webhooks, and the agent queue.
"""

import json
import os
import re
import subprocess
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple, Union

if TYPE_CHECKING:
    from flask import Flask

# Agent queue (shared with server.py API routes)
AGENT_QUEUE = Path(__file__).parent.parent.parent / "agent_queue.json"
AGENT_QUEUE_LOCK = threading.Lock()

# ── Import from board.py ───────────────────────────────────────────────────────

from talaria.board import (
    _load_card,
    _save_card,
    _load_board,
    _save_board,
    _repo_dir,
    _log,
    _append_log,
    _slugify,
    LOG_FILE,
)


# ── Worktree management ───────────────────────────────────────────────────────

def _create_worktree(card: dict) -> None:
    """Create a git worktree when a card enters In Progress.

    Uses the repo path from the card's 'repo' field (via talaria.config.json),
    falling back to BASE_DIR. Idempotent if the worktree already exists.
    """
    card_id = card["id"]
    slug = _slugify(card.get("title", card_id))
    branch_name = f"{card_id}-{slug}"
    repo_path = _repo_dir(card)
    worktree_path = repo_path / f"{card_id}-{slug}"
    base_branch = card.get("base_branch", "main")

    # Check if this exact worktree already exists
    try:
        wt_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        existing_paths = []
        for line in wt_list.stdout.splitlines():
            if line.startswith("worktree "):
                existing_paths.append(line.split(" ", 1)[1].strip())
    except Exception:
        existing_paths = []

    if str(worktree_path) in existing_paths:
        # Already exists — just link it to the card
        card["worktree_path"] = str(worktree_path)
        card["branch_name"] = branch_name
        print(f"[talaria] Worktree already exists, linked to card: {worktree_path}")
        return

    try:
        # Check if branch already exists
        existing = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        if existing.stdout.strip():
            # Branch exists — add worktree pointing to it
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=str(repo_path), capture_output=True, text=True,
            )
            if result.returncode != 0:
                # Worktree path already used by another worktree
                print(f"[talaria] Worktree path already in use for {card_id}: {result.stderr.strip()}")
                return
        else:
            # New branch + worktree
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base_branch],
                cwd=str(repo_path), check=True, capture_output=True, text=True,
            )
        card["worktree_path"] = str(worktree_path)
        card["branch_name"] = branch_name
        print(f"[talaria] Worktree created: {worktree_path} (branch: {branch_name})")
    except subprocess.CalledProcessError as e:
        print(f"[talaria] Worktree creation failed for {card_id}: {e.stderr}")
    except Exception as e:
        print(f"[talaria] Worktree creation error for {card_id}: {e}")


def _cleanup_worktree(card: dict) -> None:
    """Merge branch to main and delete worktree when a card enters Done."""
    branch_name = card.get("branch_name")
    worktree_path = card.get("worktree_path")
    if not branch_name:
        return

    repo_path = _repo_dir(card)

    try:
        # ── Get diff stat BEFORE merge (while branch changes are visible) ──
        diff_stat = _get_diff_stat(repo_path, branch_name)

        # ── Extract GitHub issue info ──
        gh_repo, gh_issue_num, _ = _get_github_issue_info(card)

        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name,
             "-m", f"Merge {branch_name} (talaria #{card['id']})"],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[talaria] Merge conflict for {branch_name}: {result.stderr}")
            return

        if worktree_path:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=str(repo_path), capture_output=True, text=True,
            )

        subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        print(f"[talaria] Worktree cleaned up: {branch_name}")

        # ── Close GitHub issue (non-blocking) ──
        gh_closed = False
        if gh_repo and gh_issue_num:
            gh_closed = _close_github_issue(gh_repo, gh_issue_num)

        # ── Clear worktree fields from card so they're not stale ──
        card["worktree_path"] = None
        card["branch_name"] = None
        card["agent_session_id"] = None

        # ── Send Telegram summary (non-blocking) ──
        _send_done_summary(card, diff_stat, gh_closed, repo_path)

    except Exception as e:
        print(f"[talaria] Worktree cleanup error for {card.get('id')}: {e}")


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _get_github_issue_info(card: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract GitHub repo, issue number, and issue URL from a card.
    Returns (repo_slug, issue_number, issue_url) or (None, None, None).

    Checks:
    1. github_issue frontmatter field (number or URL)
    2. github_issue_url frontmatter field
    3. URLs in the card body matching github.com/<owner>/<repo>/issues/<number>
    """
    # 1. Check github_issue field
    github_issue = card.get("github_issue") or card.get("issue")
    if github_issue:
        if isinstance(github_issue, int):
            repo = card.get("repo")
            return repo, str(github_issue), None
        # Could be a URL or a number as string
        issue_url = github_issue if github_issue.startswith("http") else None
        match = re.search(r"issues/(\d+)", str(github_issue))
        if match:
            issue_num = match.group(1)
            repo = card.get("repo")
            return repo, issue_num, issue_url

    # 2. Check github_issue_url field
    github_issue_url = card.get("github_issue_url") or card.get("issue_url")
    if github_issue_url:
        match = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", github_issue_url)
        if match:
            return match.group(1), match.group(2), github_issue_url

    # 3. Scan card body (description + log) for github issue URLs
    card_text = card.get("description", "") + "\n" + card.get("log", "")
    matches = re.findall(r"https?://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)", card_text)
    if matches:
        repo, num = matches[0]
        return repo, num, f"https://github.com/{repo}/issues/{num}"

    return None, None, None


def _get_diff_stat(repo_path: Path, branch_name: str) -> str:
    """Get git diff --stat for a branch vs main. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"main...{branch_name}"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _close_github_issue(repo: str, issue_number: str) -> bool:
    """Close a GitHub issue via gh CLI. Returns True on success."""
    try:
        comment = f"Closed via Talaria — delivered in commit."
        cmd = ["gh", "issue", "close", issue_number, "--repo", repo,
               "--comment", comment, "--reason", "completed"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"[talaria] Closed GitHub issue {repo}#{issue_number}")
            return True
        else:
            print(f"[talaria] gh issue close failed: {result.stderr.strip()}")
    except FileNotFoundError:
        print("[talaria] gh CLI not found — skipping issue close")
    except Exception as e:
        print(f"[talaria] GitHub issue close error: {e}")
    return False


def _create_github_issue(card: dict, column: dict, repo: str = None) -> None:
    """Create a GitHub issue when a card enters a column.

    Reads GITHUB_TOKEN and GITHUB_REPO from the environment (or accepts an
    explicit repo override from the column config).
    """
    token = os.getenv("GITHUB_TOKEN", "")
    repo = repo or os.getenv("GITHUB_REPO")
    if not token or not repo:
        print("[talaria] GitHub issue skipped: GITHUB_TOKEN or GITHUB_REPO not set")
        return

    title = card.get("title", "Untitled")
    description = card.get("description", "")
    col_name = column.get("name", column.get("id", ""))
    body_lines = []
    if description:
        body_lines.append(description)
        body_lines.append("")
    body_lines.append(f"**Talaria card:** `{card['id']}` → *{col_name}*")

    payload = {
        "title": title,
        "body": "\n".join(body_lines),
    }
    labels = [l for l in card.get("labels", []) if not l.startswith("priority:")]
    if labels:
        payload["labels"] = labels

    url = f"https://api.github.com/repos/{repo}/issues"
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"[talaria] GitHub issue created: {result.get('html_url')}")
    except Exception as e:
        print(f"[talaria] GitHub issue creation failed: {e}")


def _send_done_summary(card: dict, diff_stat: str, gh_closed: bool, repo_path: Path) -> None:
    """Send a rich Telegram summary when a card lands in Done."""

    def _send():
        try:
            lines = [f"✅ *Done: {card.get('title', card['id'])}*"]
            lines.append(f"`#{card['id']}`")

            # Diff stat
            if diff_stat:
                # Summarize: show just the summary line(s)
                diff_lines = diff_stat.strip().split("\n")
                for dl in diff_lines[-5:]:  # last 5 lines max
                    dl = dl.strip()
                    if dl:
                        lines.append(f"`{dl}`")

            # GitHub
            repo = card.get("repo")
            gh_issue = card.get("github_issue") or card.get("issue")
            if gh_issue:
                issue_ref = f"{repo}#{gh_issue}" if repo else f"#{gh_issue}"
                status = "🔒 Closed" if gh_closed else "⚠️ Could not close"
                lines.append(f"{status} {issue_ref}")
            elif repo:
                lines.append(f"📁 Repo: `{repo}`")

            msg = "\n".join(lines)
            _notify_telegram(msg)
        except Exception as e:
            print(f"[talaria] Done summary Telegram failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


# ── Notifications ──────────────────────────────────────────────────────────────

def _notify_telegram(msg: str) -> None:
    """Send a Telegram message via bot API. Non-blocking."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (os.getenv("TELEGRAM_HOME_CHANNEL_ID") or os.getenv("TELEGRAM_HOME_CHANNEL") or "").lstrip("@")
    if not token or not chat_id:
        return
    try:
        def _send():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                print(f"[talaria] Telegram notify failed: {e}")
        threading.Thread(target=_send, daemon=True).start()
    except Exception as e:
        print(f"[talaria] Telegram notify setup failed: {e}")


def _fire_webhook(url: str, card: dict, column: dict) -> None:
    """POST card data to a webhook URL when a card enters a column."""
    payload = {
        "event": "card.moved",
        "column": {"id": column["id"], "name": column["name"]},
        "card": card,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    headers.update(column.get("webhook_headers") or {})
    req = urllib.request.Request(url, data=data, headers=headers)

    def _send():
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[talaria] Webhook fired: {url}")
        except Exception as e:
            print(f"[talaria] Webhook failed ({url}): {e}")
    threading.Thread(target=_send, daemon=True).start()


# ── Agent queue ────────────────────────────────────────────────────────────────

def _queue_agent(card: dict) -> None:
    """Write card to agent queue so a watcher/cron can dispatch an agent."""
    with AGENT_QUEUE_LOCK:
        queue = []
        if AGENT_QUEUE.exists():
            with open(AGENT_QUEUE) as f:
                queue = json.load(f)
        queue.append({
            "card": card,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        })
        with open(AGENT_QUEUE, "w") as f:
            json.dump(queue, f, indent=2)


# ── Trigger dispatcher ─────────────────────────────────────────────────────────

def _trigger_action(column: dict, card: dict, board: dict) -> None:
    """Fire side-effects when a card enters a trigger column."""
    trigger = column.get("trigger")
    col_id = column["id"]
    col_name = column["name"]

    if col_id == "in_progress":
        _create_worktree(card)
    elif col_id == "done":
        _cleanup_worktree(card)
        # Always clear agent_session_id when entering done
        card["agent_session_id"] = None

    if not trigger:
        return

    if trigger == "agent_spawn":
        _queue_agent(card)
        _notify_telegram(f"🤖 Agent dispatched: *{card['title']}* moved to *{col_name}*")
    elif trigger == "notify":
        _notify_telegram(f"📋 Card moved to *{col_name}*: *{card['title']}*")
    elif trigger == "webhook":
        webhook_url = column.get("webhook_url")
        if webhook_url:
            _fire_webhook(webhook_url, card, column)
        else:
            print(f"[talaria] webhook trigger on column '{col_id}' but no webhook_url configured")
    elif trigger == "github_issue":
        _create_github_issue(card, column, repo=column.get("github_repo"))

    # Fire webhook as side-effect on any column that has webhook_url set
    if trigger != "webhook":
        webhook_url = column.get("webhook_url")
        if webhook_url:
            _fire_webhook(webhook_url, card, column)

    # Fire GitHub issue as side-effect on any column that has github_repo set
    if trigger != "github_issue":
        github_repo = column.get("github_repo")
        if github_repo:
            _create_github_issue(card, column, repo=github_repo)

    _log("trigger_fired", card, to_col=col_id)
