# Today Study Workspace V1

## Product boundary

Today is an executable study queue, not a candidate browser. Its counts, minutes, ordering and completion state come only from the persisted `daily_plan`.

The three layers are deliberately separate:

1. `curriculum_candidates`: bounded candidate pool, normally 15–30 items.
2. `validated_directions`: admitted future, route and exploration directions shown only as compact previews.
3. `daily_plan`: the only list of work scheduled for today.

Admission uses a deterministic `CandidateAdmissionResult`. A-grade candidates may enter Today, B-grade candidates remain future/exploration directions, and C-grade/noise candidates are hidden. Oversized topics are split into bounded lessons with a shared root identity.

## Workspace layout

- Module navigation remains on the left.
- Category pane shows five Today categories and compact future directions.
- Task pane lists only `daily_plan` items.
- Detail pane shows recommendation evidence and changes in place to Study Workspace when learning starts.

No Modal or new window is used for study. At wide widths the lesson and assistance panes coexist; medium widths use an assistance drawer; narrow widths stack the study content while preserving navigation.

## Study lifecycle

The supported state machine is:

```text
recommendation → starting → learning ↔ paused
                              ↓
                             quiz
                              ↓
                         completing → completed
                              ↘ error → retry
```

Every session persists the current section, completed sections, quiz answers and learner notes. Exit pauses rather than deletes. Re-entering resumes the same session. Completion is idempotent and can be undone while preserving progress.

## Lesson contract

Each V1 lesson contains five bounded sections, learning objectives, Markdown/math content, one quiz, prerequisites, related notes, route context and sources. Obsidian's renderer owns Markdown and math rendering.

Completion records duration/progress/quiz/difficulties and returns a mastery suggestion. Mastery changes require explicit user confirmation. Generating a learning note calls the governed Intake path and creates a draft/Change Set; the Study Workspace cannot write or overwrite reviewed/core notes.

## Verification contract

- Tests never use a real model, PDF or secret.
- `LA_TEST_SEED` reproduces randomized failures.
- Required randomized gates: 1,000 plans, 10,000 transitions, 1,000 lessons, 10 fixed plus 100 randomized widths and 20 regression seeds.
- Real Obsidian screenshots are captured from the installed plugin, not an HTML mock.
