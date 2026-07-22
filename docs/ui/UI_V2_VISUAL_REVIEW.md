# 知序 UI V2 视觉验收

参考：`design/learning-agent-ui-v2-reference.png`。

## 对照结果

- 今日：三列、右侧环形时长、70/30、任务和快速输入均存在。
- 资料：状态 chips、密集列表、Change Set 概览、进度与底部操作存在。
- 审核：三列、正文、Diff、证据 Inspector 与固定操作存在。
- 计划：分区任务看板、任务操作和周摘要存在。
- 助手：聊天、上下文、模型选择、Provider/API 设置与模型路由存在。
- 浅色/深色：颜色完全派生自 Obsidian 变量。
- 窄屏：统计切换按钮出现；Provider 设置使用右侧 Drawer。

## 截图

位于 `artifacts/ui-v2-screenshots/`：Today light/dark、Materials、Review、Plan、Assistant、Today narrow、Assistant settings Drawer。

## 设计偏差

- 预览不复制 Obsidian 原生窗口 chrome；生产插件由 Obsidian 提供。
- 公式与 Markdown 的最终排版取决于用户主题和 Obsidian renderer。
- 数据不足时用可操作空状态，不填充静态生产样例。

## 真实 Obsidian 验收

- Obsidian 1.12.7 中五个主标签均能加载真实 API 数据。
- 右侧快速栏与主工作区同时显示，服务状态为在线、协议 v1。
- 右栏使用现有标签组，不再创建零高度的第二分栏。
