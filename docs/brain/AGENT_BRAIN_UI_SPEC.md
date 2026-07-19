# Agent Brain V1 UI 规格

主脑不是第六页。五个现有模块共享 Brain Run 与 Proposal 组件；当前已删除的 Global Header 不恢复，模块导航直接占满工作区高度。

## 助手

Composer 上方使用四个紧凑模式：对话、整理、研究、规划。对话模式由 Intent Router 继续区分问答、辅导、短测与复述检查；提交后显示人类可读 Run Timeline，技术 ID 默认折叠。Capture 使用 `CaptureProposalCard`，Research 使用 `ResearchBundleCard`，写入前打开 Change Set 预览。

## 今日

推荐区支持复习、学习、AI 补全、探索、资料。AI 补全明确显示“尚未成为正式笔记”、依据、桥梁、模型、时间与置信度。无模型时规则推荐保持可用并提供配置入口。

## 资料 / 审核 / 计划

- 资料增加“研究包”筛选和来源/阅读顺序详情。
- 审核区分用户原文、Agent 整理、模型推断、来源、Policy 和 Verifier。
- 计划接收 Research/AI Bridge proposed 任务，确认后才进入 confirmed。

## 交互与布局

外层不滚动；列表、正文、Inspector、聊天分别滚动；Composer 和操作栏固定。使用 `.la-` 前缀、Obsidian 变量、深浅主题、窄屏 Drawer、focus-visible、reduced motion、Loading/Empty/Error。内部 ID、Prompt、路径和技术字段默认折叠。
