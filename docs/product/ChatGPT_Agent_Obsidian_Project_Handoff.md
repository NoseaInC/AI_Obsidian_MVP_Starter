# ChatGPT Desktop Agent 项目交接说明

## 0. 你的角色

你将接手一个正在开发中的个人 Obsidian AI 学习系统。

项目路径：

```text
/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter
```

请直接检查、修改和测试项目文件。目标是减少用户复制粘贴和手工维护。遇到真实资料写入、覆盖正式知识或隐私边界时，先展示计划并请求确认。

---

## 1. 项目目标

这是一个以 Obsidian 为知识控制台的 AI 辅助学习系统。

首个 MVP 只验证两个闭环：

### B：资料转化为知识

```text
选择 PDF
→ 本地提取文本
→ 调用 DeepSeek API
→ 创建来源索引
→ 创建论文整理草稿
→ 创建一篇主线主题草稿
→ 创建 0–3 篇主线概念草稿
→ 对已有知识生成更新建议
→ 全部进入待审核队列
```

用户不应该再手工创建笔记、复制 Copilot 输出或逐段粘贴。

### C：知识推动持续学习

系统最终应支持：

- 今日复习推荐；
- 今日新学习任务；
- 未来两天学习推荐；
- 工作日 2–3 道短测；
- 周末复述、辨析和应用题；
- AI 评估 mastery，用户确认；
- 每周日生成待确认的下周计划。

---

## 2. 用户学习目标

主要学习：

- 统计；
- 机器学习；
- 因果推断；
- LLM；
- Agent。

秋招方向：

- 数据科学；
- Agent；
- 机器学习与 AI 相关岗位。

路线：

- 统计与机器学习主线约 70%；
- LLM 与 Agent 支线约 30%。

学习偏好：

```text
定义与推导
> 做题与被提问
> 具体例子
> 自己复述
> 代码实验
```

时间：

- 工作日每天 20–30 分钟；
- 周末 3–4 小时；
- 周末知识库维护不超过 30 分钟。

---

## 3. 隐私和同步边界

永久遵守：

- PDF 原件留在 Zotero 或 MacBook 本地；
- 原始 PDF 不上传到云存储；
- 可以在本地提取文本，并把提取文本发送给 DeepSeek API；
- API Key 只能从环境变量读取；
- API Key 不得写入代码、日志、Markdown、缓存或测试；
- 可同步人工审核后的知识笔记、学习计划和学习记录；
- 未审核草稿、完整 AI 对话、提取文本、JSON、manifest、事务 journal 只留在 `90-Local-Only/`。

---

## 4. Obsidian 写入规则

状态：

```yaml
status: ai-draft
status: reviewed
status: core
```

规则：

- AI 可以创建和刷新同源 `ai-draft`；
- `reviewed` 和 `core` 永远不可自动覆盖；
- 对 `reviewed/core` 只能创建更新建议；
- 所有知识必须回链到来源；
- 重要论文尽量保留页码；
- AI 推断必须与论文原结论区分；
- 主题优先于论文局部技术；
- 每篇资料最多创建 0–3 篇高价值概念草稿。

概念优先：

- 可跨论文、教材和项目复用；
- 有独立定义、假设或适用边界；
- 值得独立复习；
- 是后续知识的前置概念；
- 属于指定领域主线。

降低优先级：

- 论文专属模块；
- 作者命名的局部组件；
- 单个实验技术细节；
- 太小、无法独立学习的术语。

---

## 5. 已完成配置

用户已完成：

- Starter Vault 解压并打开；
- Obsidian 核心插件、Templates、Daily Notes 配置；
- QuickAdd 安装和“记录灵感”命令；
- Copilot 安装；
- DeepSeek API 配置；
- Copilot“灵感展开”命令测试成功。

已成功处理：

```text
/Users/suyk/Desktop/因果图/dragonnet.pdf
```

曾生成：

```text
10-Sources/Papers/dragonnet-938e97f334d2.md
90-Local-Only/AI-Drafts/dragonnet-AI草稿-938e97f334d2.md
```

用户验证摘要准确、页码可靠。

理念已经调整：

> 不再采用 Copilot 生成后手工插入，而是让本地摄入系统自动生成主题和概念草稿，用户只审核。

---

## 6. 当前代码状态

Codex 已完成一轮改造。

新增：

