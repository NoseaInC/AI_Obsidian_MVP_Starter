# Pi Runtime Hardening 自动续跑日志

计划：docs/agent_runner/PI_RUNTIME_HARDENING_AUTORUN.md
分支：pi-runtime-hardening
已完成：T01、T02、T03、T04、T05

## T03 Stall Guard 副作用感知
- 提交：fix: make Stall Guard side-effect and idempotency aware

## T04 模型流三层超时
- 提交：fix: add rolling inactivity watchdog to model streams

## T05 Session Tree 完整投影

- 修改文件：
  - obsidian-agent-plugin/src/core/runtime/pi/PiSessionTree.ts（新增）
  - obsidian-agent-plugin/tests/pi-session-tree.test.mjs（新增）
  - agent/tests/test_pi_session_tree.py（新增 1 个后端用例）
- 现状：后端 `pi_session()` 已返回含 `entries` 的完整树（所有事件含 partial/aborted/error 回合的 text/thinking 等），`test_pi_session_tree.py` 3 用例已通过。缺失的是前端的 `PiSessionTree.ts` 投影器。
- 实现：新增 `projectSessionTree(events)` 纯函数投影器——只投影真实事件，不合成 reasoning/工具；输出可序列化树：每 Turn（含 partial/aborted/error Assistant）、工具调用（blocked/running/failed）、工具结果（error/partial）、reasoning 块、usage 累计、stopReason；partial 回合（无 done/error）标记为 "partial"；可 JSON 序列化且无 undefined 泄漏。
- 目标测试：pi-session-tree.test.mjs（6 用例）：partial Turn 显示 assistant 文本、blocked 工具调用可见、aborted Turn 保留内容、reasoning 投影、usage 累计、可序列化无 undefined。后端新增 test_partial_run_includes_assistant_content_in_tree 锁定 partial 内容进入 tree。
- 完整门禁：python compileall + unittest 196 passed（含 1 新）；npm typecheck/build/test 112 passed（含 6 新）；./scripts/check.sh All offline checks passed。
- 提交：fix: project full session tree including partial assistant turns
- 未完成项：无。
