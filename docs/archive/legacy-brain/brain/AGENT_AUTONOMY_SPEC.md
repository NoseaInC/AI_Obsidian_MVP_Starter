# Agent Autonomy Spec

Updated: 2026-07-14

## Modes

- Cautious: Today can be recommended; Vault mutations require confirmation.
- Balanced: Agent-managed areas and small ordinary-note maintenance may apply automatically.
- High: low-risk ordinary-note maintenance may apply automatically inside the authorized Vault.

All modes preserve protected/core confirmation, snapshots, audit and Undo. The current default is `high`; the first use displays a permission summary and can be downgraded at any time.

## Allowed automatic operations

- Create notes only in declared Agent-managed roots.
- Refresh a named managed block.
- Adjust Today plans and reschedule non-fixed tasks.

## Never automatic

- Delete files or user prose.
- Overwrite reviewed/core, source material, `agent_access: denied` or `agent_protected: true`.
- Traverse outside the Vault, follow symlink targets, run arbitrary shell/SQL, or use unrestricted local network access.
- Apply a high-risk/bulk/conflicting change.

Protected targets create a traceable update-suggestion Change Set rather than modifying the original.
