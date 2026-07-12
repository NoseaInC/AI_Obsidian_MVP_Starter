# PDF 自动摄入

`ingest_pdf.py` 把本地 PDF 转成一组待人工审核、带来源和页码的知识草稿。它不会上传 PDF 文件，也不会自动修改 `reviewed` 或 `core` 笔记。

## 架构

流程固定为：本地逐页提取 → 保留 `PAGE` 标记并分块 → DeepSeek 提取证据 JSON → 综合结构化 JSON → 本地严格校验（失败只修复一次）→ 扫描 Vault → 生成完整写入计划 → staging → 带备份的跨文件事务提交。

模型不直接生成最终 Markdown。标题、frontmatter、回链、审核区和证据表都由本地 Python 渲染。

## 安装

需要 macOS 和 Python 3.13。建议使用脚本目录现有虚拟环境：

```bash
cd "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter/00-System/Scripts"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

API Key 只放在当前 shell 的环境变量中：

```bash
export DEEPSEEK_API_KEY="在这里填写你的密钥"
```

不要把上面的真实值保存到 Vault、shell 脚本或版本库。

## Prepare → Inspect → Apply Prepared

正式工作流不会在确认后重新调用模型：`prepare` 调用模型一次并把不可变 Bundle 写入 `90-Local-Only/Prepared-Bundles/`；`inspect` 只读展示；`apply-prepared` 校验 Bundle、PDF、schema 和目标状态后事务提交，且不会创建模型客户端。

```bash
python 00-System/Scripts/prepared_pdf.py prepare \
  --pdf "/path/to/paper.pdf" --vault "/path/to/vault" \
  --kind paper --domain-focus "因果推断" --model deepseek-v4-pro

python 00-System/Scripts/prepared_pdf.py list-prepared --vault "/path/to/vault"
python 00-System/Scripts/prepared_pdf.py inspect --vault "/path/to/vault" <prepared_id>
python 00-System/Scripts/prepared_pdf.py apply-prepared --vault "/path/to/vault" <prepared_id>
```

也支持 `reject-prepared --reason ...` 和仅清理已应用/已拒绝 Bundle 的 `clean-prepared`。首次真实 `apply-prepared` 必须由用户确认。

Bundle 至少保存 request、标准化结果、证据、Vault inventory、Change Set、预览和 manifest；提取文本及模型原响应同样只在 `90-Local-Only`。

## 审核命令

Prepared 内容写入后无需手工移动文件或修改 YAML：

```bash
python 00-System/Scripts/review.py --vault "/path/to/vault" list
python 00-System/Scripts/review.py --vault "/path/to/vault" show <artifact_id>
python 00-System/Scripts/review.py --vault "/path/to/vault" diff <artifact_id>
python 00-System/Scripts/review.py --vault "/path/to/vault" approve <artifact_id>
python 00-System/Scripts/review.py --vault "/path/to/vault" approve-edited <artifact_id>
python 00-System/Scripts/review.py --vault "/path/to/vault" reject <artifact_id> --reason "..."
python 00-System/Scripts/review.py --vault "/path/to/vault" reopen <artifact_id>
```

所有状态转换与审计记录在同一事务中提交。`reviewed/core` 不会被 reopen 降级；拒绝记录会阻止同源同 artifact 重复生成。

## AI 对话与教材

教材 PDF 使用相同的 `prepared_pdf.py prepare --kind textbook`，生成教材学习草稿后再 inspect/apply/review。

高价值 AI 对话使用：

```bash
python 00-System/Scripts/prepared_conversation.py \
  --input "/path/to/conversation.md" --vault "/path/to/vault" \
  --platform ChatGPT --model deepseek-v4-pro
```

该命令只创建 Prepared Bundle。完整对话保存在 Bundle 的 `raw-conversation.txt`，助手结论强制标记 `needs-verification`；随后用通用 `prepared_pdf.py inspect/apply-prepared` 和 `review.py` 完成同一审核闭环。

## 兼容 dry-run

`--dry-run` 是默认模式：它会提取文本、调用模型、校验 JSON 并打印完整计划，但不写入任何文件。

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/path/to/paper.pdf" \
  --vault "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter" \
  --kind paper \
  --domain-focus "因果推断" \
  --model deepseek-v4-pro \
  --max-concepts 3 \
  --dry-run
```

