# Groom Guide — Reviewing and Refining Specifications

Your job: review the card's spec section for completeness, clarity, and correctness. A "groomed" card is one where a competent engineer could implement it from the spec alone.

## What "Groomed" Means

A card is groomed when:
- The spec section exists and is complete
- Every behavior, edge case, and acceptance criterion is explicit
- There are no ambiguous or underspecified sections
- The acceptance criteria are independently verifiable

## Self-Review Checklist

Go through each section of the spec and check:

### Overview
- [ ] One clear paragraph explaining what and why
- [ ] No jargon or acronyms without definition
- [ ] Scope is clear — what's in, what's explicitly out

### Behavior
- [ ] All user-facing behaviors are described
- [ ] Side effects (file writes, API calls, state changes) are listed
- [ ] Happy path AND error paths are described
- [ ] No TODOs or placeholder language ("TBD", "handle this later")

### API / Interface
- [ ] All endpoints have method, path, and request/response shape
- [ ] All error responses have status codes and body shape
- [ ] CLI tools have all arguments and flags documented
- [ ] Exit codes are specified for CLI

### Data Model
- [ ] All new fields/tables/files are defined
- [ ] Schemas are concrete (not "a JSON object with relevant fields")
- [ ] File formats are specified

### Edge Cases
- [ ] Empty input handled
- [ ] Large input / overflow handled
- [ ] Concurrent access / race conditions considered
- [ ] Partial failure scenarios covered

### Acceptance Criteria
- [ ] Every criterion is verifiable (true/false, not "looks good")
- [ ] Criteria don't encode implementation details
- [ ] No criterion depends on a previous criterion being true first
- [ ] At least 3 criteria exist for non-trivial features

## Common Issues to Look For

| Issue | Fix |
|-------|-----|
| Vague language ("appropriately", "gracefully") | Replace with specific behavior |
| Missing error handling | Add explicit error case sections |
| Ambiguous scope ("related features") | Name exactly what's included |
| Implementation details in criteria | Remove "how"; keep only "what" |
| Untestable criteria ("intuitive UX") | Rewrite as measurable behavior |

## What to Do

1. Read the card's full content (description + existing spec section)
2. Walk through the checklist above
3. If spec is complete: add your checklist results as a note, move to `ready`
4. If spec needs changes: add specific, actionable notes describing what to add/fix

## When You're Done

Add a status note:
```bash
POST /api/card/:id/note { "text": "Groom complete. Spec verified: X criteria, no gaps found.", "author": "groom-agent" }
```

Move the card:
- **If spec is complete:** `PATCH /api/card/:id { "column": "ready" }`
- **If spec needs revision:** add a note describing the gaps, then `PATCH /api/card/:id { "column": "spec" }` to send back
