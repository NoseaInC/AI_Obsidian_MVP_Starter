# 知序模型 Provider 规格

## Profile

Provider 类型：DeepSeek、OpenAI、OpenAI-compatible、Custom。Profile 保存名称、Base URL、Keychain 引用、默认模型、模型列表、能力开关、超时、Temperature、Max Tokens 和安全自定义 Headers。

禁止自定义覆盖 `Authorization`、`Host`、`Content-Length`。外部 Base URL 必须使用 HTTPS；HTTP 只允许 localhost。URL 不允许内嵌凭据、query 或 fragment。

## KeyStore

- 生产：`MacKeychainStore` 使用固定 `/usr/bin/security` argv，`shell=False`，不记录 stdout、stderr 或密钥。
- 开发：环境变量引用和 `FakeKeyStore`。
- SQLite、Markdown、插件 data.json 和日志只保存 Key 引用、configured 和 masked hint。

## Provider

`OpenAICompatibleProvider` 集中实现 models、connection test、chat、stream chat 和 structured output。DeepSeek/OpenAI 是可编辑预设，复用同一协议适配器。

DeepSeek 的结构化请求使用 `response_format.type = json_object`，并在 system message 中附加受限 JSON Schema；意图识别同时关闭 thinking，避免推理文本污染 JSON。其他 OpenAI-compatible Provider 默认使用 `json_schema`。HTTP 400、认证、限流和超时必须映射为可读的结构化错误，不能统一折叠成“模型不可用”。

## 路由

独立任务：`brain_orchestrator`、`intent_router`、`curriculum_planner`、`research_synthesis`、`tutor`、`quiz`、`evaluation`、`pdf_prepare`、`assistant_chat`。没有配置模型时，确定性推荐、资料状态、审核和学习计划仍可使用。

助手顶部模型选择器只更新 `assistant_chat`。主脑编排和意图识别必须通过路由设置单独选择，避免一次普通聊天模型切换影响整个 Brain。
