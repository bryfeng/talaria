"""
Talaria board.py — Card and board file I/O.

Handles all reading/writing of cards/*.md files and board.json.
"""

import json
import os
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────────

def _project_root() -> Path:
    """Return the Talaria project root (parent of src/talaria)."""
    return Path(__file__).parent.parent.parent


BASE_DIR = _project_root()
CARDS_DIR = BASE_DIR / "cards"
BOARD_FILE = BASE_DIR / "board.json"
CONFIG_FILE = BASE_DIR / "talaria.config.json"
TALARIA_HOME = Path(os.getenv("TALARIA_HOME", os.path.expanduser("~/.talaria/talaria")))
LOG_FILE = BASE_DIR / "logs" / "talaria.log"


# ── config I/O ─────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load talaria.config.json from BASE_DIR or TALARIA_HOME."""
    for path in [CONFIG_FILE, TALARIA_HOME / "talaria.config.json"]:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    return {}


def _get_repos() -> list:
    """Return repos list from config (normalises old object format)."""
    config = _load_config()
    repos = config.get("repos", [])
    if isinstance(repos, dict):
        return [{"name": k, **v} for k, v in repos.items()]
    return repos


def _get_repo(name: str):
    """Return a repo entry by name, or None."""
    return next((r for r in _get_repos() if r.get("name") == name), None)


def _repo_dir(card: dict) -> Path:
    """Return the git repo root for a card, falling back to BASE_DIR."""
    repo_name = card.get("repo")
    if repo_name:
        repo = _get_repo(repo_name)
        if repo and repo.get("path"):
            return Path(repo["path"]).expanduser()
    return BASE_DIR


# ── card file I/O ───────────────────────────────────────────────────────────────

def _card_path(card_id: str) -> Path:
    return CARDS_DIR / f"{card_id}.md"


def _card_from_md(text: str) -> dict:
    """Parse YAML frontmatter + description + log from a card .md file."""
    if not text.strip():
        return {}

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_raw, rest = parts[1], parts[2]
            fm = yaml.safe_load(fm_raw) or {}
        else:
            fm = {}
            rest = text
    else:
        fm = {}
        rest = text

    rest = rest.strip()

    # Split description from ## Log section
    log_marker = rest.find("## Log")
    if log_marker != -1:
        description = rest[:log_marker].strip()
        log_text = rest[log_marker + len("## Log"):].strip()
    else:
        description = rest
        log_text = ""

    card = dict(fm)
    card["description"] = description

    # Parse status_notes — prefer frontmatter (handles multiline correctly)
    # Fall back to ## Log section for existing cards
    if "status_notes" in fm:
        card["status_notes"] = fm["status_notes"]
    else:
        # Legacy: parse ## Log section line-by-line (drops multiline content)
        status_notes = []
        for line in log_text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"\[([^\]]+)\]\s+\*\*([^*]+)\*\*:\s*(.*)", line)
            if m:
                ts_display, author, text = m.group(1), m.group(2), m.group(3)
                try:
                    dt = datetime.strptime(ts_display, "%Y-%m-%d %H:%M:%S")
                    ts = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                except Exception:
                    ts = ts_display
                status_notes.append({"ts": ts, "author": author, "text": text})
        card["status_notes"] = status_notes

    # Parse cost_log from frontmatter
    if "cost_log" in fm:
        card["cost_log"] = fm["cost_log"]
    else:
        card["cost_log"] = []

    return card


def _card_to_md(card: dict) -> str:
    """Serialize a card dict back to .md format with YAML frontmatter."""
    # Fields that go into frontmatter
    fm_keys = [
        "id", "title", "column", "priority", "assignee", "labels",
        "created_at", "updated_at", "worktree_path", "branch_name",
        "agent_session_id", "base_branch", "cost_log", "github_issue", "repo",
        "tests", "due_date",
    ]
    fm = {}
    for k in fm_keys:
        v = card.get(k)
        if v is not None and v != [] and v != "":
            fm[k] = v

    # Store notes in frontmatter as YAML list (handles multiline correctly)
    notes = card.get("status_notes", [])
    if notes:
        fm["status_notes"] = notes

    lines = []
    lines.append("---")
    lines.append(yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip())
    lines.append("---")
    lines.append("")

    desc = card.get("description", "").strip()
    if desc:
        lines.append(desc)
        lines.append("")

    return "\n".join(lines)


def _load_card(card_id: str) -> dict:
    """Load a single card from its .md file."""
    path = _card_path(card_id)
    if not path.exists():
        return None
    return _card_from_md(path.read_text())


def _save_card(card: dict) -> None:
    """Write a card dict to its .md file."""
    CARDS_DIR.mkdir(exist_ok=True)
    path = _card_path(card["id"])
    path.write_text(_card_to_md(card))


def _all_cards() -> list:
    """Load all cards from cards/*.md files."""
    CARDS_DIR.mkdir(exist_ok=True)
    cards = []
    for path in sorted(CARDS_DIR.glob("*.md")):
        card_id = path.stem
        card = _card_from_md(path.read_text())
        card["id"] = card_id  # ensure id matches filename
        cards.append(card)
    return cards


# ── board file I/O ─────────────────────────────────────────────────────────────

def _load_board() -> dict:
    """Load board config (meta + columns) from board.json."""
    with open(BOARD_FILE) as f:
        return json.load(f)


def _save_board(data: dict) -> None:
    """Save board config to board.json."""
    with open(BOARD_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _slim_card(card: dict) -> dict:
    """Strip heavy derived fields from a card for board-level reads."""
    slim = {k: v for k, v in card.items() if k != "status_notes"}
    return slim


def _full_board() -> dict:
    """Return the full board response: meta + columns + all cards (slim)."""
    board = _load_board()
    board["cards"] = [_slim_card(c) for c in _all_cards()]
    return board


# ── helpers ───────────────────────────────────────────────────────────────────

def _log(action: str, card: dict, from_col: str = None, to_col: str = None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "card_id": card.get("id"),
        "card_title": card.get("title"),
        "from_column": from_col,
        "to_column": to_col,
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _slugify(text: str) -> str:
    """Convert text to a lowercase hyphen-separated slug (max 40 chars)."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:40]
