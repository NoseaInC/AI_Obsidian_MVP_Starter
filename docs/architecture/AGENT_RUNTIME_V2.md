# Agent Runtime V2

Last updated: 2026-07-16

## Outcome

The production Assistant no longer bypasses the Agent runtime. Ordinary turns now follow one auditable path:

```text
Obsidian Assistant
→ /api/v1/assistant/stream
→ Conversation Focus + Context Builder
→ AssistantRunCoordinator
→ registered read-only Tool contracts
→ local Tool execution and audit
→ model answer grounded in Tool observations
→ persisted Brain Run and NDJSON events
```

Explicit write requests continue through `/intake/submit` and the existing Brain / Change Set transaction path. This is intentional: a chat model never receives direct write, shell, SQL or unrestricted network authority.

## Runtime ownership

- Python owns Keychain access, model calls, local PDF extraction, context assembly, Tool authorization, Brain state, audit, Change Sets and transactions.
- TypeScript owns Obsidian interaction, NDJSON reduction, cancellation, rendering and human approval controls.
- Markdown remains the knowledge source of truth. SQLite stores Run state and bounded summaries; full prompts and results remain local-only files.

Changing language is allowed when it materially improves the boundary. Moving filesystem policy or transactions into the plugin would duplicate authority and is therefore rejected.

## Architectural references

Runtime V2 borrows patterns, not source code or framework dependencies:

- OpenAI Agents SDK: a small run loop composed from tools, guardrails, sessions, human-in-the-loop and tracing. <https://github.com/openai/openai-agents-python>
- LangGraph: explicit persisted state, durable recovery and human inspection for long-running workflows. <https://github.com/langchain-ai/langgraph>
- Pydantic AI: provider-neutral model adapters, typed tool inputs, dependency boundaries and per-call tool approval. <https://github.com/pydantic/pydantic-ai>
- Cline: a host-application assistant where tool execution remains visible and high-impact actions require approval. <https://github.com/cline/cline>

The project deliberately keeps its own smaller coordinator because importing a general multi-agent framework would duplicate the existing StateStore, Policy, Change Set and Obsidian event contracts. Handoffs and unrestricted code execution are not part of Runtime V2.

## Run lifecycle

```text
created
→ understanding
→ planning
→ awaiting_authorization
→ running
→ verifying
→ completed | awaiting_confirmation | failed | cancelled
```

Assistant streaming emits schema-v1 additive events:

- `run.started`
- `context.resolved`
- `plan.created`
- `tool.requested`
- `tool.started`
- `tool.completed`
- `step.updated`
- `message.started`
- `message.delta`
- `message.completed`
- `approval.required`
- `run.completed`
- `run.failed`

The plugin renders these events as actual execution state. It does not invent Tool completion.

## Tool Broker

Every model-visible Tool has:

- a stable name and human description;
- a concrete JSON Schema;
- local argument validation;
- mutation and network flags;
- an audit event;
- bounded output before it returns to the model.

The interactive model receives only a per-run allow-list of read-only Tools. The current set includes Vault overview/search, note metadata/excerpts, related notes, reviewed/core learning state, due reviews, recent materials and local/imported academic sources.

Vault reads use one incremental in-memory index shared by the registered read tools. It only indexes Markdown under `00-Inbox`, `01-Inbox`, `10-Sources`, `20-Knowledge`, `30-Learning` and `40-Projects`; `90-Local-Only`, project source, dependencies and root engineering documents are excluded. Exact note reads enforce the same policy independently of the UI.

Mutation Tools such as `create_change_set` and `apply_confirmed_change_set` are never exposed in ordinary chat. If a model invents or requests one, the Broker blocks it before handler execution and records `brain.tool-blocked` without storing the prompt.

## Model orchestration

- Profiles declaring Tool Calling use a bounded native model/Tool loop with at most three planning rounds.
- Profiles without Tool Calling use deterministic intent-aware retrieval, followed by the same Tool Broker and grounded final model call.
- Greetings and model-identity questions skip unnecessary Vault scans.
- Vault-overview requests use `get_vault_overview`; ordinary knowledge questions search and then read bounded excerpts of matching notes.
- Explicit current-note requests read that note directly. Negative constraints such as “不要搜索其他笔记” are honored and do not trigger a wider retrieval pass.
- The final model context is token-bounded, source-labelled and secret-redacted.
- Attachment and note text is explicitly labelled as untrusted data, never as Runtime instructions.

This preserves useful Agent behavior even when an OpenAI-compatible provider has unreliable Tool Calling.

## Recovery and safety

- Client cancellation closes the upstream stream, retains only received partial text and marks the Run cancelled.
- Runtime restart marks interrupted transient Runs as `brain_runtime_interrupted` instead of leaving false “running” state.
- Duplicate model Tool calls are skipped.
- Formal `reviewed` and `core` Markdown remains read-only.
- No Agent path can perform first real `apply-prepared` without the existing explicit gate.

## Offline acceptance

Offline tests cover native Tool selection, deterministic fallback, Tool audit persistence, invented mutation Tool denial, strict Tool arguments, safe Vault roots, overview/search/current-note routing, secret redaction, interrupted-run recovery, NDJSON event reduction and approval semantics. No automated test uses a real model, network service, API key or real PDF.

An explicitly authorized manual production smoke additionally used the configured DeepSeek profile. It verified actual Vault overview, natural-language search plus three note excerpts, native note selection, and an exact-note request whose final audit contained only metadata, excerpt and related-note reads. No mutation tool, Change Set apply, reviewed/core write or `apply-prepared` operation was executed.
