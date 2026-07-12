# AI 辅助 Obsidian 学习系统：MVP Starter

这套 Starter 用来验证两个闭环：

1. 资料能否稳定转化为可追溯的知识。
2. 知识能否驱动每天复习和未来两天的学习推荐。

## 一、导入方式

把压缩包解压后，将其中的文件夹复制到你的 Obsidian Vault 根目录。

如果你已有同名目录，请先备份，再按文件逐项合并，不要直接覆盖已有内容。

建议先在一个测试 Vault 中运行 3—7 天。

## 二、第一版只安装这些插件

### Obsidian 核心插件

在“设置 → 核心插件”中启用：

- Bases
- Properties view
- Templates
- Daily notes
- Backlinks
- Search

### 社区插件

第一阶段必装：

1. Copilot for Obsidian
2. QuickAdd

PDF / Zotero 工作流需要：

3. Zotero Integration
4. Zotero 端的 Better BibTeX

第一周先不要安装 Dataview、Tasks、复杂 Agent 或知识图谱插件。

## 三、模板设置

设置 → Templates：

- Template folder location：
  `00-System/Templates`

设置 → Daily notes：

- New file location：
  `30-Learning/Daily`
- Template file location：
  `00-System/Templates/T-每日学习.md`
- Date format：
  `YYYY-MM-DD`

## 四、QuickAdd 设置

创建一个 Template Choice：

### 记录灵感

- Choice type：Template
- Template path：`00-System/Templates/T-灵感.md`
- File name：`灵感-{{DATE:YYYYMMDD-HHmm}}`
- Create in folder：`01-Inbox/Ideas`
- 建议快捷键：`Cmd + Shift + I`

再创建两个 Template Choice：

### 新建论文来源

- Template path：`00-System/Templates/T-论文来源.md`
- File name：`{{VALUE:标题}}`
- Create in folder：`10-Sources/Papers`

### 新建教材章节

- Template path：`00-System/Templates/T-教材章节.md`
- File name：`{{VALUE:标题}}`
- Create in folder：`10-Sources/Textbooks`

## 五、Copilot 与 DeepSeek

建议配置两个模型：

- 日常整理、短测、灵感展开：`deepseek-v4-flash`
- 论文精读、概念更新建议、周计划：`deepseek-v4-pro`

DeepSeek 参数：

- Base URL：`https://api.deepseek.com`
- API Key：你的 DeepSeek API Key
- Model：`deepseek-v4-flash` 或 `deepseek-v4-pro`

不要把 API Key 写入笔记或脚本。脚本通过环境变量读取：

```bash
export DEEPSEEK_API_KEY="你的密钥"
```

把 `00-System/Prompts` 中的提示词添加到 Copilot 的自定义命令中。

## 六、隐私边界

可以同步：

- `20-Knowledge`
- `30-Learning`
- 经审核的来源索引
- `00-System` 中不含密钥的模板和 Bases

建议排除同步：

- `90-Local-Only`
- 原始 PDF 所在目录
- 完整 AI 对话归档
- 提取文本和处理中间文件

如果使用 Obsidian Sync，请在首次同步前把 `90-Local-Only` 加入 Excluded folders。

## 七、每日流程

打开：

`00-System/Home.md`

工作日只做：

1. 完成“今日到期”中的 1—3 个复习主题。
2. 完成一个 10—15 分钟的新知识任务。
3. 用 Copilot 的“每日短测”命令生成 2—3 道题。
4. 回答后让 AI 建议 mastery 和 next_review。
5. 你确认后再修改属性。

## 八、周末流程

总时长 3—4 小时，其中系统维护不超过 30 分钟：

1. 处理本周 1—3 份高价值资料。
2. 审核 AI 草稿。
3. 接受、修改或拒绝概念更新建议。
4. 做一次复述、辨析和应用题。
5. 用“周计划生成”提示词生成下周计划。
6. 确认后把状态从 `proposed` 改为 `active`。

## 九、简单复习间隔

第一版先用可解释规则，不上复杂推荐算法：

| AI 建议掌握度 | 含义 | 建议下次复习 |
|---|---|---|
| 0 | 未接触/完全错误 | 明天 |
| 1 | 看过但不能解释 | 2 天后 |
| 2 | 能说定义和原理 | 4 天后 |
| 3 | 能答典型题和辨析 | 8 天后 |
| 4 | 能迁移应用或完整讲解 | 21 天后 |

出现关键错误时，掌握度最多下降 1 级，下一次安排在 1—2 天后。AI 只提出建议，你负责确认。

## 十、本地 PDF 自动摄入

脚本不会上传 PDF 文件，只会：

1. 在本地逐页提取文字；
2. 给每页加入页码标记；
3. 把提取文字发送给 DeepSeek API；
4. 先返回严格 JSON 并在本地校验页码和字段；
5. 扫描已有主题、概念与状态；
6. 在一次完整计划中创建来源索引、论文整理、主线主题、概念草稿和必要的更新建议。

模型不会直接写 Markdown。`reviewed` 和 `core` 笔记永远只读；同一 PDF 使用 SHA-256 幂等处理。详细架构、安全边界、审核与回滚见 [[00-System/Scripts/README|PDF 自动摄入说明]]。

安装依赖：

```bash
cd "/你的/Vault/00-System/Scripts"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

处理论文：

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/本地路径/paper.pdf" \
  --vault "/你的/Vault" \
  --kind paper \
  --domain-focus "因果推断" \
  --model deepseek-v4-pro \
  --max-concepts 3 \
  --dry-run
```

处理教材章节或学习资料：

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/本地路径/chapter.pdf" \
  --vault "/你的/Vault" \
  --kind textbook \
  --domain-focus "统计与机器学习" \
  --model deepseek-v4-pro \
  --apply
```

日常正式命令：

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

旧入口 `pdf_to_obsidian.py` 仍可用，内部转到新实现。

## 十一、本地 Agent 与 Obsidian 插件

启动只监听本机的服务：

```bash
./scripts/start-agent.sh
```

服务默认地址为 `http://127.0.0.1:8765`，健康检查为 `/health`。运行状态保存在 `90-Local-Only/Agent/agent.sqlite3`；Markdown 仍是知识正文真相。

插件构建与安装：

```bash
cd obsidian-agent-plugin
npm install --cache .npm-cache
npm test
npm run typecheck
npm run build
```

把 `manifest.json` 和 `dist/main.js` 复制到 `.obsidian/plugins/obsidian-learning-agent/` 后在 Obsidian 中启用。插件不保存密钥，只连接 localhost，也不会静默批准 Change Set。

处理导出的高价值 AI 对话：

```bash
python conversation_to_obsidian.py \
  --input "/本地路径/conversation.md" \
  --vault "/你的/Vault" \
  --platform ChatGPT \
  --model deepseek-v4-pro
```

这个兼容入口现在只生成 Prepared Bundle，不会直接写入知识目录。复制输出的 `prepared_id` 后使用 `prepared_pdf.py inspect`；确认后才进入 `apply-prepared`。

## 十二、四周内不要做的事

- 不要一次导入所有 Zotero 文献。
- 不要让 AI 自动修改 `reviewed` 或 `core` 笔记。
- 不要为每个术语创建原子笔记。
- 不要追求完整知识图谱。
- 不要为了自动化而自动化。
- 不要每天维护超过 5 分钟。
