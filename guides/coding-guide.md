# Coding Guide — Implementing Features

Your job: implement the feature described in the card. You are working in a **git worktree** created specifically for this card. The worktree path is in the card's `worktree_path` field.

## Before You Start

1. Read the card spec section carefully
2. Read the relevant existing code to understand patterns
3. Identify which files you'll create/modify
4. Plan your approach before writing any code

## Repo Conventions

### Python (talaria package)
- Use `import` statements at the top; no inline imports
- Flask routes return JSON: `return jsonify(...), 200`
- Error responses: `return jsonify({"error": "..."}), 400`
- No external dependencies beyond what's in `requirements.txt`
- No `print()` for logging — use structured logging

### Frontend (static/index.html)
- Vanilla JS, no framework
- CSS classes follow BEM: `block__element--modifier`
- All state comes from `/api/board`; no local state duplication

### Configuration (board.json, talaria.config.json)
- Never commit real credentials or tokens
- Use environment variables for secrets
- Config files use camelCase for keys

## Testing

- Python: `pytest` tests in a `tests/` directory
- Test file naming: `test_<module>.py`
- Each test is self-contained: setup + exercise + assert
- Mock external HTTP calls; use real file I/O for local storage

## Commit Style

Make commits as you go. Each commit should be:
- **Atomic**: one logical change
- **Descriptive**: `add pytest fixture for card creation` not `fix stuff`
- **Tested**: tests pass before committing

```bash
git add <changed files>
git commit -m "brief present-tense description"
```

## What to Do

1. **Read** the card spec and existing code
2. **Plan** your file changes
3. **Implement** incrementally, committing as you go
4. **Test** each unit before moving on
5. **Final test** run before marking done:
   ```bash
   pytest -v
   ```
6. When done, move the card to the `review` column via the API.

## If You're Blocked

Add a detailed note explaining the blocker via the API, then signal for human intervention.
