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

状态：进行中
