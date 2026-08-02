# Project Status

Last updated: 2026-08-02

## Current phase — Workspace Policy + Long-term Memory V1

Branch: `workspace-memory-v1` (based on `9f3c071`).

Implemented:

- Vault layout adjusted: `20-Knowledge/概率论与数理统计` moved to
  `20-Knowledge/Courses/` (git mv); 10 chapters marked
  `type: course-chapter, review_unit: false`; both top-level MOCs restored
  to `status: core`.
- Workspace Policy files: `00-System/AI/{VAULT_CONSTITUTION,NOTE_TYPES,
  WRITING_POLICY,LINKING_POLICY}.md`, loaded by
  `agent/core/workspace_policy/` (loader/models/validator/resolver).
- Long-term memory: `memory_items` + `memory_evidence` (migration in
  `_migrate_memory`), `agent/core/memory/` (service/policy/retrieval/
  extractor). Explicit-intent remember, candidate promotion rules,
  supersede-on-conflict, reference-only evidence.
- Pi integration: `PiAgentRuntime.resolveMemoryContext` injects
  `<zhixu_memory_context>` (≤8 items / ≤800 tokens); new tools
  `search_memory` / `remember_memory` / `forget_memory` /
  `validate_write_intent` + 4 policy read tools; `pi_agent_runs` gains
  `memory_snapshot_json`.
- Endpoints: `/memory/{context,search,remember,forget,list}`,
  `/write-intent/validate`, `/workspace/policy/{name}`, `/workspace/profile`.
- Learning scan: `scan_reviewed` now requires
  `review_unit != false AND status ∈ {reviewed, core}` (legacy notes default
  to reviewable).
- Home.md converged to five sections (今天 / 当前学习 / 当前项目 / 待处理 /
  快速入口).
- Tests: `test_memory.py` (11), `test_workspace_policy.py` (13), learning
  review-unit cases (4). All offline gates pass.

Out of scope for this phase (kept): no vector DB, no memory profile/usage
tables, no first-class memory UI module, model does not decide Today order,
`conversation_knowledge_signals` stays read-only.

Full design: `docs/architecture/WORKSPACE_MEMORY_V1.md`.

## Current phase

The `pi-runtime-hardening-repair` audit has completed R01–R08 plus the
post-audit production-semantic closure for durable Tool Observations, reasoning
privacy, cold-start Fork identity and provider error categories. Its
temporary-environment A–S production-code acceptance and latest full-suite gate
both passed. Pi
Agent Core and Pi AI own the only production model–tool loop for Main Assistant,
contextual learning assistance and study-note generation. Python is limited to
the secure model proxy, Keychain access, typed tool execution, task
authorization, reversible transactions, hybrid retrieval, capability facts and
persistence. The old Brain/intake path remains only for explicitly named
compatibility workflows such as Prepared PDF handling. Ordinary in-scope
reversible Markdown work executes through snapshot → atomic apply → verify →
Action Result and conflict-safe Undo, while scope expansion, irreversible work
and external side effects remain interruptible. The first real Dragonnet
`apply-prepared` gate is unchanged. The current repair build has not yet been
validated by a real DeepSeek turn plus an actual Obsidian reload.

## Assistant copy and selection closure

- Completed and streaming Assistant messages expose a keyboard-focusable
  `复制回答` action that resolves the latest raw Markdown at click time. It never
  derives content from rendered DOM, Tool Observations, metadata or reasoning
  status.
- Copy prefers the Clipboard API and falls back to a temporary hidden textarea;
  the fallback is always removed, copied text is never logged or persisted, and
  success/failure is visible through Obsidian Notices.
- Assistant Markdown explicitly permits text selection. Progressive rendering
  defers atomic DOM replacement while the user has a selection inside the
  message, then commits the latest revision when selection ends or after a
  bounded 500 ms delay.
- Empty streaming output keeps the action disabled; partial, cancelled and
  failed runs retain copy access to content already received.

## Pi runtime production-semantic closure

- Completed Tool Results now persist a secret-free `modelObservation` capped at
  12 KiB. Paths, titles, structured hits, excerpts, pagination and action
  references survive restart; complete long notes and command output do not.
  The same bound applies to reconnect events, and schema 16 backfills old
  Session shells from historical local Tool Result events before scrubbing
  those events.
- Authentic provider reasoning deltas now cross the local Run/UI boundary as
  ordered `reasoning` events. SQLite, reconnect and bounded local conversation
  metadata preserve them for collapsible display and independent copy, while
  Session Projection still excludes them from every future model request.
  Reasoning already erased by the former status-only migration is not recoverable.
- `pi_agent_runs` now stores `profile_id`, `selected_model` and
  `provider_adapter_version`; a cold-start Fork reconstructs all three instead
  of falling back to an empty Profile.
- Provider authentication, rate limit, missing model, context limit, provider
  and stream-protocol failures retain distinct terminal codes. Only the hard
  request timer emits `model_request_deadline_exceeded`.
- Latest offline gates passed: 40/40 ingestion/review tests, 240/240 Python
  tests, 174/174 plugin tests, strict TypeScript typecheck, production build and
  `./scripts/check.sh`.
- These are code/transaction/deterministic Runtime results. No real key was
  read, no real provider was called and no running Obsidian process was
  restarted for this follow-up.

## Pi runtime hardening repair — R01–R08

