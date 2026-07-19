# 知序 UI V2 规格

参考基线：`design/learning-agent-ui-v2-reference.png`。

## 信息架构

- Ribbon 单击打开 260–480px 快速侧栏；双击打开完整工作区；右键提供首页、导入、审核、助手、重启和诊断。
- 主工作区包含：今日、资料、审核、计划、助手。
- 今日页在宽屏使用“推荐列表 / 推荐详情 / 今日统计”三列。
- 资料页使用“资料状态列表 / Change Set 与错误详情”。
- 审核页使用“草稿列表 / Markdown 与 Diff / 来源证据”。
- 计划页使用可操作的未来两天、明天、周末、本周和延期任务区域。
- 助手页使用“会话主区 / Provider 与 API 设置”。

## 响应式

- ≥1180px：完整三列 Inspector。
- 860–1179px：列表与详情，统计及 Provider 设置使用右侧 Drawer。
- <860px：单列/分层内容；统计按钮和 Drawer 保持可访问。
- 主视图使用 container query，避免 Obsidian 侧栏宽度影响断点判断。

## 安全交互

- 模型写入必须转成 Change Set。
- API Key 输入是临时 DOM 状态，只提交给 localhost Runtime；插件 data.json 不存 Key。
- reviewed/core 只允许更新建议。
- Apply、接受、删除失败记录和 mastery 均使用显式确认或受限状态转换。

