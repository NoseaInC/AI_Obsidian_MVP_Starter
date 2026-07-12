# Technical Decisions

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
