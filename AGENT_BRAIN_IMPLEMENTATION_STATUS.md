# Agent Brain V1 Implementation Status

Last updated: 2026-07-13

## Baseline

- Existing ingestion/review/conversation: 40 tests passed.
- Existing runtime/learning/provider: 31 tests passed.
- Existing plugin: 10 tests, typecheck and build passed.
- Runtime DB currently has no model profile or routing rows; schema `user_version` was 0 at inspection.
- Production service already defaults to `MacKeychainStore`; plugin has no persisted `data.json` containing model secrets.
- Worktree was already dirty; all Brain work is incremental and preserves existing changes.

## Implemented

- Unified Brain lifecycle: understanding, planning, policy, restricted execution, verification, proposal and completion.
- Persistent Runs, Steps, Tool Events, Proposed Actions, Change Sets, Research Bundles, curriculum candidates and plan proposals with repeatable schema migration.
- Request prose and Change Set bodies remain in `90-Local-Only`; SQLite stores only runtime metadata, hashes and local references.
- Registered Skills cover capture, organization, research, curriculum planning, recommendations, tutoring, quizzes and evaluation. No arbitrary filesystem, shell or SQL tool exists.
- Change Set writes enforce traversal/symlink checks, base hashes, reviewed/core protection, payload integrity, explicit confirmation, transactions and idempotence.
- Brain, Research, Curriculum, plan-proposal and Change Set endpoints are available under authenticated `/api/v1` routes with CORS/OPTIONS and structured redacted errors.
- macOS Keychain-backed OpenAI-compatible providers support editable Key references, capability flags and nine task routes. Profile deletion never deletes a shared/pre-existing Keychain item.
- All five Obsidian modules consume Brain data: Assistant Run Timeline and proposals, Today AI-completion candidates, Materials Research Bundles, Review quality gates and Plan proposals.

## Verification status

- Unified `./scripts/check.sh` passed: ingestion/review/conversation 40; Agent runtime/Brain/provider 54; plugin 15 plus Python compile, TypeScript typecheck and production build.
- Latest plugin build is installed in `.obsidian/plugins/obsidian-learning-agent/`; installed `main.js`, `styles.css` and `manifest.json` hashes match the verified build.
- Real Obsidian 1.12.7 loaded the current five-page build and reported `在线 · 协议 v1`.
- Twenty required deterministic visual states plus the Provider Drawer and a real-Obsidian capture passed review under `artifacts/agent-brain-v1-screenshots/`.
- The production Keychain reference `deepseek-main` exists, but the runtime database intentionally has zero Provider Profiles and zero routing rows. No key value was read and no real model call was made.

## Safety gates

- No real PDF Apply.
- No real model call without a user-confirmed Base URL, model and Profile route.
- No secret output.
- No reviewed/core overwrite.
- No external research provider enabled implicitly.
