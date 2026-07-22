# Pi Runtime Hardening 自动续跑日志

计划：docs/agent_runner/PI_RUNTIME_HARDENING_AUTORUN.md
分支：pi-runtime-hardening
已完成：T01、T02、T03、T04、T05、T06

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

## T06 Pending Permission 跨重启恢复
- 提交：feat: recover pending pi permissions after plugin restart
- 后端：storage `pi_pending_tool_calls` 表（run_id/session_id/turn_id/tool_call_id/tool_name/arguments_json/permission_request_json/task_authorization_id/state/时间戳），service `save/get/resolve_pending_tool_call`，server 路由 `POST .../pending-tool-calls`、`POST .../pending-tool-calls/resolve`、`GET .../pending-tool-calls`、`GET /agent/sessions/{id}/pending-tool-calls`；SCHEMA_VERSION 9→10。
- 前端：types 新增 `PiPendingToolCall*`；api.ts 三个 bridge；PiToolAdapter 在 request 中补 `arguments`；PiAgentRuntime `requestPermission` 先持久化再展示卡片、`confirm` 解析后端并幂等；新增 `recoverPendingPermission`：重建 assistant tool_call 消息、采用原 runId/turnId/authorizationId、过期守卫、按 continuation 续跑（不重新调用模型）。
- 目标测试：pi-permission-recovery.test.mjs（4 用例：allow 跨重启恢复并重执行工具+续跑、deny 不重执行、双确认幂等、过期不恢复）；test_pi_pending_tool_calls.py（7 用例）。test_brain_security 因 SCHEMA_VERSION 提升同步改为 10。
- 完整门禁：python 203 passed；npm typecheck/build/test 116 passed；./scripts/check.sh All offline checks passed。
- 未完成项：无。

## T07 Fork/Regenerate 正确分支
- 提交：fix: project fork context from the selected session node
- 数据模型修正：仓库后端 `pi_session` 不提供按 parentId 链接的工具对树（与计划 T05 假设不符），故 T07 在内存 transcript 层以 1-based 消息索引作 `sequence` 实现投影，不依赖后端树；运行时等价「首 Entry parentId 指向 fork point」用 `parentRunId` + `forkedFromSequence` 标注。
- 新增 `obsidian-agent-plugin/src/core/runtime/pi/PiSessionProjector.ts`：`projectForkMessages(messages, sequence?, mode="fork"|"regenerate")`。forkIndex=sequence??total；regenerate 保留 `forkIndex-1`，否则 `forkIndex`；若末条为 assistant toolCall 且下条是 toolResult 则丢弃末尾孤立调用（不拆 pair）；返回 kept 切片（新数组，源消息对象不被改写）。
- 修改 `obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts`：`fork(runId, sequence?, mode)` 用 `projectForkMessages` 投影 `source.agent.state.messages` 替代全文复制；新 Authorization 显式重置 `resourceScope.allowAllRunCapabilities:false`、全新 unbound write scope、空 `operationScope`，并新增顶层 `networkPolicy:"deny"`，确保不继承源的 allow_all / workspace / network / 旧 write scope。
- 目标测试：pi-session-projector.test.mjs（7 纯函数用例：无 sequence 全保留、future 排除、toolCall 末尾不孤立、Result 后完整 pair、regenerate 删旧回答、regenerate 在 toolCall、普通 answer 保留）；pi-fork.test.mjs（2 集成用例：fork 投影所选分支且排除未来 + 源分支不变；fork 不继承 allow_all/network 授权且源授权未被改写）。
- 完整门禁：python compileall + unittest 203 passed（无变更）；npm typecheck/build/test 125 passed（含 9 新）；./scripts/check.sh All offline checks passed。
- 未完成项：无。
