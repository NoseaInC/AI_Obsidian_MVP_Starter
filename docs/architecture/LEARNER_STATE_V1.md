# Unified Learner State V1

Status: implemented on `workspace-memory-v1` (2026-08).

## Goal

One derived Learner State drives both Pi Agent personalization and the Today
Ranking. Memory, learning behavior, quiz results, mastery and feedback are
projected into a single rebuildable state — no second user-profile database.

```
Raw Evidence
  ├── memory_items          (durable semantic memory)
  ├── learning_events       (behavior)
  ├── quiz / mastery        (objective learning evidence)
  └── recommendation feedback
          ↓
   LearnerStateBuilder
          ↓
   Unified Learner State
      ↙              ↘
Pi Agent Context    Today Ranking
```

## Data model

`agent/core/learner_state/models.py`

```
LearnerState
├── active_goals:      [GoalState]        (memory_type=goal, active)
├── knowledge_states:  [KnowledgeState]   (topic, state, gap_score, confidence, evidence, reasons)
├── domain_states:     [DomainState]      (domain, interest, behavior_fit, confidence, evidence)
├── preferences:       [PreferenceState]  (memory_type=preference)
├── behavior:          BehaviorState      (event_count, covered_days, maturity, completion, hint_dep, preferred_duration)
└── evidence_summary:  EvidenceSummary
```

Four unified ranking signals (0..1):

| signal | source |
|---|---|
| goal_alignment | active goals × topic/domain overlap (explicit > inferred, topic > domain) |
| knowledge_gap | mastery + quiz correctness + hint usage + abandonment + memory knowledge_state |
| behavior_fit | completion rate + abandon rate + duration fit per domain |
| interest | explicit > repeated active > favorite > clicked > single exposure; negative feedback lowers |

## Authority boundaries

- Mastery / Quiz = objective learning evidence (never copied into memory).
- Memory `knowledge_state` = user's long-term semantic claim (claimed ≠ verified).
- LearnerState = merged current projection. Rebuildable; never a source of truth.

`claimed` never auto-upgrades to `verified`; only repeated evidence + policy
promotion can move a candidate to active/observed.

## Confidence shrinkage

```
effective = neutral + (raw - neutral) * confidence * maturity
neutral = 0.5
maturity = sqrt(min(1, evidence/40) * min(1, days/14))
```

Explicit preferences bypass shrinkage (confidence=1, maturity=1).
Negative feedback is treated as an explicit signal (not shrunk).

## Ranking (TypeScript, daily-intelligence.ts)

```
Score(i) =
  0.18 Due
+ 0.17 Route
+ 0.18 KnowledgeGap
+ 0.12 GoalAlignment   (replaces legacy conversation)
+ 0.12 BehaviorFit
+ 0.10 Prerequisite
+ 0.06 TimeFit
+ 0.04 Interest
+ 0.03 Novelty
```

- `behaviorWeight()` dead code removed; single authority is
  `behaviorMaturity` / `shrinkFeature` + Python `learner_state.features`.
- Every recommendation carries `topFactors` (deterministic explanation:
  factor / score / real reason) — LLM never invents reasons.
- Python `list_recommendations` now fills `learnerSignals` per item.

## Pi context

`POST /memory/context` merges compact learner signals into the existing
`<zhixu_memory_context>`: active goals (from memory), top knowledge gaps
(from learner state), recent behavior pattern. Bounded ≤800 tokens.

## Memory candidates (behavior-derived)

`run_behavior_memory_extraction` (conservative, candidate-only):

- knowledge_state candidate: ≥2 quiz failures on a topic + ≥2 hints + ≥2 days.
- preference candidate: ≥3 completed sessions in a domain across ≥2 days.
- Single click/exposure/session never forms a long-term candidate.
- Promotion still requires the standard policy (≥3 evidence, ≥2 days, ≥0.75).

## WriteIntent enforcement

`plan_vault_change` now enforces the contract at the service layer:

- With `writeIntent` → must pass `validate_intent`, else PermissionError.
- Without `writeIntent` → every write target must live under a recognized
  note-type/draft/inbox root, else PermissionError.
- The validator cannot be bypassed by omitting the intent field.

## Endpoints

```
GET /learner/state
GET /learner/context
GET /learner/rank-signals?topic=&domain=
```

## Evals

`evals/learner_personalization/` — deterministic offline:

- test_memory_ablation: recall / no deleted leak / conflict resolution.
- test_ranking_ablation: top-k hit, pairwise, NDCG@K, time budget.
- test_confidence_maturity: single click minimal, cross-day growth, explicit
  strong, negative feedback lowers.

Run: `python -m evals.learner_personalization.test_runner`

## Known limits

- goal_alignment uses lexical overlap (no embeddings).
- quiz correctness is aggregated from learning_events payloads; the `quizzes`
  table remains schema-only (no writer).
- `conversationScore` is no longer a ranking feature; conversation relevance
  is not yet wired into goal evidence.
