# Today Study Workspace V1 Status

Last updated: 2026-07-15

## Implemented

- Strict three-layer Today data model and exact candidate admission fields.
- A-only Today admission, B future/exploration placement, C/noise exclusion and long-topic splitting.
- Daily category/list totals derived only from `daily_plan`.
- In-place Study Workspace with five sections, progress, pause/resume/exit, quiz, notes, source/knowledge/route assistance and contextual assistant drawer.
- Runtime GET/PATCH lifecycle, resumable sessions, idempotent completion and completion undo.
- Explicit mastery confirmation boundary and governed learning-note Change Set path.
- Responsive layout classification and reduced-motion-safe styling.
- Latest plugin installed at `.obsidian/plugins/obsidian-learning-agent/`.
- The separate right-side statistics panel and its dormant in-page drawer have been removed; Today now uses the full available workspace width and keeps only the left navigation summary.

## Verification

- PDF ingestion/review/conversation: 40 passed.
- Runtime/learning/provider/Brain/Intake: 113 passed.
- Plugin: 36 passed.
- TypeScript `typecheck`: passed.
- Production build: passed.
- `./scripts/check.sh`: passed on 2026-07-15.
- Property gates: 1,000 daily plans, 10,000 valid state actions, 1,000 lesson blueprints and 110 viewport widths passed.
- Twenty fixed random regression seeds passed; failures print an exact `LA_TEST_SEED` replay command.

## Real Obsidian verification

- Online protocol state: passed.
- Today plan shows 2 scheduled tasks / 24 of 25 minutes and separates 5 future directions: passed.
- In-place lesson start and five-section rendering: passed.
- Pause and persisted runtime state: passed.
- Exit then resume-label refresh: fixed after live QA, rebuilt, reinstalled and reverified in Obsidian.
- Five-section progression, quiz entry, answer persistence/feedback and contextual assistant drawer: passed.
- Completion summary, idempotent completion Undo and explicit mastery-confirmation boundary: passed.
- Dark theme and narrow responsive/list/detail/drawer layouts: passed.
- Runtime-offline inline error, plugin-managed service restart and same-session 5/5 recovery: passed.
- Real installed-plugin evidence: 21 screenshots under `artifacts/today-study-workspace-v1-screenshots/`.

## Safety

No real model call, real PDF read/apply, real key read or reviewed/core write occurred. The live interaction changed only local learning runtime state and remains resumable/undoable.

## Acceptance result

Today Study Workspace V1 has passed its offline, build, install, lifecycle and real-Obsidian visual gates. The reversible PSM QA session is left paused with 5/5 progress preserved. The unrelated first real Dragonnet `apply-prepared` remains user-gated.
