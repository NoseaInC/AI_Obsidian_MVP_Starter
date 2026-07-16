# 知序 UI V2 实施状态

Last updated: 2026-07-13

## 完成

- 产品显示名改为“知序”，插件 ID 保持兼容。
- V2 参考图已保存。
- 今日三列与时长统计 Inspector；窄屏统计按钮/Drawer。
- 资料双栏、状态筛选、真实进度、Change Set、失败详情与重试。
- 审核三栏、预览/Diff/来源证据和固定操作区。
- 计划看板与底部时长摘要。
- 助手会话、上下文、Slash Commands、模型选择、Provider Drawer，以及经过本地 Runtime 的真实发送链路。
- Provider Profile CRUD、KeyStore、OpenAI-compatible 适配器、连接测试、模型列表和任务路由 API。
- 深浅主题、container query、键盘 1–5、方向键、Enter、Space、Esc、Context Menu 和 Undo。
- 正式 visual preview 与 V2 截图。

## 安全门

- 本轮没有真实网络模型调用。
- 助手链路使用 Fake Provider 完成离线端到端测试。
- 没有真实 apply-prepared。
- 没有读取、显示或写入真实 API Key。

## 实机验收

- 已在 Obsidian 1.12.7 重新加载安装后的 0.3.0。
- 今日、资料、审核、计划、助手五个主模块均通过真实 ItemView 棚查。
- 快速侧栏显示在线、协议 v1、今日时长、资料/审核/任务计数与快速输入。
- 修复侧栏回调字段 `open` 覆盖 Obsidian View 生命周期方法的问题，并把零尺寸分栏迁回标准右侧标签组。
