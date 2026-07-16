# Material to Obsidian V2 Visual Review

Last updated: 2026-07-14

## Result

The installed Obsidian build was reloaded and inspected in Obsidian 1.12.7. The Assistant now shows “当前理解”, the resolved material/method, organization activity and governed recent changes. The stale “写入先审核” label is gone; the UI now says low-risk writes are undoable while reviewed/core remains protected.

A hot-reload lifecycle failure (`containerEl.children[1]` missing while Obsidian restored a leaf) was reproduced during this review. The main view now renders through the stable `ItemView.contentEl`; a second real reload completed without a startup notice. This P0 is covered by the plugin lifecycle contract test.

## Screenshot matrix

All files are under `artifacts/context-material-obsidian-v1-screenshots/`:

- `assistant-delta-focus.png`: installed Obsidian, real DeepSeek answer, active Delta Method focus and rendered formula.
- `assistant-pronoun-resolved.png`: installed Obsidian, “继续解释这个方法，但不要保存” keeps Delta Method active and produces no write.
- `assistant-minimal-confirmation.png`: installed Obsidian, unresolved pronoun receives one compact clarification and a non-writing preview.
- `assistant-pdf-understanding.png`: installed Obsidian materials center with real material/runtime rows; no missing Prepared Bundle error.
- `assistant-text-understanding.png`: installed Obsidian, classroom text plus URL produces an explicit preview-only result.
- `assistant-mixed-material.png`: installed Obsidian, mixed text/URL context is visible, an Organization Plan is rendered and no write result exists.
- `assistant-organization-plan.png`: installed Obsidian, organization plan, suggested target path and confirmation state after the real no-save smoke.
- `assistant-diff.png`: installed Obsidian review surface with Preview/Diff/Original/User-edit controls.
- `assistant-protected-review.png`: installed Obsidian review surface; accepted/protected knowledge remains review-governed.
- `assistant-math-rendering.png`: installed Obsidian Delta Method response with display math.
- `assistant-dark.png`: installed Obsidian dark theme.
- `assistant-narrow.png`: installed Obsidian at the compact breakpoint; detail collapses and navigation becomes icon-only.
- `assistant-write-result.png`: deterministic temporary-data render of the production CSS/contract for path, snapshot, Diff, Open and Undo actions.
- `assistant-undo.png`: deterministic temporary-data render of conflict-safe snapshot restoration and audit feedback.

The last two states intentionally use deterministic temporary data. Triggering a real write only to obtain a screenshot would violate the no-real-user-note-mutation acceptance gate. Their behavior is validated separately by temporary-Vault E2E tests.

## Visual P0/P1 review

- Main/list/context columns own their scroll regions and the composer remains fixed.
- DeepSeek model identity is visible in the header; the answer no longer claims to be Tongyi/Qwen.
- Formula blocks render through Obsidian Markdown rather than raw delimiters.
- Dark and compact states remain readable; no icon/text negative offsets were found.
- Startup reload, context headings and autonomy wording match the current build.
- “整理成预览，但不要保存” no longer falls through to an answer-only state; the installed smoke shows the plan and no Vault mutation.
- No known visual P0/P1 remains.
