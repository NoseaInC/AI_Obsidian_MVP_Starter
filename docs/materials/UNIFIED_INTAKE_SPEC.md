# Unified Intake 规格

## 输入

`POST /api/v1/intake/submit` 接受：

- `message`：用户目标；
- `conversation_id`：已有会话，可省略；
- `attachments`：已上传附件引用；
- `references`：Vault 笔记、当前笔记或显式本地路径；
- `active_artifact_id`：继续修改的当前成果；
- `mode` 与有限执行选项。

二进制附件先通过 `/intake/attachments` 上传。插件以原始 `ArrayBuffer` 发送，不把内容转入 `data.json`。

## 附件策略

- 单文件上限 80 MB；
- PDF 同时校验 MIME、扩展名与 `%PDF-` 文件签名；
- SHA-256 去重；
- symlink 拒绝；
- Vault 外路径仅在用户明确选择后允许；
- 文件夹超过 20 项时返回最小澄清，不静默全量扫描；
- URL 经过协议、DNS、私网和重定向 SSRF 检查；
- 原始内容位于 `90-Local-Only/Agent/Attachments/` 和 `Conversations/`。

## Intent

确定性路由优先，模型只补充歧义。支持材料整理、保存到 Obsidian、知识缺口、今日计划、资料比较、理解评估和 Artifact 继续修订；一次请求最多三个 secondary intents。

## 幂等和继续修改

客户端为提交生成 `Idempotency-Key`。重复请求返回同一结果。存在 active Artifact 时，用户的“不要拆”“移到周末”“标为猜测”等要求创建下一版本并保留父版本，而不是复制成果。

## 隐私

SQLite 不保存原始消息或附件正文，只保存哈希、字符数、私有文件引用和结构化状态。API 返回公开附件元数据，不暴露真实存储路径。

