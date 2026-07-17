# 知序 Local API v1

All business endpoints are under `/api/v1`, require the in-memory bearer token, and return structured errors. `/health` is the limited public readiness endpoint.

## Agent Brain

- `POST /api/v1/brain/requests` — unified request entry with `Idempotency-Key`.
- `GET /api/v1/brain/runs` and `GET /api/v1/brain/runs/{id}` — Runs, Steps, Tool Events and proposals.
- `POST /api/v1/brain/runs/{id}/cancel|retry` — cancellation or linked retry.
- `POST /api/v1/brain/capture|organize|research|tutor` — explicit intent shortcuts.
- `GET /api/v1/brain/capabilities|health|diagnostics` — redacted capability and runtime diagnostics.
- `GET /api/v1/brain-change-sets/{id}` and `POST .../{id}/apply` — explicit, integrity-checked transactional apply.

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
- `POST /api/v1/assistant/stream` — canonical Runtime V3 Agent loop as schema-v2 NDJSON: model decision, one typed Tool, real Observation, replan, answer/clarification/Change Set proposal.
- `GET /api/v1/assistant/runs/{id}/events?after=&limit=` — ordered event reconnect plus latest checkpoint.
- `POST /api/v1/assistant/runs/{id}/resume|reject` — explicitly confirm or reject an awaiting Change Set; Apply is never exposed to the model.

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
