# Pi Agent Runtime Acceptance

Date: 2026-07-23

Branch: `pi-runtime-hardening-repair`

Current repair gate: deterministic provider; no real model, Keychain reference,
user Vault or running Obsidian process was used.

## Post-R08 production-semantic closure

The latest follow-up closes four restart/privacy/error gaps:

- completed Tool Results recover a secret-free, model-usable Observation capped
  at 12 KiB, including migration/backfill for pre-fix Session shells;
- provider reasoning prose is absent from SQLite, reconnect events,
  conversation metadata and UI; only phase/Token status survives;
- cold-start Fork restores Profile, model and adapter identity from the source
  Run;
- provider/auth/rate-limit/model/context/protocol errors remain classified, and
  only the hard timer reports a request deadline.

Latest `./scripts/check.sh` result:

- 40/40 ingestion/review tests;
- 240/240 Python Agent/Runtime/security tests;
- 165/165 plugin tests;
- strict TypeScript typecheck and production build passed.

This is still code-level, transaction-level and deterministic Runtime
acceptance. A real DeepSeek request and actual Obsidian reload of this build
remain a separate smoke test.

## R08 temporary-environment A–S acceptance

Command:

```bash
python scripts/pi_runtime_repair_acceptance.py
```

Result: 19/19 passed. The runner removes secret-like environment variables and
places every test fixture under a temporary root. A–C drive the bundled
production `PiAgentRuntime` with a deterministic model transport and real
temporary Markdown files. D–E and P–S execute the production transaction and
developer-workspace implementations against temporary SQLite/Vault/Git state.
F–O execute the production TypeScript Runtime, persistence, recovery, fork,
compaction and shared learning-turn code.

This is a real headless production-code acceptance, not a live user-Obsidian UI
session. It intentionally does not call a real provider or restart/install the
user's plugin, so it must not be cited as evidence of manual UI clicking or a
live model response.

| Case | Result | Executed invariant |
| --- | --- | --- |
| A — ordinary Q&A | Passed | Bundled Pi Runtime completed one deterministic text Turn |
| B — current note | Passed | `get_current_note` read a real temporary note and its Observation returned to Pi |
| C — cross-Vault | Passed | `search_vault` → `read_vault_note` traversed real temporary Markdown |
| D — direct reversible write | Passed | planned write applied atomically, verified and produced Diff/Undo without repeated confirmation |
| E — Undo | Passed | authorized update restored the exact prior snapshot |
| F — permission card | Passed | Tool Call paused and resumed under the same Run |
| G — reload waiting | Passed | two Runtime recreations preserved one pending Tool Call and Authorization |
| H — recover approve | Passed | rebuilt card approved and continued the original call |
| I — recover deny | Passed | denial injected one blocked Observation without executing the tool |
| J — Fork | Passed | persisted Entry boundary excluded future history without mutating the source |
| K — Regenerate | Passed | pre-answer boundary preserved completed Actions and removed the old answer |
| L — Compaction | Passed | no Tool Call/Tool Result pair was split |
| M — compaction restart | Passed | persisted checkpoint was recoverable after Runtime recreation |
| N — learning assistant Q&A | Passed | bounded learning context used the shared Pi prepare/query/reducer path with network off |
| O — generate study note | Passed | bundled Pi plan/apply loop atomically wrote a temporary Draft and shared reducer projected its verified Action Result |
| P — Developer Workspace | Passed | isolated temporary Git worktree and atomic project write |
| Q — controlled Bash | Passed | sandboxed Bash ran inside the worktree with network denied |
| R — Skill Draft | Passed | valid draft accepted and secret-accessing draft rejected |
| S — MCP Validate/Probe | Passed | temporary MCP server validated, started in minimal environment and listed tools |

## R08 full release gate

The final full-suite was run after the A–S gate and before the final repair
commit:

- Python compileall: passed;
- Python Agent/Runtime/security suite: 236/236 passed;
- Obsidian plugin suite: 163/163 passed;
- TypeScript strict typecheck: passed;
- production plugin build: passed;
- unified `./scripts/check.sh`: passed (40/40 ingestion/review tests,
  236/236 Python Agent tests, 163/163 plugin tests, typecheck and build).

The existing non-failing legacy SQLite `ResourceWarning` messages remain P2
test-resource hygiene; no gate failed.

## Historical 2026-07-20 offline release gate

`./scripts/check.sh` passed with:

- PDF ingestion, Prepared Bundle, conversation and review: 40 tests;
- Python Agent/Runtime/security suite: 147 tests;
- Obsidian plugin suite: 76 tests;
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

## Historical real DeepSeek A–H gate

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
- Dedicated provider reasoning prose remains ephemeral inside the current
  protocol exchange. Durable Pi events and UI retain only phase/Token status;
  reconnect, Markdown, ordinary conversation metadata and future model context
  contain no reasoning prose.
- Stall limits are scoped to one Run and reset on every new Turn, so a long-lived conversation cannot fail its next request merely because the conversation itself is older than thirty minutes.
- Ordinary in-scope reversible Markdown changes apply without a second approval
  and always produce snapshot, hashes, verification, Diff and conflict-safe Undo.
- The first plan freezes the Turn's concrete resource/operation scope; expansion
  requires a new user-authorized step.

The deliberate first real Dragonnet `apply-prepared` gate was not executed and
remains separately user-controlled.
