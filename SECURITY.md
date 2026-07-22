# Learning Agent Security Model

## Trust boundaries

- Plugin UI is trusted to request restricted business actions, never arbitrary filesystem or shell actions.
- Runtime owns validation, paths, policy, transactions and audit.
- Model output is untrusted structured input.
- Markdown is authoritative; SQLite indexes are rebuildable.

## Runtime channel

- Bind only to `127.0.0.1`.
- A random per-plugin-session bearer token authenticates business API requests.
- Token is passed only through the child-process environment and held in plugin memory.
- Token is never written to Markdown, SQLite, settings, logs or model prompts.
- Runtime ID is non-secret and permits safe lifecycle matching. A stale PID is terminated only when health reports the exact current Vault path; an unrelated localhost process is never killed.
- Health exposes protocol/service readiness and local Vault/runtime counters; CORS prevents web origins from reading it. Business data always requires the bearer token.
- Errors use a structured envelope with code, message, retryability and details.

## Filesystem

- Resolve every path and reject traversal or symlink escape.
- All knowledge mutations pass through Task Authorization and the reversible transaction layer. Change Sets are internal plans, not approval records.
- The Harness validates each concrete action against task scope, safe roots, base hashes and protection state. In-scope reversible Markdown creates and updates apply directly; only scope expansion or external/irreversible effects ask the user.
- Every successful Harness commit is verified against the immutable Change Set payload hashes before the Run can report success.
- `reviewed/core` prose is immutable; only explicit user-confirmed learning metadata transitions are permitted.
- Human content outside managed blocks is preserved.
- High autonomy is still scoped: only Agent-managed creation and small managed-block maintenance may apply automatically. Each automatic change creates a private snapshot, bounded diff record and audit action.
- Undo refuses to run if the current target changed after the Agent action, preventing restoration over a later user edit.
- `Private/`, `Personal/`, `Secrets/`, `agent_access: denied`, `agent_protected: true`, source material and reviewed/core remain outside automatic mutation.
- Task authorization never permits deletes, moves, protected/core changes, path escape, stale-hash overwrite or real Prepared PDF Apply unless a separate governed workflow explicitly supports the action.
- The first structured write plan freezes the Turn's operation/path scope. A later plan that adds a target, root or operation is rejected as scope expansion and cannot inherit authority from model prose.
- Conversation Focus, Material Bundles and Knowledge Units store named entities and references only. Long token-like entities and raw pasted/message bodies are excluded from SQLite.

## Secrets

- Development compatibility: environment variables.
- Product preference: macOS Keychain through a testable KeyStore abstraction.
- Never display, log or persist full keys in plugin data.
- V2 uses `MacKeychainStore` with fixed subprocess argv and no shell interpolation. Only Key references, configured state and masked hints leave the Runtime.
- A Profile does not own its Key reference; deleting a Profile never deletes a shared/pre-existing Keychain secret.
- Provider custom headers reject Authorization, Host, Content-Length and newline injection. External Base URLs require HTTPS; local HTTP is limited to loopback hosts.

## Pi runtime and Harness

- Full requests and Change Set bodies remain under `90-Local-Only`; SQLite contains hashes, summaries and references only.
- Every write-producing tool creates an internal plan. Apply rechecks authorization, payload hash, path and symlink policy, base hashes and reviewed/core status under the writer lock, then snapshots, atomically writes, verifies and records conflict-safe Undo.
- Research source fetch rejects credentials in URLs, private/non-global destinations, unsafe redirects, unsupported content types and oversized bodies.
- External source adapters are unavailable by default and provider failures are surfaced rather than replaced with invented content.
- Tool events, error responses and exported diagnostics pass through secret and long-text redaction.
- Public web content is wrapped as untrusted source text; embedded instructions cannot grant tools, filesystem access or policy changes.
- Conversation export stays under `90-Local-Only`. Summary-only retention and deletion require explicit confirmation; bulk conversation deletion is never implicit.
- Runtime activation accepts no model-authored command. Python validates a merged, clean, exact commit; the plugin runs only fixed project scripts with a scrubbed environment, restarts the loopback service, checks health and performs governed source rollback plus rebuild if activation fails.

## Security test checklist

Token enforcement, CORS/preflight, localhost binding, path traversal, symlink escape, Bundle/Change Set/private-request tampering, transaction rollback, reviewed/core protection, log redaction, duplicate Jobs/Runs, SSRF redirects, oversized responses, timer/process cleanup and safe Markdown rendering. Automated backend and plugin coverage plus a real-Obsidian visual pass form the release gate.
