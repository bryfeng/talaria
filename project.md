# Talaria — Project Overview

Talaria is a lightweight kanban board for agentic team coordination. It uses itself to build itself.

## Repos

This Talaria instance tracks the following repositories:

| Name | Path | Instance Config |
|------|------|-----------------|
| talaria | `/Users/bryanfeng/talaria` | `~/.talaria/talaria` |

Repos are configured in `talaria.config.json` (project root) under the `repos` array. Each entry has:
- `name` — short identifier used on cards and in the UI
- `path` — absolute path to the git repository
- `talaria_instance_path` — path to the per-project Talaria config directory

## How Repos Relate

Each card can be assigned to a repo via the `repo` field. When a card enters **In Progress**, Talaria creates a git worktree inside that repo's directory and branches off `base_branch`. When the card reaches **Done**, the branch is merged back and the worktree is removed.

Cards without a `repo` field default to the Talaria server's own repository.

## Adding a Repo

1. Add an entry to the `repos` array in `talaria.config.json`:
   ```json
   {
     "name": "my-project",
     "path": "/path/to/my-project",
     "talaria_instance_path": "~/.talaria/my-project"
   }
   ```
2. Restart the Talaria server — the new repo appears in the UI repo filter.
3. When creating a card, select the repo from the dropdown.

## Pipeline

```
Backlog → Spec → Groom → Ready → In Progress → Review → Done
```

- **Spec** and **Groom** trigger agent_spawn (claude-code by default)
- **In Progress** creates a git worktree in the card's repo
- **Done** merges the branch and removes the worktree