- Stall detection is side-effect aware, progress structured and bounded; model
  timeouts terminate independently of provider AbortSignal cooperation.
- Restart context comes only from the persisted Session Projection, including
  typed Tool pairs, control messages, Action references and selected lineage.
- Pending permission survives restart as the original validated
  `runId + toolCallId`; approve and deny resume the same Run idempotently.
- Fork and Regenerate resolve persisted Entry boundaries, exclude future/sibling
  history and never inherit Run-scoped allow-all, network or workspace grants.
- Structured compaction persists its checkpoint before changing in-memory
  history and is recoverable across restart without fake conversation turns.
- Main Assistant, learning assistant and study-note generation share the Pi
  prepare/query/reducer path. Learning Q&A is network-off/read-only by default;
  note generation accepts only a verified reversible draft Action.
- Ordinary standalone Review, Artifact and Task Thread UI paths have been
  removed. The legacy Brain is restricted to explicit compatibility workflows.
- Runtime-generated knowledge notes were split to
  `knowledge-notes-from-runtime-hardening` (`d22d6f8`); this branch's
  `20-Knowledge` tree matches `origin/pi-agent-runtime`.
- The repeatable headless A–S acceptance passed 19/19 against temporary
  Vault/SQLite/Git fixtures with deterministic model transport. It did not read
  a real key, call a real model, touch the user's Vault or restart Obsidian.
- Final gates passed: Python compileall, 236/236 Python tests, 163/163 plugin
  tests, TypeScript strict typecheck, production build and
  `./scripts/check.sh` (including its 40/40 ingestion/review gate).

## Pi runtime migration — current implementation

- Exact `@earendil-works/pi-agent-core` and `@earendil-works/pi-ai` packages are bundled into the plugin; no Pi Coding Agent, TUI or sidecar is used.
- The stable tool registry is always visible to Pi. DeepSeek chooses tools, receives real Observations and replans in the same Run.
- Ordered Pi events are persisted before UI delivery. Session Tree, Fork, Steering, Follow-up, cancellation, token-aware compaction and stall protection are implemented.
- Task Authorization and the unified reversible transaction service enforce safe roots, protected/reviewed/core, symlinks, base hashes, snapshots, verification, idempotency and multi-file Undo.
- Hybrid exact/metadata, FTS/BM25 and graph/context retrieval use RRF and degrade safely when optional local embeddings are unavailable.
- Capability probes cache facts without keys and conservatively resolve Tool Calling, streaming, parallelism, strict schema, usage and reasoning options.
- Developer workspaces use isolated Git worktrees, structured commands, a minimal environment and a deny-by-default network/shell policy.
- Conversation turns and final answers use stable message IDs, are persisted idempotently and hydrate Pi after a plugin/runtime restart.
- Provider-returned thinking text is kept as a separate local-only UI/recovery
  artifact. Ordered deltas survive SQLite, reconnect and bounded conversation
  metadata, render in a collapsible plain-text block and have an independent
  copy action. They never enter the final answer, knowledge Markdown or future
  model context.
- Pi stall budgets now reset at the start of every Run instead of inheriting the age of a long-lived conversation. This closes the real `stall_guard_elapsed_time_limit` failure that previously rejected the first model request after a conversation had remained open for more than 30 minutes.
- Pi model dispatch now resolves the provider-neutral `configured-assistant-model` placeholder to the selected Profile's real `defaultModel`. Provider rejections, premature NDJSON EOF and 45-second no-activity stalls always terminate the Run with a recoverable error instead of leaving “正在连接已选模型…” spinning forever. Assistant submit setup is single-flight, clears the submitted composer immediately and keeps the in-flight Stop control usable; Runtime restart also fails and releases orphaned Pi Runs.
- Assistant Auto mode now resolves an enabled configured Profile before constructing the Pi Turn, and the model proxy has a single-configured-Profile fallback when routing is temporarily empty. Startup validation/profile failures stay inside the versioned model stream, legacy `run.failed` is terminal, and `agent_end` no longer emits a false success before an error. The installed Obsidian build was restarted and a real Keychain-backed UI turn completed with the exact answer “知序实时链路正常”; full offline checks passed (40 ingestion/review tests, 155 Agent tests and 76 plugin tests, plus typecheck/build).
- The first structured write plan freezes concrete paths, roots and operations for the Turn; a later plan cannot silently expand authority.
- Developer deployment is a two-phase handshake: Python validates a merged clean commit, while the plugin owns fixed check/build/install/restart/health steps and governed rollback.
- Real Keychain-backed DeepSeek A–H acceptance passed against a temporary Vault and project. It covered read, retrieval, direct verified write, exact-byte Undo, plan-only, developer worktree/test/build/merge/activation, Steering and three queued Follow-ups without exposing the Key or touching real knowledge notes.
- The verified plugin build is installed at `.obsidian/plugins/obsidian-learning-agent/`. Build/install hashes are refreshed after each accepted runtime repair; the current hashes are recorded after installation below.
- The reasoning restoration and per-Run stall reset build is installed. Build/installed hashes match: `main.js` `bce9f1da5165d7736065d3557e4181b7e7d33d04cc96b45f87ca59ce947d527d`; `styles.css` `0777f20a598830d8e6b1264973650b1ed5a7ef2dbce1feec450203f71077fcc7`. Obsidian reload is pending because the Mac locked during the final live UI gate.
- The stuck-model repair was installed and activated in Obsidian 1.12.7 on 2026-07-20. Runtime restart changed orphaned `pi-run-097c…` from `running` to recoverable `failed / pi_runtime_interrupted`. A real Keychain-backed UI turn using the selected `deepseek-v4-pro` completed as `pi-run-db777a10…` with the exact answer “连接修复验证通过”; the composer returned to idle and no knowledge file or Change Set was written. Runtime health returned HTTP 200 with PID `94844`. Build/installed hashes match: `main.js` `c4e9ea800f5d6d6f8c625a756add061d454ea39b2af9c3de6e55c0200514d1ff`; `styles.css` `424075822de3474aabe7b248b38d92fae1ad68f0c6d1c10366c18bde0dc9ac3e`.

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

