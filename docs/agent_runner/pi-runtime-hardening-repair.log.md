# Pi Runtime Hardening Repair Log

基线：`origin/pi-runtime-hardening` (`8b7e229`)

分支：`pi-runtime-hardening-repair`

旧 `pi-runtime-hardening.state.json` 的 completed 状态不作为本轮验收依据。本日志只记录 R01–R08 在最新代码上的真实实现、目标测试、完整门禁和提交。

## R01 — Stall Guard 副作用和进展状态

状态：已完成

- 真实行为：缓存条件只检查 `mutatesState`；`remember` 重复计数一次加 2；缺少完整 RunProgress；第 6 次无进展直接抛出不可理解异常。
- 实现：缓存限定为 `read_only + idempotent + non-mutating`；重复计数单次递增；结构化追踪 Observation、Action、分页、路径、测试结果、失败和最终文本；2/4/6 阈值返回可供模型处理的状态/安全终止观察。
- 目标测试：`node --test tests/pi-stall-guard.test.mjs tests/pi-runtime.test.mjs`，20/20 通过。
- 完整门禁：Python compileall 与 214/214 tests、plugin 143/143 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`54d1e62 fix: complete side-effect-aware pi stall protection`

## R02 — Provider Abort 无关的模型流终止

状态：已完成

- 真实行为：first/idle/hard timer 只 abort 内部 signal，终态依赖 Provider Promise 随后 resolve/reject；忽略 AbortSignal 的 Provider 会令流永久悬挂。
- 实现：统一 `terminate` 先锁定唯一终态、清理 timer、保留 partial assistant、立即 push error，再 best-effort abort；Provider 回调和 Promise 的迟到结果均受 terminal guard 约束。
- 目标测试：`node --test tests/pi-model-timeout.test.mjs tests/pi-runtime.test.mjs`，16/16 通过；覆盖永不 resolve Provider、partial abort、迟到事件和唯一终态。
- 完整门禁：Python compileall 与 214/214 tests、plugin 144/144 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`29e2cd1 fix: terminate stalled model streams independently of provider abort`

## R03 — Session Tree 到 Pi AgentMessage 投影

状态：已完成

- 实现：后端按 `parent_id` 投影唯一持久化 lineage，恢复标准 user/assistant/tool pair、blocked/failed/interrupted、控制消息、Action 引用与 checkpoint；delta 聚合，孤立结果丢弃，孤立调用转为明确 interrupted Result。
- 隐私：provider reasoning 不进入恢复上下文；敏感键与 secret-shaped 文本统一脱敏；附件、Action 与大型值只返回有界元数据/引用。
- Runtime/API：新增 projection API 与前端 transport；`projectPiSessionMessages()` 生成 Pi Agent Core 标准消息，删除 `restoredMessages()` 旧路径。
- 目标测试：后端 `test_pi_session_tree` 11/11，前端投影/生命周期/Runtime 27/27 通过。
- 完整门禁：Python compileall 与 220/220 tests、plugin 149/149 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`2d0e41d feat: restore complete pi context from persisted session lineage`

## R04 — Pending Permission 重启恢复

状态：已完成

- 恢复顺序：先按 session 查 pending 并由后端验证，成功后直接换回原 run/turn/Authorization；只有无可恢复记录时才注册新 Run。
- 安全验证：持久化无正文的 Vault/Workspace guard；检查 active Authorization、Tool Contract、工作区、路径、protected/base hash/碰撞、可逆性和已持久化 Tool Result。
- 继续执行：优先复用 R03 标准 Tool Call；Tool Result 先持久化，再注入 Pi 消息、标记 pending completed 并调用 `agent.continue()`；拒绝注入 blocked Result。
- 目标测试：后端 pending/session/security 38/38，前端 permission recovery/e2e/projector 21/21；覆盖连续两次重启、重复确认、失效/stale/missing 边界和完成后不再出卡。
- 完整门禁：Python compileall 与 227/227 tests、plugin 152/152 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`15a127b fix: resume pending pi tool calls from the original persisted run`

