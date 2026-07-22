# Daily Intelligence V2 Status

Last updated: 2026-07-14

## 已完成

- 参考图保存为 `design/daily-learning-intelligence-v2.png`。
- TypeScript `DailyIntelligenceEngine`、确定性排序、配额和事件 Buffer。
- Schema v1：推荐、候选、画像、事件、Lesson、验证。
- SQLite schema 4：learning events、learner features、lesson versions、candidate verification。
- 模型候选生成与 A/B/C 准入；无模型确定性候选不冒充每日新知识。
- Today 四段布局、五类别、排序、六个详情 Tab、固定操作栏、窄屏模式。
- 直接 Study Session、错误报告、完成事件、自动复习安排、显式 mastery 确认。
- 行为隐私设置、脱敏导出、确认式最近 7 天/全部清理和每日诊断。
- 已使用现有 DeepSeek 配置完成一次最小、受限的真实候选生成：生成 3 条候选，其中 A 级 1 条、B 级 2 条；请求不包含密钥、PDF、完整笔记或行为原文。
- 当前质量门：40 项摄入测试、73 项 Agent 测试、20 项插件测试通过；TypeScript 类型检查和生产构建通过。
- 最新构建已安装并在 Obsidian 1.12.7 中重载；安装文件与 `dist/main.js` SHA-256 一致。
- 真实 Obsidian 已验收每日新知识、已知知识、依据来源和 Study Session 四个状态，截图位于 `artifacts/daily-intelligence-v2-screenshots/`。
- 1000 条候选排序基准：中位数 0.436 ms、P95 0.559 ms、最大 0.644 ms；本地 health 响应 0.018 s。

## 最终验收结果

- P0：无。
- P1：无。
- P2：外部 Arxiv/Crossref/通用搜索适配器尚未配置；每日知识仍可使用本地 reviewed/core 证据生成并明确展示来源等级。
- P3：后续可补充更长时间的真实行为样本，校准个性化行为权重；冷启动阶段当前权重按规范保持低值。

## 安全状态

未执行真实知识写入或 Dragonnet Apply；未修改 reviewed/core；未读取或输出 API Key。视觉验收仅产生本地学习事件，并在结束时暂停测试会话。
