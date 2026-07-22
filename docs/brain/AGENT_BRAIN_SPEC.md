# 知序 Agent Brain V1 规格

Last updated: 2026-07-13

## 目标与边界

Agent Brain 是本地 Runtime 的统一编排层。助手、今日、资料、审核和计划共享同一套意图、上下文、Skill、Tool、Policy、Verifier、审计和运行状态。Obsidian 仍是唯一主应用；Markdown 仍是知识正文真相；SQLite 只保存运行、索引、提案和反馈。

模型输出始终是不可信候选。模型不能选择任意 Skill/Tool、路径、权限、事务、Apply、Shell、SQL 或网络地址。所有写入先生成 Change Set；`reviewed/core` 只产生更新建议；mastery 必须人工确认。

## 组件

- `brain/orchestrator.py`：统一生命周期、状态转换、取消、重试与结果汇总。
- `brain/intent_router.py`：确定性优先，模型分类可选，结果限制在已知意图。
- `brain/context_builder.py`：最小上下文、相关性排序、预算、去重和秘密排除。
- `brain/planner.py`：只从 Skill Registry 生成结构化步骤。
- `brain/policy_engine.py`：只读、低风险 Change Set、高风险二次确认和受保护资源判定。
- `brain/executor.py`：顺序执行授权步骤，不暴露通用文件或 Shell 能力。
- `brain/verifier.py`：验证原文、来源、路径、预算、去重、保护状态和提案完整性。
- `brain/memory_manager.py`：偏好、知识状态、工作记忆和运行引用；不复制知识正文。
- `skills/`：面向目标的白名单能力。
- `tools/`：最小、受限、可审计的本地或网络动作。

## 生命周期

`created → understanding → planning → awaiting_authorization → running → verifying → awaiting_confirmation → completed`

终止态还包括 `failed` 与 `cancelled`。只读任务在 Verify 后可直接完成；产生 Change Set 的任务必须停在 `awaiting_confirmation`。每个 Run 保存 request/correlation id、主次意图、输入来源、Skill、当前步骤、Tool 事件、提案、警告和结构化错误。

## 第一版意图

`ask_question`、`learn_topic`、`research_topic`、`capture_text`、`organize_text`、`organize_vault`、`import_material`、`summarize_material`、`create_note`、`update_note`、`find_related_notes`、`create_study_plan`、`generate_recommendations`、`generate_quiz`、`evaluate_explanation`。

## 兼容策略

- PDF Prepared Bundle、Review、事务和 mastery 继续使用现有已测试模块。
- 现有 Job API 保留；Brain Run 为更高层的目标执行记录。
- 现有确定性推荐保持无模型可用；课程候选作为额外输入。
- Provider 统一复用 `ModelProfileService` 与 `OpenAICompatibleProvider`。
- 插件不增加第六个主模块；诊断使用 Modal/工作区入口。

## 完成门槛

Capture、Organize、Research、Curriculum、Recommendations 与 Tutor 至少在 Fake Model/临时 Vault 下端到端工作；策略和验证可阻断恶意路径、任意 Tool、SSRF、秘密泄漏和正式知识覆盖；五页能展示同一 Run/Proposal；全部离线测试、构建、视觉和安装门槛通过。
