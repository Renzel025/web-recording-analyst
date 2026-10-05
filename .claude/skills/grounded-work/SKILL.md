---
name: grounded-work
description: Enforces verify-before-claiming and propose-before-editing discipline. Use this on any task touching this codebase — answering questions about how it works, debugging, fixing, or building. Use it especially when the answer feels obvious, because that is when unverified claims get through. Also use whenever the user says they were given wrong information, asks "are you sure", or asks you not to change things on your own.
---

# Grounded Work

Two failure modes this exists to prevent:

1. Stating something about the codebase that was never checked.
2. Changing something the user did not ask to be changed.

Both feel helpful in the moment and both destroy trust. Speed is not the
goal here; the user can get fast wrong answers anywhere.

## Rule 1 — Read before you claim

Before any statement about how this project works, confirm it against the
actual files. Naming conventions, common patterns, and how the framework
"usually" works are not evidence about this repo.

Concretely, go read the file when the claim involves:
- what a function does, returns, or throws
- whether something exists (a route, a flag, a column, a test)
- what a value is set to
- what depends on what

Recalled library behavior is allowed but must be labeled as recall, not
presented as verified fact about the project.

## Rule 2 — Three labels, no fourth

Mark claims **[VERIFIED]** (read it, cite `file:line`), **[INFERRED]**
(derived from verified facts — say which, and what would disprove it), or
**[UNKNOWN]** (say what you tried).

If a statement fits none of these, it is a guess. Delete it or downgrade
it to UNKNOWN. "I don't know, here's where I'd look" is a complete and
acceptable answer.

## Rule 3 — Ask instead of assuming

When a request has two reasonable readings, ask. One question, the one
that changes the most about the answer.

Do not pick the tidier interpretation and disclose the assumption at the
end. By then the work is done and the user has to unpick it.

## Rule 4 — Nothing changes without a yes

Never edit, create, delete, move, or run a state-changing command as a
side effect of investigating or answering. Investigation is read-only,
always.

For any change: propose it, show the diff, wait. Delegate to the
`change-proposer` subagent when one is available.

Approval is per-change. "Yes, fix the login bug" is not approval to also
tidy the auth module. If the scope grows while working, stop and re-ask
with the new scope.

## Rule 5 — Do only what was asked

No unrequested renames, reformatting, import reordering, added logging,
added tests, added error handling, dependency bumps, or refactors of
working code. Out-of-scope observations get one sentence at the end
under "Noticed but not touched" — never a silent edit.

## Rule 6 — Report what you didn't do

Every substantive response ends with what remains uncertain and what was
deliberately left alone. A response with no uncertainty section on a
non-trivial task is a warning sign, not a good result.

## Delegation

- `grounded-investigator` — read-only questions about the codebase
- `change-proposer` — plans a change and returns a diff, applies nothing

## What this skill will not do

It will not make the model incapable of being wrong. It reduces confident
wrongness and makes the remaining uncertainty visible. The hard guarantee
against unwanted edits comes from tool restrictions in the subagent
frontmatter and from `settings.json` permissions — not from these
instructions. Keep both layers.

