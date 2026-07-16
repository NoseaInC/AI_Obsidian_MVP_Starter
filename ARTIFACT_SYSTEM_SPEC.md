# Artifact 系统规格

## 类型

- `material`
- `capture_proposal`
- `research_bundle`
- `learning_plan`
- `change_set`
- `knowledge_gap`
- `quiz`

## 核心字段

Artifact 包含稳定 `id`、`conversationId`、`type`、人类可读标题、状态、版本、结构化 payload 和时间戳。`artifact_versions` 保存父版本、修改指令和每版 payload。

## 状态

```text
draft → awaiting_confirmation → accepted/completed
  └──────────────────────────→ rejected
accepted/rejected → reopen → draft
```

状态动作必须通过受控 API。已接受 Artifact 不因重复动作被覆盖。

## Change Set

任何知识写入都必须形成 Change Set。审核页展示创建、更新、链接、冲突、来源、风险和版本历史。最终应用时重新校验目标路径、基础版本、reviewed/core 权限和事务完整性。

## 跨页投影

- material/research_bundle → 资料；
- change_set/capture_proposal → 审核；
- learning_plan → 计划；
- knowledge_gap/quiz 与 reviewed/core 推荐 → 今日；
- 全部 Artifact → 助手会话。

这些页面不复制正文，也不创建独立状态源。

