# Workspace Policy + Long-term Memory V1

Status: implemented on `workspace-memory-v1` (2026-08-02).
Production assistant loop remains the embedded Pi Agent Runtime.

## Goals

1. The Agent understands Vault directory semantics and writing rules
   (what to write, which note type, update vs create).
2. The Agent has cross-session user memory (long-term goals, preferences,
   knowledge state, project decisions) without a second Agent runtime.

## Architecture

```
Workspace Policy (00-System/AI/*.md)
+ User Long-term Memory (memory_items / memory_evidence)
+ Pi Session Context (zhixu_turn_context + zhixu_memory_context)
        ↓
Typed WriteIntent → Policy Validation → Write Plan
        ↓
Snapshot → Apply → Verify → Undo (reversible transaction, unchanged)
```

Python owns Workspace Policy, MemoryService, WriteIntent validation, tool
execution, persistence, security and transactions. Pi remains the only
model–tool loop.

## Vault layout (adjusted)

```
00-System/ (Home, AI/, Bases/, Templates/, Canvases/)
01-Inbox/
10-Sources/ (Papers, Books, Courses, Web)
20-Knowledge/ (MOCs, Courses, Topics, Concepts)
30-Learning/ (Daily, Weekly, Plans)
40-Projects/
80-Archive/
90-Local-Only/
```

Migration performed: `20-Knowledge/概率论与数理统计/` →
`20-Knowledge/Courses/概率论与数理统计/` (git mv, history preserved;
Wikilinks resolve by filename so no link rewrite was needed).
Both top-level MOCs restored to `status: core`.

## Workspace Policy files

- `00-System/AI/VAULT_CONSTITUTION.md` — directory duties, writing discipline.
- `00-System/AI/NOTE_TYPES.md` — the 8 note types and decision boundaries.
- `00-System/AI/WRITING_POLICY.md` — retrieve-first, update vs create,
  reviewed/core protection, review-unit rules.
- `00-System/AI/LINKING_POLICY.md` — semantic Wikilink fields.

Two delivery mechanisms to Pi:

1. **Resident Workspace Profile** (~800 tokens): injected automatically into
   every turn via `agent/core/workspace_policy/loader.py#profile_summary()`.
2. **On-demand read tools**: `get_vault_constitution`, `get_note_type_policy`,
   `get_writing_policy`, `get_linking_policy`.

## Memory data model

`memory_items`:

| field | meaning |
|---|---|
| id | `mem-<hex>` |
| memory_type | goal / preference / knowledge_state / project_decision |
| scope_type + scope_id | user / project / vault / conversation scoping |
| memory_key | canonical short key (indexed) |
| value_json | structured value (no conversation prose) |
| source_type | explicit_user / verified / repeated_observed / single_observed / model_candidate |
| evidence_level | claimed / observed / verified |
| confidence | 0..1 |
| status | active / candidate / superseded / deleted |
| expires_at / supersedes_id | expiry and supersede chain |

`memory_evidence`: references only — `evidence_id` ∈
{message_id, learning_event_id, quiz_id, recommendation_feedback_id, action_id}.
No conversation body is copied.

### Rules

- Explicit memory (`remember_memory` tool): confidence 1.0, source
  `explicit_user`, status `active`, written only when the user message hits an
  explicit-intent pattern (`记住…/以后请…/我的长期目标是…/这个项目以后统一…`).
- Implicit candidates promote only when: evidence_count ≥ 3, ≥ 2 distinct
  dates, confidence ≥ 0.75.
- Conflict: new record `active`, old record `superseded`,
  `supersedes_id` points to the old record. Never overwrite in place.
- `claimed` never auto-upgrades to `verified`.
- Deleted/superseded/expired memories never enter Pi context.

## WriteIntent

```typescript
interface WriteIntent {
  purpose: "source_capture"|"course_learning"|"knowledge_consolidation"|
           "project_progress"|"learning_log";
  noteType: "source"|"course"|"course-chapter"|"topic"|"concept"|
            "project"|"project-note"|"learning-log";
  action: "create"|"update";
  title: string;
  targetPath: string;
  existingTarget?: string;
  reason: string;
  links: {parent?, course?, project?, sources[], prerequisites[], related[]};
  status: "ai-draft"|"proposed";
  reviewUnit: boolean;
}
```

Fixed directory mapping and `review_unit` rules live in
`agent/core/workspace_policy/models.py`. Validation
(`agent/core/workspace_policy/validator.py`) checks:

- noteType ↔ directory match;
- `course-chapter` → `review_unit: false`; only topic/concept allow `true`;
- duplicate or near-duplicate targets → suggest `update`;
- reviewed/core targets → reject with `update_proposal`;
- link targets exist or are flagged;
- target within Task Authorization scope.

## Learning scan change

`agent/core/learning.py#scan_reviewed` now requires
`review_unit != false AND status ∈ {reviewed, core}`.
Notes without the field default to `true` (backward compatible).
Course chapters are excluded from Today review.

## Pi integration points

- `PiAgentRuntime.ts#resolveMemoryContext` → `POST /memory/context`
  (≤8 items, ≤800 tokens) → appended as `<zhixu_memory_context>` in
  `promptWithContext`. Best-effort: failure yields empty context.
- Tools exposed to Pi: `search_memory` (read_only), `remember_memory`
  (proposal), `forget_memory` (proposal), `validate_write_intent` (proposal),
  plus the 4 policy read tools.
- Regenerate snapshot: `pi_agent_runs.memory_snapshot_json` (migration added).
- Memory write operations are audited via `service.log(...)`.

## Endpoints

```
POST /memory/context            GET  /workspace/policy/{name}
POST /memory/search             GET  /workspace/profile
POST /memory/remember           POST /write-intent/validate
POST /memory/forget
POST /memory/list
```

## Compatibility

- `conversation_knowledge_signals` remains read-only, usable as candidate
  evidence source; it is not a second long-term memory authority.
- `memory_items` is the single authority for long-term user state.
- Legacy notes without `review_unit` keep current review behavior.

## Known limits (V1)

- Memory search is lexical (exact/metadata/FTS-BM25), no vector DB.
- `remember_memory` requires explicit-intent match; implicit extraction is
  deliberately conservative (宁可少记).
- Regenerate snapshot persistence is in place; UI wiring for it is minimal.
- No per-user memory UI module (explicitly out of scope).
