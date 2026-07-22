# Chat-first 视觉验收

参考图位于 `design/chat-first-*-reference.png`。最终实机截图输出到 `artifacts/chat-first-v1-screenshots/`。

## 对照项目

### 助手

- [x] 统一输入框固定在底部；
- [x] 欢迎区和快捷动作密度接近参考；
- [x] 文件/URL/当前笔记入口清晰；
- [x] Artifact 与消息在同一会话流；
- [x] 无大块调试区域。

### 资料

- [x] 一列状态卡片；
- [x] 标题、来源、进度、状态层级正确；
- [x] 内部 ID 不出现在主标题；
- [x] 搜索和状态筛选对齐。

### 审核

- [x] 左列表、中预览、右证据三栏；
- [x] 底部操作固定；
- [x] 风险与证据可见；
- [x] 退回助手调整保留同一 Artifact。

### 计划

- [x] 今日学习块为主；
- [x] 时间、70/30、任务数在顶部；
- [x] 周末预览弱化但可操作；
- [x] 空状态不产生大片无意义空白。

### 今日

- [x] 左侧五个分组；
- [x] 右侧单一学习详情；
- [x] 学习目标和底部操作清晰；
- [x] 窄屏列表/详情切换可用。

## 结果

已在安装后的 Obsidian 中逐页重载、操作并截图。实机界面沿用 Obsidian 当前主题、字体和强调色，布局、信息层级、固定操作区与五张参考图对齐。

- P0：无。
- P1：无。资料页曾把 `prepared_id` 暴露为标题，已改为可读标题并重新构建、安装和复拍。
- P2：没有真实会话或任务数据时，助手消息区和计划页会比参考图更疏；这是数据状态差异，不影响布局与操作。
- P3：字体字重、强调色和控件边角随用户当前 Obsidian 主题变化，不强行覆盖主题。

实机截图：

- `artifacts/chat-first-v1-screenshots/01-assistant-real-obsidian.png`
- `artifacts/chat-first-v1-screenshots/02-materials-real-obsidian.png`
- `artifacts/chat-first-v1-screenshots/03-review-real-obsidian.png`
- `artifacts/chat-first-v1-screenshots/04-plan-real-obsidian.png`
- `artifacts/chat-first-v1-screenshots/05-today-real-obsidian.png`
