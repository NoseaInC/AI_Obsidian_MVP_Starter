# UI Implementation Plan

1. Add versioned dashboard/recommendation/feedback/study APIs backed by reviewed/core Markdown and SQLite runtime state.
2. Replace the debug dashboard with one ItemView containing Today, Sources, Review, Plan and Assistant tabs.
3. Build reusable `la-*` DOM components: header, tabs, split pane, recommendation rows/details, badges, chips, progress ring, summary rows, jobs and empty/error states.
4. Reduce the right sidebar to actionable summaries and a compact assistant composer.
5. Keep PDF/Change Set/review safety in the existing backend; no UI action bypasses confirmation.
6. Add deterministic tests, DOM/source contract tests, a local visual preview and install through the existing verified script.

## Acceptance focus

- Real data only; reviewed/core is the sole knowledge recommendation source.
- Feedback changes later recommendations without changing Markdown.
- No model or network call is required for recommendation fallback.
- No real Apply is performed during implementation.

## Status (2026-07-13)

- Steps 1–6 implemented and covered by offline tests.
- Main Today UI verified in Obsidian with real reviewed/core recommendations.
- Remaining acceptance blocker: persisted right-sidebar leaf still appears `Uninitialized` in this workspace even though the registered view DOM contains the online dashboard. Track as P1 in `PROJECT_STATUS.md` and `PLUGIN_STATUS.md`.
