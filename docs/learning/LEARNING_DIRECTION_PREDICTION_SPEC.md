# Learning Direction Prediction Spec

Updated: 2026-07-14

## Purpose

Directions are explainable possible next steps, not automatic mastery claims or model-selected truth. They combine reviewed/core state, due dates, conversation signals, prerequisites, route and available time.

## Horizons

- Near: useful next step for today/tomorrow.
- Weekend: a larger consolidation or application task.
- Longer: a curriculum direction requiring more evidence.

Each card exposes title, minutes, horizon, rationale, connections, novelty basis, prerequisites, source quality, priority and confidence.

## Deterministic score authority

```text
due 18% + route 17% + gap 18% + conversation 12%
+ behavior 12% + prerequisite 10% + time 6%
+ interest 4% + novelty 3%
```

The model may explain or propose candidates but cannot replace these weights or final local admission. The persisted Today plan owns visible order and time budget.

## Feedback

Tomorrow, weekend, later, not interested, too easy, too hard, complete and skip are persisted and affect later ranking. Actions support Undo where meaningful. User-fixed tasks are never removed by automatic adjustment.
