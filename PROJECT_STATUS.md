# Project Status

Last updated: 2026-07-12

## Current phase

All planned MVP phases implemented; final offline audit and real Dragonnet approval gate.

## Completed before autonomous run

- Local PAGE-marked PDF extraction and structured model validation.
- Stable PDF source identity, cross-file transaction and rollback.
- Source managed block and legacy Dragonnet migration.
- Mainline topic reuse and paper-specific concept suppression.
- 22 offline ingestion tests passing.

## Completed in autonomous run

- Phase 0: safe Git baseline, private-data exclusions and `./scripts/check.sh`.
- Phase 1: immutable Prepared Bundles with prepare/inspect/list/reject/apply/clean commands.
- Apply Prepared performs no model/network work, validates bundle/PDF/schema/targets and commits transactionally.
- All generated artifacts use `obsidian-learning-agent` identity and `agent:managed:*` blocks.
- Human-area edits survive apply; managed-content changes and reviewed/core transitions abort.
- Phase 2: list/show/diff/approve/approve-edited/reject/reopen review commands.
- Review transitions and audit files commit transactionally; rejection memory prevents duplicate regeneration.
- Phase 3: SQLite runtime state, recoverable jobs, restricted service methods, structured local logs and localhost-only HTTP API.
- Phase 4: Obsidian TypeScript plugin with six commands, sidebar, Change Set confirmation, review actions and offline-service message.
- Phase 5: reviewed-only due/future/new-learning recommendations, 70/30 routing, quizzes, mastery suggestion plus explicit confirmation, rescheduling and proposed weekly plans.
- Phase 6: textbook PDFs use the Prepared workflow; AI conversations create immutable local bundles, retain the full raw export locally and mark assistant claims `needs-verification`.

## Current work

- Final security/idempotency audit, documentation and Dragonnet commands.

## Test status

- Python ingestion/review/conversation: 40 tests passed.
- Runtime/learning: 12 tests passed, including rebuildable Markdown artifact registry and queue worker behavior.
- Plugin: 2 logic tests, strict TypeScript check and build passed.
- Unified `./scripts/check.sh`: passed after final implementation.

## Hard blockers

- First real `apply-prepared` is intentionally user-gated after all offline development and inspection.

## Known lower-priority limits

- Idea expansion job currently records a safe “awaiting prepared expansion” result; it never performs unrestricted writes.
- Plugin installation is manual in this MVP.
- No automatic background launch agent is installed.

## Resume point

Run `./scripts/check.sh`, read this file and continue the first incomplete phase in `PROJECT_ROADMAP.md`.
