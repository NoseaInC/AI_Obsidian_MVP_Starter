# Language Architecture

智能今日推荐采用增量式混合运行时。Obsidian 原生交互和每日排序放在 TypeScript；已经稳定且涉及 Keychain、SQLite、PDF、事务与审核的能力继续由受限 Python Worker 提供。不存在 Rust Worker，也没有为语言统一重写稳定管线。

| 功能 | 当前实现 | 目标实现 | 本轮处理 |
|---|---|---|---|
| 今日 UI | TypeScript | TypeScript | 四段布局、独立滚动、窄屏路由完成 |
| 行为采集 | TypeScript | TypeScript | 幂等事件队列、批量 flush、关闭前 flush 完成 |
| 推荐排序 | Python 原始分数 + TypeScript 展示 | TypeScript 权威 Daily domain | `daily-intelligence.ts` 成为今日排序、配额和预算的唯一权威 |
| 学习画像 | Python SQLite 聚合适配器 | Worker 持久化 + TypeScript 消费 | 完成；原始事件不发送给模型 |
| 模型调用 | Python Provider | 受控 Provider 抽象 | 复用；课程候选通过 `BrainModelGateway`，Key 不离开 Worker |
| Keychain | Python | 安全 Worker 适配器 | 保留 `MacKeychainStore`，插件只接收 configured/hint/reference |
| 网页检索 | Python 受控 Provider | Provider 抽象 | 复用 SSRF 与来源类型策略；未配置通用搜索时降级 |
| PDF | Python | Worker | 保留稳定 Prepared Bundle 管线 |
| Change Set | Python 事务层 | 暂不破坏 | 完全兼容，今日学习不会直接写正式知识 |

迁入 TypeScript 的原因是这些能力直接依赖 Obsidian 生命周期、焦点、页面状态和用户事件；保留 Python 的原因是对应代码已经有离线测试、密钥边界或事务保证。当前没有真实性能证据支持引入 Rust。

共享 Schema 位于 `packages/domain/`，API 和 TypeScript domain 均声明 `schemaVersion: 1`。运行数据库 schema 为 4。
