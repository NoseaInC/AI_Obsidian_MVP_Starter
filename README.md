<div align="center">

# 知序 Zhixu

**本地优先的 Obsidian 学习 Agent —— 把资料变成可追溯、可复习、可复用的知识**

```
资料摄入 → 知识沉淀 → 每日复习 → 长期记忆 → 下一轮学习
```

*Local-first AI learning agent that turns raw materials into traceable, reviewable knowledge — and turns knowledge into a daily learning plan.*

</div>

---

## 为什么做知序

大多数 AI 知识工具只解决一半问题：它们能生成笔记，但生成的内容**不可追溯、不可复习、不可复用**——你不知道它来自哪一页，它不会进入你的复习计划，更不会记住你的长期目标。

知序（Zhixu）是一个完整的闭环：

1. **资料 → 知识**：PDF、网页、AI 对话经过本地提取、结构化校验、事务化写入，成为带页码证据的知识笔记。
2. **知识 → 学习**：可复习单元驱动每天的复习调度与学习推荐，掌握度由你确认，不靠模型猜测。
3. **对话 → 记忆**：跨会话长期记忆（目标、偏好、知识状态、项目决策），让 Agent 越来越懂你。

所有知识正文以 **Markdown 为唯一真相**，所有写入**可回滚、可审计**，所有私密数据**留在本地**。

---

## 核心特性

