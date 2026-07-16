# Agent Brain V1 调试与诊断

## 诊断内容

- 服务：Agent/Brain/API/schema/plugin 版本、端口、Runtime ID、状态。
- 模型：Profile 名、Provider、Base URL host、Key configured、模型、最后连接结果；不显示秘密。
- Runs：意图、Skill、状态、步骤、耗时、重试和错误码。
- Tool Events：Tool、状态、耗时、脱敏输入/输出摘要。
- 索引：reviewed 数、候选数、待审核数、Research Bundle 数和最近同步时间。

## 命令

打开主脑诊断、查看最近执行、重试最近失败、取消当前任务、测试模型连接、刷新课程候选池、导出脱敏诊断包。

## 日志

结构化事件日志位于 `90-Local-Only/Agent/logs/events.jsonl`；用户主动导出的脱敏诊断包位于 `90-Local-Only/AgentLogs/`。两者使用 `correlation_id`。Redactor 在写日志、审计和错误响应之前执行。诊断包仅含结构、计数、状态和脱敏摘要，不含 Key、Token、Header、完整正文、Prompt、真实 PDF 或完整对话。
