# Daily Runtime Boundaries

## TypeScript（Obsidian 进程）

- `LocalDailyIntelligenceEngine`：每日分数、工作日/周末配额、时间适配、类别分组。
- `LearningEventBuffer`：幂等去重、批量提交、非阻塞 flush、销毁清理。
- Today View：类别、列表、详情 Tab、固定操作栏、曝光/点击/反馈事件。
- Study Session：前端会话状态、小测、自评、错误报告、完成事件。
- 隐私开关：在事件进入队列前执行；关闭后基础推荐仍可用。

## Python Worker（localhost）

- `MacKeychainStore` 与 OpenAI-compatible/DeepSeek Provider。
- 课程候选模型调用、受控学术来源适配器、机器验证持久化。
- SQLite 事件、画像、候选、会话和计划状态。
- PDF、Prepared Bundle、审核事务和正式知识权限。

## Contract

TypeScript 只调用版本化 localhost API，不导入 Python 模块。Worker 返回原始候选和画像，最终今日排序只在 TypeScript domain 执行。Python 的历史 `score` 保留给旧 API 和兼容页面，但不再决定 Today V2 的最终次序。

Worker 失败时，页面使用已取得的 Dashboard 和本地确定性排序；打开今日页不会触发模型或网页检索。课程候选只在显式/后台刷新时调用模型。
