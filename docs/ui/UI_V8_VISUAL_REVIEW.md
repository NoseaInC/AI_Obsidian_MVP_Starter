# 知序 Assistant UI V8 视觉验收

验收日期：2026-07-19

## 对照结论

| 验收项 | 实现结果 | 视觉检查 |
| --- | --- | --- |
| 用户消息气泡 | 时间和编辑操作合并为同一底栏，气泡高度随正文收缩 | 通过 |
| 输入框聚焦外圈 | 移除文本域和容器的蓝色 focus ring，仅保留中性边框 | 通过 |
| 模型选择 | 原生 `select` 替换为自定义弹层；含 Auto、模型图标、型号和配置入口，不显示消耗系数 | 通过 |
| 输入区域 | 大圆角输入面板；正文输入在上，添加、联网、模型和发送操作在下 | 通过 |
| 字体与排版 | 使用 Obsidian 字体变量及 macOS 中文系统字体栈，正文 14.5px / 1.78 行高 | 通过 |
| 深浅主题边界 | 颜色均来自 Obsidian 主题变量，无固定白底或黑字依赖 | 通过（当前浅色主题实测） |
| 模型品牌标识 | DeepSeek 显示鲸鱼品牌图形；OpenAI、Claude/Anthropic、Gemini、Qwen、Kimi、GLM、MiniMax、Mistral、Ollama、Hugging Face、Meta、xAI 等按模型身份自动映射，未知模型使用中性回退图标 | 通过 |
| Agent 头像 | 历史回答、流式回答和欢迎态统一使用用户提供的“机器人读书”头像 | 通过 |

## 实机截图

- `artifacts/ui-v8-screenshots/assistant-composer-focused.png`
- `artifacts/ui-v8-screenshots/assistant-model-picker.png`
- `artifacts/ui-v8-screenshots/model-brand-and-agent-avatar-final.png`
- `artifacts/ui-v8-screenshots/model-brand-picker-open-final.png`

截图来自当前 Vault 的 Obsidian 1.12.7，使用已安装插件构建，不是静态 Mockup。

## 行为确认

- Auto 模式继续使用 `assistant_chat` 当前路由。
- 选择具体模型只修改助手聊天路由，不影响 Brain、PDF 或其他任务路由。
- “配置自定义模型”继续打开现有 Provider 设置抽屉。
- Escape 和失焦均可关闭模型弹层。
- 本轮视觉验收没有发送消息，也没有确认或执行任何 Vault 写入。
