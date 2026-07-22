# 知序 Pi Runtime 自动续跑计划

## 使用目标

执行 Agent 必须自动按顺序完成剩余任务，不得在任务之间等待用户回复“继续”。

已完成：T01、T02。

剩余顺序：

T03 Stall Guard 副作用感知
→ T04 模型流三层超时
→ T05 Session Tree 完整投影
→ T06 Pending Permission 跨重启恢复
→ T07 Fork / Regenerate 正确分支
→ T08 结构化 Compaction
→ T09 隔离旧 Brain / Intake

T06、T07、T08 依赖 T05，禁止提前执行。

## 总控规则

仓库：
/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter

基线：
pi-agent-runtime

工作分支：
pi-runtime-hardening

开始时：

```bash
cd /Users/suyk/Obsidian/AI_Obsidian_MVP_Starter
git fetch origin
git checkout pi-runtime-hardening 2>/dev/null || git checkout -b pi-runtime-hardening pi-agent-runtime
git status --short
git log -3 --oneline
```

存在未提交修改时，先保存：

```bash
git diff > /tmp/zhixu-before-runtime-hardening.patch
git diff --cached > /tmp/zhixu-before-runtime-hardening-staged.patch
```

禁止 reset、clean、force push、修改 main、恢复 PydanticAI、增加第二套 Agent Loop、恢复关键词 Intent Router、绕过 Task Authorization、绕过 reviewed/core/protected、泄露 API Key。

## 状态文件

创建并维护：

docs/agent-runner/pi-runtime-hardening.state.json

初始内容：

```json
{
  "planVersion": 1,
  "branch": "pi-runtime-hardening",
  "completed": ["T01", "T02"],
  "currentTask": "T03",
  "status": "running",
  "blockedReason": "",
  "lastCommit": "",
  "updatedAt": ""
}
```

同时维护：

docs/agent-runner/pi-runtime-hardening.log.md

每个任务完成后记录修改文件、目标测试、完整门禁、提交 SHA、未完成项。

## 单任务状态机

对每个任务严格执行：

1. 重新读取本文件和 state；
2. 将 currentTask 设为当前任务；
3. 阅读指定源码和测试；
4. 说明当前行为和最小方案；
5. 实现；
6. 运行目标测试；
7. 失败时最多修复三轮；
8. 运行完整门禁；
9. 通过后独立提交；
10. 更新 state/log；
11. 立即开始下一任务，不询问用户是否继续。

仅在以下情况停止：

- 用户未提交修改产生真实冲突；
- 安全规则有无法自行决定的歧义；
- 必需环境不可用；
- 修复三轮后目标测试仍失败；
- 完整门禁出现无法在当前任务安全解决的架构冲突。

停止时必须把 status 设为 blocked，写明文件、命令、错误，不得伪造完成。

完整门禁：

```bash
python -m compileall agent
python -m unittest discover -s agent/tests -p "test_*.py"
cd obsidian-agent-plugin
npm test
npm run typecheck
npm run build
cd ..
./scripts/check.sh
```

---

## T03 Stall Guard 副作用感知

提交：
fix: make stall protection side-effect aware

必须阅读：

- obsidian-agent-plugin/src/core/runtime/pi/PiStallGuard.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiToolAdapter.ts
- obsidian-agent-plugin/src/core/runtime/pi/types.ts
- agent/tools/base.py
- agent/tools/registry.py
- obsidian-agent-plugin/tests/pi-runtime.test.mjs
- obsidian-agent-plugin/tests/pi-permission-e2e.test.mjs

要求：

- beforeTool 接收 toolCallId、name、args、mutatesState、idempotent、permissionLevel。
- 只读且幂等工具可复用 Observation。
- 有副作用且幂等工具不能由前端缓存，授权恢复使用同一 toolCallId，由后端幂等。
- 有副作用且非幂等工具的等价重复调用返回 duplicate_non_idempotent_tool_call Observation，不复用旧结果。
- 增加 consecutiveNoProgress、uniqueObservationCount、uniqueActionIds 等进展状态。
- 连续无进展 2 次返回 no_new_information，4 次返回 stall_replan_required，6 次安全终止。
- 保留 32 model requests、96 tool calls、30 min 硬限制。
- 新 Turn 重置预算。

目标测试：

- 只读重复不重复访问后端；
- 非幂等写不复用旧结果；
- 授权后同 callId 正常恢复；
- organize_vault_notes 正常；
- 新 Observation 清零 no-progress；
- 新 Turn 重置。

---

## T04 模型流三层超时

提交：
fix: add rolling inactivity watchdog to model streams

必须阅读：

