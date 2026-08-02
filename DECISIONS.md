# Technical Decisions

## D-048 — Permission is a paused tool call, not a completed conversational turn

- A sensitive operation emits one typed inline permission request and suspends the exact tool Promise inside the active Pi Run.
- Confirmation expands only that Run's persisted Task Authorization and retries the same `toolCallId`; rejection returns a blocked Observation so the model can continue without pretending the operation occurred.
- Permission UI must be mounted while consuming the live stream. Rendering it only after the stream ends creates a deadlock because the stream is waiting for the decision.
- “Allow all” means all otherwise-safe reversible capabilities for the current Run. It never means unrestricted filesystem, secret access, external side effects, or permission to mutate reviewed/core knowledge.

## D-049 — Folder organization is a first-class reversible Vault transaction

- Directory creation and Markdown moves use a dedicated `organize_vault_notes` contract instead of shell commands or asking the user to perform manual file operations.
- Authorization contains exact directories and source→target pairs. The Harness normalizes paths, rejects traversal/symlinks/collisions/protected content, snapshots sources, atomically writes and verifies targets, removes sources only after verification, and can restore the whole batch.
- Non-formal source-index drafts under `10-Sources` may be reorganized with explicit permission. reviewed/core and other protected sources remain immutable regardless of permission mode.
- Developer self-modification remains isolated to a persisted run-owned Git worktree. A permission grant verifies that persisted workspace record and never trusts a client-provided project path.

## D-050 — Developer authority is operation-scoped and server-derived

- Creating an isolated Git worktree establishes only an execution location; it grants no read, write, command, Git, validation, activation or rollback authority by itself.
- A permission expansion names the persisted workspace and the exact operation. The server reloads the workspace record, ignores client-supplied project paths and grants either that one operation or the fixed reversible developer operation set for the current Run.
- Developer mode never extends to the real Vault knowledge tree, arbitrary shell access, secrets, external side effects or reviewed/core mutation.

## D-051 — Rollback must preserve later human edits

- A transaction may replace or restore a path only when its current hash still matches the state that transaction created or staged.
- Apply and organization move old inodes into private transaction staging before publishing new files with exclusive creation. Rollback and Undo use the same exclusive/hash-checked protocol and fail closed rather than deleting or overwriting concurrent human content.
- Frontmatter protection parsing accepts BOMs, quoted values and inline comments and fails closed on invalid UTF-8 or ambiguous protection metadata.

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

## D-025 — Superseded by task-scoped authorization

The former autonomy-mode write exception is replaced by D-042. Permission is derived from the current structured Task Authorization, not from prose matching or a global autonomy level.

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

## D-035 — Superseded: prose is never the permission boundary

The runtime no longer interprets terse phrases as approval. Pi decides whether a tool is needed; the Harness validates the concrete operation against the Task Authorization. Ordinary in-scope reversible Markdown writes execute directly and return an Action Result with Diff and Undo.

## D-036 — Agent planning is Observation-driven and authority remains local

Each Assistant Runtime V3 round selects exactly one action: call one currently allowed typed tool, answer, or request one minimal clarification. The resulting Observation returns to the model before the next decision. Native Function Calling and strict JSON Planner mode share the same Tool Registry, permission levels, schemas and resource-scope checks. The model may see `create_change_set` as a proposal capability but can never see or invoke `apply_confirmed_change_set`. Ordered events and checkpoints are runtime metadata; request prose, note excerpts and Change Set bodies remain private files outside SQLite.

## D-037 — Superseded by Pi runtime

The PydanticAI production kernel described here has been retired. D-044 defines the only supported production loop.

## D-038 — Questions and scope expansion stay inside the same Run

Inline interruption is reserved for `ask_user`, scope expansion, irreversible work and external side effects. Ordinary authorized Markdown changes do not pause the Run. Backend hashes, protection policy and transactions remain authoritative and cannot be overridden by confirmation.

## D-039 — Settings persistence has one frontend boundary

Views may read model settings but cannot write provider Profiles or routing endpoints directly. SettingsService is the only frontend persistence boundary; the backend still validates URLs, protected headers, known routes and Keychain references. API keys never enter plugin persistence.

## D-040 — Superseded: Harness validates actions, not intentions

The Harness validates actual tool calls against task scope, safe roots, hashes and protection state. It directly executes authorized reversible work, asks only for a real scope expansion, and denies non-bypassable policy violations. There is no ordinary-write approval inbox.

