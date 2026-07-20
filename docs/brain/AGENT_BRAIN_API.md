# Retired Python Brain API

This document is a migration tombstone. The Python Agent Brain and its
`/api/v1/brain/*` and `/api/v1/brain-change-sets/*` routes are no longer part of
the production architecture.

## Canonical interactive runtime

Interactive Assistant turns use:

- `POST /api/v1/model/stream` for the Keychain-backed secure model transport;
- `GET /api/v1/tools/contracts` and `POST /api/v1/tools/call` for typed tools and Observations;
- `POST /api/v1/task-authorizations` for the current Turn's structured authority;
- `POST /api/v1/agent/events` and `GET /api/v1/agent/runs/{id}/events` for durable ordered events;
- `POST /api/v1/agent/runs/{id}/control` for Steering and Follow-up;
- `POST /api/v1/agent/runs/{id}/cancel` for real cancellation;
- `GET /api/v1/agent/sessions/{id}` for Session Tree and conversation recovery;
- `GET /api/v1/actions/{id}`, `GET .../diff` and `POST .../undo` for verified action results.

Pi Agent Core and Pi AI run the only production model–tool loop inside the
Obsidian TypeScript plugin. Python supplies facts, secrets, policy, storage and
execution. It never selects the next tool or replans a turn.

## Structured non-chat projections

`POST /api/v1/workflows/{mode}` supports only an explicit mode supplied by the
caller: `qa`, `tutor`, `capture`, `save`, `organize`, `material`, `research` or
`plan`. It executes one registered workflow and does not classify natural-language
intent. Runtime facts are exposed by `/api/v1/runtime/capabilities` and
`/api/v1/runtime/diagnostics`.

## Persistence compatibility

Some SQLite tables and migration helpers retain `brain_*` names so existing local
state can still be opened. They are storage compatibility names, not a second
Agent runtime. Production architecture tests prohibit imports of the retired
Brain/Pydantic planners and prohibit the old HTTP routes.

Provider `reasoning_content` crosses the secure provider/Pi boundary only as a
typed, ordered `reasoning` `AgentChunk`. The Assistant may render that exact block
in a collapsible section, but must not synthesize or enrich it. It is never written
to Markdown, stored as ordinary conversation text or inserted into future model
context.
