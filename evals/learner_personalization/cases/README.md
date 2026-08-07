# Learner Personalization Eval Cases

Synthetic deterministic cases for offline evaluation. No real model, no real
PDF, no network.

| Case | 场景 | 预期 |
|---|---|---|
| memory/goal | 用户设定"准备数据分析秋招" | 目标可召回，goal_alignment > 0 |
| memory/preference | 用户说"先核心思想再推导" | 偏好可召回 |
| memory/gap | 多次 quiz 错误 + hint | knowledge_state candidate（不激活） |
| memory/conflict | 同 key 偏好两次显式表达 | 只有一条 active，旧记录 superseded |
| memory/deleted | 删除后查询 | search 返回 0，Pi context 无泄漏 |
| ranking/weak | 假设检验多次答错 | 相关 topic gap 高，排名上升 |
| ranking/goal | 与目标相关的 topic | goal_alignment 高 |
| ranking/disliked | 负反馈领域 | interest 下降 |
| ranking/time | 45min 任务 vs 25min 预算 | timeFit 低 |
| confidence/single | 单次 click | 影响 < 0.2 |
| confidence/cross-day | 5 行为跨 3 天 | 影响递增，maturity 单调增 |
| confidence/explicit | 显式偏好 | 直接强影响（不收缩） |
| confidence/negative | 负反馈 | 降低下次相关排序 |
