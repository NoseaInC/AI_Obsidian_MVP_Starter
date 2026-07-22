# Chat-first 安全说明

## 已实施控制

- 服务仅绑定 localhost，业务 API 使用随机会话 Token；
- CORS 仅允许 Obsidian/Electron 所需本地来源，支持 OPTIONS；
- URL Intake 拒绝 file、localhost、回环、私网、链路本地和危险重定向；
- 路径解析后必须位于允许根目录，拒绝 symlink 与路径穿越；
- PDF 校验文件签名、MIME 和大小；
- API Key 通过后端 KeyStore/macOS Keychain 保存，插件只持有 reference 和 masked hint；
- 自定义 Header 禁止 Authorization、Host、Content-Length；
- 日志和 SQLite 使用递归脱敏，不保存原始会话正文；
- 诊断包位于 `90-Local-Only`；
- 所有正式写入经过 Change Set、Verifier 和事务层；
- reviewed/core 自动写入由 Policy 拒绝。

## 信任边界

模型可以提出结构化计划和候选内容，但不能决定路径权限、覆盖正式知识、提交事务、访问 Keychain 或执行 shell。插件只能调用版本化本地 API，不能直接实现知识写入规则。

## 测试边界

测试使用临时 Vault、Fake Model、FakeKeyStore 和受控 HTTP adapter；禁止读取真实 PDF、真实 Key 或真实网络。

