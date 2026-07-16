# Material to Obsidian V2 Status

Last updated: 2026-07-14

## Implemented

- SQLite schema 7: Conversation Focus, Material Bundles, Knowledge Units, Organization Plans and Actions.
- Cross-turn method/PDF focus, bounded pronoun corpus and one-shot minimal clarification.
- Explicit answer/preview/save/current-note/source/method/daily intents.
- Target resolution by current note, title and aliases; maximum three planned actions.
- High-autonomy draft creation and managed-block append with snapshot, diff metadata, audit and Undo.
- reviewed/core protection with update-suggestion Change Set.
- Assistant “当前理解 / Agent 正在做什么 / 最近修改” UI with open, inspect and Undo controls.
- Delta Method, current PDF, pasted/mixed text, duplicate alias, protected target, schema migration and privacy tests.
- Limited real DeepSeek smoke used Keychain reference `deepseek-main` in a temporary Vault: connection, two-turn Delta resolution, note creation and Undo all passed; no secret or private Vault content was printed or transmitted.
- Public-web smoke fetched `https://www.nist.gov/itl`: one source, `untrusted_source_content`, body cached outside SQLite, localhost/private-IP fetch rejected, no injection marker detected.
- Installed Obsidian smoke passed: latest build reload, Delta focus, pronoun continuation without write, one-shot minimal clarification, materials/review surfaces, dark theme and compact breakpoint.
- Fourteen visual states are stored under `artifacts/context-material-obsidian-v1-screenshots/`; real user-note write states are represented with deterministic temporary data instead of mutating the live Vault.
- Hot reload no longer assumes `containerEl.children[1]`; both main and sidebar views render through stable `ItemView.contentEl`.
- Installed Obsidian now recognizes Chinese and English “organize as a preview, do not save” phrasing, renders the Organization Plan and performs no Markdown write.

## Safety gates retained

- No real Dragonnet `apply-prepared`.
- No real user PDF or note mutation in tests.
- No raw message/pasted body in SQLite.
- No API Key or Authorization in logs or artifacts.

## Remaining lower-priority work

- Heading-aware field Diff editing is P2; V1 existing-note updates use an isolated managed block.
- True streaming/cancellation for model chat remains P2.