- obsidian-agent-plugin/src/core/runtime/pi/PiModelTransport.ts
- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/src/core/runtime/pi/types.ts
- agent/core/pi_model_proxy.py
- agent/core/model_capabilities.py
- obsidian-agent-plugin/tests/pi-runtime.test.mjs
- agent/tests/test_pi_model_proxy.py

要求：

- first event 默认 45 秒；
- idle 普通 90 秒，深度推理 180 秒；
- hard 普通 15 分钟，深度推理 30 分钟；
- start、text、thinking、tool call、usage 都重置 idle；
- 错误码区分 model_first_event_timeout、model_idle_timeout、model_request_deadline_exceeded、model_request_aborted；
- timeout 必须 abort、保留 partial、只发一次 terminal；
- 构造器允许测试注入毫秒级 timeout。

目标测试：

无首包、首包后停滞、持续 delta、hard deadline、done 后无二次错误、用户 Abort、thinking 停滞、tool delta 重置。

---

## T05 Session Tree 完整投影

提交：
feat: restore pi context from session tree projection

必须阅读：

- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiEventAdapter.ts
- obsidian-agent-plugin/src/core/runtime/pi/types.ts
- obsidian-agent-plugin/src/core/runtime/types.ts
- agent/core/storage.py
- agent/core/service.py
- agent/api/server.py
- agent/tests/test_pi_session_tree.py
- obsidian-agent-plugin/tests/pi-runtime.test.mjs
- obsidian-agent-plugin/tests/pi-lifecycle.test.mjs

新增：

obsidian-agent-plugin/src/core/runtime/pi/PiSessionProjector.ts

后端新增：

project_pi_session_context(session_id, leaf_id=None, upto_run_id=None, upto_sequence=None)

要求：

- 从 current leaf 沿 parent_id 回溯，只恢复当前 lineage；
- 不按 timestamp 读取所有分支；
- 恢复 User、Assistant 完整文本、Tool Call/Result、blocked/failed、Action 引用、Steering、Follow-up、Compaction；
- Tool Call/Result 配对，不产生孤立 Result；
- delta 聚合成完整 Assistant 文本；
- 不保存完整 Vault 正文、API Key、provider reasoning；
- 新增 GET /agent/sessions/{sessionId}/projection；
- 删除 restoredMessages 简化恢复。

目标测试：

重启后 Tool Pair 顺序一致、blocked/failed 可见、Action 不重复、兄弟分支隔离、Steering/Follow-up 恢复、未完成 Tool Call 变 interrupted、reasoning/Key 不进入。

---

## T06 Pending Permission 跨重启恢复

前置：T05 完成。

提交：
feat: recover pending pi permissions after plugin restart

必须阅读：

- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiToolAdapter.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiSessionProjector.ts
- obsidian-agent-plugin/src/assistant-inline-confirmation.ts
- obsidian-agent-plugin/src/views.ts
- agent/core/storage.py
- agent/core/task_authorization.py
- agent/core/service.py
- agent/api/server.py
- obsidian-agent-plugin/tests/pi-permission-e2e.test.mjs
- obsidian-agent-plugin/tests/pi-lifecycle.test.mjs
- agent/tests/test_pi_session_tree.py

新增表 pi_pending_tool_calls：

run_id、session_id、turn_id、tool_call_id、tool_name、arguments_json、permission_request_json、task_authorization_id、state、created_at、updated_at、resolved_at。

状态：pending/allowed/denied/cancelled/interrupted/completed。

要求：

- 先持久化 pending，再保存 confirmation event，再展示 UI；
- 重启后重建同一权限卡；
- 不重新调用模型；
- 使用原 runId、turnId、toolCallId、authorizationId；
- 授权后执行原 Tool Call，将 Result 注入 Pi，并使用 Pi continuation API；
- 禁止伪造用户消息“请继续”；
- 重复点击幂等；
- Authorization 过期、Run cancelled、stale hash、protected 变化、workspace 丢失、Contract 丢失时不得恢复执行。

目标测试：

权限出现→销毁 Runtime→新 Runtime→权限卡恢复→同 call 继续→最终回答；另测拒绝、重复点击、过期、stale、连续两次重启、完成后不再显示。

---

## T07 Fork / Regenerate 正确分支

前置：T05 完成。

提交：
fix: project fork context from the selected session node

必须阅读：

- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiSessionProjector.ts
- obsidian-agent-plugin/src/core/runtime/types.ts
- obsidian-agent-plugin/src/views.ts
- agent/core/storage.py
- agent/core/service.py
- agent/api/server.py
- obsidian-agent-plugin/tests/pi-lifecycle.test.mjs
- obsidian-agent-plugin/tests/pi-runtime.test.mjs
- agent/tests/test_pi_session_tree.py