## Historical milestones

The following entries describe earlier product stages. Where they mention Brain,
PydanticAI, deterministic intent routing or ordinary write confirmation, they are
superseded by D-042 through D-044 and the Pi runtime migration above. Provider
reasoning visibility follows the narrower D-046 contract.

- Chat-first V1 implemented: unified conversations, binary/URL/path/folder attachments, deterministic multi-Intent routing, idempotent submit and seven versioned Artifact types.
- Assistant is the only universal input and authorization surface. Materials is a one-column status center; Plan is today blocks plus weekend preview; Today is five queues plus a single learning detail. The former standalone Review UI was later removed from ordinary production code; Prepared history remains available only through explicit compatibility workflows.
- Follow-up instructions revise the active Artifact and retain parent versions. Raw conversation text and attachment bodies live only under `90-Local-Only`; SQLite stores hashes, counts and state.
- Sixteen V3 historical visual/live acceptance screenshots cover the earlier five-module shell. The current installed shell has four primary modules and routes legacy Review commands into Assistant.
- Secure model subsystem includes Keychain/FakeKeyStore, OpenAI-compatible adapter, connection diagnostics and per-task routing.
- Deterministic reviewed/core-only recommendations, feedback/undo, study sessions and explicit mastery confirmation are implemented.
- Installed Obsidian `main.js` matches the verified build.
- Model-backed assistant now routes through the secured localhost Runtime; editable Change Set candidates and full idea expansion remain next milestones.
- Real Obsidian 1.12.7 acceptance on 2026-07-18 passed the four-module navigation, service-online state, Assistant page and Harness footer. No standalone Review button or tab is reachable. Things-theme color leakage, machine-ID exposure and the obsolete “学习 Agent” label remain closed; the former compact statistics sidebar remains retired.
- Follow-up alignment hardening overrides host-theme fixed button heights for Materials/Review document cards and centers icon/text controls explicitly. The obsolete plugin global strip plus the main view's native Obsidian header—including the three-dot split menu—are now removed.
- Agent Brain V1 now unifies intent routing, context, planning, Policy, restricted execution, verification, proposals and audit under one lifecycle.
- Registered Skills and restricted Tools cover safe capture, organization, research, curriculum planning, recommendations, tutoring, quizzes and evaluation. There is no arbitrary model-accessible filesystem, shell or SQL tool.
- Full request prose and Change Set bodies stay under `90-Local-Only`; SQLite stores runtime state, hashes and references. Payload tampering, stale bases, traversal, symlink escape and reviewed/core writes are rejected before transaction commit.
- Five modules consume Brain state: Assistant Run Timeline and proposals, Today curriculum candidates, Materials Research Bundles, Review quality gates and Plan proposals.
- Model settings now expose editable Key references, organization ID, custom headers, capability flags and nine task routes while keeping secrets in macOS Keychain.
- The complete Agent Brain visual matrix has 20 numbered screenshots plus Provider Drawer, five-page and real-Obsidian evidence in `artifacts/agent-brain-v1-screenshots/`.
- The user-configured DeepSeek Profile now routes assistant chat and Brain intent classification independently. The Assistant model picker changes only `assistant_chat`; it no longer silently replaces `brain_orchestrator`.
- DeepSeek structured output uses its supported `json_object` mode with an explicit bounded schema instruction and thinking disabled. Generic OpenAI-compatible providers retain `json_schema` mode.
- The Brain tutor path now calls the routed `assistant_chat` model with only bounded reviewed-note excerpts; when no reviewed evidence exists, the response is explicitly marked `needs-verification`.
- A minimal real connection and Obsidian end-to-end request using the user-configured `deepseek-v4-pro` succeeded. The prompt was only `你好`; no Vault note, PDF, secret or private source text was transmitted.
- The current runtime schema is 8; health remains protocol 1 and excludes secrets.
- Daily Intelligence V2 uses TypeScript as the Today ranking/event authority, while the Python Worker remains the secure Keychain/model/SQLite/PDF/transaction adapter.
- Today now has five categories, model-backed Daily Knowledge with A/B/C admission, direct Study Sessions, automatic review scheduling and privacy-controlled learning events.
- Assistant Chat-first V1 now persists five-step Task Threads and Artifact Groups. A learning request produces one deduplicated Learning Pack with one embedded quiz, explicit recovery actions and a real Today add/undo link.
- The installed Assistant uses the reference three-column chat/task/context layout; first requests name new conversations automatically and honest empty context replaces blank panels.
- Conversation intelligence now persists bounded summaries and evidence-backed learning signals without storing raw prose in SQLite. Per-conversation personalization, local export, summary-only retention, single deletion and confirmed recent/all clearing are available.
- Ordinary answers no longer inherit an older Task Thread or Artifact Group. Explicit Artifact creation, Obsidian write proposals and Today adjustments remain separate deterministic outcomes.
- Today supports persisted time/format constraints, three explainable next-direction horizons and version-checked Undo. Conversation evidence is fixed at 12% of the documented deterministic score.
- Non-PDF materials create traceable Material Artifacts. Public URLs are SSRF-checked, cached once and converted to Research Bundles/Change Sets without storing page bodies in SQLite.
- Vault autonomy exposes cautious/balanced/high modes. Every eligible low-risk write has a snapshot, bounded diff, audit action and conflict-safe Undo; protected/core remains proposal-only.
- Native Obsidian Markdown rendering now covers assistant answers, tutor output, Artifact summaries and review/proposed-note previews.
- Redacted diagnostics now cover conversations, summaries, signals, directions, Today adjustments, Web state, Actions, Snapshots, Undo and runtime boundaries.
- Schema 7 adds Conversation Focus, Material Bundles, Knowledge Units, Organization Plans and Organization Actions without moving knowledge truth out of Markdown.
- The canonical Delta Method two-turn flow resolves “这个方法”, creates one derivation-rich method draft, records snapshot/diff/audit and supports conflict-safe Undo.
- Current PDF attachments carry across later turns without duplicate Prepare jobs. Pasted text and mixed material preserve local provenance; raw bodies remain outside SQLite.
- Explicit “整理成预览 / organize as a preview” requests now create an Organization Plan even when paired with “不要保存 / do not save”; the installed Obsidian smoke shows the preview card and no write result.
- Explicit high-autonomy save/update can create marked drafts or append an isolated managed block. reviewed/core and protected source notes only produce update-suggestion Change Sets.
- The assistant context column now presents current understanding, organization intent and recent modifications with Open, View Changes and Undo actions.
- A limited real DeepSeek temporary-Vault smoke passed connection, Delta two-turn resolution, draft creation and Undo using the existing Keychain reference without printing the key. A public NIST page smoke passed untrusted-source caching and private-IP blocking.
- Installed Obsidian 1.12.7 now passes consecutive latest-build reloads. A restored-leaf crash caused by `containerEl.children[1]` was fixed by using the stable `ItemView.contentEl` surface.
- Real installed UI checks passed for Delta focus, pronoun continuation without write, one-shot clarification, materials/review, formula rendering, dark theme and compact layout. Fourteen screenshots are under `artifacts/context-material-obsidian-v1-screenshots/`.
- Today Study Workspace V1 strictly separates `curriculum_candidates`, `validated_directions` and executable `daily_plan`; only A-grade candidates may enter Today, while B directions stay future/exploration-only and C/noise is hidden.
- Starting learning now replaces the recommendation detail in place with a resumable five-section workspace. Progress, quiz answers and notes persist through pause/exit; completion is idempotent and undoable, and mastery still requires explicit confirmation.
- Live Obsidian QA found and fixed a stale resume label after exit. The dashboard now refreshes after the pause commit and paused/in-progress tasks show “继续/继续学习”.
- The complete Study Workspace matrix has 21 real installed-plugin screenshots covering plan, start, pause/resume, five sections, quiz persistence, contextual assistance, completion, Undo, dark, narrow, offline error and same-session recovery.
- A deliberate Runtime stop produced an inline recoverable error; plugin-managed restart plus Retry restored the PSM session in paused state at 5/5 progress. The QA session remains paused.
- The redundant right-side statistics panel has been retired. The plugin no longer registers or opens a sidebar view; persisted legacy leaves are detached on load and on later layout changes. The old internal statistics drawer, preview fixture and responsive toggle were also removed, while the ribbon now opens the Today workspace directly.
- Assistant Real Product V1 now uses a conversation rail, central chat/Agent work area and Context/Sources/Changes Inspector modeled from audited interaction patterns in AnythingLLM, OpenCowork and OpenWork; no reference code or brand assets were copied.
- The model adapter consumes the provider's real SSE stream and the localhost API exposes versioned NDJSON events. Pending UI, delta batching, verified Trace, partial-result retention and AbortController cancellation are production paths rather than fake chunking.
- Ordinary Q&A persists only the user/assistant turns and Conversation Focus; it does not create Learning Packs, Change Sets or Today items. Explicit write language continues through the governed intake/transaction path.
- User-message “编辑后重发” is append-only. Assistant “重新生成” reuses the selected prior user turn without inserting a duplicate user message; old answers remain auditable, and write-intent turns do not get a one-click regeneration bypass.
- Assistant cancellation now immediately repaints the latest live Trace as stopped while preserving provider text already received. This closed the real-Obsidian stale-running P1.
- Assistant Sources and Changes Inspector data is now derived from immutable conversation artifacts as well as the active execution result. Real artifact sources and pending Change Sets remain visible after reload, with deduplication and a read-only Diff path.
- Assistant responsive behavior now collapses the conversation rail to a 72px module rail at an effective width of 1420px or below and hides the Inspector; the 920px breakpoint further protects toolbar and message width. Real 1121px and 901px windows passed visual checks.
- The redundant lower-left “输入问题或命令 / 设置 / 帮助” shortcut stack has been removed from every module and from the visual preview. The compact Agent connection status remains as the only footer content.
- The 22-state Assistant matrix is stored under `artifacts/assistant-real-product-v1-screenshots/`. Twenty states were captured from the current installed plugin; Apply and Undo are explicitly labeled offline component previews because no real Vault write was authorized.
- The latest plugin was rebuilt and installed. Installed/build hashes are identical: `main.js` `22fe88c32f61d968a7c9fe20b6be904254d2f8c55a692e98be5c83853489cfeb`; `styles.css` `27ad672becd4a2892bb57e12f7c1bf21df19d1275d28d4f0224729a12fdfc7db`.
- Assistant Runtime V3 replaces fixed single-pass planning. Every interactive turn creates a persisted Brain Run, builds bounded conversation/note/material context, asks the model for one allowed action, executes one typed tool, returns the real Observation and replans until it can answer, clarify or propose a Change Set.
- Model-native tool calling is supported for capable OpenAI-compatible profiles. Profiles without tool calling use strict structured-output Planner decisions against the same restricted Tool Registry; the former deterministic-tool main path is removed.
- `create_change_set` is the only model-visible write proposal tool. `apply_confirmed_change_set` is filtered from all model schemas and can run only through the authenticated explicit-confirmation API. Proposal, approval, apply, reject and cancellation states are persisted as schema-v2 events.
- Runtime schema 8 adds ordered Agent Run events and checkpoints. Reconnect supports sequence cursors; awaiting runs support idempotent Resume/Reject. Checkpoints retain only state, paths, counts and content fingerprints—note excerpts and Change Set prose remain local files outside SQLite.
- The model receives concrete tool contracts and observations but cannot select mutating tools, filesystem paths, shell commands, SQLite access or transaction behavior. Unknown or mutating calls are rejected before handler execution and recorded as `brain.tool-blocked`.
- Assistant NDJSON now exposes real `plan.created`, `tool.requested`, `tool.started`, `tool.completed`, `step.updated` and `approval.required` events. The Obsidian Trace renders these events rather than a fabricated progress sequence.
- Context and conversation persistence now redact secret-shaped values before model dispatch or local archival. Runtime restart marks interrupted Brain Runs failed/recoverable instead of leaving false-running state.
- Language ownership remains deliberate: Python owns security, model/provider adapters, PDF processing, SQLite, policy and transactions; TypeScript owns Obsidian state reduction and rendering. No framework rewrite was needed to obtain the real Agent chain.
- Obsidian was reloaded after installation and now runs health protocol 1 with `assistant_runtime_version: 2.0`. Installed/build hashes are identical: `main.js` `6dea2e6778502c559e23bc5356685f864b57e303231780d8e40aedd6d2ac79d6`; `styles.css` `27ad672becd4a2892bb57e12f7c1bf21df19d1275d28d4f0224729a12fdfc7db`.
- Vault access is no longer a generic project-file scan. A shared incremental index only admits Markdown under `00-Inbox`, `01-Inbox`, `10-Sources`, `20-Knowledge`, `30-Learning` and `40-Projects`; project source, `node_modules`, root specifications and `90-Local-Only` never enter model retrieval.
- `get_vault_overview` returns actual counts, status/type/domain distribution and recent notes. Natural-language Vault questions use deterministic `search_vault` followed by bounded excerpts of the top matches, while an explicitly selected note uses only metadata, its bounded excerpt and related-note links unless the user explicitly asks for wider search.
- The Assistant “选择笔记” control is now a native searchable Obsidian picker filtered by the same safe-root policy. Its `@path` result is resolved into the backend `active_note`; the previous non-interactive first-20-file list that exposed project and `node_modules` paths is removed.
- Real DeepSeek acceptance on 2026-07-16 passed three production paths: Vault overview found 9 allowed notes; a causal-inference search read 3 actual note bodies and returned their real paths; an explicit `reviewed` note request returned the correct grounded explanation and path. The final audit Run `run-cd18e08e62da4da49197404e028a6230` contains exactly `read_note_metadata`, `read_note_excerpt` and `get_related_notes`, with no search, mutation or apply event.
- Latest installed/build hashes remain identical after the note-picker and context-binding fixes: `main.js` `32dd73d1f52de03dd36b665dad28a54b5012b2f5dd74213f4e0ed0b0de5a4e9b`; `styles.css` `27ad672becd4a2892bb57e12f7c1bf21df19d1275d28d4f0224729a12fdfc7db`.
- Assistant Markdown now renders provider deltas progressively through an off-DOM staging node and atomic swaps. Final completion no longer clears the answer or refreshes the entire view, closing the visible flash and preserving Markdown structure while streaming.
- Bold terms inside assistant Markdown remain inline; the prior host CSS rule that forced every `<strong>` onto its own line is now limited to structural message headings.
- A terse `写入` confirmation inherits only the latest explicit assistant proposal and its validated target. It creates exactly one immutable Change Set, never auto-applies, and truthfully reports the target and pending state. With no proposal it requests a target and creates nothing.
- reviewed/core inherited targets produce only a local update suggestion. A second Change Set guard rejects command-only artifacts such as `01-Inbox/写入.md`, including the historical bad proposal left by the previous implementation.
- Real installed-Obsidian DeepSeek smoke on 2026-07-16 passed inline Markdown list rendering and a ten-item streamed response. A live bare `写入` safety smoke left the Change Set count unchanged at 5 and created no `01-Inbox/写入.md`; no real Change Set was applied.
- Latest installed/build hashes are identical: `main.js` `599864272b08dca8b9d54b5da4ec2192ce13a788203a0deb65bd5f79882232b7`; `styles.css` `f632f3ea547b5f51bc2d0b52debfa758dbe6529c7774e7f8ea5a2d927aacf7ec`.
- Installed plugin artifacts were rebuilt on 2026-07-17: `main.js` `7d9e342b9ef5da73afed67356aa12efb796e4a69756a4bd31c8e7e049fb39d6c`; `styles.css` `b47e9b289d6c99edf422a599c4e5283d9135b1515b0f0b291b92e8986018b7fb`.
- Provider-neutral frontend contracts now include AgentRuntime, capabilities, registry, turn preparation, normalized chunks and durable conversation state. Reconnect, cancel, compact, fork and regenerate call real backend endpoints.
- PydanticAI deferred tools are the canonical confirmation path for both governed writes and `ask_user`. The model decides whether a concrete Change Set is proposal-only or should proceed to commit; no keyword marker or output validator forces that choice.
- Tool Trace and write Diff are generated from real ordered events. Diff bodies are generated on demand and never copied into SQLite; public confirmation payloads omit private payload references.
- The CodeMirror Inline Edit extension captures document snapshot, selection offsets and selected text and rejects stale acceptance with “原文已变化，请重新生成。” Backend base-hash verification remains mandatory.
- Provider/profile and model-routing writes now pass through the frontend SettingsService. Architecture tests prohibit core→feature, runtime→DOM, UI→Python/PydanticAI, legacy coordinator and direct settings-write regressions.
- A real DeepSeek `deepseek-v4-pro` smoke in a temporary Vault passed `get_current_note → propose_vault_change → commit_vault_change → inline confirmation → reject/resume`. The temporary note was byte-for-byte unchanged before confirmation and after rejection; no real Vault note or PDF was read or modified.
- The standalone Review navigation and tab were retired. Backward-compatible commands now open Assistant, where scoped confirmation is rendered inline and resumes the same Run. The legacy audit/review backend remains for Prepared PDF history and does not grant model authority.
- The canonical Runtime exposes a stable 16-tool set including real Vault folder listing and typed `ask_user`. The Harness then allows a scoped create, asks inline, or denies; permission is never inferred from natural-language markers.
- A repeatable real-model topic smoke (`scripts/real_harness_topic_smoke.py`) passed with the configured `deepseek-v4-pro` in a temporary Vault: `get_conversation_focus → search_vault → read_vault_note ×2 → find_related_notes ×2 → propose_vault_change → commit_vault_change`. The generated PSM note passed topic, assumption, limitation, source-link and post-apply hash gates. No real Vault note was changed.
- The rebuilt plugin was installed and hot-reloaded in Obsidian 1.12.7. Installed/build hashes are identical: `main.js` `3be69e99dabcba894429b7688c99d621d69340b89b8e8cb07e9e7ebc53e329e4`; `styles.css` `b47e9b289d6c99edf422a599c4e5283d9135b1515b0f0b291b92e8986018b7fb`.
- The model-driven tool-loop correction is installed and Force Reloaded in Obsidian 1.12.7. Build/installed hashes match: `main.js` `f31005a2a6423f676eb1091931fe9441dd7f88ed9696917f014ad2ec0057d78c`; `styles.css` `3a197aa5f092c00197032a6b9d0410b320e7219167508afac6ecfc1dd3994a8d`. The plugin restarted the backend with a fresh Runtime ID.
- A real installed-Obsidian `deepseek-v4-pro` read-only smoke passed on 2026-07-18: the model selected `list_vault_folder`, received the real `20-Knowledge/Concepts` observation and returned the three complete Markdown paths and statuses. No Change Set was created and no Vault note was written.
- Vault-root directory listing now treats `/` and `.` as explicit safe aliases. They enumerate only model-visible roots (`01-Inbox`, `10-Sources`, `20-Knowledge`, `30-Learning`, `40-Projects` in the current Vault), never project code or `90-Local-Only`. Direct folder listing also returns child directories without reading note bodies.
- Expected local-tool failures (`ValueError`, missing files/folders and policy denials) now become bounded failed Observations inside the same PydanticAI Run. The model can retry, ask or choose another safe tool; unexpected implementation failures still terminate the Run.
- Real installed-Obsidian acceptance on 2026-07-19 passed `list_vault_folder("/")` with the configured `deepseek-v4-pro`. Run `run-e2f62fa2886043e493765cb21e621374` completed after one real tool call, returned five safe roots, emitted no `write.diff`/confirmation event and created no Change Set.
- Web Research V2 federates Bing RSS, GitHub and Hacker News with DuckDuckGo only as a result-shortage fallback. Academic search federates arXiv and Crossref. Distinctive-term ranking rejects generic keyword noise, while `/docs/` sources receive official-documentation classification.
- Public-page evidence is capped at 20,000 characters for the active model turn and remains under `90-Local-Only/Agent/WebCache`; SQLite and public Tool Events keep metadata only. SSRF, redirect, DNS, content-type and byte-limit gates remain enforced.
- Additional deterministic tools expose current time, safe Vault overview, learning state, due reviews and recent materials. These tools are always available to PydanticAI and do not depend on an Intent classifier.
- Assistant UI V6 adds a visible `仅本地 / 联网开启` control, refined research-oriented message/composer styling, real web Tool Trace labels and a live Sources Inspector with provider/domain/quality/snippet cards. The conversation rail and inspector remain responsive and use Obsidian theme variables.
- Real installed-Obsidian acceptance on 2026-07-19 passed a configured `deepseek-v4-pro` web turn. The model called actual search/fetch tools, returned three Pydantic official documentation sources and populated the Sources Inspector; no Change Set or Vault write was created.
- Assistant UI V7 removes the crowded action header. The header now contains only the conversation title; model and network state live in the composer, and all secondary actions are grouped under the composer `+` menu.
- A real installed `deepseek-v4-pro` mixed-write regression passed: one new knowledge draft and one protected MOC update became a single confirmable Change Set whose protected operation was redirected to a local update suggestion. The same Run is intentionally paused at inline confirmation; neither candidate file was applied and the MOC was not modified.
- Provider reasoning display is implemented under D-046: only an actual provider
  `reasoning_content` / Pi `thinking_*` block is shown, independently from the
  final answer and real Tool Trace. It is collapsible, independently copyable,
  locally recoverable and absent when the provider returns none.
