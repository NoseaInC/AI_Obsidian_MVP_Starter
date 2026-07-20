# 知序助手参考组件审计

审计日期：2026-07-15  
审计范围：只研究交互模式、状态组织和边界处理；正式插件继续使用 Obsidian API、原生 DOM、现有 TypeScript/Python 运行时。参考仓库位于 `90-Local-Only/ReferenceRepos/assistant-ui/`，不进入插件构建或发布物。

| 项目 | 源文件 | 组件/模式 | 我们借鉴什么 | 如何重新实现 | 是否复制代码 | 许可证记录 |
|---|---|---|---|---|---|---|
| AnythingLLM `4482fd6` | `frontend/src/components/WorkspaceChat/ChatContainer/index.jsx` | ChatContainer | 用户消息和 pending 回复立即出现；会话首次发送前先落库；滚动区与 Composer 协调 | 使用 `AgentClient.streamNDJSON`、本地事件 reducer 和 Obsidian DOM；首个事件到达前先渲染 pending | 否 | MIT，已读取根目录 `LICENSE` |
| AnythingLLM `4482fd6` | `frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/index.jsx` | PromptInput | Enter/Shift+Enter、附件、命令、停止、草稿焦点 | 使用原生 `textarea`、`AbortController`、附件 chip 和可访问按钮；不引入其 hooks 或状态实现 | 否 | MIT |
| AnythingLLM `4482fd6` | `frontend/src/components/WorkspaceChat/ChatContainer/SourcesSidebar/index.jsx` | Sources Sidebar | 来源去重、选中来源、窄屏抽屉 | 右侧 Inspector 使用“上下文 / 来源 / 变更”三个标签，数据来自本地 Agent API | 否 | MIT |
| AnythingLLM `4482fd6` | `frontend/src/components/WorkspaceSidebar/ThreadItem/index.jsx` | ThreadItem | 当前会话、悬停菜单、重命名/删除、键盘可达 | 在知序左栏实现会话搜索、切换和菜单，持久化仍由现有 SQLite/本地 JSON 消息文件负责 | 否 | MIT |
| OpenCowork `6f0c047` | `src/renderer/components/ChatView.tsx` | ChatView | 真实 partial streaming；接近底部才自动滚动；会话滚动位置恢复；取消与清理 | TypeScript NDJSON reducer 批量消费增量；`AbortController` 取消；DOM 更新按帧合并 | 否 | MIT，已读取根目录 `LICENSE` |
| OpenCowork `6f0c047` | `src/renderer/components/MessageCard.tsx` | MessageCard | 用户气泡、助手无大气泡；排队/取消状态；结构化消息块 | 定义 `AssistantMessagePart`，分别渲染文本、状态、工具、来源和错误块 | 否 | MIT |
| OpenCowork `6f0c047` | `src/renderer/components/message/ToolUseBlock.tsx` | ToolUseBlock | 工具调用与结果合并；运行/成功/失败；耗时；可折叠 | 工具事件按 `runId + stepId` 合并；供应商独立返回的 reasoning 走单独事件和折叠区，二者不混合 | 否 | MIT |
| OpenCowork `6f0c047` | `src/renderer/components/PermissionDialog.tsx` | PermissionDialog | 按风险展示明确允许/拒绝 | 继续使用现有 Change Set、`ExplicitConfirmModal`、reviewed/core 保护和事务 API | 否 | MIT |
| OpenWork `7d72a0d` | `apps/app/src/react-app/domains/session/chat/session-page.tsx` | Session Page | 会话、中央消息、右侧面板和长任务恢复的职责分离 | 左栏会话、中央对话、右侧 Inspector 三栏；状态仍由现有 ItemView 与后端会话记录恢复 | 否 | 根目录 MIT；未读取、未使用 `/ee` |
| OpenWork `7d72a0d` | `apps/app/src/react-app/domains/session/surface/composer/composer.tsx` | Composer | 附件、粘贴文本、提及、命令、模型和停止按钮共同组成输入面 | 用 Obsidian 原生 DOM 独立实现；保留现有安全附件入口和本地 URL/路径校验 | 否 | MIT（不含 `/ee`） |
| OpenWork `7d72a0d` | `apps/app/src/react-app/domains/session/panel/side-panel.tsx` | Side Panel | 明确标签状态、关闭、焦点和卸载清理 | Inspector 三标签、关闭按钮、窄屏 overlay；ItemView 关闭时中止流和清理监听器 | 否 | MIT（不含 `/ee`） |
| OpenWork `7d72a0d` | `apps/app/src/components/chat/message-list.tsx` | Message List | 结构化消息 part、工具结果错误边界、文件和状态块 | 本地 discriminated union + 穷尽 reducer；未知事件降级为技术详情，不破坏会话 | 否 | MIT（不含 `/ee`） |

## 许可证与隔离结论

- 三个参考项目均只用于只读审计；没有源文件复制到知序。
- AnythingLLM 和 OpenCowork 根许可证为 MIT；OpenWork 根许可证为 MIT，但 `/ee` 为特殊许可范围，本次未读取或使用该目录。
- 正式 Bundle 不包含参考仓库、其品牌、图标、路由、状态库或依赖，因此当前无需新增第三方代码声明。
- 若未来复用超过琐碎程度的实现，必须先更新 `THIRD_PARTY_NOTICES.md` 并单独审查许可证。

## 落地约束

1. 会话真相来自本地 Agent 的 SQLite 索引和 `90-Local-Only/Agent/Conversations` 消息正文。
2. 流式回复必须来自上游模型的 `stream=true` 数据流，不得把完整回复切片伪装成 streaming。
3. Trace 展示可验证阶段、受限工具事件和结果摘要；只有供应商真实返回的独立 reasoning block 才能进入单独的可折叠“模型推理”区，禁止前端伪造。
4. 所有写入继续经过快照、Diff、Change Set、确认和事务层；reviewed/core 不自动覆盖。
5. Markdown 由 Obsidian `MarkdownRenderer` 渲染，参考项目的 HTML 渲染代码不复用。
