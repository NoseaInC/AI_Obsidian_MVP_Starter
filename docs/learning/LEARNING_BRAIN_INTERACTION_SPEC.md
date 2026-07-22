# Learning Brain Interaction Spec

Updated: 2026-07-14

## Product contract

The assistant is the single natural-language and material intake surface. Today, Materials, Review and Plan are projections of governed results; they are not parallel chat products.

```text
conversation → bounded context → deterministic intent/outcome
→ restricted skill/tool execution → verifier → Artifact/Change Set
→ explicit confirmation when knowledge is written
→ structured feedback → next Today plan
```

Ordinary questions return an answer and optional learning signals. They do not create a Task Thread or stale Artifact. Explicit requests such as “整理成学习包”, “生成小测” or “保存到 Obsidian” create a versioned result. Follow-ups revise the active result instead of duplicating it.

## Five-module linkage

- Assistant: input, private conversation, Task Thread and editable results.
- Materials: traceable source/material/research status.
- Review: proposed knowledge, Markdown/Diff/evidence and acceptance.
- Plan: tomorrow/weekend/weekly queues and user-confirmed plan state.
- Today: deterministic reviewed-only review/learning plus verified candidates, directions and direct actions.

No module writes formal knowledge directly. Protected content always becomes an update suggestion.

## User-visible state

Loading, partial, failed, awaiting-confirmation, completed and undoable states must have human language. Technical IDs and raw JSON remain collapsed. A current task/result is shown only when the last conversation message references it.

## Acceptance

- An answer cannot inherit an earlier Task Thread or Artifact Group.
- Today time/format constraints apply immediately and can be undone.
- Every file change has snapshot, diff, audit and conflict-safe Undo.
- API/model/network failures never fabricate a successful result.
