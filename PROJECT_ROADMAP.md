# Project Roadmap

Implementation status: Phases 0–10 are historical foundations. Phase 11 is the active Pi runtime migration. The separate first real Dragonnet `apply-prepared` remains user-gated after inspection.

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

## Phase 7 — Agent Brain V1

Historical phase, superseded by Phase 11. Its storage, policy and source-integrity
assets remain; its deterministic intent and Python planning architecture do not.

- One request entry with deterministic intent routing and optional strictly-structured model assistance.
- Registered Skills and restricted tools only; policy, verification, audit and cancellation are mandatory lifecycle stages.
- Capture, research, curriculum candidates and learning plans remain proposals until explicit user confirmation.
- Research Bundles retain source ordering, provenance, confidence and provider failures without silently inventing results.
- Five Obsidian modules share Brain Runs, proposals and diagnostics; model configuration supports Keychain references and per-task routing.

Acceptance: tamper protection, idempotency, reviewed/core policy, Key redaction, SSRF controls, schema migration, Brain API, all five plugin modules, typecheck and build pass offline; no real model or real PDF apply is required for this phase.

## Phase 8 — Interaction intelligence and bounded autonomy

- Private summaries/signals, per-conversation controls and retention/export/delete APIs.
- Explainable learning directions, exact local weighting, persisted Today constraints and Undo.
- Traceable non-PDF Materials and cached untrusted public-source Research Bundles.
- Shared Obsidian Markdown/formula rendering.
- High/balanced/cautious Vault autonomy with first-use summary, snapshots, audit, change inspection and conflict-safe Undo.
- Expanded diagnostics and adversarial free-text/prompt-injection corpus.

Acceptance: `./scripts/check.sh`, plugin tests/typecheck/build and installed-artifact hashes pass; no real model, real external web call, real PDF or first `apply-prepared` is required.

## Phase 9 — Today Study Workspace V1

- Strict `curriculum_candidates` → `validated_directions` → `daily_plan` separation with A/B/C admission and bounded long-topic splitting.
- Four-part Today workspace: module navigation, category/direction pane, executable daily list and recommendation/study detail.
- In-place resumable Study Workspace with five sections, Obsidian Markdown/math, quiz, related knowledge, notes, route, sources and contextual assistant drawer.
- Runtime-backed pause/resume/progress/completion/undo. Mastery remains an explicit user confirmation and learning-note output remains a governed Change Set.
- Property/random regression coverage for 1,000 plans, 10,000 state actions, 1,000 lesson blueprints, 110 responsive widths and 20 reproducible seeds.

Acceptance: offline checks, plugin tests/typecheck/build/install pass; real Obsidian verifies Today, learning, pause/resume, quiz persistence, completion/Undo, dark/narrow layouts and offline recovery without real model calls, PDF application or reviewed/core writes.

## Phase 10 — Assistant Runtime V2

Historical phase, superseded by Phase 11. PydanticAI and deterministic fallback
planning are no longer valid production architecture.

- One canonical interactive Run Coordinator for ordinary assistant turns: Brain Run lifecycle, bounded context, plan, restricted tools, verification, answer and audit.
- Provider-neutral typed tool contracts with strict payload validation and model-visible read-only subsets only.
- Optional native OpenAI-compatible tool calling plus a deterministic restricted-tool fallback for providers without function calling.
- Real plan/tool/approval NDJSON events rendered by the Obsidian execution Trace.
- Secret-shaped context redaction, bounded model inputs, partial-output cancellation and honest interrupted-run recovery.
- Keep the efficient language boundary: Python for policy/model/storage/transactions and TypeScript for Obsidian interaction/rendering.

Acceptance: fake-provider integration proves a registered Vault tool is actually called and observed by the final model; invented mutating tools are blocked; secrets never reach model context or persistence; interrupted runs recover honestly; all backend/plugin/type/build/install gates pass; installed Obsidian health reports Assistant Runtime V2 without a real model call or knowledge write.

## Phase 11 — Pi Agent Runtime

Status: implementation and deterministic release gates complete; current-build
real DeepSeek plus Obsidian reload smoke remains before mainline recommendation.

- Pi Agent Core/AI are the sole production Agent kernel in TypeScript; Python is a secure I/O and execution boundary only.
- DeepSeek selects stable typed tools and replans from Observations without a keyword or auxiliary Intent Router.
- Task-scoped reversible Markdown changes apply directly with snapshot, hash verification, Action Result, Diff and conflict-safe Undo.
- Session Tree, Fork, Steering, Follow-up, cancellation, turn-aware compaction, stall protection and durable reconnect survive restarts.
- Hybrid Vault retrieval, capability probing and isolated developer workspaces are production tools with conservative policy fallbacks.
- Provider private reasoning is never a public event, ordinary message, Markdown field or DOM element.

Acceptance: architecture gates prove no PydanticAI/Brain loop or prose router remains; all Python/plugin/type/build/security tests pass; real Keychain-backed DeepSeek scenarios A–H exercise read, retrieval, direct reversible write/undo, plan-only, developer execution, Steering and Follow-up without exposing a key or private reasoning.

## Deferred backlog

WeChat articles, blogs, video, notebooks, GitHub ingestion, vector databases, GraphRAG, background multi-agent orchestration and cancellable SSE remain out of scope until both core loops are stable in daily use.
