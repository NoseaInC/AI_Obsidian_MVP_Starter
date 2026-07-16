# Vault Permission and Undo Spec

Updated: 2026-07-14

## Read scope

The Runtime may read authorized Markdown, frontmatter, links/backlinks, folder structure and explicitly selected attachments. `Private/`, `Personal/`, `Secrets/`, symlinks and `agent_access: denied` are excluded.

## Write protocol

1. Resolve and classify the target.
2. Verify mode, scope, status and expected hash.
3. Create an `agent_action` and private snapshot.
4. Apply an atomic write to a declared managed block or managed path.
5. Verify the after hash and record a file change.
6. Expose “查看变化” and “撤销” in diagnostics.

Undo verifies that the current file still matches the recorded after hash. If the user edited the file later, Undo stops with a conflict instead of overwriting that edit. Undo itself creates an audit action.

Diagnostics expose action type, target, status, bounded diff summary and snapshot reference; they never expose the snapshot body or secrets.