- Assistant reasoning now has an explicit `Auto / 深度` runtime mode in the composer model menu. Auto keeps the provider default; DeepSeek Deep sends `thinking=enabled` plus `reasoning_effort=max`, raises the per-turn budget to at least 8192 tokens, compacts oversized history by complete request/response pairs, and adds evidence/edge-case/final-verification instructions.
- A real Keychain-backed `deepseek-v4-pro` temporary-Vault smoke on 2026-07-19 completed with `reasoning_effort=max`: 5,395 streamed reasoning characters, 4,362 answer characters and two real tool calls. It ended `run.completed`, produced no API error and wrote zero Markdown files. The smoke output recorded only counts/status, never the key or reasoning text.
- The rebuilt plugin was installed and reloaded in Obsidian 1.12.7. The live model popover exposes the Deep switch and the composer reports `Auto · 深度` after activation. Build/installed hashes match: `main.js` `5f7936000ff0609c7284844baf6661015c20fb7adbac041fedf082841e44dd1d`; `styles.css` `cf9bb0581fe87d6012ac1c68b3705c69dcaaf3c4ea753eab56a15144baf011f5`.
- Network-source recovery no longer turns an expected read denial into a fatal Assistant Run. Attachment metadata/PDF tools now state that only current-conversation attachment IDs are valid; arXiv IDs, DOIs and public URLs must use the public-source path. A denied/unavailable read remains visible as a failed Tool Observation so PydanticAI can re-plan in the same Run, while the underlying attachment, SSRF and private-network permission gates remain unchanged.
- Regression coverage reproduces the exact failure mode (`2505.09343v2` incorrectly supplied as `attachment_id`) and verifies that the same Run continues to completion. A Keychain-backed real `deepseek-v4-pro` temporary-Vault smoke completed 16 network searches, 7 public-page fetches and 23 total tool calls with no failed tool, no `run.failed` and zero Markdown writes. The plugin-managed Runtime was restarted in Obsidian; health returned HTTP 200 with PID `80111`.
- The original failed Obsidian turn was then retried in place after restart. Live Run `run-2f45f3eab68a4d4ab17a369d63fcdec3` completed with 24 real Tool calls, successfully fetched AI21 Jamba, Meta Llama 4, DeepSeek-V3, Mamba-2 and Google Gemma sources, and emitted `run.completed` instead of `PermissionError`.
- Repository documentation was consolidated on 2026-07-19. The Vault root now contains only eight engineering entry/safety files; 74 stage specifications and implementation records live under `docs/{architecture,assistant,brain,learning,materials,product,ui}` with `docs/README.md` as the index. Three legacy empty root placeholders were preserved under `99-Archive/Legacy-Root-Placeholders/`; no specification or history file was deleted, and explicit stale root-path references were eliminated.
- The 2026-07-21 long-note/write failure was traced to two transport bugs rather than a Vault permission failure: `read_vault_note` exposed an obsolete 4,000/8,000-character page ceiling, while Pi and the secure proxy silently capped model output at 32K and could forward a half-serialized tool-call JSON. Note reads now paginate up to 50,000 characters per page with `next_offset`; model context/output limits come from the selected Profile/capability probe; truncated or malformed tool arguments terminate with explicit protocol errors.
- `plan_vault_copy` is a governed Harness-side batch operation for complete Markdown copies. It reads allowed source notes locally, rejects `agent_access: denied`, creates at most 10 writes per authenticated Change Set, returns compact Change Set metadata, and leaves full bodies/Diffs outside model transport. Destination remains limited to `01-Inbox` and `20-Knowledge/Drafts`; reviewed/core overwrite policy is unchanged.
- Offline acceptance copied 12 long concept notes through two Change Sets (10 + 2), applied both transactions, and verified every destination body byte-for-byte against its source. Long-note pagination reconstructed a 60,008-character CJK note exactly. Provider-limit regression verified a 96,000-token Profile is no longer reduced to 32,000, and incomplete function-call JSON is rejected before the plugin attempts `JSON.parse`.