### 🧠 单一 Agent 内核（Pi Agent Runtime）
- 内嵌 [@earendil-works/pi-agent-core](https://www.npmjs.com/package/@earendil-works/pi-agent-core) + `pi-ai`，模型 ↔ 工具 ↔ 观察 → 重规划的单循环
- 会话树 / Fork / Steering / Follow-up / 上下文压缩 / 停滞保护
- 模型推理文本与最终答案严格分离，永不进入后续上下文

### 🔐 安全第一的写入模型
- **Task Authorization**：每轮 Turn 冻结可写路径范围，越权必须显式确认
- **可逆事务**：Snapshot → 原子写入 → 校验 → Diff → Undo，并发人工编辑永远优先
- `reviewed` / `core` 知识永远只读，自动化只能生成更新建议
- 权限卡 = 挂起的 Tool Call，重启后原样恢复

### 📚 Workspace Policy —— Agent 懂你的 Vault
- 目录语义、8 种笔记类型、写作规则、链接规则以 Markdown 定义（`00-System/AI/`）
- 每轮注入 ~800 token 的常驻规则摘要 + 按需读取工具
- 所有写入先产生 **WriteIntent**，经策略校验后才进入事务

### 💾 长期记忆（Memory V1）
- 四种记忆类型：目标 / 偏好 / 知识状态 / 项目决策
- 显式表达才写入（`记住…/以后请…`），候选晋升需多日多证据
- 证据只存引用（消息/测验/行为 ID），绝不复制对话正文

### 📈 学习看板（Dashboard）
- 每日学习时长、知识资产、Agent 工作量、存储占用——单接口快照
- 真实 SQLite `PRAGMA quick_check`，无假健康状态

### 📅 今日 / 计划
- 复习单元 = `review_unit=true AND status∈{reviewed,core}`（语义判定，非目录判定）
- 70/30 主线加权、到期复习优先、掌握度由用户确认

---

## 架构

```
┌────────────────────────────────────────────────┐
│            Obsidian Plugin (TypeScript)         │
│  今日 · 计划 · 看板 · 助手     Session Tree     │
└──────────────────────┬─────────────────────────┘
                       │ localhost NDJSON
┌──────────────────────▼─────────────────────────┐
│           Pi Agent Core + Pi AI (唯一 Agent)    │
│        Model ↔ Tool ↔ Observation ↔ Replan     │
└──────────────────────┬─────────────────────────┘
                       │ typed tool requests
┌──────────────────────▼─────────────────────────┐
│          Python Secure Runtime (I/O 边界)       │
│  Model Proxy · Keychain · Authorization        │
│  Workspace Policy · Memory · Retrieval         │
│  Reversible Transactions · Dashboard           │
└──────────────────────┬─────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────┐
│        Markdown (知识真相) + SQLite (运行时)     │
└────────────────────────────────────────────────┘
```

**语言边界**：TypeScript 拥有 Obsidian 交互、状态归约与 Pi Agent 循环；Python 拥有安全边界——密钥、模型代理、工具执行、策略、事务与持久化。两者互不越界。

---

## 快速开始

### 1. 安装到 Obsidian

1. 把仓库中的文件夹复制到你的 Obsidian Vault 根目录（先备份同名目录）。
2. 启用核心插件：Bases、Properties view、Templates、Daily notes、Backlinks。
3. 安装社区插件：Copilot for Obsidian、QuickAdd（PDF 工作流还需 Zotero Integration + Better BibTeX）。
4. 模板与 QuickAdd 配置见 [`README-先读.md`](README-先读.md)。

### 2. 配置模型

```bash
export DEEPSEEK_API_KEY="你的密钥"   # 脚本与 Runtime 只从环境变量/Keychain 读取
```

推荐：`deepseek-v4-pro`（论文精读、周计划）/ `deepseek-v4-flash`（日常整理、短测）。

### 3. 启动本地 Agent

```bash
./scripts/start-agent.sh
# 健康检查: http://127.0.0.1:8765/health
```

插件构建与安装：

```bash
cd obsidian-agent-plugin
npm install --cache .npm-cache
npm test && npm run typecheck && npm run build
```

### 4. 摄入第一份资料

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/本地路径/paper.pdf" \
  --vault "/你的/Vault" \
  --kind paper \
  --domain-focus "因果推断" \
  --model deepseek-v4-pro \
  --max-concepts 3 \
  --dry-run          # 先预览，确认后再 --apply
```

每日 10–15 分钟：完成 1–3 个到期复习 → 一个学习任务 → 一次 AI 短测。详见 [`README-先读.md`](README-先读.md)。

---

## 目录结构

```
00-System/       系统：Home、AI 规则、Templates、Bases
01-Inbox/        临时输入
10-Sources/      外部资料出处（Papers / Books / Courses / Web）
20-Knowledge/    正式知识（MOCs / Courses / Topics / Concepts）
30-Learning/     学习记录（Daily / Weekly / Plans）
40-Projects/     项目专属内容
80-Archive/      归档
90-Local-Only/   本地私有：提取文本、模型 JSON、中间产物（不同步）

agent/            Python 安全运行时（core / tools / memory / workspace_policy）
obsidian-agent-plugin/   Obsidian 插件（TypeScript）
docs/             架构、验收、产品与 UI 规范
```

---

## 工程文档

| 文档 | 内容 |
|---|---|
| [`README-先读.md`](README-先读.md) | 安装、配置与每日使用（中文） |
| [`docs/architecture/PI_AGENT_RUNTIME_SPEC.md`](docs/architecture/PI_AGENT_RUNTIME_SPEC.md) | Pi Agent 运行时规范 |
| [`docs/architecture/WORKSPACE_MEMORY_V1.md`](docs/architecture/WORKSPACE_MEMORY_V1.md) | Workspace Policy + 长期记忆 V1 |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | 当前状态与恢复点 |
| [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) | 阶段路线图 |
| [`DECISIONS.md`](DECISIONS.md) | 技术决策记录（D-001 … D-060） |
| [`SECURITY.md`](SECURITY.md) | 安全模型 |

---

## 安全模型

- **原始 PDF 只在本地读取**，模型 API 只接收带 `PAGE` 标记的本地提取文本
- **密钥**只从环境变量 / macOS Keychain 读取，绝不写入仓库、日志或笔记
- **`reviewed` / `core` 永远只读**；自动化只能创建更新建议
- **所有写入**先结构化校验与完整计划，临时文件 + 原子 rename，避免半成品
- **`90-Local-Only` 不同步**，提取文本、原始模型 JSON 与更新建议留在本地
- 测试**不读取真实 PDF、不调用真实模型 API、不依赖真实密钥**

---

## 测试与质量门禁

```bash
./scripts/check.sh   # 一键离线门禁
```

- 276 Python 单元/集成测试（摄入、事务、Pi 运行时、记忆、策略）
- 195 插件测试（TypeScript，含随机化回归、模糊流解析）
- 严格 TypeScript typecheck + 生产构建
- 20,000 事件确定性重放、1,000 计划 / 10,000 状态动作随机回归

---

## 路线图

- ✅ **Phase 0–10**：工程基础 → 资料摄入 → 复习闭环 → 本地 Agent → Obsidian 插件 → 学习推荐 → 附加来源 → 学习工作台 → Assistant Runtime V2
- ✅ **Phase 11**：Pi Agent Runtime（唯一生产 Agent 内核）
- ✅ **Workspace Policy + Memory V1**：Vault 语义 + 跨会话长期记忆
- ⏳ Deferred：向量数据库、GraphRAG、多 Agent 编排、Cancellable SSE

---

## License

本仓库暂未声明开源许可证（未见 `LICENSE` 文件）。第三方组件与依赖的许可声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。如有使用需求，请联系仓库所有者。*No license is declared for this repository; third-party notices are listed in `THIRD_PARTY_NOTICES.md`.*

---

<div align="center">

**知序 —— 让 AI 真正参与你的学习，而不只是替你生成内容。**

</div>
