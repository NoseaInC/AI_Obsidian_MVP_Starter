<div align="center">

# 知序 Zhixu

**把资料变成知识，把知识变成行动。**

A local-first AI learning agent for Obsidian — turns PDFs, web pages and AI conversations into traceable, reviewable knowledge, then drives your daily review and long-term learning plan.

[中文 README](README-先读.md) · [架构规范](docs/architecture/PI_AGENT_RUNTIME_SPEC.md) · [路线图](PROJECT_ROADMAP.md) · [决策记录](DECISIONS.md)

</div>

<div align="center">

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?style=flat-square&logo=typescript&logoColor=white)
![Obsidian](https://img.shields.io/badge/Obsidian-1.12.7-7C3AED?style=flat-square&logo=obsidian&logoColor=white)
![Pi Agent Core](https://img.shields.io/badge/Agent-Pi%20Agent%20Core-0B5563?style=flat-square)
![Tests](https://img.shields.io/badge/tests-276%20Python%20%2B%20195%20TS-22C55E?style=flat-square)
![Local-first](https://img.shields.io/badge/local--first-90--Local--Only-0EA5E9?style=flat-square)

</div>

---

<p align="center">
  <img src="artifacts/assistant-real-product-v1-screenshots/assistant-agent-running.png" alt="知序 助手界面" width="90%" />
</p>

**知序（Zhixu）** 是一个运行在 Obsidian 里的本地优先 AI 学习 Agent。它不是又一个"AI 聊天插件"——它把整个学习闭环装进你的 Vault：

```
原始资料 (PDF / 网页 / AI 对话)
   ↓  本地提取 + 页码证据
Prepared Bundle
   ↓  结构化校验 + 审核
可追溯知识笔记 (Markdown)
   ↓  review_unit 语义判定
每日复习 + 学习推荐
   ↓  用户确认的掌握度
长期记忆 (目标 / 偏好 / 知识状态)
```

## 为什么是知序

大多数 AI 笔记工具只解决"生成"：模型吐出一段文字，你复制粘贴。没有来源、没有复习、没有记忆——知识是死的。

知序从第一天就把三件事做对：

| | 传统 AI 插件 | 知序 |
|---|---|---|
| **来源** | 无法追溯，AI 可能编造 | 每条结论携带页码 / 消息证据 |
| **写入** | 直接改文件，不可回滚 | WriteIntent → 策略校验 → 可逆事务 → Undo |
| **复习** | 生成完就结束 | 进入每日复习调度，掌握度由你确认 |
| **记忆** | 每轮对话失忆 | 跨会话记住你的目标、偏好与项目决策 |

---

## 核心能力

### 🧠 单一 Agent 内核 — Pi Agent Runtime

<details>
<summary><b>模型 ↔ 工具 ↔ 观察 → 重规划</b>，唯一生产 Agent Loop</summary>

- 内嵌 [`@earendil-works/pi-agent-core`](https://www.npmjs.com/package/@earendil-works/pi-agent-core) + `pi-ai`，打包在 Obsidian 插件内，无 sidecar
- 会话树 / Fork / Steering / Follow-up / 上下文压缩 / 停滞保护 / 断线重连
- 模型推理文本与最终答案严格分离：思考过程可折叠查看、可独立复制，但**永不进入后续模型上下文**
- Python 只做安全 I/O：模型代理、Keychain、工具执行、授权、事务、检索、持久化

</details>

### 🔐 企业级写入安全

<details>
<summary><b>任务授权 + 可逆事务 + 冲突安全 Undo</b></summary>

- 每轮 Turn 冻结可写路径范围（Task Authorization），首次写计划自动绑定，越权必须显式确认
- 写入链路：快照 → 原子写入 → 哈希校验 → Diff → Undo；并发人工编辑永远优先，绝不覆盖
- `reviewed` / `core` 知识**永远只读**——自动化只能生成更新建议
- 权限卡是持久化的挂起 Tool Call：重启后原样恢复，批准/拒绝幂等
- 未授权路径、符号链接逃逸、过期哈希：硬拒绝，不可绕过

</details>

### 📚 Workspace Policy — Agent 懂你的 Vault

<details>
<summary><b>目录语义、笔记类型、写作规则以 Markdown 定义</b></summary>

- `00-System/AI/` 下四份规则文档：Vault 章程、8 种笔记类型、写作策略、链接策略
- 每轮自动注入 ~800 token 常驻摘要 + 按需读取工具
- 所有写入先产生**结构化 WriteIntent**（类型 / 目录 / 更新或新建 / 链接 / 复习单元），经策略校验后才进入事务
- 固定映射：`concept→Concepts`、`topic→Topics`、`course-chapter→Courses`、`project-note→40-Projects`

</details>

### 💾 长期记忆 V1

<details>
<summary><b>跨会话记住目标、偏好、知识状态与项目决策</b></summary>

- 四种记忆类型；只有用户**明确表达**（"记住…/以后请…/我的长期目标是…"）才写入
- 隐式候选晋升需 ≥3 条证据、≥2 个不同日期、置信度 ≥0.75
- 冲突走 supersede 链（新 active / 旧 superseded），绝不原地覆盖
- 证据只存引用（消息 / 测验 / 行为 ID），**绝不复制对话正文**
- `PiAgentRuntime` 每轮注入 ≤8 条 / ≤800 token 的相关记忆

</details>

### 📈 学习看板

<details>
<summary><b>今日 / 计划 / 看板 / 助手 四模块</b></summary>

- 单接口快照：学习时长、知识资产、Agent 工作量、存储分类、真实 SQLite 健康检查
- 复习单元 = `review_unit=true AND status∈{reviewed,core}`——课程长文不进复习，原子概念可复习
- 70/30 主线加权，掌握度由你确认，AI 只建议

</details>

---

## 界面

<p align="center">
  <img src="artifacts/today-study-workspace-v1-screenshots/03-study-learning-wide-light.png" alt="今日 · 学习工作台" width="90%" />
</p>

| 今日 | 计划 | 看板 | 助手 |
|---|---|---|---|
| 到期复习 + 新学习推荐 | 周计划与候选 | 学习/存储/Agent 指标 | Pi Agent 对话 + 工具轨迹 |

<p align="center">
  <img src="artifacts/assistant-real-product-v1-screenshots/assistant-empty-dark.png" alt="助手 · 深色主题" width="90%" />
</p>

更多界面：`artifacts/` 下包含 142 张真实 Obsidian 验收截图（深色/浅色、窄屏、离线、权限卡、Undo、推理折叠等）。

---

## 架构

<p align="center">
  <img src="docs/assets/architecture.svg" alt="知序 系统架构图" width="100%" />
</p>

**语言边界是安全边界**：TypeScript 拥有交互与 Agent 决策；Python 拥有密钥、策略、事务与持久化。互不越界，架构测试强制。

---

## 快速开始

### 安装

```bash
# 1. 复制 Vault 目录结构到你的 Obsidian Vault（先备份同名目录）
# 2. 启用核心插件：Bases / Properties / Templates / Daily notes / Backlinks
# 3. 安装社区插件：Copilot for Obsidian、QuickAdd（PDF 需 Zotero Integration）
```

### 配置模型

```bash
export DEEPSEEK_API_KEY="你的密钥"   # 或使用 macOS Keychain 引用
```

推荐：`deepseek-v4-pro`（精读 / 计划）/ `deepseek-v4-flash`（日常 / 短测）。

### 启动本地 Agent

```bash
./scripts/start-agent.sh          # http://127.0.0.1:8765/health
```

构建插件：

```bash
cd obsidian-agent-plugin
npm install --cache .npm-cache
npm test && npm run typecheck && npm run build
```

### 摄入第一份资料

```bash
python 00-System/Scripts/ingest_pdf.py \
  --pdf "/path/to/paper.pdf" \
  --vault "/你的/Vault" \
  --kind paper \
  --domain-focus "因果推断" \
  --model deepseek-v4-pro \
  --dry-run          # 先预览 → 确认后 --apply
```

### 每日使用

打开 `00-System/Home.md`，完成 1–3 个到期复习 → 一个学习任务 → 一次 AI 短测。详见 [中文 README](README-先读.md)。

---

## 项目结构

```
00-System/         系统：Home · AI 规则 · Templates · Bases
01-Inbox/          临时输入
10-Sources/        资料出处 (Papers / Books / Courses / Web)
20-Knowledge/      正式知识 (MOCs / Courses / Topics / Concepts)
30-Learning/       学习记录 (Daily / Weekly / Plans)
40-Projects/       项目专属内容
80-Archive/        归档
90-Local-Only/     本地私有（不同步）

agent/             Python 安全运行时
├── core/          service · memory · workspace_policy · learning
├── tools/         受控工具注册
└── api/           localhost HTTP API
obsidian-agent-plugin/   Obsidian 插件 (TypeScript)
docs/              架构 · 验收 · 产品 · UI 规范
```

---

## 工程文档

| 文档 | 内容 |
|---|---|
| [中文 README](README-先读.md) | 安装配置与每日使用 |
| [Pi Agent Runtime 规范](docs/architecture/PI_AGENT_RUNTIME_SPEC.md) | Agent 内核设计 |
| [Workspace Policy + Memory V1](docs/architecture/WORKSPACE_MEMORY_V1.md) | Vault 语义与长期记忆 |
| [项目状态](PROJECT_STATUS.md) | 当前状态与恢复点 |
| [路线图](PROJECT_ROADMAP.md) | Phase 0–11 与延后项 |
| [决策记录](DECISIONS.md) | D-001 … D-060 |
| [安全模型](SECURITY.md) | 隐私与边界 |

---

## 质量保证

```bash
./scripts/check.sh   # 一键离线门禁
```

- **276** Python 测试：摄入、事务、Pi 运行时、记忆、策略、随机化回归
- **195** 插件测试：确定性事件重放、模糊 NDJSON 解析、响应式布局
- 严格 TypeScript typecheck + 生产构建 + 安装产物哈希校验
- 测试**不读取真实 PDF、不调用真实模型、不依赖真实密钥**

---

## 贡献

1. Fork 本仓库，从 `pi-runtime-hardening-repair` 或最新 release 分支新建功能分支
2. 修改后运行 `./scripts/check.sh` 确保全绿
3. 提交信息遵循仓库现有风格（`feat:` / `fix:` / `docs:` / `refactor:`）
4. 打开 Pull Request

安全敏感改动（密钥、授权、事务）请附架构测试；UI 改动请附真实 Obsidian 截图。

---

## 安全模型

- 原始 PDF 只在本地读取，模型 API 只接收带 `PAGE` 标记的提取文本
- 密钥只从环境变量 / macOS Keychain 读取，绝不写入仓库、日志或笔记
- `reviewed` / `core` 永远只读；自动化只能创建更新建议
- 所有写入先结构化校验与完整计划，临时文件 + 原子 rename
- `90-Local-Only` 不同步；提取文本、模型 JSON、更新建议留在本地

---

<div align="center">

**知序 —— 让 AI 真正参与你的学习，而不只是替你生成内容。**

<p>
  <a href="README-先读.md">中文文档</a> ·
  <a href="PROJECT_ROADMAP.md">路线图</a> ·
  <a href="DECISIONS.md">决策记录</a> ·
  <a href="SECURITY.md">安全模型</a>
</p>

</div>
