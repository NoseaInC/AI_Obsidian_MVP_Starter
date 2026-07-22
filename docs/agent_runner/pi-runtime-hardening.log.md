# Pi Runtime Hardening 自动续跑日志

计划：docs/agent_runner/PI_RUNTIME_HARDENING_AUTORUN.md
分支：pi-runtime-hardening
已完成：T01、T02、T03、T04

## 任务记录格式

每个任务：修改文件、目标测试、完整门禁、提交 SHA、未完成项。

## T03 Stall Guard 副作用感知

- 修改文件：PiStallGuard.ts、PiToolAdapter.ts、tests/pi-stall-guard.test.mjs（新增）
- 提交：fix: make Stall Guard side-effect and idempotency aware
- 门禁：python 195 passed；npm 96 passed（含 9 新）；check.sh passed。

## T04 模型流三层超时

- 修改文件：
  - obsidian-agent-plugin/src/core/runtime/pi/PiModelTransport.ts
  - obsidian-agent-plugin/tests/pi-model-timeout.test.mjs（新增）
- 实现：用 first/idle/hard 三层定时器替换单一 45s 活动定时器。firstEvent 默认 45s；idle 普通 90s、深度推理 180s，且 start/text/thinking/tool call/usage 均重置 idle；hard 普通 15min、深度推理 30min。错误码区分 model_first_event_timeout / model_idle_timeout / model_request_deadline_exceeded / model_request_aborted。超时即 abort、保留 partial、只发一次 terminal。构造器支持注入毫秒级超时（PiModelTransport(transport, timeouts)），深度推理由 options.reasoning 决定。
- 目标测试：tests/pi-model-timeout.test.mjs（10 用例）：无首包、首包后停滞、持续 delta、hard deadline、done 后无二次错误、用户 Abort、thinking 停滞、tool delta 重置、深度推理更大 idle 预算、普通模式同等 200ms 间隙超时。
- 完整门禁：python compileall + unittest 195 passed；npm typecheck/build/test 106 passed（含 10 新）；./scripts/check.sh All offline checks passed。
- 提交：fix: add rolling inactivity watchdog to model streams
- 未完成项：无。
