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

状态：进行中
