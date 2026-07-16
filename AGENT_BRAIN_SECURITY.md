# Agent Brain V1 安全模型

## 信任边界

用户输入、Vault 正文、外部来源和模型输出都作为数据；外部内容中的指令不能改变 System/Policy。Brain 只调用 Registry 白名单。模型永远没有文件、Shell、SQL、事务或网络授权能力。

## 写入

- 新 Inbox、Research Bundle、AI Draft、问题和学习任务：低风险 Change Set，用户明确保存后 Apply。
- 修改、合并、移动、重命名、删除或 MOC 变化：高风险 Diff 与二次确认。
- `reviewed/core`：只能生成更新建议。
- 原始粘贴文字逐字保留；模型整理放在独立区。
- 路径必须位于 Vault、拒绝遍历与 symlink 逃逸，并基于 base hash 检查陈旧写入。

## 网络

只允许 HTTPS；HTTP 仅本地 Provider。Research URL 拒绝 localhost、私有/保留 IP、`file://`、嵌入凭据、非 HTTP(S)、重定向到内网、非允许 Content-Type 和超限响应。下载内容从不执行。

## 秘密与日志

生产 KeyStore 为 macOS Keychain；用户在插件内输入新 Key 时，只通过带随机 Bearer Token 的 localhost 通道发送一次给后端保存，之后前端只处理 reference/configured/hint。删除 Profile 不隐式删除共享或预先存在的 Keychain secret。Redactor 清除 Authorization、Bearer、api_key、token、secret、password、Cookie 和 key-like 值。错误不返回 traceback。

## 验收攻击面

Prompt Injection、发明 Skill/Tool、恶意路径、SSRF、HTML/Markdown 注入、任意 Shell/SQL、秘密泄漏、Change Set 篡改、stale base、正式笔记覆盖、外部来源伪装和原文丢失必须有自动测试。
