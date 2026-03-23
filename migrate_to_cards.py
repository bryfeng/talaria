"""
Migration: Split kanban.json cards into individual cards/{id}.md files.
Reads kanban.json, writes one .md per card, then rewrites board.json.
"""
import json, os, yaml
from pathlib import Path
from datetime import datetime

SRC = Path("/Users/bryanfeng/talaria/kanban.json")
CARDS_DIR = Path("/Users/bryanfeng/talaria/cards")
OUT_BOARD = Path("/Users/bryanfeng/talaria/board.json")

with open(SRC) as f:
    data = json.load(f)

# ── Write each card as a .md file ──────────────────────────────────────────────
CARDS_DIR.mkdir(exist_ok=True)

for card in data["cards"]:
    card_id = card["id"]

    # Build frontmatter (all card fields except description + status_notes)
    fm = {
        "id": card["id"],
        "title": card.get("title", ""),
        "column": card.get("column", "backlog"),
        "priority": card.get("priority", "medium"),
        "assignee": card.get("assignee", ""),
        "labels": card.get("labels", []),
        "created_at": card.get("created_at", ""),
        "updated_at": card.get("updated_at", ""),
        "worktree_path": card.get("worktree_path"),
        "branch_name": card.get("branch_name"),
        "agent_session_id": card.get("agent_session_id"),
        "base_branch": card.get("base_branch", "main"),
        "cost_log": card.get("cost_log", []),
        "github_issue": card.get("github_issue"),
    }
    # Remove None values
    fm = {k: v for k, v in fm.items() if v is not None and v != [] and v != ""}

    lines = []
    lines.append("---")
    lines.append(yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip())
    lines.append("---")
    lines.append("")

    # Description
    desc = card.get("description", "").strip()
    if desc:
        lines.append(desc)
        lines.append("")
    lines.append("")
    lines.append("## Log")
    lines.append("")

    # Log entries (status_notes)
    for note in card.get("status_notes", []):
        ts = note.get("ts", "")
        author = note.get("author", "user")
        text = note.get("text", "")
        # Format timestamp nicely
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_display = ts
        lines.append(f"[{ts_display}] **{author}**: {text}")

    md_content = "\n".join(lines)

    out_path = CARDS_DIR / f"{card_id}.md"
    out_path.write_text(md_content)
    print(f"  Wrote {out_path.name}  ({len(md_content)} bytes)")

# ── Write board.json (meta + columns only, no cards/activity_log) ─────────────
board_out = {
    "meta": data.get("meta", {}),
    "columns": data.get("columns", []),
}

with open(OUT_BOARD, "w") as f:
    json.dump(board_out, f, indent=2, ensure_ascii=False)
print(f"\nWrote {OUT_BOARD.name}")

# ── Backup original kanban.json ────────────────────────────────────────────────
backup = SRC.with_suffix(".json.bak")
import shutil
shutil.copy2(SRC, backup)
print(f"Backed up original → {backup.name}")

print("\nMigration complete!")
print(f"  Cards directory: {CARDS_DIR}")
print(f"  Board file: {OUT_BOARD}")
print(f"  Original backup: {backup}")
