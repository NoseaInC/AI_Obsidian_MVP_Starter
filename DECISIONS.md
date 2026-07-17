# Technical Decisions

## D-001 — Markdown is the knowledge source of truth

SQLite may store jobs, prepared bundle indexes, audit records, mastery history, quizzes and recommendations. Knowledge prose and user edits remain Markdown in the Vault.

## D-002 — Local-only intermediates

Extracted text, model responses, prepared bundles, transaction journals, logs and update suggestions live under `90-Local-Only/` and are excluded from Git and synchronization.

## D-003 — Prepared bundles separate model work from writes

Prepare may call a model once and produces an immutable local bundle. Apply Prepared must not create a model client or access the network; it validates hashes, current targets and policy before a transactional commit.

## D-004 — Filesystem policy is deterministic

The model proposes structured content only. Local code owns artifact identity, paths, permissions, managed blocks, conflict checks, transactions and rollback.

## D-005 — Paper-specific concepts are not auto-promoted in the MVP

Author-named modules, losses and local methods remain inside source summaries unless later promoted through explicit review. This favors reusable mainline learning concepts.

## D-006 — Minimal dependencies first

Use the Python standard library for the local runtime and HTTP API where practical. Add dependencies only when they materially reduce risk or maintenance.

## D-007 — One Vault-wide writer lock

Prepared validation and transactional commit share an advisory file lock under `90-Local-Only`. Review and mastery transitions use the same lock, closing local process TOCTOU windows while retaining per-transaction rollback journals.

## D-008 — Plugin is a localhost client

The Obsidian plugin contains presentation and explicit user actions only. Policy, model calls, artifact generation, review transitions and learning state writes remain in the local Python service.

## D-009 — Conversation claims are untrusted by default

Full conversation exports stay in local Prepared Bundles. Any assistant-authored claim must carry `needs-verification`; user-authored context is `user-provided`. Both still require normal artifact review.

## D-010 — One Brain, registered Skills, restricted Tools

The plugin submits user intent to one auditable Brain lifecycle. The Brain may select only registered Skills, and Skills may call only typed restricted tools. Models cannot invent tools or acquire filesystem, shell, SQL, transaction or network authority.

## D-011 — Runtime prose is local files, not SQLite knowledge

Full Brain requests, Change Set payloads and Research working material live under `90-Local-Only`. SQLite stores lifecycle state, hashes, summaries and local references. This keeps Markdown as knowledge truth and allows payload-integrity checks without turning the runtime database into a prose store.

## D-012 — Key references can outlive Profiles

A model Profile points to a Keychain reference and does not own that secret. Deleting the Profile therefore never deletes a shared or pre-existing Keychain item. Key removal is a separate explicit credential-management action.

## D-013 — External research is disabled until explicitly configured

Local Vault and imported-material providers are deterministic defaults. Arxiv, Crossref, generic web search and explicit URL retrieval have named adapter boundaries, but no external provider is silently enabled during offline or no-model operation.

## D-014 — Assistant is the universal Intake surface

PDF, textbook, conversation, URL, text, path and current-note workflows enter through the same conversation and `/intake/submit` contract. Command-palette imports open the assistant instead of creating a second product path.

## D-015 — Artifact is the cross-module unit

Materials, Review, Plan and Today are projections of versioned Artifacts generated in conversations. They may add governed state transitions, but they do not duplicate prose or become independent truth sources.

## D-016 — Original prose is private-file state

Conversation messages, binary attachments and full Brain results remain in `90-Local-Only`. SQLite stores only references, hashes, counts, bounded metadata and lifecycle state; this protects user prose while keeping recovery and querying deterministic.

## D-017 — TypeScript owns the daily interaction and ranking domain

Today UI, recommendation categories, deterministic daily ranking, quotas and learning-event collection run in the Obsidian TypeScript process. The Python service is an adapter for secure model calls, Keychain, SQLite, PDF and transactional review. This reduces UI latency without duplicating or rewriting stable worker capabilities.

