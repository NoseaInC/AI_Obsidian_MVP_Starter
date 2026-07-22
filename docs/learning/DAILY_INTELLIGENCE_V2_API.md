# Daily Intelligence V2 API

所有业务接口位于 `/api/v1`，需要内存 Bearer Token，仅监听 localhost。

| Method | Path | Purpose |
|---|---|---|
| GET | `/daily/dashboard` | 原始推荐、摘要、画像和运行时边界；不触发模型 |
| GET | `/learning/profile` | 本地聚合画像 |
| POST | `/learning/profile/rebuild` | 重建推断画像 |
| GET | `/learning/events?limit=&since=` | 查看脱敏结构化事件 |
| POST | `/learning/events` | 1–100 个幂等事件批次 |
| DELETE | `/learning/events?scope=recent-7-days|all` | 明确确认后的清理 |
| POST | `/curriculum/refresh` | 显式刷新模型候选 |
| GET | `/curriculum/candidates` | 候选与验证等级 |
| POST | `/recommendations/:id/action` | 稍后、计划、反馈和冷却 |
| POST | `/study-sessions` | 开始微型学习会话 |
| POST | `/study-sessions/:id/complete` | 完成、建议 mastery、自动安排复习 |

事件 Contract 使用 `packages/domain/learning-event.schema.json`。只允许白名单 payload 字段，拒绝完整笔记、Prompt、Key、Token 或任意扩展字段。Schema 版本不匹配返回结构化 `invalid_request`。