## D-041 — Main Assistant intent is the model's tool loop, not a classifier

Pi exposes the stable typed tool set on every turn. The same DeepSeek Agent selects a tool, receives its Observation and replans. No production or legacy intake path uses keyword, regex, token or fixed-phrase intent routing; structured intake modes remain explicit API fields.

## D-042 — Explicit task authorization replaces repetitive write approval

Once the user gives an explicit task, that Turn authorizes reversible operations inside its bounded resource and operation scope. The Agent may directly execute controlled Markdown or developer-workspace changes. The first structured plan establishes and freezes the concrete operation/path scope for that Turn; later plans cannot silently add paths, roots or operations. Safety comes from path policy, protected status, snapshots, base hashes, atomic transactions, verification and conflict-safe Undo—not repeated confirmation. Only scope expansion, irreversible operations or external side effects pause the user.

## D-043 — Action Journal is recovery infrastructure, not an approval inbox

Action, Snapshot, Diff, Transaction and Undo records remain private recovery infrastructure. The product does not expose a standalone audit center or pending-approval list. Normal interaction presents only the operation result, View Changes and Undo.

## D-044 — Pi is the sole Agent kernel

Pi Agent Core and Pi AI own the model/tool loop, Session Tree, Steering, Follow-up, Compaction and events in the Obsidian TypeScript process. Python retains the secure model proxy, Keychain, governed tool execution, hybrid index, transactions and persistence. PydanticAI no longer participates in production. Runtime upgrades use a two-phase handshake: Python validates the merged clean commit, while the plugin owns fixed check/build/install/restart/health operations and invokes governed rollback on failure.

## D-045 — Model identity is resolved at the proxy and every stream is terminal

Provider-neutral runtime identifiers such as `configured-assistant-model` never cross the secure model-proxy boundary as provider model names. The proxy resolves them through the selected Profile and emits the protocol's canonical `error` event for provider failures. The TypeScript transport treats timeout and EOF without `done` or `error` as failure, so every started turn reaches exactly one terminal client state. UI setup is single-flight and crash recovery expires orphaned Pi Runs; neither a provider rejection nor a plugin restart may leave a permanently running task.

## D-046 — Provider reasoning is local UI/recovery data, separate from the answer

When a configured provider returns a dedicated `reasoning_content` block, the
secure proxy, Pi transport and Agent event boundary preserve its authentic
ordered start/delta/end events. The original deltas are bounded, secret-redacted
and stored only in the local Run/event store and local conversation UI metadata,
so reconnect and plugin restart can restore a collapsible reasoning block. The
UI renders provider text as plain text and exposes a dedicated “复制思考” action;
“复制回答” still copies only the final source Markdown.

Provider reasoning is never inferred from final text, tools or runtime stages,
never merged into the final answer or knowledge Markdown, and never restored to
future model context. Ordinary blocks are preserved exactly; oversized event
deltas and metadata blocks are bounded. Prose already erased by a status-only
build cannot be reconstructed, so those older records remain status-only.

## D-047 — Large content is paged or moved locally, never serialized through the model twice

A model context window, model output budget and tool-result page are distinct limits. The selected Model Profile/capability probe owns context and output limits; the runtime must not impose an undocumented 32K clamp. Long Markdown reads use explicit `offset`, `next_offset` and `truncated` fields. Exact multi-file copy/organization operations use a governed Harness-side batch tool so note bodies travel from local source files into authenticated Change Sets without first being echoed through model function arguments. Provider output ending at `finish_reason=length` or containing malformed function arguments is a typed terminal protocol error and must never reach the plugin as a partial JSON tool call.

## D-052 — The first safe write plan freezes the Turn scope; later plans expand it

Before the model issues any write plan, the Task Authorization's `resourceScope.writeScopeState` is `"unbound"`: Q&A and any write proposal still cannot write, and every plan would surface a permission card. The *first* reversible, in-scope Markdown write plan (via `plan_vault_change` / `plan_vault_copy`, including batched copy under one `toolCallId`) is the Turn's concretization: the Harness auto-binds the explicit path/operation scope and transitions `writeScopeState` to `"bound"` with no confirmation card. This is deterministic, not intent classification—no keyword, regex, fixed phrase, or Intent Router is involved. Auto-bind is gated by the same path policy and protection checks as `grant_scope`: updates require an explicit scoped path, creates require `DEFAULT_CREATE_ROOTS` (`01-Inbox`, `20-Knowledge/Drafts`), reviewed/core/protected targets and path escapes are hard-denied, and the scope is frozen atomically under the StateStore lock. A *later* plan in the same Run that adds new paths, roots or operations is a real expansion and still raises a permission card (a different `toolCallId` cannot auto-bind). The same `toolCallId` re-binding is idempotent and only extends the already-frozen scope.

