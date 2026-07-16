# Chat-first API

所有业务路由支持 `/api/v1` 前缀，只监听 `127.0.0.1` 并要求随机 Bearer Token。

## 会话与 Intake

```text
GET    /conversations
POST   /conversations
GET    /conversations/:id
POST   /conversations/:id/messages
POST   /intake/attachments
GET    /intake/attachments/:id
DELETE /intake/attachments/:id
POST   /intake/submit
```

## Artifact 与材料

```text
GET  /artifacts
GET  /artifacts/:id
POST /artifacts/:id/revise
POST /artifacts/:id/action
GET  /materials
GET  /materials/:id
```

## 学习与计划

```text
GET   /dashboard
GET   /recommendations
POST  /recommendations/:id/action
GET   /learning/today
GET   /plans/current
PATCH /plans/tasks/:id
POST  /study-sessions
POST  /study-sessions/:id/complete
POST  /learning/mastery/confirm
```

## 模型

Provider Profile、Keychain、连接测试、模型列表和任务路由沿用 `/model-profiles`、`/model-routing`。API 永不返回完整 Key。

## 错误

错误使用 `{"error":{"code","message"}}`。不暴露内部堆栈、真实私有路径、原始附件内容或密钥。OPTIONS 返回允许的方法和受控 CORS 头。

