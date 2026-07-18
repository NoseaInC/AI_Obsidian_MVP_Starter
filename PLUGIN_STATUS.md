# Learning Agent Plugin Status

Last updated: 2026-07-19

## Current phase

知序 Chat-first now uses four primary modules: Today, Materials, Plan and Assistant. Assistant is the unified input and the only daily authorization surface; the separate Review module has been retired from navigation.

PydanticAI schema-v3 events expose typed tools, observations, real Change Sets, inline Harness confirmation, post-apply verification and lifecycle state. The client provides authenticated reconnect, Resume and Cancel without granting the plugin or model direct Vault write authority.

The Harness now treats a concrete proposal independently from brittle write keywords. It automatically commits only explicit low-risk new files in approved Draft/Inbox roots, asks once inside the current conversation for higher-risk operations, and blocks protected targets. A real DeepSeek temporary-Vault topic test confirmed local search, two source reads, synthesis, Change Set commit and byte-level verification.

## Resolved regression

- PDF import uses a structured modal, returns a persisted queued Job immediately, refreshes the UI and starts a single lifecycle-owned poller.
- Prepare progresses through named stages and remains `prepared → awaiting_confirmation` until explicit Apply.
- Active task counts include queued/running/prepared/awaiting_confirmation/applying/failed states.
- Failures are visible; Task Center exposes timeline, cancel, retry and Prepared ID.

## Existing foundations

- Prepared Bundles, transactional apply and review backend.
- Reviewed-only learning recommendations.
- Localhost HTTP service and installed Obsidian plugin.
- CORS/OPTIONS and health protocol v1.
- Versioned `/api/v1`, structured errors and in-memory bearer session authentication.
- Plugin-owned Runtime using the project venv, Local-Only logs and graceful shutdown.
- Dashboard, Task Center, Change Set, Study, Assistant shell and Diagnostics workspace views. Legacy Review data remains available to Prepared/audit APIs but is not a primary workspace module.
- Reproducible build/install scripts; installed artifact hash verified.

## Live acceptance