要求：

- 删除完整复制 source.agent.state.messages；
- 从指定 Session Entry 沿 parentId 投影；
- sequence 之后的未来消息不得进入；
- 分支点不能拆 Tool Pair；
- Regenerate 删除旧回答及之后内容；
- 已真实 Action 不自动撤销，但标记已存在；
- 新 Authorization 不继承 allow_all、workspace grants、network allow、旧 write scope；
- 新分支首 Entry parentId 指向 fork point。

目标测试：

从 Turn 2 Fork 看不到未来；Tool Pair 中间不孤立；Result 后保留完整 Pair；Regenerate 不见旧回答；原分支不变；allow_all 不继承。

---

## T08 结构化 Compaction

前置：T05 完成。

提交：
feat: add structured session-aware pi compaction

必须阅读：

- obsidian-agent-plugin/src/core/runtime/pi/PiCompaction.ts
- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiSessionProjector.ts
- obsidian-agent-plugin/src/core/runtime/pi/PiEventAdapter.ts
- agent/core/storage.py
- agent/core/service.py
- obsidian-agent-plugin/tests/pi-lifecycle.test.mjs
- agent/tests/test_pi_session_tree.py

新增 PiCompactionState：

goal、explicitConstraints、conversationFocus、activeNote、selection、attachments、sourcesRead、completedActions、pendingActions、failedTools、activeWorkspace、taskBranch、taskAuthorization、currentLeafId、branchId、actionIds、undoState。

要求：

- 状态来自 Session Tree、Focus、Authorization、Tool Result、Action Journal、Workspace、Attachments；
- 删除 JSON.stringify(...).includes(...) pending 判断；
- Pending Permission/Question、未解决 Tool Call、活动事务时不拆；
- 不把摘要伪装为 user message；
- 使用 Custom Message 或明确的 <zhixu_runtime_checkpoint> assistant/runtime context；
- 保存 compaction entry：structured state、tokensBefore/After、cutEntryId、keptFromEntryId、summaryVersion；
- 构建失败保留原 Session，不中止 Run。

目标测试：

不拆 Tool Pair；pending 时不压缩；恢复当前笔记/附件/Action/Workspace；兄弟分支隔离；摘要不是 user；失败不破坏；重启复用 Entry。

---

## T09 隔离旧 Brain / Intake

提交：
refactor: isolate legacy workflows from the pi assistant

必须阅读：

- agent/core/service.py
- agent/core/context_material.py
- agent/core/structured_workflow.py
- agent/core/assistant_outcomes.py
- agent/core/intake.py
- agent/brain/
- agent/api/server.py
- obsidian-agent-plugin/src/views.ts
- obsidian-agent-plugin/src/api.ts
- obsidian-agent-plugin/src/core/runtime/PiAgentRuntime.ts
- obsidian-agent-plugin/main.ts
- agent/tests/test_intake.py
- agent/tests/test_context_material.py
- agent/tests/test_structured_workflow.py
- agent/tests/test_runtime_architecture_boundaries.py
- obsidian-agent-plugin/tests/architecture-boundaries.test.mjs

新增：

agent/core/explicit_workflow_service.py

要求：

- 普通 Assistant 只走 Pi model/tool/events/session/action/undo；
- 不调用 submit_intake、BrainModelGateway、IntentResult、StructuredWorkflowRunner、precise_intent、assistantIntent；
- /intake/attachments 只负责附件存储，上传后由 Pi 处理；
- Prepared PDF、apply-prepared、显式导入、固定学习流程可使用 ExplicitWorkflowService；
- 普通对话清除“审核页”“等待确认后写入”“保存提案”等旧文案；
- reviewed/core update suggestion 可保留，但不是审批中心；
- 架构测试禁止双 Agent 语义链。

目标测试：

普通问答只 Pi；普通整理只 Pi+事务；附件由 Pi；Prepared PDF 不回归；普通 Assistant 生产代码不出现旧 intent/workflow 调用。

---

## 全部完成

state.completed 必须为 T01–T09，status=completed。

运行完整门禁和真实 UI 回归：

- 普通问答
- 当前笔记读取
- 安全直接写入
- Undo
- 权限卡
- 权限等待中重载
- Fork
- Regenerate
- 长会话 Compaction
- Developer Workspace

更新：

- docs/architecture/PI_AGENT_RUNTIME_SPEC.md
- docs/architecture/PI_AGENT_RUNTIME_ACCEPTANCE.md
- PROJECT_STATUS.md

push pi-runtime-hardening，但不得自动合并 main，除非用户明确授权。
