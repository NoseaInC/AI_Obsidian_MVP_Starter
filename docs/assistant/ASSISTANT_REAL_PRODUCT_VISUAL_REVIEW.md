# 知序助手真实产品 V1：视觉验收记录

最后更新：2026-07-16

## 验收边界

- 主验收对象是当前 Vault 中真实安装的 Obsidian 1.12.7 插件，而不是独立应用或静态 Mock。
- 参考基线：`design/assistant-workbuddy-anythingllm-v1.png`。
- 安装位置：`.obsidian/plugins/obsidian-learning-agent/`。
- 真实模型测试只使用通用问题；没有发送 Vault 正文、PDF、密钥或私人材料。
- 没有执行首次真实知识写入、`apply-prepared` 或真实 Undo。Proposal、Diff 和权限边界均为只读检查。

## 当前构建一致性

- `main.js`：构建与安装 SHA-256 均为 `ad4428df776613da3fabaf15bc732f0ec0567a4522db3f90534c8f71d07df97e`。
- `styles.css`：构建与安装 SHA-256 均为 `930d66fd32ee07e346df1c577e8537ca00a03e5083ff5fe14d95256d05da67d5`。
- 本地 Runtime 恢复后 `/health` 返回 HTTP 200、`protocol_version: 1`、`schema_version: 7`。

## 真实 Obsidian 验收结论

| 检查项 | 结果 | 证据 |
|---|---|---|
| 三栏布局 | 通过 | 左侧私有会话、中央对话、右侧 Context / Sources / Changes Inspector 同时可用 |
| 会话恢复与长对话 | 通过 | 历史会话真实加载；消息区、会话栏和 Inspector 分别滚动 |
| 模型 Streaming | 通过 | 真实 Provider SSE 转换为版本化 NDJSON；首帧、增量正文和完成态均可见 |
| Stop / 部分结果 | 通过 | Abort 后保留已返回正文，Trace 改为“已停止生成”，不再假显示运行中 |
| Markdown / 数学 | 通过 | Obsidian MarkdownRenderer 正确显示标题、列表与公式 |
| 上下文 | 通过 | 当前主题、置信度、Organization Plan、最近信号和相关笔记均来自真实接口 |
| 来源 | 通过 | Inspector 可展示 conversation artifact 中的真实本地来源，不再只依赖本轮执行结果 |
| 变更 | 通过 | 真实待确认 Change Set、目标路径、摘要和 Diff 可以只读查看 |
| 错误与恢复 | 通过 | 主动停止本地 Runtime 后显示 `ERR_CONNECTION_REFUSED`；从 Obsidian 命令重启后恢复 HTTP 200 |
| 响应式 | 通过 | 901px 为窄布局；1121px 为 72px 模块栏 + 单列聊天；完整宽度恢复三栏 |
| 深浅主题 | 通过 | 使用 Obsidian 主题变量，均无固定暗色/亮色泄漏 |
| reviewed/core 保护 | 通过 | UI 没有直接写入入口；真实 Change Set 仅查看，仓库测试继续覆盖只读策略 |

## 最终截图矩阵

目录：`artifacts/assistant-real-product-v1-screenshots/`

- [x] `assistant-empty-light.png`
- [x] `assistant-empty-dark.png`
- [x] `assistant-context-ready.png`
- [x] `assistant-user-message.png`
- [x] `assistant-streaming.png`
- [x] `assistant-agent-running.png`
- [x] `assistant-trace-expanded.png`
- [x] `assistant-markdown-math.png`
- [x] `assistant-sources.png`
- [x] `assistant-context.png`
- [x] `assistant-file-change-proposal.png`
- [x] `assistant-diff.png`
- [x] `assistant-permission.png`
- [x] `assistant-file-change-applied.png`（确定性组件预览；未对真实 Vault 应用）
- [x] `assistant-undo.png`（确定性组件预览；未对真实 Vault 撤销）
- [x] `assistant-partial-success.png`
- [x] `assistant-cancelled.png`
- [x] `assistant-error.png`
- [x] `assistant-long-conversation.png`
- [x] `assistant-medium.png`
- [x] `assistant-narrow.png`
- [x] `assistant-showcase-reference.png`

其中 20 张是当前安装构建在真实 Obsidian 中采集的状态；`file-change-applied` 与 `undo` 来自使用同一生产 CSS/组件契约的离线确定性预览。采用该区分是因为首次真实 Apply 属于用户确认边界，不能为了截图越权制造真实写入。

## 本轮发现并修复的 P1

1. Abort 后 Trace 仍显示运行中：取消分支现在立即刷新最新 Live Trace，并保留部分正文。
2. Conversation 已有关联来源与 Change Set，但 Inspector 显示为空：Inspector 现在从不可变 conversation artifacts 派生 Sources / Changes，并与本轮执行结果去重。
3. 中窄窗口会话栏挤压标题：Assistant 在有效宽度不超过 1420px 时折叠为 72px 模块栏并隐藏 Inspector；不超过 920px 时进一步收紧工具栏与正文边距。

## 最终视觉结论

- 已知视觉 P0/P1：无。
- 22 项截图矩阵已完成，真实状态和组件预览已明确区分。
- 首次真实 Apply、真实 reviewed/core 变更和真实 Undo 均未执行；它们继续保留在独立用户确认门之后。
