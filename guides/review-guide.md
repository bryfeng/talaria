# Review Guide — Final Review Before Merge

Your job: perform a final review before the card's branch is merged into main.

## What to Check

### Correctness
- Does the implementation match the spec in the card?
- Are all acceptance criteria met?
- Are there any edge cases not covered?

### Code Quality
- No obvious bugs or security issues
- Clear variable and function names
- Appropriate error handling
- No commented-out code or TODO comments left in

### Testing
- Tests cover the new behavior
- All existing tests still pass: `pytest -v`
- No test that passes only because it mocks too much

### Architecture
- Does this change the data model? Update `docs/architecture.md` if so.
- Does this add a new dependency? Justify it.
- Does this change any API contract? Update relevant documentation.

## CI Gate

If the card has a `tests.command` field, the CI gate will run it automatically when the card enters the Review column:

- **Pass** → card advances to Done, branch is merged
- **Fail** → card reverts to In Progress, agent is notified

Do NOT manually advance a card to Done if the CI gate has not passed.

## What to Do

1. Run the full test suite: `pytest -v`
2. If tests pass and code looks good:
   - Leave a review note via the API
   - The CI gate handles the final Done transition
3. If there are issues:
   - Add a detailed note via the API
   - Move the card back to In Progress

## When to Pass vs. Request Changes

**Pass if:**
- Implementation matches spec
- Tests pass
- No critical or important issues

**Request changes if:**
- Implementation diverges from spec
- Tests fail or are missing
- Security concern found
- Breaking change not discussed
