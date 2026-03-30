# Changelog

All notable changes to Talaria are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-03-24

### Added

- **Pipeline columns**: Backlog → Spec → Groom → Ready → In Progress → Review → Done
- **Agent-native kanban**: Cards in Spec/Groom/In Progress auto-spawn AI workers
- **Git worktree isolation**: Each card gets its own branch and worktree
- **Branch-per-card**: `worktree_path` and `branch_name` tracked on every card
- **CI gate in Review**: Per-card `tests.command` — auto-advances on pass, reverts to In Progress on fail
- **Real-time polling**: Frontend refreshes every 10s; active agents show pulsing indicator
- **CLI**: `talaria create`, `talaria move`, `talaria list`, `talaria log`, `talaria context`, `talaria note`
- **Context guides**: Agent-specific prompts for Spec/Groom/Coding/Review stages
- **Markdown source of truth**: Cards are `cards/<id>.md` — git-tracked with full history
- **Groom context injection**: Priority, labels, and status notes auto-injected into groom agent prompts
- **Review CI gate**: Per-card `tests.command` with auto-advance on pass, revert on fail
- **Pytest suite**: Full test coverage for API, triggers, CLI, and board state
- **GitHub Actions CI**: Automated test runner on push and pull requests
- **pyproject.toml**: Proper package layout, `pip install -e .` support, `talaria-server` entry point
- **MIT License**, **CONTRIBUTING.md**, **CODE_OF_CONDUCT.md**, **SECURITY.md**
- **Docker + docker-compose**: Production-ready multi-stage Dockerfile
- **README overhaul**: Badges, comparison table, quick start, architecture diagram

[unreleased]: https://github.com/bryfeng/talaria/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bryfeng/talaria/releases/tag/v0.1.0
