# Material to Obsidian V2 Security Review

## Controls

- Paths are Vault-relative, resolved, traversal checked and symlink rejected.
- Only named safe roots accept new automatic drafts.
- Automatic notes require `status: ai-draft|draft|inbox` and `agent_managed: true`.
- Existing content is preserved; automatic updates use a named managed block.
- reviewed/core/source/protected targets generate a Change Set update suggestion.
- Every automatic write is atomic and records snapshot, hashes, change summary, source conversation and Undo boundary.
- Undo stops if the target changed after the Agent action.
- Conversation/PDF/text bodies remain local files; database records are bounded references.
- Token-like entities, secrets and protected headers are rejected/redacted.
- Web content remains untrusted and cannot alter tools or policy.

## P0 / P1

None known after offline context/material, privacy, protected-target, traversal, migration and rollback checks. First real PDF Apply remains user-gated.
