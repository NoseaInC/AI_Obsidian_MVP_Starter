# Pi Agent Runtime

## Status

Production architecture as of 2026-07-23. Pi is the sole model–tool loop for
every ordinary interactive surface.

## Runtime placement and compatibility

- Obsidian: 1.12.7
- Obsidian Electron: 39.8.3
- Embedded Node: 22.22.1
- Development Node: 24.15.0
- `@earendil-works/pi-agent-core`: 0.80.10 (exact pin)
- `@earendil-works/pi-ai`: 0.80.10 (exact pin)
- Runtime location: bundled TypeScript inside the Obsidian plugin
- Sidecar: none

Only Pi Agent Core and Pi AI are included. Pi Coding Agent, TUI, default Bash,
generic file tools and unrestricted current-working-directory behavior are not.

## One production loop

```text
User Turn
→ PiAgentRuntime
→ Secure model stream proxy
→ DeepSeek model decision
→ Pi typed Tool Call
→ local policy hook
→ Python restricted tool adapter
→ Tool Result Observation
→ DeepSeek replanning
→ final answer
```

Python never plans a turn or runs a second Agent loop. It owns the Keychain,
model transport, Hybrid Retrieval, PDF and web adapters, Task Authorization,
path policy, transactions, persistence and Undo.

The Main Assistant, contextual learning assistant and user-triggered study-note
generation all use the same `prepareTurn → query → AgentChunk reducer`
pipeline. The retired Brain/intake coordinator is reachable only from explicitly
named compatibility workflows such as Prepared PDF handling; it is not a
fallback for ordinary chat, learning or note generation.

## Runtime modules

- `PiAgentRuntime` owns a Pi Agent per conversation and provides query,
  reconnect, cancel, compact, fork, Steering and Follow-up.
- `PiModelTransport` maps the secure NDJSON proxy to Pi provider events.
- `PiToolAdapter` exposes only contracts returned by the local service and
  converts recoverable failures into Observations.
- `PiEventAdapter` persists ordered `AgentChunk` events before UI delivery.
- `PiCompaction` preserves whole turns and paired tool calls/results.
- `PiStallGuard` detects identical calls/results and bounded execution stalls.
- `TaskAuthorization` creates a per-Turn, run-expiring scope from structured
  Obsidian context. It never parses intent keywords.

## Stable tools

Knowledge/Vault/PDF/web tools are read-only and may run in parallel. Reversible
write and developer tools are serial. The service publishes the authoritative
contracts at `GET /api/v1/tools/contracts`; the model cannot invent authority.

Writing uses `plan_vault_change` and `apply_vault_change`. Apply requires the
current Task Authorization and performs writer lock, path/protection checks,
base-hash validation, snapshot, atomic write, after-hash verification and Action
Journal commit. The result contains file statistics, Diff and Undo capability.
Ordinary authorized Markdown work never pauses for repeated approval.

## Authorization and interruption

Each Turn receives a Task Authorization with resource scope, operation scope,
network policy and `reversibleOnly`. Only real scope expansion, irreversible
work or external side effects can produce an inline question. Protected/core,
path traversal, symlink escape, stale hashes and failed verification are hard
denials or conflicts and cannot be bypassed by confirmation.

The Turn begins with context-derived resources but no prose-derived write
permission. The first structured `plan_vault_change` establishes the concrete
operation and resource scope. That scope is then frozen for the rest of the
Turn: a later plan cannot silently add a new path, writable root or operation.
Pi must surface a scope-expansion question or start a new user-authorized Turn.
This is action validation, not keyword intent classification.

An inline permission request is a persisted paused Tool Call, not a completed
Turn. After a plugin or Runtime restart the service validates the original
Run, Authorization, Tool Contract, arguments, path/workspace guards and absence
of a completed Tool Result. Approval or denial then resolves that same
`runId + toolCallId`; it never creates a replacement Run or silently replans the
operation.

## Session and events

The append-only Session Tree stores message, tool, authorization, action,
compaction, steering and follow-up references. It does not store secrets or
knowledge-file bodies. A completed Tool Result retains a secret-free
`modelObservation` capped at 12 KiB so structured hits, paths, necessary
excerpts and pagination survive restart without retaining a full long note or
command log. The persisted Session Projection is the only restart
recovery source for Pi context; renderer memory and conversation-message
shortcuts are not authoritative. Projection follows one `parent_id` lineage,
restores standard Pi user/assistant/Tool Call/Tool Result shapes, and excludes
sibling branches and provider reasoning. Every UI event has
session/run/turn identifiers and a strictly increasing sequence. Reconnect
reads the durable event stream.

