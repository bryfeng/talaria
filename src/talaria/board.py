"""
Talaria board.py — Card and board file I/O.

Handles all reading/writing of cards/*.md files and board.json.
"""

import json
import os
import re
import shutil

import yaml
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────────

def _project_root() -> Path:
    """Return the Talaria project root (parent of src/talaria)."""
    return Path(__file__).parent.parent.parent


BASE_DIR = _project_root()
CARDS_DIR = BASE_DIR / "cards"
ARCHIVE_DIR = CARDS_DIR / "archive"
BOARD_FILE = BASE_DIR / "board.json"
CONFIG_FILE = BASE_DIR / "talaria.config.json"
TALARIA_HOME = Path(os.getenv("TALARIA_HOME", os.path.expanduser("~/.talaria/talaria")))
LOG_FILE = BASE_DIR / "logs" / "talaria.log"
DONE_CAP = 20
GRAPH_SCHEMA_VERSION = "1"
PRIMARY_TYPES = {"feature", "bugfix", "infra", "docs", "chore"}
DEFAULT_DOMAIN = "general"
DEFAULT_COMPONENT = "general"


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
    card.pop("cost_log", None)
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

    return card


def _card_to_md(card: dict) -> str:
    """Serialize a card dict back to .md format with YAML frontmatter."""
    # Fields that go into frontmatter
    fm_keys = [
        "id", "title", "column", "priority", "assignee", "labels",
        "created_at", "updated_at", "worktree_path", "branch_name",
        "agent_session_id", "base_branch", "github_issue", "repo",
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


def _archive_graph_file() -> Path:
    return ARCHIVE_DIR / "graph.jsonl"


def _extract_label_values(labels: list[str], prefix: str) -> list[str]:
    values = []
    for label in labels or []:
        if not isinstance(label, str):
            continue
        if label.startswith(prefix):
            value = label[len(prefix):].strip().lower()
            if value:
                values.append(value)
    return sorted(set(values))


def _primary_type(labels: list[str]) -> str:
    for label in labels or []:
        if isinstance(label, str) and label.lower() in PRIMARY_TYPES:
            return label.lower()
    return "chore"


def _build_archive_graph_entry(card: dict, archived_path: Path) -> dict:
    labels = card.get("labels", [])
    domains = _extract_label_values(labels, "domain:") or [DEFAULT_DOMAIN]
    components = _extract_label_values(labels, "component:") or [DEFAULT_COMPONENT]

    entry = {
        "schema": f"talaria.archive-graph.v{GRAPH_SCHEMA_VERSION}",
        "card_id": card.get("id"),
        "title": card.get("title", ""),
        "type": _primary_type(labels),
        "completed_at": card.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        "release": card.get("release") or None,
        "domains": domains,
        "components": components,
        "depends_on": _extract_label_values(labels, "depends:") + _extract_label_values(labels, "depends_on:"),
        "supersedes": _extract_label_values(labels, "supersedes:"),
        "touches": _extract_label_values(labels, "touch:"),
        "summary": (card.get("description") or "")[:240],
        "archived_path": str(archived_path.relative_to(BASE_DIR)) if archived_path.is_absolute() else str(archived_path),
    }
    return entry


def _append_archive_graph_entry(entry: dict) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = _archive_graph_file()
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _iter_archive_graph_entries() -> list[dict]:
    path = _archive_graph_file()
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return entries


def _history_query(
    q: str = "",
    domain: str | None = None,
    component: str | None = None,
    type_: str | None = None,
    release: str | None = None,
    limit: int = 20,
) -> list[dict]:
    q = (q or "").strip().lower()
    domain = (domain or "").strip().lower() or None
    component = (component or "").strip().lower() or None
    type_ = (type_ or "").strip().lower() or None
    release = (release or "").strip().lower() or None

    archived = list(_iter_archive_graph_entries())
    live_done = []
    for card in _all_cards():
        if card.get("column") != "done":
            continue
        live_done.append(
            {
                "schema": f"talaria.archive-graph.v{GRAPH_SCHEMA_VERSION}",
                "card_id": card.get("id"),
                "title": card.get("title", ""),
                "type": _primary_type(card.get("labels", [])),
                "completed_at": card.get("updated_at") or card.get("created_at"),
                "release": card.get("release") or None,
                "domains": _extract_label_values(card.get("labels", []), "domain:") or [DEFAULT_DOMAIN],
                "components": _extract_label_values(card.get("labels", []), "component:") or [DEFAULT_COMPONENT],
                "depends_on": _extract_label_values(card.get("labels", []), "depends:") + _extract_label_values(card.get("labels", []), "depends_on:"),
                "supersedes": _extract_label_values(card.get("labels", []), "supersedes:"),
                "touches": _extract_label_values(card.get("labels", []), "touch:"),
                "summary": (card.get("description") or "")[:240],
                "archived_path": None,
                "source": "done",
            }
        )

    rows = archived + live_done

    def match(row: dict) -> bool:
        if domain and domain not in row.get("domains", []):
            return False
        if component and component not in row.get("components", []):
            return False
        if type_ and row.get("type") != type_:
            return False
        if release and str(row.get("release") or "").lower() != release:
            return False
        if q:
            hay = " ".join(
                [
                    str(row.get("card_id", "")),
                    str(row.get("title", "")),
                    str(row.get("summary", "")),
                    " ".join(row.get("domains", [])),
                    " ".join(row.get("components", [])),
                ]
            ).lower()
            return q in hay
        return True

    rows = [r for r in rows if match(r)]
    rows.sort(key=lambda r: r.get("completed_at") or "", reverse=True)
    return rows[: max(1, limit)]


def _archive_excess_done_cards(done_cap: int = DONE_CAP) -> list[str]:
    """Archive oldest Done cards to keep Done as a rolling operational window.

    Archive is file-based (cards/archive/*.md) with graph index updates.
    Returns a list of archived card IDs.
    """
    done_cards = [c for c in _all_cards() if c.get("column") == "done"]
    overflow = len(done_cards) - done_cap
    if overflow <= 0:
        return []

    done_cards.sort(key=lambda c: (c.get("updated_at") or c.get("created_at") or ""))
    to_archive = done_cards[:overflow]

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived_ids: list[str] = []

    for card in to_archive:
        card_id = card.get("id")
        if not card_id:
            continue
        src = _card_path(card_id)
        if not src.exists():
            continue

        dst = ARCHIVE_DIR / src.name
        if dst.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            dst = ARCHIVE_DIR / f"{card_id}-{stamp}.md"

        shutil.move(str(src), str(dst))
        _append_archive_graph_entry(_build_archive_graph_entry(card, dst))
        _log("archived", card, from_col="done", to_col="archive")
        archived_ids.append(card_id)

    return archived_ids


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
