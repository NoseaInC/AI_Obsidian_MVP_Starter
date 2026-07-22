# 工程文档索引

根目录只保留项目入口、当前状态、安全边界和关键决策。阶段性规格与实现记录按职责归档在本目录，避免 Vault 根目录被大量小文件占满。

## 建议阅读顺序

1. [`../README-先读.md`](../README-先读.md)：项目用途、安装与日常使用。
2. [`../AGENTS.md`](../AGENTS.md)：永久工程与安全约束。
3. [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md)：当前完成项、测试结果与恢复点。
4. [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md)：阶段目标与验收标准。
5. [`../DECISIONS.md`](../DECISIONS.md)：重要技术决策。
6. [`../SECURITY.md`](../SECURITY.md)：安全模型与禁止事项。

## 专题目录

| 目录 | 内容 |
|---|---|
| `architecture/` | Runtime、Provider、语言边界、联网研究等总体架构 |
| `assistant/` | 助手对话、上下文解析、任务线程、确认与恢复体验 |
| `brain/` | Brain、Harness、自主执行、技能、安全与调试 |
| `learning/` | 今日推荐、学习计划、学习工作台与方向预测 |
| `materials/` | 资料入口、Artifact、知识落点与 Obsidian 组织规则 |
| `ui/` | UI 规范、实施计划、视觉检查与版本记录 |
| `product/` | 产品边界、插件状态、用户手册与项目交接 |

Phase 11 的当前生产架构与真实模型验收分别见
[`architecture/PI_AGENT_RUNTIME_SPEC.md`](architecture/PI_AGENT_RUNTIME_SPEC.md)
和
[`architecture/PI_AGENT_RUNTIME_ACCEPTANCE.md`](architecture/PI_AGENT_RUNTIME_ACCEPTANCE.md)。

## 其他目录

- `design/`：设计参考图和视觉素材。
- `artifacts/`：测试、构建和视觉验收生成物。
- `99-Archive/Legacy-Root-Placeholders/`：历史遗留的空占位文件，不参与当前运行。

本次整理只移动文件，不删除规范或历史记录。Obsidian 可继续按文件名解析普通 Wiki 链接；工程脚本和文档中的显式路径应使用上表中的新位置。
