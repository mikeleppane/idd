---
name: lesson
description: Manually record a cross-feature trap as a lesson in `.forge/intel/lessons.md`. Use when /forge:review didn't auto-harvest the trap (e.g. lesson learned outside a feature, or the row resolved via a spec edit rather than a SHA). Dispatches the `forge-lesson` skill.
---

# /forge:lesson

## Behavior

Dispatch the `forge-lesson` skill, which walks the user through manual
lesson authoring: trap, avoidance, tag selection from the controlled
vocabulary, severity. Uses `Resolved by: manual` (the parser-accepted
literal for non-SHA resolutions).

The skill appends via `tools.intel.lessons.append(repo_root, lesson)`.
The allocator (`tools.intel.lessons.next_id`) reserves the next free
`L<NNN>` slot.

## Failure modes

- `.forge/intel/lessons.md` malformed — `tools.intel.lessons.parse`
  raises `LessonError`; the skill surfaces the message and aborts. Fix
  the file before re-running.
- User cancels mid-authoring — no disk mutation.

## See also

- `/forge:review` — auto-harvest path for findings resolved by a SHA
  during review convergence.
- `tools.intel.lessons.append` — atomic append helper.
- `tools.validate.lessons` — structural validator (`python -m tools.validate --target lessons`).