## D-053 — Every ordinary interactive surface uses Pi

- Main Assistant, contextual learning assistance and study-note generation use
  one shared `prepareTurn → query → AgentChunk reducer` path.
- The retired Brain/intake coordinator is not an ordinary fallback. It may
  remain only behind explicitly named compatibility workflows such as Prepared
  PDF processing.
- Ordinary Review, Artifact and Task Thread UI projections are removed rather
  than retained as unreachable alternate product paths.

## D-054 — Persisted projection and Entry lineage define recovery

- Session Projection is the only source for reconstructing Pi messages after
  restart. Renderer memory, conversation-message shortcuts and ad-hoc local
  caches are not recovery authorities.
- Projection follows one persisted `parent_id` lineage and restores typed user,
  assistant, Tool Call and Tool Result shapes while excluding provider
  reasoning, siblings and sensitive bodies.
- Fork and Regenerate resolve a persisted Entry boundary. They do not use array
  indexes, approximate message positions or inherited Run-scoped permissions.

## D-055 — A permission decision resumes the original persisted Tool Call

- Pending permission is durable state keyed by the original
  `runId + toolCallId + taskAuthorizationId`.
- On restart the backend revalidates the Tool Contract, arguments,
  path/workspace guards, Authorization and absence of a completed Tool Result
  before the card is shown.
- Approve or deny injects exactly one typed Tool Result and continues the same
  Run. Repeated decisions are idempotent; invalid or stale requests fail closed.

## D-056 — Compaction is structured and persistence-first

- A compaction checkpoint contains bounded structured state from the selected
  Session Projection, Focus, attachment metadata, Actions, pending results and
  workspace authorization; it is never a fake conversational turn.
- The checkpoint Entry is committed before in-memory messages change. A failed
  commit leaves the exact active context untouched.
- Restart recovery reuses the checkpoint plus Entries after
  `keptFromEntryId`, never sibling branches, secrets, reasoning or unbounded
  tool output.

## D-057 — Learning writes are direct, reversible drafts

- Contextual learning Q&A is network-off and read-only by default.
- An explicit study-note action may let Pi plan and apply a change directly,
  but the UI accepts only a verified Action under `01-Inbox` or
  `20-Knowledge/Drafts` and exposes Diff and conflict-safe Undo.
- Authorized reversible draft work does not ask for repeated confirmation.
  `reviewed`, `core`, protected paths and policy failures remain non-bypassable
  and can produce only update suggestions or explicit denials.

## D-058 — Workspace Policy is Vault rules, delivered as profile + on-demand tools

- Directory semantics and writing rules live in `00-System/AI/*.md`
  (user-editable), not in code or in `memory_items`.
- Pi receives a ~800-token resident Workspace Profile every turn plus
  on-demand read tools (`get_writing_policy`, `get_note_type_policy`, …).
- All ordinary knowledge writes must produce a typed WriteIntent validated by
  `agent/core/workspace_policy/` before entering the transaction path.

## D-059 — Long-term memory is `memory_items`; evidence is reference-only

- `memory_items` is the single authority for user goals, preferences,
  knowledge state and project decisions. `memory_evidence` stores only
  references (message/learning_event/quiz/feedback/action ids) — never prose.
- Explicit memory requires an explicit-user-intent match; implicit candidates
  promote only with ≥3 evidence, ≥2 distinct days, confidence ≥0.75.
- Conflicts supersede (new active, old superseded) instead of overwriting in
  place. `claimed` never auto-upgrades to `verified`.
- `conversation_knowledge_signals` stays read-only and is not a second
  authority.

## D-060 — Review units are semantic, not directory-based

- `scan_reviewed` requires `review_unit != false AND status ∈ {reviewed, core}`.
  `course-chapter` notes are `review_unit: false` by definition and never enter
  Today review; only topic/concept may be review units.
- Directories remain safe boundaries but are no longer the sole learning
  semantic. Legacy notes without `review_unit` default to `true`.
