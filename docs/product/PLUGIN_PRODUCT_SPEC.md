# Learning Agent Plugin Product Specification

## Product boundary

Obsidian is the only user-facing application. Learning Agent is a desktop-only Obsidian plugin; its Python runtime is a hidden, plugin-managed localhost process. Markdown is the knowledge source of truth. SQLite contains only recoverable runtime state.

The model may select typed tools and propose structured work. It never controls path safety, protected status, transaction commit, rollback, mastery confirmation, secrets or unrestricted shell execution.

## Primary workflows

1. Source → Prepare Job → Prepared Bundle → Change Set → explicit Apply → Review → reviewed knowledge.
2. Reviewed knowledge → daily plan → study session → quiz/retelling → mastery suggestion → explicit confirmation.
3. Current idea → Prepared Expansion → Change Set → explicit Apply to managed block.
4. Assistant task → Pi tool loop → Task Authorization → reversible transaction → verified Action Result → optional Diff/Undo.

## Navigation

- Sidebar: compact service status, today, pending work, jobs and quick actions.
- Workspace views: Today, Materials, Plan, Assistant, Study Session and Diagnostics. There is no standalone daily approval inbox.
- Commands expose every primary entry point without requiring the terminal.

## Definition of done

- Plugin starts and monitors the runtime.
- Import creates a visible queued Job immediately and progresses to awaiting confirmation.
- Apply never calls a model and remains transactional/idempotent.
- Review and learning confirmation are complete inside Obsidian.
- All model-driven writes use internal plans, snapshots, verified reversible transactions and Action Journal records. Ordinary in-scope Markdown writes do not ask twice.
- No known P0/P1 security, integrity or usability issue remains.
