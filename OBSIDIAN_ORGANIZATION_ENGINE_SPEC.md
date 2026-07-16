# Obsidian Organization Engine V1

The engine consumes a Material Bundle, intent, focus and Vault inventory. It detects existing roots, resolves the target by current note, exact title and aliases, then emits an immutable `OrganizationPlan` with actions, paths, sections, merge strategy, risks and confidence.

High-autonomy low-risk operations may create a new `ai-draft`/`agent_managed` note or append a managed block to an ordinary draft after an explicit save request. Every applied action creates an Agent Action, snapshot, before/after hashes, change record and conflict-safe Undo.

Existing `reviewed`, `core`, protected and source notes are never overwritten. They generate an update-suggestion Change Set. Cautious mode, ambiguous targets, conflicts, stale bases, large rewrites, delete/move/rename and protected targets require confirmation.

Models may supply structured content; deterministic code owns result type, destination, permission, transaction and rollback.
