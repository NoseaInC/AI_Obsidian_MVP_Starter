# PydanticAI Assistant Runtime

Status: implemented, 2026-07-17

## Canonical chain

```text
PydanticAI
→ real model/tool events
→ schema-v3 NDJSON
→ AgentChunk
→ chat StreamController/reducer
→ inline confirmation
→ resume the same Run
→ Change Set transaction
→ write Diff
→ final answer
```

PydanticAI is the only model–tool execution kernel. The TypeScript
`AgentRuntime` boundary is provider-neutral, but the only registered adapter is
`PydanticAgentRuntime`, which calls the localhost Python service. There is no
Claude Code, Codex CLI, OpenCode, Pi, unrestricted shell or generic file tool.

## Event contract

The frontend consumes normalized `AgentChunk` values in persisted sequence
order: text, tool_use, tool_result, write_diff, confirmation_required, usage,
context_compacted, notice, error and done. Tool state is pending, running,
completed, failed, blocked or cancelled. The UI never synthesizes tool traces
and never renders hidden reasoning.

## Governed writes

The model can propose a bounded Change Set and call the governed commit tool.
The local Harness classifies each concrete Change Set as auto-apply, inline
confirmation, or blocked. Explicit low-risk creation under approved Draft/Inbox
roots may be committed automatically. Existing-note changes, missing inherited
write authority and medium/high-risk operations suspend the same PydanticAI run
with deferred tool approval. The inline confirmation displays the tool, paths,
summary, risk and on-demand Diff; the normal composer is hidden until
confirm/cancel resolves. Confirmed execution revalidates the immutable payload,
base hashes, path policy and reviewed/core protection, then compares every
written file with the immutable payload hash. The apply primitive is not
exposed as an independent model tool, and there is no separate daily Review UI.

If a model creates a proposal and then tries to simulate “waiting for approval”
in prose, a PydanticAI output validator requests a model retry and requires the
governed commit tool. Explicit “proposal only / do not apply” requests are not
forced into commit.

Terse continuation such as “好” or “继续” is not treated as an error after a
concrete proposal exists. The model creates the Change Set and the Harness—not
the keyword classifier—decides whether the current run may continue or must ask.

## Lifecycle

Runtime state records conversationId, runId, checkpointId, event sequence,
model, status, parentRunId and forkedFromSequence. Reconnect, resume, cancel,
compact, fork and regenerate are explicit backend operations. Cancel changes
backend state; closing a frontend reader is not cancellation.

## Inline edit

The Obsidian editor extension uses CodeMirror StateEffect, StateField,
Decoration and WidgetType. It captures the full document snapshot, selection
range and selected text before generation. Acceptance fails with “原文已变化，
请重新生成。” if the source changed. This frontend check supplements, never
replaces, the backend Change Set base hash.

## Architecture boundaries

- Core runtime does not import feature or DOM layers.
- Runtime adapters do not manipulate DOM.
- Tool renderers consume only AgentChunk.
- PydanticAI exists only in the Python backend.
- The main assistant does not import the retired AssistantRunCoordinator.
- Provider and model-routing writes pass through SettingsService.
- Markdown remains knowledge truth; runtime prose remains local-only files.

Claudian was studied as an MIT-licensed design reference. No unrestricted CLI,
provider implementation, generic file tool or source file was copied; see
`THIRD_PARTY_NOTICES.md`.
