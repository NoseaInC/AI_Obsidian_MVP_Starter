# Agent Brain V1 API

所有业务端点位于 `/api/v1`，需要内存 Bearer Token，返回 JSON 和结构化错误；`/health` 仍是有限公开健康检查。

## Brain Runs

- `POST /brain/requests`：创建统一请求；支持 `Idempotency-Key`、`mode`、`text`、`active_note`、`selected_text`、`time_budget_minutes`。
- `GET /brain/runs?limit=&offset=&status=`：分页列表。
- `GET /brain/runs/{id}`：Run、Steps、Tool Events、Proposed Actions。
- `GET /brain/runs/{id}/events`：可轮询事件快照。
- `POST /brain/runs/{id}/cancel`：请求取消。
- `POST /brain/runs/{id}/retry`：从可重试失败创建关联 Run。
- `POST /brain/capture`、`/brain/organize`、`/brain/research`、`/brain/tutor`：显式意图快捷入口。
- `GET /brain/capabilities`、`/brain/health`、`/brain/diagnostics`：能力、版本和脱敏诊断。

## Interactive Assistant Runtime V2

- `POST /assistant/stream`：唯一普通助手流入口；返回 `application/x-ndjson`。
- 每轮创建持久化 Brain Run，并先解析 Conversation Focus、当前笔记、附件和 reviewed/core 检索上下文。
- 新增兼容事件：`plan.created`、`tool.requested`、`tool.started`、`tool.completed`、`approval.required`。
- Tool Calling Profile 使用模型选择的只读 Tool 循环；未启用 Tool Calling 时使用确定性只读检索，最终回答仍基于真实 Tool 结果。
- `run.completed.brainRunId` 可用于读取 `/brain/runs/{id}` 的完整审计状态。
- 普通助手接口永不暴露 mutation Tool；写请求继续进入 Intake、Change Set、Diff 和明确确认链路。

## Research / Curriculum

- `GET /research-bundles`、`GET /research-bundles/{id}`。
- `POST /research-bundles/{id}/save`：创建 Research Bundle Note Change Set。
- `POST /research-bundles/{id}/add-to-plan`：创建 proposed 计划项。
- `GET /curriculum/candidates`、`POST /curriculum/refresh`。
- `POST /curriculum/candidates/{id}/action`：收藏、冷却、不感兴趣、加入计划。
- `GET /brain-change-sets/{id}`、`POST /brain-change-sets/{id}/apply`：检查并显式事务应用主脑提案。
- `POST /plan-proposals/{id}/confirm`：将 proposed 计划转为 confirmed；不会自动提升 mastery。

## 通用约定

- Brain 写请求带 `correlation_id`，响应回传；其他业务写接口延续现有结构化错误与审计字段。
- 错误：`{ok:false,error:{code,human_message,retryable,suggested_action,correlation_id,technical_details:{}}}`。
- 默认分页 `limit=50`，最大 100。
- 长任务可返回 `202`；第一版离线快速 Skill 可同步执行并仍生成完整 Run 事件。
- UI 只展示人类信息，ID 与技术字段放折叠区。
- 不返回 Key、Authorization、Session Token、完整 Prompt 或 Python traceback。
