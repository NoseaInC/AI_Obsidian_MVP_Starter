# Learning Brain Implementation Status

Updated: 2026-07-14

## Complete

- Private conversations, summaries, signals, per-conversation personalization and data controls.
- Ordinary-answer vs Artifact vs Change Set vs Today-action separation.
- Three explainable learning-direction horizons and persisted Today plan/Undo.
- Exact deterministic ranking weights with conversation evidence fixed at 12%.
- Unified non-PDF Material Artifacts and cached public-web Research Bundles.
- SSRF, redirect, size/type and prompt-injection controls.
- High/balanced/cautious Vault autonomy, first-use summary, snapshots, audit, view changes and conflict-safe Undo.
- Native Obsidian Markdown/formula rendering across Assistant and Review.
- Expanded redacted diagnostics and 160-case free-text/adversarial corpus.

## Verification

- Focused Python: 60 passed, 1 localhost-only test skipped by the managed sandbox.
- Unified gate: 40 ingestion/review tests passed; 101 Agent tests passed or were environment-skipped (9 socket-only tests); 29 plugin tests, typecheck and production build passed.
- Installed `main.js`, `styles.css` and `manifest.json` match their build-source SHA-256 values.
- No real model, real web provider, real PDF or real `apply-prepared` was used in this implementation pass.

## Environment limitation

The current managed sandbox rejects localhost socket binding and blocks browser navigation to `file://` visual previews. Those environment-specific checks remain executable on the normal desktop runtime; all no-network business/security contracts run offline here.

## Data gate

The first real Dragonnet `apply-prepared` remains explicitly user-gated.
