# Chat-first 实施状态

更新时间：2026-07-14

## 已完成

- 统一会话和私有消息归档；
- 二进制、URL、路径和文件夹附件；
- Intake 幂等提交与多 Intent；
- 七类版本化 Artifact 和 active Artifact 连续修订；
- 资料、审核、计划、今日跨页投影；
- 助手拖放、粘贴、当前笔记、`@`、`/` 和固定输入区；
- Provider Profile、Keychain、模型路由和连接测试；
- 五页响应式视觉系统；
- 插件命令和 Ribbon 统一进入助手导入流程。

## 当前质量门

- PDF 摄入/审核/对话：40/40 通过；
- Agent/学习/Provider/Brain/Intake：69/69 通过；
- 插件：16/16 通过；
- `npm run typecheck`、生产构建和 `./scripts/check.sh`：通过；
- 安装目录中的 `main.js`、`styles.css` 与验证构建哈希一致；
- 首次真实 `apply-prepared`：未执行，仍需用户确认。

## 实机验收

- Obsidian 1.12.7 已重载安装构建；
- 本地 Agent 返回 HTTP 200、协议 1、schema 3；
- 助手、资料、审核、计划、今日均在线渲染；
- 唯一 P1（Prepared ID 被当作资料标题）已修复并补拍；
- 最终截图位于 `artifacts/chat-first-v1-screenshots/`；
- 当前没有已知视觉或安全 P0/P1。
