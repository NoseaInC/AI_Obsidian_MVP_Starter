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
- All knowledge mutations pass through Change Set and transaction layers.
- The Harness, not the model or UI, classifies each Change Set as auto-apply, inline confirmation or blocked. Auto-apply is limited to explicit, low-risk new files in configured Draft/Inbox roots.
- Every successful Harness commit is verified against the immutable Change Set payload hashes before the Run can report success.
- `reviewed/core` prose is immutable; only explicit user-confirmed learning metadata transitions are permitted.
- Human content outside managed blocks is preserved.
- High autonomy is still scoped: only Agent-managed creation and small managed-block maintenance may apply automatically. Each automatic change creates a private snapshot, bounded diff record and audit action.
- Undo refuses to run if the current target changed after the Agent action, preventing restoration over a later user edit.
- `Private/`, `Personal/`, `Secrets/`, `agent_access: denied`, `agent_protected: true`, source material and reviewed/core remain outside automatic mutation.
- Explicit save in high-autonomy mode permits only marked new drafts and managed-block appends under approved roots. The permission does not extend to deletes, moves, renames, bulk rewrites or real Prepared PDF Apply.
- Conversation Focus, Material Bundles and Knowledge Units store named entities and references only. Long token-like entities and raw pasted/message bodies are excluded from SQLite.

## Secrets

- Development compatibility: environment variables.
- Product preference: macOS Keychain through a testable KeyStore abstraction.
- Never display, log or persist full keys in plugin data.
- V2 uses `MacKeychainStore` with fixed subprocess argv and no shell interpolation. Only Key references, configured state and masked hints leave the Runtime.
- A Profile does not own its Key reference; deleting a Profile never deletes a shared/pre-existing Keychain secret.
- Provider custom headers reject Authorization, Host, Content-Length and newline injection. External Base URLs require HTTPS; local HTTP is limited to loopback hosts.

## Agent Brain

- Full requests and Change Set bodies remain under `90-Local-Only`; SQLite contains hashes, summaries and references only.
- Every write-producing Skill creates a Change Set. Apply rechecks payload hash, path policy, symlink policy, base hashes, reviewed/core status and explicit confirmation under the writer lock.
- Research source fetch rejects credentials in URLs, private/non-global destinations, unsafe redirects, unsupported content types and oversized bodies.
- External source adapters are unavailable by default and provider failures are surfaced rather than replaced with invented content.
- Tool events, error responses and exported diagnostics pass through secret and long-text redaction.
- Public web content is wrapped as untrusted source text; embedded instructions cannot grant tools, filesystem access or policy changes.
- Conversation export stays under `90-Local-Only`. Summary-only retention and deletion require explicit confirmation; bulk conversation deletion is never implicit.

## Security test checklist

Token enforcement, CORS/preflight, localhost binding, path traversal, symlink escape, Bundle/Change Set/private-request tampering, transaction rollback, reviewed/core protection, log redaction, duplicate Jobs/Runs, SSRF redirects, oversized responses, timer/process cleanup and safe Markdown rendering. Automated backend and plugin coverage plus a real-Obsidian visual pass form the release gate.