旧 `ingest_pdf.py --dry-run/--apply` 入口保留兼容，但日常正式写入应使用 Prepared 工作流，避免确认后模型结果漂移。

## 正式执行

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/path/to/paper.pdf" \
  --vault "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter" \
  --kind paper \
  --domain-focus "因果推断" \
  --model deepseek-v4-pro \
  --max-concepts 3 \
  --apply
```

旧命令 `pdf_to_obsidian.py` 保留为兼容包装器，参数与新入口相同。

## 生成位置

- `10-Sources/Papers/` 或 `10-Sources/Textbooks/`：来源索引。
- `90-Local-Only/AI-Drafts/`：论文或教材整理草稿。
- `20-Knowledge/Topics/`：默认最多一篇主线主题草稿。
- `20-Knowledge/Concepts/`：通过确定性评分筛选的 0–N 篇概念草稿。
- `90-Local-Only/AI-Drafts/Update-Suggestions/`：对已有正式知识的更新建议。
- `90-Local-Only/Extracted-Text/`：带页码的提取文本。
- `90-Local-Only/Processing-Cache/model-results/`：验证结果与模型原始响应。
- `90-Local-Only/Processing-Cache/manifests/`：本次创建、更新、跳过、警告和缓存位置。
- `90-Local-Only/Processing-Cache/transactions/`：事务 journal、staging 与目标文件备份；状态为 `completed`、`rolled_back` 或 `failed`。

## 审核流程

打开 `00-System/Home.md` 的“待审核 AI 草稿”。先审核论文整理，再审核主线主题和概念，最后处理更新建议。确认无误后，由人工把状态改为 `reviewed` 或合并进 `core`；脚本不会替你完成这一步。

同一 PDF 以完整 SHA-256 形成稳定 `source_id`。生成物通过 `generated_from`、`artifact_role` 和 `artifact_id` 复用已有路径，模型标题变化或 `--force-regenerate` 不会创建第二份来源。来源索引只刷新 `ingest-pdf:managed` 区块和脚本管理的 YAML 字段，区块外的人工批注及自定义属性会保留。同名或高度相似的 `reviewed`/`core` 只会收到更新建议。

## Dragonnet 示例

处理 Dragonnet 论文时指定 `--domain-focus "因果推断"`。主题应优先落在“观测数据中的处理效应估计”“因果效应估计”或“基于机器学习的因果推断”等学习主线。MVP 阶段，Targeted Regularization、Dragonnet 网络头或论文专属损失等 `paper_specific` 候选只保留在论文整理中，不自动提升为独立概念笔记。

## 安全边界与常见错误

- `缺少环境变量 DEEPSEEK_API_KEY`：在当前终端设置环境变量后重试。
- `没有提取到文字`：PDF 可能是扫描件，先在本地 OCR。
- `页码超出范围`：模型结果被拒绝；脚本修复一次，仍失败则不写知识文件。
- `稳定生成文件存在非本来源或正式内容`：人工检查冲突文件，不要强制覆盖。
- 网络或模型错误：不会产生半篇 Markdown；修复连接后重新运行。
- 事务提交错误：脚本会恢复已替换文件并删除本事务已创建文件；查看对应 transaction journal。若状态是 `failed` 而非 `rolled_back`，停止重跑并先人工检查 journal 中的目标。

终端错误不会输出 API Key。测试使用 fake client，不访问外部 API。

## 回滚一次摄入

1. 提交中途失败时不需手工回滚；先确认 transaction journal 为 `rolled_back`。
2. 对已经 `completed`、但事后决定撤销的摄入，打开 manifest 和对应 transaction 目录。
3. 只删除 `created_files` 中确认由该次运行新建的文件；`updated_files` 从 transaction backups 或 Vault 版本历史恢复。
4. 可一并删除 manifest 指向的本地模型缓存和同 source hash 的提取文本。
5. 如果草稿已经人工合并为 `reviewed`/`core`，不要自动回滚，改为人工审查。

## 验证

```bash
python -m unittest discover -s 00-System/Scripts/tests -v
python 00-System/Scripts/ingest_pdf.py --help
```
