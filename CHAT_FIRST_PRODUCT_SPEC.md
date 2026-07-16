# 知序 Chat-first 产品规格

## 产品结论

知序以助手作为唯一通用输入和任务执行入口；资料、审核、计划、今日是同一批 Artifact 的结构化结果页面。Obsidian 仍是唯一主应用，本地 Agent Runtime 是受控执行内核。

## 用户心智

```text
告诉助手目标 + 附件/引用
→ 主脑理解与拆分 Intent
→ 受限 Skill/Tool 执行
→ 生成可版本化 Artifact
→ 资料/审核/计划/今日自动出现
→ 需要写入时生成 Change Set
→ 用户确认后事务提交
```

助手支持 PDF、文本、Markdown、AI 对话、URL、Vault 内路径、显式选择的本地路径和当前笔记。后续要求默认修改当前会话的 active Artifact，不创建平行副本。

## 五个模块

- 助手：统一输入、附件、会话、任务执行与 Artifact 预览。
- 资料：材料处理状态中心，不承担聊天和正文编辑。
- 审核：Change Set、AI 草稿和更新建议的质量门。
- 计划：proposed/confirmed 学习计划与可调整任务。
- 今日：必须复习、下一步学习、AI 补全、探索和资料任务。

## 不变量

- Markdown 是知识真相；SQLite 只存运行状态、索引和历史。
- reviewed/core 永远只读，后续只能生成更新建议。
- 模型没有任意写文件、事务提交或权限决策能力。
- 原始 PDF、本地对话和中间结果只留在本地私有区。
- mastery 和正式写入必须由用户确认。

## 视觉基线

本轮以以下项目内参考图为准：

- `design/chat-first-assistant-reference.png`
- `design/chat-first-materials-reference.png`
- `design/chat-first-review-reference.png`
- `design/chat-first-plan-reference.png`
- `design/chat-first-today-reference.png`

