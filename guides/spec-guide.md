# Spec Guide — Writing a Card Specification

Your job: read the card title and description, then write a detailed **SPEC.md** section directly into the card file. Append to the card's existing content.

## What a Good Spec Looks Like

A spec is a complete blueprint for implementation. It answers:

1. **What** is being built (concisely)
2. **Why** it matters (user or system need)
3. **How** it works — behavior, inputs, outputs, edge cases
4. **Acceptance criteria** — concrete, testable conditions

## Spec Format

Append a new section to the card using this structure:

```markdown
## Spec

### Overview
One-paragraph summary of the feature.

### Behavior
- Explicit list of behaviors, interactions, and side effects
- State transitions described explicitly

### API / Interface
- For HTTP endpoints: method, path, request/response shape
- For CLI tools: arguments, flags, exit codes
- For UI changes: component behavior, keyboard shortcuts

### Data Model
- New files, database tables, config fields
- File format and schema

### Edge Cases
- Error conditions and how they're handled
- Boundary conditions (empty input, large input, concurrent access)
- What happens on failure / partial success

### Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Each must be verifiable by a human or automated test
```

## Acceptance Criteria Rules

- Each criterion must be **verifiable** (pass/fail, not subjective)
- Each criterion should correspond to a **testable behavior**
- Use "the system does X" language, not "the system should do X"
- Do NOT write implementation details (no "iterate over the list", "use a dict")

## What to Do

1. Read the card's `description` field — this is the starting prompt
2. Read related context files if referenced
3. Research: read existing code in the worktree to understand patterns
4. Draft the spec section in your head first
5. Append it to the card using the API:

```bash
# Read current card state
curl http://localhost:8400/api/card/<card-id>

# Add spec section by updating the card description
curl -X PATCH http://localhost:8400/api/card/<card-id> \
  -H "Content-Type: application/json" \
  -d '{"description": "<existing description>\n\n## Spec\n\n<your spec content>"}'
```

Or use the CLI: `talaria context <card-id>` to read, then `talaria note <card-id> "Spec written."`

## Scope Assessment

After writing the spec, assess the implementation scope and add labels via the API:

**Criteria for `scope:large`** (add label if ANY are true):
- Touches more than 3 files
- Requires creating more than 2 new files
- Involves refactoring a file over 500 lines
- Has more than 5 acceptance criteria
- Estimated implementation time > 30 minutes

**Criteria for `scope:small`** (add label if ALL are true):
- Touches 1-3 files
- Clear, mechanical changes
- Estimated implementation time < 15 minutes

```bash
# Add scope label
curl -X PATCH http://localhost:8400/api/card/<card-id> \
  -H "Content-Type: application/json" \
  -d '{"labels": ["<existing-labels>", "scope:large"]}'
```

Also add `subsystems:N` and `component:*` labels to indicate how many modules are affected. These help Groom decide whether to decompose.

## When You're Done

Add a status note to the card:
```
POST /api/card/:id/note { "text": "Spec written. Review section appended to card.", "author": "<your-agent-name>" }
```

Then move the card to the next column:
```
PATCH /api/card/:id { "column": "groom" }
```
