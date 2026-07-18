# PydanticAI Assistant Runtime

Status: implemented, corrected 2026-07-18

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
The local `ToolPermissionGate` classifies the concrete tool request as `allow`,
`ask`, or `deny`. Commit defaults to an inline question. A scoped, conversation-
local rule may auto-allow future *creates* in one explicit root such as
`10-Inbox`; it never auto-allows updates. Invalid/absolute paths, path or symlink
escape, reviewed/core targets, stale base hashes and unknown actions remain
deny rules and override every session grant.

The inline confirmation displays the real tool, paths, summary, risk and
on-demand Diff; the normal composer is hidden until the user answers. Confirmed
execution revalidates the immutable payload, base hashes, path policy and
reviewed/core protection, then compares every written file with the immutable
payload hash. The apply primitive is owned by the dependency/Harness boundary,
not exposed as an independent model tool, and there is no separate daily Review
UI. The model decides from the actual user turn whether a Change Set is only a
preview or should proceed to commit; there is no keyword-derived
`write_requested` or `commit_required` flag and no output-validator coercion.

## Model-driven tool loop

Every turn exposes the same typed safe tools. The user text is not preclassified
into intent, allowed-tools, tool budget or write flags. The current user turn is
augmented only by a bounded `<runtime-context>` block containing conversation
focus, recent messages, active-note identity, attachment metadata, permission
mode and runtime capabilities. The same DeepSeek/PydanticAI run selects a tool,
receives its real Observation and replans.

`ask_user` is a typed deferred tool, not a prose convention. It is reserved for
information that Vault/PDF/web tools cannot resolve or for a decision only the
user can make. The answer resumes the same run and becomes a tool result.
`list_vault_folder` provides a validated, paginated Markdown file inventory;
file bodies still require explicit `read_vault_note` calls.

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
- Runtime code cannot import or consume the legacy keyword intent classifier.
- Markdown remains knowledge truth; runtime prose remains local-only files.

Claudian was studied as an MIT-licensed design reference. No unrestricted CLI,
provider implementation, generic file tool or source file was copied; see
`THIRD_PARTY_NOTICES.md`.
