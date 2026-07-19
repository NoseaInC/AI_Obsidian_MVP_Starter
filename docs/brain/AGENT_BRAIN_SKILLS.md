# Agent Brain Skill 与 Tool 规格

## Skill 声明

每个 Skill 声明 `name`、描述、输入/输出 Schema、`allowed_tools`、是否读 Vault/用网络/创建 Change Set/需确认、超时与重试。Registry 拒绝未注册名称；Planner 不能自行发明 Skill。

第一批 Skill：

- `capture_text`：逐字保留原文，分类、标题、路径、相关笔记、重复和 Markdown 提案。
- `organize_text`：一个主笔记、0–3 个概念候选和最多一个问题列表。
- `research_topic`：本地知识优先，Fake/可选学术来源，生成 Research Bundle。
- `curriculum_planner`：前置/桥梁/核心/应用/比较/探索候选池，去重和冷却。
- `daily_recommendations`：规则排序，混合到期、下一步、AI 补全、探索与资料。
- `tutor_topic`：定义、推导、题目、例子、复述评估和 mastery 建议。
- `organize_vault`：只读扫描，输出整理建议或 Change Set，绝不自动移动/删除。
- `save_to_obsidian`：只创建并验证 Change Set；Apply 由现有确认/事务层完成。

## Tool 白名单

`search_vault`、`read_note_metadata`、`read_note_excerpt`、`get_related_notes`、`get_backlinks`、`get_learning_state`、`get_due_reviews`、`get_recent_materials`、`create_change_set`、`validate_change_set`、`apply_confirmed_change_set`、`create_plan_proposal`、`save_recommendation_feedback`、`search_academic_sources`、`fetch_user_provided_url`。

禁止通用 `read_file`、`write_file`、Shell、subprocess、任意 URL、任意 SQL。每个 Tool 记录名称、时间、状态和脱敏输入/输出摘要，不记录全文、Prompt 或秘密。

## 数据来源标签

`local_vault`、`imported_source`、`academic_api`、`official_documentation`、`user_provided_url`、`ai_curriculum_suggestion`。AI 课程建议不得伪造为外部来源。