## R05 — 基于 Session Entry 的 Fork / Regenerate

状态：已完成

- 持久化边界：后端按 `runId + event sequence` 解析真实 Entry，沿 `parent_id` 投影；分支 Run 独立维护 `current_leaf_id`，首 Entry 精确挂到 `resolvedForkEntryId`。
- Tool/Regenerate：Tool Pair 中间边界自动前移；重新生成保留回答前完整 Pair 与 `completedActionIds`，删除旧回答、后续 pending 和未来历史，不重放已提交 Action。
- 上下文与授权：保留当前笔记/附件/Focus/Action 引用；新 Authorization 清空 allow_all、network、workspace、write/create/operation grants。插件重启后也可从持久化 source Run 分叉。
- UI：重新生成按钮调用持久化 `runtime.fork(..., "regenerate")`；本地分支句柄与原持久化 conversation 分离，确保用户消息和新回答仍写入同一会话。
- 目标测试：后端 session tree 15/15；前端 fork/lifecycle/projector/assistant 28/28 通过。
- 完整门禁：Python compileall 与 231/231 tests、plugin 147/147 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`54a27b7 fix: fork pi sessions from persisted entry boundaries`

## R06 — Structured Compaction

状态：已完成

- 完整状态：从 Session Projection、Focus、附件元数据、Action Journal、pending Tool Result和 Workspace 授权构建有界的 structured checkpoint；`currentLeafId` 使用真实 Entry ID，兄弟分支不进入投影。
- 事务顺序：先构建并原子持久化 Compaction Entry/checkpoint，确认后才替换内存消息并发送 `context_compacted`；持久化失败保留原消息并返回诊断。
- 恢复与安全：重启后复用同一 checkpoint 与 `keptFromEntryId` 后记录；待授权/待回答时不压缩；秘钥、完整输出和 reasoning 不进入 checkpoint。
- 目标测试：后端 compaction/session tree 24/24；前端 compaction/projector/lifecycle 19/19 通过。
- 完整门禁：Python compileall 与 234/234 tests、plugin 152/152 tests、typecheck、build、`./scripts/check.sh` 全部通过。
- 提交：`b047300 fix: make pi compaction state-complete and persistence-first`

## R07 — 普通学习助手全量迁移至 Pi

状态：已完成

- 统一回路：新增 DOM 无关的 `runPiAssistantTurn()`，主 Assistant、学习助手和学习笔记生成共同复用 `prepareTurn → query → reducer`；课程、小节、Lesson Version、相关笔记与来源作为有界结构化上下文传入。
- 学习助手：默认关闭网络并保持只读；任何意外写授权都会取消当前 Run，最终文本只来自 Pi 流聚合。
- 学习笔记：用户明确点击后由 Pi 执行 `plan_vault_change → apply_vault_change`；只接受 `20-Knowledge/Drafts` 或 `01-Inbox` 下 `state=applied` 的真实 Action Result，并在 UI 提供查看与撤销。
- 产品边界：删除普通 UI 中不可达的审核中心、Artifact 卡片和旧 Task Thread；普通前端生产目录全量扫描禁止 legacy intake/intent 状态；变更检查器只显示 Pi 的持久化/实时 Action Result。
- Legacy 收缩：旧 intake 的 Artifact、摘要、状态与会话智能 helper 全部迁入 `ExplicitWorkflowService`；兼容 API 明确标记 deprecated explicit/legacy；Prepared PDF、apply-prepared 和附件入口保留。
- 目标测试：R07 架构、学习助手、Action Result、Runtime 与显式工作流目标测试 36/36；后端边界/intake/context/structured workflow 43/43 通过。
- 完整门禁：Python compileall 与 235/235 tests、plugin 159/159 tests、typecheck、build、`./scripts/check.sh` 全部通过。

## R08 — 分支清理、状态纠正和最终验收

状态：进行中
