# Project Status

Last updated: 2026-07-17

## Current phase

Assistant Runtime V3 is implemented and verified. Each turn now follows a bounded model decision → one restricted tool → real Observation → model replan loop. Profiles with native Tool Calling use function calls; DeepSeek and other profiles with `toolCalling=false` use the same Tool Registry through a strict JSON Planner. Write intent can create a real Change Set, but Apply is never model-visible and Vault files remain unchanged until explicit confirmation. Persisted schema-v2 events, checkpoints, reconnect, Resume and Reject complete the approval lifecycle. The separate first Dragonnet `apply-prepared` gate remains untouched.

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

- Chat-first V1 implemented: unified conversations, binary/URL/path/folder attachments, deterministic multi-Intent routing, idempotent submit and seven versioned Artifact types.
- Assistant is the only universal input. Materials is a one-column status center; Review is list/preview/evidence; Plan is today blocks plus weekend preview; Today is five queues plus a single learning detail.
- Follow-up instructions revise the active Artifact and retain parent versions. Raw conversation text and attachment bodies live only under `90-Local-Only`; SQLite stores hashes, counts and state.
- Sixteen V3 visual/live acceptance screenshots cover light/dark, all five modules, provider settings, compact layout, alignment hardening and real Obsidian validation.
- Secure model subsystem includes Keychain/FakeKeyStore, OpenAI-compatible adapter, connection diagnostics and per-task routing.
- Deterministic reviewed/core-only recommendations, feedback/undo, study sessions and explicit mastery confirmation are implemented.
- Installed Obsidian `main.js` matches the verified build.
- Model-backed assistant now routes through the secured localhost Runtime; editable Change Set candidates and full idea expansion remain next milestones.
- Real Obsidian 1.12.7 acceptance passed for all five modules, the model/API drawer and Review rendering. Things-theme color leakage, machine-ID exposure and the obsolete “学习 Agent” label are closed. The former compact statistics sidebar was subsequently retired by product decision.
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
- Assistant Runtime V3 installed/build hashes are identical: `main.js` `703b6f5af0762b0ea35cf43c161a8c2e5213c14c3e727102ffa14dccb946df61`; `styles.css` `f632f3ea547b5f51bc2d0b52debfa758dbe6529c7774e7f8ea5a2d927aacf7ec`.

## Test status

- Python ingestion/review/conversation: 40 tests passed.
- Runtime/learning/provider/Brain/Intake/Daily Intelligence/context-material/assistant streaming: 140 tests passed.
- Plugin: 52 logic/security/lifecycle/chat-first/context-material/daily-intelligence/study-workspace/privacy/autonomy/streaming/random tests passed; strict TypeScript check and production build passed.
- Unified `./scripts/check.sh`: passed on 2026-07-17.
- Right-sidebar retirement gate: 40 ingestion/review/conversation tests, 113 Runtime tests and 36 plugin tests passed; TypeScript and production build passed on 2026-07-15.
- Today randomized gates passed for 1,000 plans, 10,000 state actions, 1,000 lesson blueprints, 110 responsive widths and 20 reproducible regression seeds.
- Daily ranking performance with 1,000 candidates: median 0.436 ms, P95 0.559 ms; localhost health response measured 0.018 s.

## Hard blockers

- First real `apply-prepared` is intentionally user-gated after all offline development and inspection.

## Known lower-priority limits

- External Arxiv/Crossref/generic-search adapters are named but disabled until explicitly configured; local/imported research remains available.
- Governed write-intent intake still returns one synchronous result after its Brain/Change Set transaction; ordinary interactive assistant work uses cancellable NDJSON streaming with persisted partial output.
- Native model-selected tools require a provider profile with tool calling enabled; strict structured-output planning is the fallback for profiles such as DeepSeek with native Tool Calling disabled.
- Agent Artifact revision/versioning is exposed through continued assistant dialogue; immutable Prepared Bundle application remains a separately gated workflow.
- Runtime launch is plugin-owned rather than a persistent macOS LaunchAgent by design.
- Heading-aware field-level Diff editing and cancellable model streaming remain P2; V1 existing-draft updates use a bounded managed block.
- Study Workspace V1 has no outstanding visual-state gap; its 21-image matrix is stored under `artifacts/today-study-workspace-v1-screenshots/`.

## P0 / P1

- None known after the complete offline, visual, installed-build and Keychain-reference gates.

## Resume point

Assistant Runtime V3 is the canonical Run Coordinator. Continue by adding crash-time model-loop continuation and richer approval editing without weakening typed tool permissions, persisted observations or the Change Set boundary. The deliberate first real Dragonnet Apply gate remains separate and untouched.