## D-018 — Daily knowledge is model-generated but locally admitted

The model may generate curriculum candidates but cannot select the final daily order or claim source authority. Local deduplication and A/B/C verification decide admission. Only fully admitted A-grade candidates may enter `daily_plan`; B-grade candidates remain future/exploration directions and C-grade or deterministic fallback candidates stay hidden from Today. No candidate may masquerade as verified knowledge.

## D-019 — Conversations are private files with explicit retention controls

Raw messages remain under `90-Local-Only`; SQLite holds references and bounded intelligence only. Export, summary-only retention, single deletion and recent/all clearing are explicit user actions. Deleting a conversation never deletes reviewed/core Markdown knowledge.

## D-020 — Deterministic planning owns order; models explain candidates

The persisted Today plan and documented local score weights own final order and budget. Conversation evidence contributes exactly 12 percent. Models may propose or explain directions but cannot silently change weights, fixed tasks or mastery.

## D-021 — Public web is untrusted cached source material

The Runtime—not the model—performs opt-in public fetches with SSRF/redirect/type/size checks. Retrieved text is untrusted source content, cached outside SQLite and converted to a Research Bundle/Change Set before any knowledge write.

## D-022 — Low-risk autonomy always carries snapshot, audit and conflict-safe Undo

High autonomy broadens eligible low-risk actions but never bypasses protected/core policy. Undo is allowed only while the current file still matches the recorded after hash; later user edits always win.

## D-023 — Conversation Focus resolves before intent

Current-message entities, authorized attachments, active artifacts and recent conversation focus are resolved before routing. Models may assist entity extraction but cannot decide paths or permissions. Low confidence receives one minimal clarification.

## D-024 — Material and organization state are separate

Material Bundles describe what the input contains; Organization Plans decide what Obsidian result should exist. This separation keeps source understanding reusable while filesystem policy remains deterministic.

## D-025 — Explicit save authorizes bounded high-autonomy writes

In high mode, an explicit save/update request may create a marked draft or append a managed block to an ordinary draft without another confirmation. Snapshot, hash verification, audit and Undo are mandatory. Protected/core and large/destructive work still require Change Set confirmation.

## D-026 — Turn bundles store references, not raw prose

SQLite schema 7 persists focus, provenance, named knowledge units and organization state. Raw messages, pasted bodies, source text and model prose remain in private local files or Markdown.

## D-027 — Today has three non-interchangeable data layers

`curriculum_candidates` is a bounded 15–30 item pool, `validated_directions` is a compact future/exploration surface, and `daily_plan` is the only execution list and the only source for Today counts and minutes. The UI may explain the first two layers but must never count or render them as scheduled work.

## D-028 — Study is a resumable in-place state machine

Starting a recommendation replaces only the right-hand detail pane. Session state and progress live in the local Runtime and follow explicit recommendation, starting, learning, paused, quiz, completing, completed and error transitions. Completion suggests mastery but cannot confirm it or write reviewed/core knowledge; any note creation remains a governed AI Draft / Change Set.

## D-029 — Assistant regeneration is append-only and permission-preserving

Editing a user message never mutates the stored historical turn; the edited text is submitted as a new turn. Regenerating an assistant answer reuses a bounded history ending at the selected user message, stores a new assistant result and does not duplicate the user turn. One-click regeneration is withheld for write-intent turns so it cannot bypass Change Set, snapshot, Diff, confirmation or reviewed/core policy.

## D-030 — The Assistant Inspector is reconstructed from conversation artifacts

Sources and governed changes must survive reloads and later turns, so the Inspector derives them from immutable artifacts linked to the active conversation, then merges and deduplicates any live execution results. It must not depend only on the most recent organization response. Change Set inspection is read-only; Apply remains an explicit governed action.

## D-031 — Responsive collapse protects the conversation before hiding content

At an effective plugin width of 1420px or below, the Assistant collapses its conversation rail to a 72px module rail and hides the Inspector, preserving a readable single-column chat and horizontal toolbar. At 920px or below, toolbar and content spacing tighten further. Conversation history and Inspector data remain available when width returns; responsive layout never deletes or mutates state.

