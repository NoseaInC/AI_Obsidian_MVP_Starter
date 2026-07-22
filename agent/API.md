# 知序 Local API v1

All business endpoints are under `/api/v1`, require the in-memory bearer token, and return structured errors. `/health` is the limited public readiness endpoint.

## Pi runtime boundary

- `POST /api/v1/model/stream` — authenticated NDJSON secure-model transport. The proxy resolves the Profile and Keychain secret and emits normalized text, thinking, tool-call, usage, error and done events without logging prompts or raw responses.
- `GET /api/v1/model/capabilities` and `POST /api/v1/model/capabilities/probe` — cached capability facts and explicit minimal re-probe.
- `GET /api/v1/tools/contracts` — stable restricted Tool contracts.
- `POST /api/v1/task-authorizations` — register a structured per-Turn Task Authorization.
- `POST /api/v1/tools/call` — execute one governed Tool Call and return a structured Observation.
- `POST /api/v1/agent/events` — persist ordered Pi events before UI delivery.
- `GET /api/v1/agent/runs/{runId}/events?after=` — reconnect to durable events.
- `POST /api/v1/agent/runs/{runId}/control` — Steering or Follow-up.
- `POST /api/v1/agent/runs/{runId}/cancel` — durable cancellation.
- `GET /api/v1/agent/sessions/{sessionId}` — restore the Session Tree leaf and checkpoints.
- `GET /api/v1/actions/{actionId}` and `GET .../diff` — operation result and post-action Diff.
- `POST /api/v1/actions/{actionId}/undo` — after-hash-checked conflict-safe Undo.

`activate_runtime_upgrade` is a governed Tool contract rather than a public
shell endpoint. It returns a fixed activation request only after the task
branch is merged and the project tree is clean. The plugin executes the fixed
check/build/install/restart/health sequence and calls the governed rollback
Tool if activation fails.

Pi is bundled in the plugin and is the only production Agent loop. Python owns
facts, secrets, policy and execution but never performs model planning.

## Explicit structured workflows

- `POST /api/v1/workflows/{mode}` — non-chat projections for an explicit `mode`: `qa`, `tutor`, `capture`, `save`, `organize`, `material`, `research` or `plan`.
- `GET /api/v1/runtime/capabilities` and `GET /api/v1/runtime/diagnostics` — redacted local runtime facts.

These endpoints do not inspect prose to infer intent, run a model planner or own an
Agent loop. The former `/brain/*` and `/brain-change-sets/*` production endpoints
were removed. Interactive work always enters the Pi runtime; Markdown apply is an
internal task-authorized reversible transaction, not a pending Brain approval.

## Research and curriculum

- `GET /api/v1/research-bundles` and detail; save creates a Change Set, add-to-plan creates a proposed task.
- `GET /api/v1/curriculum/candidates`, refresh and feedback/action.
- `POST /api/v1/plan-proposals/{id}/confirm` — proposed to confirmed.

## Dashboard and recommendations

- `GET /api/v1/dashboard` — daily summary, active/failed jobs and deterministic recommendations.
- `GET /api/v1/recommendations` — reviewed/core recommendations, pending Prepared sources and clearly-labelled AI curriculum candidates that are not yet formal notes.
- `GET /api/v1/recommendations/{id}` — full reason, prerequisites, related notes, micro concepts and quiz preview.
- `POST /api/v1/recommendations/{id}/action` — `later`, `tomorrow`, `weekend`, `favorite`, `not_interested`, `mastered`, `too_easy`, `too_hard`, `off_route`, `undo`.

## Study

- `POST /api/v1/study-sessions` with `recommendation_id`. Creates or resumes one session and returns its Lesson Blueprint and saved progress.
- `GET /api/v1/study-sessions/{id}` restores state, section progress, quiz answers and notes.
- `PATCH /api/v1/study-sessions/{id}` accepts a bounded `action` (`pause`, `resume`, `open_quiz`, `leave_quiz`, `progress`, `abandon`, `retry`, `undo_complete`) plus `progress`; it cannot write Markdown.
- `POST /api/v1/study-sessions/{id}/complete` with `correctness` and optional notes. Completion is idempotent and returns a mastery suggestion with `requires_confirmation: true`; it schedules review state but does not update Markdown or confirm mastery.
- `POST /api/v1/learning/mastery/confirm` is the explicit user-confirmed write.

## Existing workflows

- Jobs: `GET/POST /api/v1/jobs`, detail, retry and cancel.
- Prepared: list, inspect and explicit transactional apply.
- Review: list, show, diff and explicit transitions.
- Learning: today plan and mastery suggestion/confirmation.

## Conversations and intelligence

- `GET/POST /api/v1/conversations` and `GET /api/v1/conversations/{id}`.
- `PATCH /api/v1/conversations/{id}/preferences` — per-conversation personalization and retention policy.
- `POST /api/v1/conversations/{id}/export` — local-only JSON export.
- `POST /api/v1/conversations/{id}/retain-summary` with `{confirmed:true}` — remove raw messages after a summary exists.
- `DELETE /api/v1/conversations/{id}?confirm=true` — explicit single deletion.
- `DELETE /api/v1/conversations?scope=recent-7-days|all&confirm=true` — explicit bulk deletion.
- `/intake/submit` remains a structured adapter for non-chat projections such as Study and Materials. It does not infer intent from message prose and is not an Agent loop.

## Directions, web and Vault autonomy

- `GET /api/v1/learning/directions`, `POST /api/v1/learning/directions/refresh`.
- `GET /api/v1/daily/dashboard|plan`, `POST /api/v1/daily/build|adjust`, `POST /api/v1/daily/adjustments/{id}/undo`.
- `POST /api/v1/web/search|fetch|research`, `GET /api/v1/web/sources`; fetched text is an untrusted local cache, not SQLite prose.
- `GET/PATCH /api/v1/autonomy` — mode and first-use permission summary.
- `GET /api/v1/agent-actions`, `GET /api/v1/agent-actions/{id}`, `POST /api/v1/agent-actions/{id}/undo`.

Recommendation feedback and study sessions are runtime state in SQLite. Knowledge content remains Markdown.

## Model Provider profiles

- `GET/POST /api/v1/model-profiles`
- `PATCH/DELETE /api/v1/model-profiles/{id}`
- `POST /api/v1/model-profiles/{id}/test`
- `POST /api/v1/model-profiles/{id}/models`
- `POST /api/v1/chat` — OpenAI-compatible 助手调用；完整会话仅归档到 `90-Local-Only/Agent/Conversations/`
- `POST /api/v1/chat/stream` — 当前返回兼容 JSON 结果，真正的 SSE 流式传输列为后续增强
- `GET/PATCH /api/v1/model-routing`

Profile responses never contain `apiKey`; they expose only a Keychain reference, configured state and masked hint. Deleting a Profile does not delete a shared/pre-existing Keychain secret. Custom headers cannot override Authorization, Host or Content-Length.