```text
00-System/Scripts/ingest_pdf.py
00-System/Scripts/tests/test_ingest_pdf.py
00-System/Scripts/requirements.txt
00-System/Scripts/README.md
AGENTS.md
```

改造：

```text
00-System/Scripts/pdf_to_obsidian.py
README-先读.md
00-System/Home.md
00-System/Bases/...
```

保留：

```text
00-System/Scripts/conversation_to_obsidian.py
```

已有能力报告：

- 本地逐页提取；
- 带页码分块证据；
- 结构化 JSON；
- 一次修复重试；
- Vault inventory；
- 确定性概念排序；
- SHA-256 source_id；
- reviewed/core 保护；
- 更新建议；
- 单文件原子写入；
- manifest；
- dry-run；
- apply；
- 12 项离线测试。

但代码审查发现三个尚未修复的问题。

---

## 7. 当前阻塞问题

在修复完成前：

```text
禁止对真实 PDF 使用 --apply
```

只允许检查代码、运行离线测试和 dry-run。

### P1：多文件写入不是事务性的

当前逐个单文件 atomic write。后续文件失败时，前面文件已落盘，可能留下半套知识结构。

需要：

- 完整 write set；
- staging；
- transaction journal；
- 目标文件备份；
- 批量 commit；
- 中途失败回滚已创建和已替换文件；
- journal 状态；
- 失败事务不得留下部分知识文件。

### P1：模型标题漂移导致重复来源

来源索引和论文草稿路径依赖模型 title。

需要：

- source_id 继续基于 SHA-256；
- 来源索引和论文草稿路径稳定；
- 模型 title 只能写进 YAML 和正文；
- 规划前按 `source_id/generated_from/artifact_role` 查找已有生成物；
- `force-regenerate` 也不能创建第二份同源来源。

建议元数据：

```yaml
generated_by: ingest_pdf
generated_from: <source_id>
artifact_role: source-index | paper-draft | topic | concept | update-suggestion
artifact_id: <stable-id>
```

### P2：重跑时来源索引保留过期回链

同源来源索引被跳过，主题或概念改变后回链不会刷新。

需要：

- 同源脚本生成的来源索引允许安全刷新；
- 每次成功摄入后刷新 paper/topic/concept/update-suggestion 回链；
- 用户人工批注不能被覆盖；
- 推荐使用 managed block：

```markdown
<!-- ingest-pdf:managed:start -->
...
<!-- ingest-pdf:managed:end -->
```

只刷新 managed block 和系统管理 YAML 字段。

---

## 8. 接手后的第一项任务

先检查实际代码，不要假设报告完全准确。

重点：

```text
00-System/Scripts/ingest_pdf.py
00-System/Scripts/tests/test_ingest_pdf.py
00-System/Scripts/README.md
README-先读.md
AGENTS.md
```

完成：

### A. 跨文件事务

事务目录：

```text
90-Local-Only/Processing-Cache/transactions/<transaction_id>/
```

至少需要：

- staged 文件；
- journal；
- backups；
- committed_targets；
- completed / rolled_back / failed 状态；
- 中途失败回滚；
- 可诊断错误记录。

多个单文件 atomic write 不等于跨文件事务。

### B. 稳定命名

同一 PDF 无论模型标题如何变化，都复用同一来源索引和论文草稿路径。主题和概念也应尽量复用同源 artifact。

### C. 来源索引 managed block

刷新 AI 管理回链，同时保护人工批注。

### D. 增强测试

至少覆盖：

1. 第二次 commit 失败时回滚第一个新文件；
2. 替换旧文件后失败时恢复旧内容；
3. 回滚后没有部分知识文件；
4. journal 状态正确；
5. 成功 journal 为 completed；
6. 标题 A/B 重跑路径不变；
7. force-regenerate 不重复来源；
8. 回链刷新；
9. 人工批注保持；
10. managed block 外保持；
11. reviewed/core 不可覆盖；
12. dry-run 完全无写入；
13. 测试不访问网络；
14. 测试不读取真实 API Key。

事务测试必须使用临时目录，并真实注入中途 rename/write 失败。

验证：

```bash
cd "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter"

source 00-System/Scripts/.venv/bin/activate

python -m unittest discover   -s 00-System/Scripts/tests   -v

python -m py_compile   00-System/Scripts/ingest_pdf.py   00-System/Scripts/pdf_to_obsidian.py   00-System/Scripts/conversation_to_obsidian.py

python 00-System/Scripts/ingest_pdf.py --help
```