## D-032 — One canonical interactive Run Coordinator

`/assistant/stream` and the governed Intake path may present different output surfaces, but both must create auditable Brain Runs and use the same restricted Tool authority. Ordinary chat no longer calls a model before Runtime context and Tool planning. Native Tool Calling is optional; deterministic retrieval remains the safe fallback for providers without reliable function calling.

## D-033 — Language follows the authority boundary

Python retains local security, model, PDF, SQLite and transaction authority because those capabilities already share a tested process boundary. TypeScript retains Obsidian state reduction and rendering. Performance-sensitive interaction may move to TypeScript, but filesystem policy, secrets and Change Set commit cannot be duplicated across languages merely for architectural fashion.

## D-034 — Streaming completion is an in-place state transition

Provider SSE deltas are cumulative state, not disposable plain text. The Obsidian client renders them at a bounded cadence into a detached staging node and atomically swaps only completed Markdown DOM. `message.completed` supplies the persisted message metadata, so ordinary completion adds actions and timestamps in place and must not clear the node or rebuild the whole workspace.

## D-035 — A write confirmation inherits a proposal, never the command text

A terse confirmation such as `写入` has no standalone document meaning. It may inherit only a recent explicit assistant proposal with an allowlisted Markdown target and provenance message. The inherited result is one pending Change Set; reviewed/core becomes an update suggestion, and application still requires explicit Diff confirmation. If no proposal exists the system requests a target. Creation and apply validation both reject command-only note artifacts so stale historical proposals cannot bypass the corrected resolver.

## D-036 — Agent planning is Observation-driven and authority remains local

Each Assistant Runtime V3 round selects exactly one action: call one currently allowed typed tool, answer, or request one minimal clarification. The resulting Observation returns to the model before the next decision. Native Function Calling and strict JSON Planner mode share the same Tool Registry, permission levels, schemas and resource-scope checks. The model may see `create_change_set` as a proposal capability but can never see or invoke `apply_confirmed_change_set`. Ordered events and checkpoints are runtime metadata; request prose, note excerpts and Change Set bodies remain private files outside SQLite.

## D-037 — PydanticAI is the sole model–tool kernel

The plugin owns a provider-neutral AgentRuntime contract but registers only PydanticAgentRuntime. Python PydanticAI owns model/tool iteration, observations, retries and deferred approval; TypeScript normalizes ordered NDJSON into AgentChunk and renders it. This boundary permits future adapter replacement without introducing Claude Code, Codex CLI, OpenCode, Pi or duplicated provider implementations.

## D-038 — Confirmation is a deferred tool result inside the same Run

An existing-note write cannot be approved by prose or a detached review bar. The governed commit tool raises deferred approval, the current conversation displays the real tool/path/risk/Diff request, and confirm/cancel resumes the same PydanticAI Run. A model that stops after proposing an explicitly requested write is retried by an output validator rather than having the frontend fabricate progress. Backend hashes and policy remain authoritative.

## D-039 — Settings persistence has one frontend boundary

Views may read model settings but cannot write provider Profiles or routing endpoints directly. SettingsService is the only frontend persistence boundary; the backend still validates URLs, protected headers, known routes and Keychain references. API keys never enter plugin persistence.

## D-040 — Harness owns write decisions; confirmation stays in the conversation

The standalone Review module is retired from the primary Obsidian navigation. A model may build a bounded Change Set but cannot decide whether it is applied. The local Harness deterministically chooses one of three outcomes: auto-apply an explicit low-risk new draft in an approved root, request one scoped confirmation inline in the current Assistant run, or block the operation. Terse continuation such as “好” or “继续” may inherit an already concrete proposal but never grants authority by itself. Every applied Change Set is revalidated before commit and hash-verified after commit. The legacy review/audit backend remains available for immutable Prepared PDF workflows and historical inspection; it is not a second daily approval inbox.
