# Learning Agent User Guide

产品界面名称现为“知序”；历史插件 ID `obsidian-learning-agent` 保持不变。

## 知序 Chat-first

- 单击 Ribbon 图标打开快速侧栏，双击直接打开完整助手，右键打开快捷菜单。
- 主工作区左侧固定五个模块：今日、资料、审核、计划、助手；⌘1–⌘5 切换。模块栏不是 Vault 文件树。
- 助手是唯一通用输入：直接输入目标，或拖入/粘贴 PDF、文本、Markdown、URL 和 AI 对话；也可引用当前笔记、`@` 笔记或显式本地路径。
- 资料页用一列状态卡片汇总所有处理任务；内部 ID 不作为标题。
- 审核页中间渲染 Markdown/提案，底部固定拒绝、稍后、退回助手调整和修改后接受；来源、风险和版本历史在右侧。
- 计划页以今日学习块为主，顶部显示可用时间、70/30、任务数和预计完成率，并提供周末预览。
- 今日页按必须复习、下一步学习、AI 补全、探索和资料任务分组，右侧只展示当前一项的目标与操作。
- 助手页右侧可以创建多个 Provider 配置。API Key 进入 macOS Keychain，不会写入 Vault 或插件设置。
- 助手会自动识别整理、研究、保存、规划和检查理解等 Intent；每次提交都会显示理解、计划、策略、执行、校验和提案时间线，需要写入时必须在 Change Set 中再次确认。
- Provider 高级设置支持组织 ID、Temperature、Max Tokens、超时、流式/JSON Schema/工具调用能力声明和受保护的自定义 Headers；九类任务可以分别路由模型。
- 推荐无需模型；模型相关功能在未配置时显示明确入口。

## 助手任务线程

- 输入一个目标后，助手依次显示“检查已有知识、检索可信来源、生成适合当前水平的内容、组织内容与练习、生成可继续修改的成果”。技术错误默认折叠。
- 学习请求只生成一张学习包卡片；学习目标、前置知识、内容结构和一道小测预览都在同一张卡片内。
- “加入今日”会立即进入今日队列；重复点击不会创建重复任务。“开始学习”会先加入今日再打开学习会话。
- 右侧“本次任务”只展示当前上下文、匹配的 reviewed/core 笔记、最近资料和推荐动作。没有匹配内容时会明确说明，不会伪造来源。
- “新会话”创建真正独立的会话；完成首个请求后会用该请求自动命名。历史按钮可切换已有会话。
- 历史菜单还支持把当前会话导出到本地私有区、仅保留摘要，或经确认删除当前会话。设置页可确认清除最近 7 天或全部本地会话；这些操作不会删除 reviewed/core 知识笔记。
- 右侧上下文会显示最近对话信号及证据数量。它不会把一次提问当成长期兴趣，也不会把模型推断当成已确认 mastery。
- 助手会记住当前方法、概念、PDF、附件和当前笔记。连续说“这个方法”“这篇”“刚才那个”时会沿用可验证的当前焦点；无法可靠判断时只显示一次最小确认。
- 明确说“整理到 Obsidian”“保存到知识库”后，高自治模式会直接完成低风险草稿创建或 managed block 追加。右侧“最近修改”可打开笔记、查看变化和撤销。
- 新建知识草稿带 `status: ai-draft` 与 `agent_managed: true`；同名 reviewed/core 笔记不会被改写，只会进入审核页形成更新建议。
- 示例：先问“介绍一下 Delta Method”，再说“把这个方法带上推导整理到 Obsidian 中”，知序会创建/解析一篇方法笔记，不需要重新指定方法名。

## 今日调整与可能方向

- 直接对助手说“今天只有 15 分钟，不想看公式”，知序会重建今天的非固定任务并显示前后时长；点击“撤销”可恢复上一版。
- “可能的下一步”展示近期、周末和更长期方向及理由、前置、来源质量和置信度。可直接放到明天/周末或标记暂不考虑。
- 用户固定任务不会被自动移除；掌握度仍必须由用户确认。

## Vault 权限与撤销

- 首次启用高自治会显示“Agent 可以/不会”的权限摘要；可在插件设置随时切换谨慎、平衡或高自治。
- 打开“Agent: 打开主脑诊断”可查看对话/信号/方向/Web/Action/Snapshot/Undo 计数。
- “最近 Agent 修改”支持查看变化；文件未被后续人工修改时可一键撤销。若检测到人工后续编辑，撤销会停止并提示冲突。

## Daily use

Open Obsidian. Learning Agent starts its local runtime automatically and shows online status in the right sidebar.

Open the command palette and use `Agent: 打开首页`, `Agent: 查看当前任务`, `Agent: 查看待确认资料`, `Agent: 查看待审核知识`, `Agent: 开始今日学习`, `Agent: 打开 AI 助手` or `Agent: 打开诊断`.

1. Review the “Today” section and start a study session.
2. Open Assistant and drag in a paper/textbook, paste a URL or type an explicit goal.
3. Follow the Job in Task Center until it reaches “Awaiting confirmation”.
4. Inspect its Change Set and evidence, adjust candidates if needed, then explicitly Apply.
5. Review generated drafts and accept, edit-then-accept or reject them.
6. Confirm mastery only after answering the quiz or completing a retelling.

## Brain workflows

- 粘贴原文并选“整理”：原文逐字保存在本地提案中，AI/规则整理与原文分区，确认后才写入 Inbox 草稿。
- 选“研究”：优先查询 Vault 与已导入资料，生成带来源顺序与置信度的 Research Bundle；保存仍会先创建 Change Set。
- 选“规划”：生成 curriculum candidate 或 plan proposal；candidate 明确不是正式笔记，计划从 proposed 变为 confirmed 需要用户确认。
- 在命令面板打开“主脑诊断”“最近执行”“重试最近失败”或“取消当前任务”可以检查和控制 Run。
- 对同一成果继续说“不要拆”“移到周末”或“这只是猜测”会创建该 Artifact 的新版本，不会复制一套平行成果。

## Privacy

Original PDFs remain local. Only PAGE-marked extracted text is sent to the selected model provider. Raw conversations, extracted text, model responses, logs and Prepared Bundles remain under `90-Local-Only`.

## Recovery

Open “Agent: Diagnostics” to view runtime health, paths, API version and logs. Restarting the runtime does not change Markdown knowledge or apply a pending Change Set.

The default Python runtime is `00-System/Scripts/.venv/bin/python`. Structured events are under `90-Local-Only/Agent/logs/`; exported redacted diagnostics are under `90-Local-Only/AgentLogs/`. Rebuild and safely reinstall with `./scripts/install-plugin.sh`; it preserves plugin settings and backs up replaced artifacts locally.
