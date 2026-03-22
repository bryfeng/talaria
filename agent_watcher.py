"""
Talaria Agent Watcher — Watches agent_queue.json and spawns AI agents
for cards that need work. Run alongside server.py or as a separate process.
"""

import json
import os
import sys
import time
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
KANBAN_FILE = BASE_DIR / "kanban.json"
QUEUE_FILE = BASE_DIR / "agent_queue.json"

# Agent configuration — override with env vars
HERMES_AGENT = os.getenv("HERMES_AGENT_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/run_agent.py"))
HERMES_VENV = os.getenv("HERMES_VENV_PATH",
    os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python"))
WORK_DIR = os.getenv("TALARIA_WORK_DIR", os.path.expanduser("~"))
KANBAN_PORT = int(os.getenv("TALARIA_PORT", os.getenv("KANBAN_PORT", 8400)))
MAX_CONCURRENT = int(os.getenv("TALARIA_MAX_CONCURRENT", "2"))

def api(path):
    try:
        with urllib.request.urlopen(f"http://localhost:{KANBAN_PORT}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None

def notify(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_HOME_CHANNEL_ID") or os.getenv("TELEGRAM_HOME_CHANNEL", "").lstrip("@")
    if not token or not chat_id:
        print(f"[watcher] notify (no telegram creds): {msg}")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[watcher] Telegram error: {e}")

def pop_queue():
    """Remove the first item from the agent queue."""
    if not QUEUE_FILE.exists():
        return None
    with open(QUEUE_FILE) as f:
        queue = json.load(f)
    if not queue:
        return None
    item = queue.pop(0)
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)
    return item

def get_agent_instructions(card: dict) -> str:
    """Build the prompt/instructions for the agent based on the card."""
    title = card.get("title", "Untitled")
    description = card.get("description", "")
    card_id = card.get("id", "")

    instructions = f"""You have been assigned a task from Talaria.

CARD: {title}
ID: #{card_id}

DESCRIPTION:
{description if description else "(No description provided)"}

WORKING DIRECTORY: {WORK_DIR}

GUIDELINES:
- Read the card description carefully and complete the task.
- Use git to commit your work with a clear message: "talaria #{card_id}: {title}"
- When done, add a note to the card via the Talaria API:
  curl -X POST http://localhost:{KANBAN_PORT}/api/card/{card_id}/note \\
    -H "Content-Type: application/json" \\
    -d '{{"text": "Task completed. Summary of work done.", "author": "agent"}}'
- Then move the card to "done" via:
  curl -X PATCH http://localhost:{KANBAN_PORT}/api/card/{card_id} \\
    -H "Content-Type: application/json" \\
    -d '{{"column": "done"}}'
- Report back with a summary of what you accomplished.

Start now.
"""
    return instructions

def spawn_agent(instructions: str) -> int:
    """Spawn a subagent with the given instructions. Returns PID."""
    cmd = [
        HERMES_VENV, HERMES_AGENT,
        "--goal", instructions,
        "--platform", "local",
        "--quiet",
    ]
    env = os.environ.copy()
    proc = subprocess.Popen(cmd, env=env, cwd=WORK_DIR)
    return proc.pid

def mark_card_in_progress(card_id: str, agent_pid: int):
    api_path = f"/api/card/{card_id}"
    resp = api(api_path)
    if resp:
        try:
            import urllib.request
            url = f"http://localhost:{KANBAN_PORT}{api_path}"
            data = json.dumps({
                "agent_session_id": str(agent_pid),
                "status_notes": [{
                    "id": card_id + "-spawn",
                    "text": f"Agent spawned (PID {agent_pid}) at {datetime.now(timezone.utc).isoformat()}",
                    "author": "watcher",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }]
            }).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            req.get_method = lambda: "PATCH"
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"[watcher] Failed to mark card in-progress: {e}")

def run(watch_interval: int = 15, dispatch_interval: int = 60, max_concurrent: int = None):
    if max_concurrent is None:
        max_concurrent = MAX_CONCURRENT

    print(f"[watcher] Talaria Agent Watcher started")
    print(f"[watcher] Checking queue every {watch_interval}s, dispatching every {dispatch_interval}s")
    print(f"[watcher] Max concurrent agents: {max_concurrent}")
    print(f"[watcher] Work directory: {WORK_DIR}")

    active_agents = 0
    last_dispatch = 0

    while True:
        now = time.time()

        # Check if we should dispatch
        if now - last_dispatch >= dispatch_interval and active_agents < max_concurrent:
            item = pop_queue()
            if item:
                card = item["card"]
                queued_at = item.get("queued_at", "unknown")
                print(f"[watcher] Dispatching agent for: {card['title']}")

                instructions = get_agent_instructions(card)
                pid = spawn_agent(instructions)
                mark_card_in_progress(card["id"], pid)

                notify(f"🤖 Agent dispatched for: *{card['title']}*\nPID: {pid}")
                active_agents += 1
                last_dispatch = now
                print(f"[watcher] Agent PID {pid} — {active_agents}/{max_concurrent} active")

        time.sleep(watch_interval)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Talaria Agent Watcher")
    parser.add_argument("--watch-interval", type=int, default=15, help="Queue check interval (seconds)")
    parser.add_argument("--dispatch-interval", type=int, default=60, help="Min seconds between dispatches")
    parser.add_argument("--max-concurrent", type=int, default=None, help="Max concurrent agents (default: from TALARIA_MAX_CONCURRENT env)")
    args = parser.parse_args()

    run(
        watch_interval=args.watch_interval,
        dispatch_interval=args.dispatch_interval,
        max_concurrent=args.max_concurrent,
    )
