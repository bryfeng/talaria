# Contributing to Talaria

Thank you for your interest in contributing! Talaria is an AI-native kanban where cards are autonomous coding agents. This guide explains how to set up your dev environment, run tests, and navigate the card pipeline.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/bryfeng/talaria.git
cd talaria

# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install dev dependencies
pip install pytest ruff

# Start the board server
python server.py

# In a separate terminal, start the agent watcher
python agent_watcher.py
```

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

- **Backlog**: Ideas and TODOs. Anyone can create cards here.
- **Spec**: A CLAUDE CODE agent writes a detailed SPEC.md for the card.
- **Groom**: The spec is reviewed. If it looks good, it moves forward.
- **Ready**: The card is queued and ready for implementation.
- **In Progress**: A CLAUDE CODE agent implements the feature. A git worktree is created for isolation.
- **Review**: Tests run automatically. On pass, the card advances to Done.
- **Done**: Work is merged, worktree is cleaned up.

**Don't edit `board.json` directly** — use the CLI or API to move cards:

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

When a card enters Spec, Groom, or In Progress, the `agent_watcher.py` polls the board and spawns a CLAUDE CODE agent. The agent:

1. Reads the card spec (`cards/<id>.md`)
2. Reads context guides for the current stage (`guides/talaria.md`, `guides/spec-guide.md`, etc.)
3. Implements or reviews the work in an isolated git worktree
4. Commits and pushes the work
5. Advances the card to the next column

You can monitor active agents via the Talaria web UI (pulsing orange dot on cards) or by checking `logs/talaria.log`.

## Getting Help

- Open an issue: https://github.com/bryfeng/talaria/issues
- Discussions: https://github.com/bryfeng/talaria/discussions
- Read the architecture docs: `docs/architecture.md`