禁止调用真实 DeepSeek API。

---

## 9. 修复后的只读审查

重点验证：

1. 中途提交失败是否真正回滚；
2. 是否只是把 atomic write 改了名字；
3. 标题变化时是否复用同源路径；
4. `force-regenerate` 是否可能重复来源；
5. 回链是否刷新；
6. 人工批注是否被保留；
7. reviewed/core 是否有覆盖路径；
8. dry-run 是否完全无写入；
9. 测试是否真实注入文件系统失败；
10. 测试是否访问网络或读取 Key。

如果仍有 P0/P1，继续修复。

只有没有 P0/P1 后，才允许真实验证。

---

## 10. 第一轮真实验证

先运行 Dragonnet dry-run：

```bash
cd "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter"

source 00-System/Scripts/.venv/bin/activate

python 00-System/Scripts/ingest_pdf.py   --pdf "/Users/suyk/Desktop/因果图/dragonnet.pdf"   --vault "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter"   --kind paper   --domain-focus "因果推断"   --model deepseek-v4-pro   --max-concepts 3   --dry-run   --verbose
```

dry-run 应优先生成因果推断主线，而不是局部方法。

合理主题候选：

- 观测数据中的处理效应估计；
- 因果效应估计；
- 基于机器学习的因果推断。

合理主线概念：

- 潜在结果框架；
- 平均处理效应；
- 无混杂性；
- 重叠性；
- 倾向得分；
- 结果回归。

局部方法：

- Dragonnet 网络结构；
- Targeted Regularization；

可以留在论文整理或作为后续分支，不应默认成为唯一主线。

先把 dry-run 计划展示给用户确认，确认后才可 `--apply`。

---

## 11. 后续路线

### Phase 1：PDF 自动摄入

完成标准：

- 真实论文稳定处理；
- 不重复；
- 不覆盖正式知识；
- 主线主题选择合理；
- 用户只需审核；
- 所有内容可追溯；
- 首页能看到待审核内容。

### Phase 2：审核工作流

集中显示：

- 来源索引；
- 论文草稿；
- 主题草稿；
- 概念草稿；
- 更新建议。

支持：

- 接受；
- 修改后接受；
- 保留为来源摘要；
- 拒绝。

尽量避免手工移动和改 YAML。

### Phase 3：学习推荐

基于：

- mastery 0–4；
- next_review；
- weak_points；
- 当前路线；
- 前置依赖；
- 主线 70% / 支线 30%。

简单复习规则：

| mastery | 下次复习 |
|---|---|
| 0 | 1 天 |
| 1 | 2 天 |
| 2 | 4 天 |
| 3 | 8 天 |
| 4 | 21 天 |

AI 建议，用户确认。

### Phase 4：其他资料

顺序：

1. 高价值 AI 对话；
2. 教材章节；
3. 微信公众号；
4. 博客；
5. 视频；
6. Notebook / GitHub。

PDF 工作流稳定前不要扩张。

---

## 12. 长期工程原则

1. 减少用户复制粘贴。
2. AI 做脏活，用户做关键决策。
3. 原始资料与派生知识分离。
4. 所有知识可追溯。
5. reviewed/core 绝不静默覆盖。
6. 主线优先于论文局部创新。
7. 不自动囤积大量外部资料。
8. 不引入复杂基础设施，除非真实瓶颈出现。
9. 第一版不引入向量数据库、GraphRAG、多 Agent。
10. 每项自动化必须降低维护成本。
11. 真实写入前先 dry-run。
12. 高风险操作展示计划并请求确认。
13. 不访问项目目录外的文件，除非用户明确授权。
14. 不读取或输出 API Key。
15. 测试不得调用真实外部 API。

---

## 13. 与用户沟通

- 一次推进一个明确阶段；
- 能自己检查、修改、测试就直接做；
- 不让用户执行大量手工步骤；
- 真实资料写入前请求确认；
- 每次汇报：
  - 做了什么；
  - 测试结果；
  - 风险；
  - 下一步；
  - 用户只需做什么。

接手后的第一句话：

> 我先检查现有 `ingest_pdf.py`、测试和审查指出的三个问题；在事务性、稳定命名和来源回链刷新通过测试前，我不会对真实 PDF 执行 `--apply`。
