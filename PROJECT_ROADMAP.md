# Project Roadmap

Implementation status: Phases 0–6 complete for the local MVP. The remaining gate is a user-confirmed first real Dragonnet `apply-prepared` after inspecting its Prepared Bundle.

## Product outcome

Build a local-first Obsidian learning agent with two complete loops: source-to-reviewed-knowledge and reviewed-knowledge-to-learning-plan. Markdown remains the knowledge source of truth; local SQLite stores runtime state only.

## Phase 0 — Sustainable engineering foundation

- Safe Git baseline and private-data exclusions.
- One-command offline checks.
- Persistent roadmap, status and decision log.

Acceptance: the project can be resumed safely and `./scripts/check.sh` verifies all implemented components.

## Phase 1 — Prepared PDF ingestion

- Immutable Prepare → Inspect → Apply Prepared workflow.
- Stable artifact identities and managed blocks.
- Bundle integrity, conflict detection, idempotency and transactional apply.
- Dragonnet mainline-selection guardrails.

Acceptance: all offline security/idempotency/rollback tests pass; first real apply remains user-gated.

## Phase 2 — Review loop

- List/show/diff/approve/approve-edited/reject/reopen.
- Evidence-aware review packets, audit trail and transactional transitions.
- Rejection memory and reviewed/core protection.

Acceptance: review transitions and rollback are tested without manual file moves or YAML editing.

## Phase 3 — Local Agent runtime

- SQLite jobs, change sets, artifacts, audit, learning state and indexes.
- Restricted service layer and localhost-only HTTP API.
- Health check, structured local logs, safe shutdown and crash recovery.

Acceptance: API and recovery tests pass; no unrestricted model write tool exists.

## Phase 4 — Obsidian plugin

- Commands, sidebar, task/review/change-set UI and offline-service messaging.
- localhost API client only; no secret storage or silent approval.

Acceptance: TypeScript build and logic tests pass with manual installation documentation.

## Phase 5 — Learning recommendation loop

- Reviewed-only due review and new-learning selection.
- Explainable intervals, 70/30 routing, rescheduling, quizzes and weekly plans.
- User-confirmed mastery changes.

Acceptance: ranking, dates, transitions, reviewed-only filters and Bases/Home views are tested.

## Phase 6 — Additional source types

- High-value AI conversation ingestion.
- Textbook/chapter ingestion.
- Reuse prepared bundles, change sets, review and transactions.

Acceptance: both inputs have offline fake-model tests and traceable source artifacts.

## Deferred backlog

WeChat articles, blogs, video, notebooks, GitHub ingestion, vector databases, GraphRAG and multi-agent orchestration remain out of scope until both core loops are stable in daily use.
