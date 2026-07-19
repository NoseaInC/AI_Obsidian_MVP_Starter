# Pi Agent Runtime Acceptance

Date: 2026-07-20

Branch: `pi-agent-runtime`

Production model used for the real gate: `deepseek-v4-pro`

## Offline release gate

`./scripts/check.sh` passed with:

- PDF ingestion, Prepared Bundle, conversation and review: 40 tests;
- Python Agent/Runtime/security suite: 144 tests;
- Obsidian plugin suite: 72 tests;
- TypeScript strict typecheck: passed;
- production plugin build: passed.

The suites cover protected/reviewed/core refusal, path and symlink escape,
stale/tampered hashes, atomic rollback, exact-byte Undo, Task Authorization
scope freeze, Key redaction, SSRF, Session Tree recovery, Tool-safe compaction,
Steering, Follow-up, Hybrid Retrieval, Capability resolution, controlled
developer commands and plugin-owned activation rollback.

The Python test process still reports non-failing `ResourceWarning` messages for
several legacy test-created SQLite connections. They do not affect correctness
but remain a P2 cleanup item.

## Real DeepSeek A–H gate

Command:

```bash
python3 scripts/real_pi_runtime_acceptance.py
```

The runner creates a temporary Vault, database and Git project, resolves the
already configured Keychain reference without printing or copying the secret,
starts an authenticated loopback Runtime and drives the production
`PiAgentRuntime`. No real Vault Markdown file is used or modified.

| Case | Result | Observed production tools / invariant |
| --- | --- | --- |
| A — current note | Passed | `get_current_note` → answer |
| B — corroborating Vault knowledge | Passed | Focus plus `get_vault_overview`, `list_vault_folder`, `read_vault_note`; Pi selected equivalent retrieval tools without a fixed router |
| C — organize into current note | Passed | read → `plan_vault_change` → `apply_vault_change` → reread; write verified and no confirmation appeared |
| D — Undo | Passed | `undo_agent_action`; exact original bytes restored |
| E — plan only | Passed | read → plan; target bytes unchanged |
| F — runtime development | Passed | worktree → read/write → four controlled commands → diff → commit → merge → `activate_runtime_upgrade`; tests/build and activation protocol verified |
| G — Steering | Passed | in-flight direction change accepted at a safe boundary and replanned to statistics-only sources |
| H — Follow-up | Passed | three practice-question follow-ups queued after the active Turn |

Case F validates the real model/tool/merge/activation handshake and live health
response. The actual Obsidian process restart and rollback implementation is
validated offline because deliberately restarting the user's live Obsidian
process is outside a temporary-Vault acceptance run.

## Architecture result

- Pi Agent Core and Pi AI are the only production model/tool loop.
- Python is the secure model, fact, policy, retrieval, transaction and storage
  boundary; it does not select the next tool.
- PydanticAI and the old Python Brain planner are absent from production.
- No keyword, regex, token or fixed-phrase Intent Router exists.
- Provider reasoning remains protocol-private and is not rendered or persisted.
- Ordinary in-scope reversible Markdown changes apply without a second approval
  and always produce snapshot, hashes, verification, Diff and conflict-safe Undo.
- The first plan freezes the Turn's concrete resource/operation scope; expansion
  requires a new user-authorized step.

The deliberate first real Dragonnet `apply-prepared` gate was not executed and
remains separately user-controlled.
