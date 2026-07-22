# Assistant Chat-first Status

Updated: 2026-07-14

## Complete

- Persisted Task Threads and five step records.
- Persisted Artifact Groups, same-run deduplication and result versioning.
- One Learning Pack with one embedded quiz preview.
- Three-column Assistant UI, fixed composer and context rail.
- Human failure recovery and collapsed technical details.
- Real Today add/duplicate/undo integration.
- Cross-restart `已加入今日` state restored from the Runtime.
- Provider selector, history, new conversation and settings drawer.
- Automatic conversation naming from the first user request.
- Offline Python and TypeScript coverage for contracts, recovery, deduplication and Today integration.

## Deliberate limits

- Legacy pre-schema-5 conversations are readable but are not rewritten into historical Task Threads.
- External research adapters remain disabled until explicitly configured.
- True cancellable streaming remains P2; the secure JSON response path is used now.

## Safety

No real Apply was executed. No reviewed/core note was changed. API keys remain in Keychain references and are absent from Markdown, SQLite and logs.

## Verification

- `./scripts/check.sh`: passed.
- Python: 40 + 76 tests passed.
- Plugin: 23 tests passed; typecheck and build passed.
- Installed `main.js` and `styles.css` matched their build sources by SHA-256.
- Real Obsidian captures: `artifacts/assistant-chat-first-v1-screenshots/`.
