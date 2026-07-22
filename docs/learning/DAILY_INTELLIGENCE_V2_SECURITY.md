# Daily Intelligence V2 Security

- API Key 仅由 `MacKeychainStore` 读取；插件设置和事件中没有明文 Key。
- 课程模型只接收有界知识状态，不接收完整 Vault、绝对路径、原始对话或完整行为事件。
- 学习事件 payload 使用字段白名单和 2KB 上限，批次最大 100，事件 ID 主键保证幂等。
- 外部检索沿用 `SafeUrlFetcher`：拒绝 localhost、私有 IP、`file://`、云 metadata、非法重定向和超大/错误 Content-Type。
- 模型候选不能直接写 Markdown；A/B 准入只允许直接学习，正式沉淀仍需 Change Set。
- reviewed/core 仍由事务权限层保护；学习完成不会自动覆盖笔记或提升 mastery。
- 来源不足时降为 B/C；C 不进入每日新知识，网页内容永远作为不可信数据。
- 清理学习画像需要 Obsidian Modal 明确确认，且只删除 SQLite 运行状态，不删除知识或资料。