- Obsidian 1.12.7 was hot-reloaded with the installed `知序` 0.3.0 manifest on 2026-07-18.
- Plugin automatically started the localhost Runtime.
- The current workspace displayed `在线 · 协议 v1` / `本地运行 · 正常` and real dashboard counts.
- Unauthenticated business API returned structured HTTP 401.
- Fixed plugin-reload token drift: health now identifies the runtime with a non-secret runtime ID and PID; a new plugin session only replaces a stale Agent when its Vault path matches exactly, otherwise it reports `PortConflict`.
- Fixed URL-encoded Prepared IDs: `%2B` path segments are decoded with `unquote` (never converted to a space). The real Dragonnet Change Set was opened read-only in Obsidian after the fix; Apply was not executed.
- 知序 0.3.0 main UI was rendered in real Obsidian with two reviewed/core recommendations and real source links.
- The installed build exposes exactly four primary modules—Today, Materials, Plan and Assistant. The standalone Review control is absent; legacy Review commands redirect to Assistant.
- Fixed the blank sidebar root cause: the callback property named `open` shadowed Obsidian View's lifecycle method. It is now `openTab`; zero-size split leaves are migrated into the normal right tab group.
- Installed build passed 40 ingestion/review/conversation tests, 56 runtime/learning/provider/Brain tests and 16 plugin tests plus Python compile, TypeScript and production-build checks.
- UI V3 removes the top tab bar and internal file tree, adds a 52px global header plus 188px module nav, and constrains scrolling to list/detail/Inspector regions.
- Visual preview passed for Today light/dark, Materials, Review, Plan, Assistant, Assistant provider drawer and narrow Today; screenshots are in `artifacts/ui-v3-screenshots/`.
- Agent Brain V1 visual acceptance adds 20 numbered states, narrow Stats Drawer, provider settings and a real-Obsidian capture under `artifacts/agent-brain-v1-screenshots/`.
- The configured DeepSeek V4 Pro Profile passed the real connection check. After rebuilding and restarting Obsidian, a minimal `你好` Assistant request completed in the installed UI; no Vault/PDF content or secret was included.
- DeepSeek structured calls now use `json_object`, the Brain intent schema constrains model output to registered intents, and tutor answers use the separate `assistant_chat` route.
- Selecting a model in the Assistant header only changes `assistant_chat`; Brain orchestration remains independently configurable in Model Routing.
- Unified Intake accepts file drop, paste, URL, explicit path and current-note references. Commands for PDF, textbook and AI conversation now open this same assistant entry instead of a parallel import UI.
- Materials is a single status center, Review includes Agent Change Sets and Vault drafts, Plan provides bounded task mutation, and Today groups review/learn/AI-gap/explore/material work.
- The current left navigation uses the Today, Materials, Plan and Assistant reference layouts. Earlier five-page captures remain historical evidence under `artifacts/chat-first-v1-screenshots/`.
- Assistant Chat-first V1 adds a five-step persisted Task Thread, one grouped/deduplicated result, an embedded quiz, human recovery actions and a real Today add/undo path. The visual baseline is `design/assistant-chat-first-v1.png`.
- Final Assistant acceptance used the configured `DeepSeek V4 Pro · deepseek-v4-pro` route with the existing local conversation. The installed build restored `已加入今日` across a full Obsidian reload, and Today displayed `PSM入门学习包` in 下一步学习.
- Final current check result: 40 ingestion/review/conversation tests, 151 Runtime/learning/model/Brain/Intake tests (2 retired schema-v2 cases skipped) and 59 plugin tests passed; typecheck, production build and installed-file hash checks passed.
- Assistant context now shows the resolved active method/material/note, organization target and recent governed writes. Applied draft cards support open, change inspection and Undo; protected targets route to Review.
- `write_result` and `update_suggestion` are first-class grouped Artifact outcomes, so a completed write no longer hides behind an older generic capture proposal.
- Plugin test count is now 30; Context/Organization UI contracts, governed write primacy, typecheck and production build pass.
- Installed-build reload now uses `ItemView.contentEl` for the main view as well as the sidebar, preventing restored/hot-reloaded leaves from dereferencing a missing child node.
- The latest real Obsidian pass verifies current understanding, Delta pronoun continuity, no-write requests, minimum clarification, materials/review, dark and narrow layouts. The 14-state matrix is in `artifacts/context-material-obsidian-v1-screenshots/`.
- The installed Runtime was restarted after the final intent fix; mixed text/URL “organize as a preview, do not save” now renders the Organization Plan instead of an answer-only fallback, without writing a note.
- Live Assistant inspection shows the Harness contract in the normal conversation surface: low-risk maintenance is validated and undoable, while protected knowledge requests scoped authorization in the current chat. Installed/build hashes match (`main.js` `3be69e99dabcba894429b7688c99d621d69340b89b8e8cb07e9e7ebc53e329e4`, `styles.css` `b47e9b289d6c99edf422a599c4e5283d9135b1515b0f0b291b92e8986018b7fb`).
- The installed Runtime was restarted on 2026-07-19 after the safe root-listing fix. A real `deepseek-v4-pro` request called `list_vault_folder("/")`, displayed five model-visible roots and completed without `ValueError`, Change Set, confirmation or Vault write.
- Current complete gate: 40 ingestion/review/conversation tests, 167 Runtime/learning/model/Brain/Intake/PydanticAI tests (2 retired cases skipped), 59 plugin tests, strict TypeScript and production build all pass. Installed/build hashes match: `main.js` `f31005a2a6423f676eb1091931fe9441dd7f88ed9696917f014ad2ec0057d78c`; `styles.css` `3a197aa5f092c00197032a6b9d0410b320e7219167508afac6ecfc1dd3994a8d`.

## P0 / P1

- None known.

## Remaining product work

- P2: richer field-level visual Diff editing before accepting a Change Set.
- P2: very large folder Intake needs a dedicated selection checklist after the current safe threshold response.
- P2: true cancellable SSE for `/chat/stream`; current endpoint deliberately uses a JSON-compatible fallback.
- P2: richer study-session controls and Markdown-rendered review split view.

## Safety gates

- Real model validation was limited to the user-requested DeepSeek configuration and a minimal `你好` request; tests remain fully offline.
- No first real Dragonnet apply.
- No reviewed/core overwrite.
- No workspace-external mutation.

## Resume point

Material-to-Obsidian V2 is implemented, installed, reloaded and visually verified. The separately authorized first real Dragonnet Apply remains gated.