Editing or regenerating forks from a resolved persisted Entry boundary. A
boundary inside a Tool Call/Tool Result pair moves to the preceding complete
Entry. Regeneration keeps already completed Actions but excludes the old answer,
future history and pending authority; the new branch starts with fresh
Run-scoped authorization. Steering is injected at a safe tool boundary;
Follow-up begins a new Turn after the current one. Cancel propagates an
AbortSignal to Pi, model transport and tools.

Each Run also persists the selected Profile ID, model and provider-adapter
version. Fork uses that Run-specific identity after a cold start. Provider
errors preserve authentication, rate-limit, model-not-found, context-limit,
provider and stream-protocol categories; only the actual hard timer is a request
deadline.

Provider reasoning prose is ephemeral. It may exist in memory while Pi consumes
the current provider stream, but the durable/UI event is only
`reasoning_status(started|completed, tokenCount)`. Reconnect, Session Projection,
conversation metadata and DOM never receive the original delta text.

## Compaction

Compaction triggers from provider usage, falling back to token estimation. It
cuts only at complete Turn boundaries and never separates a Tool Call from its
Tool Result or removes pending questions/actions. Recent context is retained by
token budget, not fixed message count. Summaries exclude secrets, private chain
of thought, full sensitive bodies and unnecessary tool arguments.

Compaction is persistence-first: the Runtime constructs a bounded structured
checkpoint from the selected Session Projection, Focus, attachment metadata,
Action references, pending results and workspace authorization, atomically
persists the checkpoint Entry, and only then replaces in-memory messages. A
persistence failure leaves the exact live history intact. Restart recovery
reuses the checkpoint plus entries after `keptFromEntryId`; it does not invent
a fake user message or merge sibling lineage.

## Learning surfaces

The contextual learning assistant sends bounded course, section, Lesson
Version, relevant-note and source metadata through the shared Pi pipeline. It
defaults to network-off and read-only. Study-note generation begins only after
the user's explicit click and is accepted by the UI only when Pi returns a real
verified `apply_vault_change` Action under `01-Inbox` or
`20-Knowledge/Drafts`. The result exposes View Changes and conflict-safe Undo.
No learning surface may write `reviewed` or `core`; those states remain
immutable and receive update suggestions instead.

## Model capability resolution

Capabilities are probed with a minimal, non-sensitive request and cached by
profile/model/base URL/safe header names/adapter version. Unsupported or
inconclusive features resolve conservatively. API keys never enter the key.

## Developer execution plane

Developer tasks use a task branch and Git worktree beneath
`90-Local-Only/Agent/DeveloperWorkspaces`. Structured commands are preferred.
`run_bash` is sandboxed, network-denied by default, uses a temporary HOME/TMPDIR,
has bounded/redacted output and cannot inherit API keys, SSH agents or cloud
credentials. It cannot escape the worktree or invoke denied system commands.

Runtime activation is deliberately two phase. Python verifies that the task
branch is merged, the project working tree is clean and `HEAD` matches the
merged commit, then returns a fixed activation request. The Obsidian plugin—not
the model and not a Python planner—runs fixed check/build/install steps,
restarts the localhost Runtime and verifies `/health`. If a step fails, the
plugin invokes the governed rollback tool, rebuilds from the restored source
and checks health again. Model-authored shell strings are never activation
inputs.

## Architecture invariants

- Production contains no `pydantic_ai`, Pydantic runtime or retired coordinator.
- No keyword/regex/fixed-phrase Intent Router exists.
- Ordinary Main Assistant, learning and study-note turns never call the legacy
  Brain/intake workflow.
- UI renders only normalized `AgentChunk` events and cannot write Vault files.
- Persisted Session Projection is the only Pi restart-recovery source.
- Fork and regeneration resolve persisted Entry boundaries, not message indexes.
- Pending permission survives restart only as the original validated Tool Call.
- Compaction changes memory only after its structured checkpoint is durable.
- The model proxy cannot apply changes.
- The model cannot bypass Task Authorization or protected-note policy.
- Action Journal is recovery infrastructure, never an approval inbox.
