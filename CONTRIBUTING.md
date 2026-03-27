# Contributing to Talaria

Thank you for your interest in contributing! Talaria is an AI-native kanban where cards are autonomous coding agents. This guide explains how to set up your dev environment, run tests, and navigate the card pipeline.

## Quick Start

```bash
git clone https://github.com/bryfeng/talaria.git
cd talaria
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

# Terminal 1: API server
TALARIA_WORK_DIR="$(pwd)" talaria-server

# Terminal 2: watcher
python agent_watcher.py
```

Open http://localhost:8400 and create/move cards via UI, CLI, or API.

## Running Tests

```bash
# Run the full test suite
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing

# Run specific test file
pytest tests/test_api.py -v
```

## The Card Pipeline

Cards move through these stages:

```
Backlog → Spec → Groom → Ready → In Progress → Review → Done
```

- **Backlog**: intake queue. Can auto-advance on rule pass.
- **Spec**: agent-spawn stage for planning/spec output.
- **Groom**: agent-spawn stage for refinement and readiness checks.
- **Ready**: staging queue; can auto-advance on rule pass.
- **In Progress**: implementation agent stage in isolated git worktree.
- **Review**: checks/test gate; transitions by policy (`on_checks_pass`, `on_fail`).
- **Done**: capped operational history (20); overflow auto-archives.

Talaria supports config-driven transitions via `board.json` per-column `auto_transition` rules.
Use CLI/API for card moves unless you are intentionally editing transition policy.

```bash
talaria list
talaria move <card-id> in_progress
talaria log <card-id>
```

## Branch Naming

Each card gets its own git worktree. The branch naming convention is:

```
<card-id>-<slug>
```

For example: `launch-001-pytest-ci`

## Submitting a Card (Pull Request Checklist)

Before marking a card Done, ensure:

- [ ] All tests pass (`pytest tests/`)
- [ ] New tests added for new behavior
- [ ] Architecture diagram updated if you changed the data model (`docs/architecture.md`)
- [ ] Changelog updated if user-facing behavior changed (`CHANGELOG.md`)
- [ ] No lint errors (`ruff check .`)

## Code Style

- **Python**: PEP 8, enforced by `ruff`
- **Formatting**: `ruff format .`
- **Linting**: `ruff check . --fix`
- **Pre-commit**: Run `ruff check .` before committing

## How Agents Work

When a card enters an `agent_spawn` column (commonly Spec, Groom, or In Progress), `agent_watcher.py` polls the board and spawns the configured worker (Hermes / Claude Code / Codex). The agent:

1. Reads the card spec (`cards/<id>.md`)
2. Reads context guides for the current stage (`guides/talaria.md`, `guides/spec-guide.md`, etc.)
3. Implements or reviews the work in an isolated git worktree
4. Commits and pushes the work
5. Advances the card to the next column

You can monitor active agents via the Talaria web UI (pulsing orange dot on cards) or by checking `logs/talaria.log`.

## Optional advanced operator topology

Most contributors should use the single-repo quick start above.
If you are operating Talaria in self-hosted production mode, use runner/target separation:
- runner (stable clone) executes server + watcher
- target (dev clone) receives agent mutations

This is enforced by Talaria self-hosting guardrails unless `TALARIA_BYPASS_ALLOWED=true`.

## Getting Help

- Open an issue: https://github.com/bryfeng/talaria/issues
- Discussions: https://github.com/bryfeng/talaria/discussions
- Read the architecture docs: `docs/architecture.md`
