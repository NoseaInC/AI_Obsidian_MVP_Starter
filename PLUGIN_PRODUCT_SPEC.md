# Learning Agent Plugin Product Specification

## Product boundary

Obsidian is the only user-facing application. Learning Agent is a desktop-only Obsidian plugin; its Python runtime is a hidden, plugin-managed localhost process. Markdown is the knowledge source of truth. SQLite contains only recoverable runtime state.

The model may propose structured content, questions and recommendations. It never controls target paths, permissions, apply, rollback, reviewed/core mutation, mastery confirmation, secrets or shell execution.

## Primary workflows

1. Source → Prepare Job → Prepared Bundle → Change Set → explicit Apply → Review → reviewed knowledge.
2. Reviewed knowledge → daily plan → study session → quiz/retelling → mastery suggestion → explicit confirmation.
3. Current idea → Prepared Expansion → Change Set → explicit Apply to managed block.
4. Assistant response → read-only answer or Change Set → explicit confirmation for every write.

## Navigation

- Sidebar: compact service status, today, pending work, jobs and quick actions.
- Workspace views: Dashboard, Task Center, Change Set, Review Center, Study Session, Assistant and Diagnostics.
- Commands expose every primary entry point without requiring the terminal.

## Definition of done

- Plugin starts and monitors the runtime.
- Import creates a visible queued Job immediately and progresses to awaiting confirmation.
- Apply never calls a model and remains transactional/idempotent.
- Review and learning confirmation are complete inside Obsidian.
- All model-driven writes are Change Sets.
- No known P0/P1 security, integrity or usability issue remains.