## Phase 11 release gate

- Python ingestion/review/conversation: 40 tests passed.
- Current backend suite: 178 tests passed.
- Current plugin suite: 80 tests passed; strict TypeScript check and production build passed.
- Unified `./scripts/check.sh` passed.
- Real DeepSeek A–H acceptance passed with the configured `deepseek-v4-pro`; details are in `docs/architecture/PI_AGENT_RUNTIME_ACCEPTANCE.md`.
- Right-sidebar retirement gate: 40 ingestion/review/conversation tests, 113 Runtime tests and 36 plugin tests passed; TypeScript and production build passed on 2026-07-15.
- Today randomized gates passed for 1,000 plans, 10,000 state actions, 1,000 lesson blueprints, 110 responsive widths and 20 reproducible regression seeds.
- Daily ranking performance with 1,000 candidates: median 0.436 ms, P95 0.559 ms; localhost health response measured 0.018 s.

## Same-Run permission and governed Vault organization (2026-07-21)

- Inline permission no longer terminates the conversation or starts a replacement Run. The original Pi tool call pauses, renders immediately below the latest turn, expands the exact active authorization, and retries with the same `runId` and `toolCallId`.
- “请求权限” grants only the displayed write paths, directory/move pairs, network capability or persisted isolated developer worktree. “当前任务全部允许” remains Run-scoped and still cannot bypass protected-note, path, symlink, collision, external-side-effect or sandbox rules.
- `organize_vault_notes` creates nested safe directories and moves up to 50 Markdown notes as one verified reversible transaction. It records snapshots, hashes, Diff and audit state; any mid-operation failure rolls back the full batch, and Undo refuses to overwrite later human changes.
- `reviewed`, `core`, `agent_access: denied`, protected notes and arbitrary `10-Sources` content remain immovable. A non-formal `type: source-index` draft may be reorganized only after explicit authorization.
- A realistic temporary-Vault workflow tests evidence discovery → permission pause → same-call retry → nested topic write → multi-file organization → conflict-safe Undo, plus denial, Run expiry, source-index organization and reviewed/core immutability. No real PDF, API key or real Vault note is used.
- Final unified verification: `./scripts/check.sh` passed with 40/40 ingestion tests, 178/178 Agent tests and 80/80 plugin tests; Python compilation, TypeScript typecheck and production build all passed.
- A later recheck from the restricted Codex outer sandbox reached 173/178 Agent tests; the five remaining cases require launching nested macOS `sandbox-exec` and were blocked before their commands ran. The previously permitted nested-sandbox run passed all 178. A fresh independent rerun of the 32 write/organization workflow tests and all 80 plugin tests, typecheck and build passed after the final documentation update.
- The reversible-write subset passes 27/27, including ordinary apply rollback, organization rollback, concurrent human edits, invalid UTF-8 fail-closed behavior and reviewed/core protection. The realistic temporary-Vault workflow passes 5/5 without reading a real PDF, API key or real Vault note.
- Developer workspaces use exact per-workspace operation grants. Creating an isolated worktree grants no implicit read/write/run permission; request-mode grants one displayed operation and Run-scoped allow-all grants only the fixed safe developer operation set for that persisted worktree.
- The latest plugin is installed under `.obsidian/plugins/obsidian-learning-agent/`. Installed/build SHA-256 values match: `main.js` `eaacbd3a4360d3d8cccba433af871872275d90c4ffe845a91a7b016402a95ef9`; `styles.css` `390f503ebd41ca6154e42fce4b551cc4f0f1d7fe589f9b24095eff507784197b`; `manifest.json` is byte-identical.
- The current live Agent process still reports PID `64517` and Runtime ID `5d0a8ede10d49d58a332bf0d83f1aa8b`. It predates the installed build. Computer Use could not reload Obsidian because macOS is locked, so the disk installation is verified but the new in-memory UI/runtime activation remains the only pending local action.

