# Today Study Workspace V1 Visual Review

Reference files:

- `design/today-study-workspace-v1.png`
- `design/today-recommendation-detail-v1.png`

Real installed-plugin evidence lives in `artifacts/today-study-workspace-v1-screenshots/`.

## Captured states

1. `01-today-plan-light.png` — four-part Today layout, persisted minutes and compact future directions.
2. `02-study-starting-or-learning-light.png` — in-place transition into the lesson surface.
3. `03-study-learning-wide-light.png` — wide learning layout with the external Obsidian right sidebar collapsed.
4. `04-study-paused-light.png` — explicit paused state.
5. `05-today-resumable-task-light.png` — return to Today after session persistence; this image exposed the stale “开始” label that was subsequently fixed.
6. `06-today-resume-label-fixed.png` — installed-build proof of “继续/继续学习”.
7. `07-study-resumed-paused-light.png` — the same paused Runtime session reopened after plugin reload.
8. `08-study-resumed-active-light.png` — explicit resume returns the session to learning.
9. `09-study-quiz-light.png` — five sections complete and quiz state entered.
10. `10-study-quiz-visible-light.png` — quiz options visible in the native scroll surface.
11. `11-study-quiz-answered-light.png` — persisted quiz answer state.
12. `12-study-quiz-feedback-light.png` — visible correct-answer feedback.
13. `13-study-assistant-drawer-light.png` — contextual assistant drawer with current section context.
14. `14-study-completed-light.png` — completed state with 5/5 sections, quiz result, next-review date and explicit mastery-confirmation boundary.
15. `15-study-completion-undone-light.png` — completion Undo returns to the retained lesson progress without duplicating the completion record.
16. `16-study-paused-dark.png` — installed-plugin dark-theme rendering of the paused workspace.
17. `17-study-narrow-responsive-light.png` — narrow Today layout with category/task navigation preserved.
18. `18-study-narrow-detail-light.png` — narrow in-place study detail without a modal or detached window.
19. `19-study-narrow-assist-drawer-light.png` — contextual assistance rendered as a narrow drawer.
20. `20-study-recoverable-error-light.png` — recoverable connection failure after the local Runtime was deliberately stopped.
21. `21-study-error-recovered-light.png` — plugin-managed Runtime restart and Retry restore the same paused session at 5/5 progress.

## Reference comparison

- The plugin keeps navigation/category/task/detail columns and does not open a modal.
- Scheduled minutes and counts are visibly separate from future directions.
- The study header, progress, five accordions, assistance tabs and fixed action row follow the supplied learning-state reference.
- Obsidian's own file pane remains visible; its unrelated right sidebar was collapsed for the wide study screenshot.
- Colors use Obsidian variables and remain scoped under `.la-`.
- Dark, narrow, completion, Undo, offline and recovery states are all captured from the installed plugin rather than an HTML substitute.

## Live-QA fix

The persisted session was correctly paused in the Runtime, but the returned Today view reused an old dashboard snapshot and displayed “开始”. `exitStudy` now refreshes the dashboard after the pause commit, and both list/detail actions expose “继续/继续学习” for paused or in-progress tasks. A regression source-contract test covers this path.

## Recovery verification

The local Runtime was stopped while the lesson was open. The study surface changed to a recoverable inline error instead of discarding progress. The plugin command `Agent: 重启本地服务` restored protocol-v1 online status; the first Retry reopened the same PSM session in `paused` state at section `5 / 5`. The QA session was intentionally left paused.

## Result

The real-Obsidian visual matrix is complete for V1. No known P0/P1 visual or lifecycle issue remains.
