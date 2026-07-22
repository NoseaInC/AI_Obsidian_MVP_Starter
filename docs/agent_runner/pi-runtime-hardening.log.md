# Pi Runtime Hardening 自动续跑日志

计划：docs/agent_runner/PI_RUNTIME_HARDENING_AUTORUN.md
分支：pi-runtime-hardening
已完成：T01、T02

## 任务记录格式

每个任务：修改文件、目标测试、完整门禁、提交 SHA、未完成项。

## T03 Stall Guard 副作用感知

- 修改文件：
  - obsidian-agent-plugin/src/core/runtime/pi/PiStallGuard.ts
  - obsidian-agent-plugin/src/core/runtime/pi/PiToolAdapter.ts
  - obsidian-agent-plugin/tests/pi-stall-guard.test.mjs（新增）
- 实现：beforeTool 接收 toolCallId/mutatesState/idempotent/permissionLevel；只读可复用 Observation；有副作用幂等工具始终重执行（后端幂等，含授权恢复）；有副作用非幂等等价重复调用返回 duplicate_non_idempotent_tool_call 且不复用旧结果。新增 consecutiveNoProgress / uniqueObservationCount / uniqueActionIds 进度状态：连续无进展 2 次 no_new_information、4 次 stall_replan_required、6 次安全终止。保留 32 请求 / 96 工具 / 30 分钟硬上限，新 Turn reset() 重置。
- 目标测试：pi-stall-guard.test.mjs（9 用例）：只读复用、非幂等重复拦截、幂等写重执行、新观察清零、新 Turn 重置、6 次终止、适配器级只读不重调后端、非幂等重复被拦截、幂等写重执行。
- 完整门禁：python compileall + unittest 195 passed；npm typecheck/build/test 96 passed（含 9 新）；./scripts/check.sh All offline checks passed。
- 提交：fix: make Stall Guard side-effect and idempotency aware
- 未完成项：无。