## Hard blockers

- First real `apply-prepared` is intentionally user-gated after all offline development and inspection.

## Known lower-priority limits

- Public search relies on several unauthenticated public endpoints, so an individual provider can rate-limit or change its response format. Federation, bounded failures and DuckDuckGo fallback keep the Run recoverable, but this is not an availability SLA.
- Explicit non-chat workflows remain synchronous projections; ordinary interactive assistant work uses Pi with cancellable streaming and durable ordered events.
- Interactive model-selected tools require a provider with compatible Tool Calling. Capability facts conservatively disable unsupported options; providers without Tool Calling can answer text but cannot perform Agent tool work.
- Agent Artifact revision/versioning is exposed through continued assistant dialogue; immutable Prepared Bundle application remains a separately gated workflow.
- Runtime launch is plugin-owned rather than a persistent macOS LaunchAgent by design.
- Heading-aware field-level Diff editing and cancellable model streaming remain P2; V1 existing-draft updates use a bounded managed block.
- Study Workspace V1 has no outstanding visual-state gap; its 21-image matrix is stored under `artifacts/today-study-workspace-v1-screenshots/`.
- Several legacy Python tests leave SQLite connections for interpreter cleanup and emit non-failing `ResourceWarning` messages; this is P2 resource hygiene, not a transaction or data-integrity failure.
- Developer workspace IDs currently include a truncated Run identifier. Persisted records and server-side project validation prevent cross-workspace authority, but a hash suffix would further reduce diagnostic ambiguity (P2).

## P0 / P1

- None known after the complete offline, visual, installed-build and Keychain-reference gates.

## Resume point

The repair implementation and offline acceptance are complete. Review the
pushed `pi-runtime-hardening-repair` branch; do not merge automatically. Live
installation/restart of the user's Obsidian and the deliberate first real
Dragonnet Apply gate remain separate and untouched.
