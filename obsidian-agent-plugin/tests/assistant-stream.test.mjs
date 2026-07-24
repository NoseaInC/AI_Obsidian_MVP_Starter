import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
import {readFile} from "node:fs/promises";

async function moduleUnderTest() {
  const result = await build({
    entryPoints: [new URL("../src/assistant-stream.ts", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node", target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

function event(seq, type, extra = {}) {
  return {schemaVersion: 3, seq, type, runId: "run-1", conversationId: "conv-1", ...extra};
}

test("five thousand real token chunks preserve exact order and Unicode", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started", {model: "deepseek-chat"}));
  const chunks = Array.from({length: 5_000}, (_, index) => index % 7 === 0 ? "影响函数" : `${index},`);
  for (let index = 0; index < chunks.length; index += 1) {
    state = mod.reduceAssistantStream(state, event(index + 2, "message.delta", {delta: chunks[index]}));
  }
  state = mod.reduceAssistantStream(state, event(5_002, "run.completed"));
  assert.equal(state.content, chunks.join(""));
  assert.equal(state.status, "completed");
  assert.equal(state.lastSequence, 5_002);
});

test("twenty thousand run events are deterministic, deduplicated and bounded", async () => {
  const mod = await moduleUnderTest();
  let first = mod.initialAssistantLiveRun();
  first = mod.reduceAssistantStream(first, event(1, "run.started"));
  for (let seq = 2; seq <= 20_001; seq += 1) {
    first = mod.reduceAssistantStream(first, event(seq, "step.updated", {
      step: {id: `step-${seq % 8}`, label: `验证步骤 ${seq % 8}`, status: seq % 3 === 0 ? "completed" : "running"},
    }));
  }
  const duplicate = mod.reduceAssistantStream(first, event(20_001, "message.delta", {delta: "不得重复"}));
  const stale = mod.reduceAssistantStream(first, event(9, "run.failed", {code: "stale"}));
  assert.deepEqual(duplicate, first);
  assert.deepEqual(stale, first);
  assert.equal(first.steps.length, 8);
  assert.equal(first.lastSequence, 20_001);
});

test("NDJSON parser survives arbitrary transport fragmentation", async () => {
  const mod = await moduleUnderTest();
  const source = [event(1, "run.started"), event(2, "message.delta", {delta: "Δ 方法"}), event(3, "run.completed")]
    .map(item => JSON.stringify(item) + "\n").join("");
  let remainder = "";
  const events = [];
  for (let index = 0; index < source.length; index += (index % 11) + 1) {
    const parsed = mod.parseNdjsonBuffer(remainder + source.slice(index, index + (index % 11) + 1));
    events.push(...parsed.events); remainder = parsed.remainder;
  }
  assert.equal(remainder, "");
  assert.deepEqual(events.map(item => item.type), ["run.started", "message.delta", "run.completed"]);
});

test("cancel keeps partial output and changes any active run", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "message.delta", {delta: "已返回部分"}));
  state = mod.cancelledAssistantRun(state);
  assert.equal(state.status, "cancelled");
  assert.equal(state.content, "已返回部分");
  assert.equal(mod.cancelledAssistantRun({...state, status: "waiting_confirmation"}).status, "cancelled");
  assert.equal(mod.cancelledAssistantRun({...state, status: "completed"}).status, "completed");
});

test("terminal run events settle unfinished public execution steps", async () => {
  const mod = await moduleUnderTest();
  let completed = mod.initialAssistantLiveRun();
  completed = mod.reduceAssistantStream(completed, event(1, "run.started"));
  completed = mod.reduceAssistantStream(completed, event(2, "step.updated", {
    step: {id: "context", label: "理解当前请求与上下文", status: "running"},
  }));
  completed = mod.reduceAssistantStream(completed, event(3, "run.completed"));
  assert.equal(completed.steps[0].status, "completed");

  let failed = mod.initialAssistantLiveRun();
  failed = mod.reduceAssistantStream(failed, event(1, "run.started"));
  failed = mod.reduceAssistantStream(failed, event(2, "step.updated", {
    step: {id: "tool", label: "读取知识库", status: "running"},
  }));
  failed = mod.reduceAssistantStream(failed, event(3, "run.failed", {code: "tool_failed"}));
  assert.equal(failed.steps[0].status, "failed");
});

test("real ordered tool events become visible execution steps", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started", {model: "deepseek-chat"}));
  state = mod.reduceAssistantStream(state, event(2, "tool.started", {callId: "call-1", tool: "search_vault", input: {query: "Delta"}}));
  state = mod.reduceAssistantStream(state, event(3, "tool.completed", {callId: "call-1", tool: "search_vault", status: "completed", summary: "返回 2 项", result: {count: 2}}));
  state = mod.reduceAssistantStream(state, event(4, "run.completed"));
  assert.deepEqual(state.toolCalls, [{id: "call-1", tool: "search_vault", status: "completed", summary: "返回 2 项", purpose: undefined, input: undefined, result: {count: 2}}]);
  assert.equal(state.steps.find(item => item.id === "tool:call-1").label, "搜索知识库");
  assert.equal(state.steps.find(item => item.id === "tool:call-1").status, "completed");
});

test("provider reasoning preserves authentic deltas separately from the final answer", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started", {model: "deepseek-reasoner"}));
  state = mod.reduceAssistantStream(state, event(2, "reasoning.started", {blockId: "provider-reasoning-0", provider: "deepseek-reasoner"}));
  state = mod.reduceAssistantStream(state, event(3, "reasoning.delta", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner", delta: "先核对上下文，",
  }));
  state = mod.reduceAssistantStream(state, event(4, "reasoning.delta", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner", delta: "再组织答案。", tokenCount: 37,
  }));
  state = mod.reduceAssistantStream(state, event(5, "reasoning.completed", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner", tokenCount: 37,
  }));
  state = mod.reduceAssistantStream(state, event(6, "message.delta", {delta: "这是最终回答。"}));
  state = mod.reduceAssistantStream(state, event(7, "run.completed"));
  assert.deepEqual(state.reasoningBlocks, [{
    id: "provider-reasoning-0",
    provider: "deepseek-reasoner",
    content: "先核对上下文，再组织答案。",
    tokenCount: 37,
    status: "completed",
  }]);
  assert.equal(state.content, "这是最终回答。");
  assert.doesNotMatch(state.content, /先核对上下文/);
});

test("completed trace metadata round-trips local reasoning without duplicating tool payloads", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started", {model: "deepseek-reasoner"}));
  state = mod.reduceAssistantStream(state, event(2, "reasoning.started", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner",
  }));
  state = mod.reduceAssistantStream(state, event(3, "reasoning.delta", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner", delta: "只保存在本地轨迹",
  }));
  state = mod.reduceAssistantStream(state, event(4, "reasoning.completed", {
    blockId: "provider-reasoning-0", provider: "deepseek-reasoner", tokenCount: 19,
  }));
  state = mod.reduceAssistantStream(state, event(5, "tool.completed", {
    callId: "call-1", tool: "read_note_excerpt", status: "completed", summary: "读取完成",
    result: {content: "不能复制到消息元数据的长笔记正文"},
  }));
  state = mod.reduceAssistantStream(state, event(6, "run.completed"));

  const metadata = mod.buildAssistantMessageMetadata(state, {actionId: "action-1"});
  const trace = mod.persistedAssistantTrace({metadata});
  assert.equal(trace.runId, "run-1");
  assert.equal(trace.status, "completed");
  assert.equal(trace.reasoningBlocks[0].tokenCount, 19);
  assert.equal(trace.reasoningBlocks[0].content, "只保存在本地轨迹");
  assert.match(JSON.stringify(metadata), /只保存在本地轨迹/);
  assert.deepEqual(trace.vaultAction, {actionId: "action-1"});
  assert.equal(trace.toolCalls[0].tool, "read_note_excerpt");
  assert.equal("result" in trace.toolCalls[0], false);
  assert.equal("input" in trace.toolCalls[0], false);
  assert.equal(mod.persistedAssistantTrace({metadata: {piTrace: {schemaVersion: 2}}}), null);
});

test("waiting confirmation remains an active cancellable run", async () => {
  const mod = await moduleUnderTest();
  assert.equal(mod.isAssistantLiveRunActive("running"), true);
  assert.equal(mod.isAssistantLiveRunActive("waiting_confirmation"), true);
  assert.equal(mod.isAssistantLiveRunActive("completed"), false);
});

test("web search sources remain ordered, public and visible to the inspector", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started", {model: "deepseek-chat"}));
  state = mod.reduceAssistantStream(state, event(2, "tool.started", {callId: "web-1", tool: "search_public_web", input: {query: "Delta Method"}}));
  state = mod.reduceAssistantStream(state, event(3, "tool.completed", {
    callId: "web-1", tool: "search_public_web", status: "completed", summary: "搜索公开网页 · 1 条 · bing-rss",
    result: {provider: "bing-rss", results: [{title: "Delta Method", url: "https://example.com/delta", domain: "example.com", qualityScore: .62}]},
  }));
  assert.equal(state.toolCalls[0].result.results[0].domain, "example.com");
  assert.equal(state.steps[0].label, "搜索公开网页");
  assert.equal(mod.toolLabel("search_academic_sources"), "检索可信来源");
  assert.equal(mod.toolLabel("get_current_datetime"), "读取当前时间");
});

test("inline confirmation is not mistaken for an applied write", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "inline.confirmation.required", {confirmation: {run_id: "run-1", proposal_id: "cs-1", title: "修改", summary: "1 个文件", risk_level: "medium", writes: [], actions: ["confirm", "reject"]}}));
  state = mod.reduceAssistantStream(state, event(3, "run.waiting_confirmation", {proposalId: "cs-1"}));
  assert.equal(state.proposalRequired, true);
  assert.equal(state.status, "waiting_confirmation");
});

test("permission confirmation pauses immediately and resolves back into the same running stream", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "inline.confirmation.required", {
    confirmation: {
      run_id: "run-permission",
      kind: "permission",
      proposal_id: "permission-call-1",
      title: "允许整理目录？",
      summary: "需要移动一篇笔记",
      risk_level: "low",
      tool_name: "organize_vault_notes",
      writes: [{action: "move", source_path: "01-Inbox/a.md", target_path: "20-Knowledge/Drafts/a.md"}],
      actions: ["confirm", "confirm_all", "reject"],
    },
  }));
  assert.equal(state.status, "waiting_confirmation");
  assert.equal(state.confirmation.tool_name, "organize_vault_notes");
  state = mod.reduceAssistantStream(state, event(3, "inline.confirmation.resolved", {reason: "once"}));
  assert.equal(state.status, "running");
  assert.equal(state.confirmation, undefined);
  assert.equal(state.proposalRequired, false);
});

test("completed message metadata survives the stream without a full view refresh", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "message.delta", {delta: "- **一致性**：定义"}));
  state = mod.reduceAssistantStream(state, event(3, "reasoning.delta", {
    blockId: "provider-reasoning-0", delta: "本地思考",
  }));
  state = mod.reduceAssistantStream(state, event(4, "message.completed", {
    message: {
      id: "msg-final",
      role: "assistant",
      content: "- **一致性**：定义",
      createdAt: "2026-07-16T20:00:00+08:00",
      reasoningBlocks: [{id: "provider-reasoning-0", tokenCount: 8}],
    },
  }));
  state = mod.reduceAssistantStream(state, event(5, "run.completed"));
  assert.equal(state.completedMessage.id, "msg-final");
  assert.equal(state.completedMessage.content, state.content);
  assert.equal(state.reasoningBlocks[0].content, "本地思考");
  assert.equal(state.reasoningBlocks[0].tokenCount, 8);
  assert.equal(state.status, "completed");
});

test("confirmation-only turn keeps the proposal inline", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "write.diff", {proposalId: "assistant-cs-1", title: "补充笔记", writes: [{path: "20-Knowledge/x.md"}]}));
  state = mod.reduceAssistantStream(state, event(3, "inline.confirmation.required", {confirmation: {run_id: "run-1", proposal_id: "assistant-cs-1", title: "补充笔记", summary: "等待确认", risk_level: "medium", writes: [], actions: ["confirm", "reject"]}}));
  state = mod.reduceAssistantStream(state, event(4, "run.waiting_confirmation", {proposalId: "assistant-cs-1"}));
  assert.equal(state.status, "waiting_confirmation");
  assert.equal(state.proposal.id, "assistant-cs-1");
  assert.equal(state.confirmation.proposal_id, "assistant-cs-1");
});

test("assistant production surface uses Pi model proxy, stop and three inspector tabs", async () => {
  const [views, api, css] = await Promise.all([
    readFile(new URL("../src/views.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
    readFile(new URL("../styles.css", import.meta.url), "utf8"),
  ]);
  assert.match(api, /\/model\/stream/);
  assert.match(api, /cancelRuntimeRun/);
  assert.match(api, /updateMessageMetadata/);
  assert.match(api, /response\.body\.getReader/);
  assert.match(views, /停止生成/);
  assert.match(views, /reduceAssistantStream/);
  assert.match(views, /paintAssistantLiveTrace\(activeTrace, this\.assistantLiveRun\)/);
  assert.match(views, /regenerateMessageId/);
  assert.match(views, /this\.agentRuntime\.fork\(regenerateRunId, undefined, "regenerate"\)/);
  assert.match(views, /编辑后重发/);
  assert.match(views, /复制消息/);
  assert.match(views, /重新生成/);
  assert.match(views, /ProgressiveAssistantMarkdown/);
  assert.match(views, /PiAgentRuntime/);
  assert.doesNotMatch(views, /PydanticAgentRuntime/);
  assert.match(views, /resumeInlineConfirmation/);
  assert.match(views, /runConversationId = conversationId/);
  assert.match(views, /buildAssistantMessageMetadata/);
  const queryLoop = views.indexOf("for await (const update of runPiAssistantTurn(turnInput");
  const livePermissionMount = views.indexOf('event.type === "inline.confirmation.required"', queryLoop);
  const confirmationResume = views.indexOf("const resumeInlineConfirmation", livePermissionMount);
  assert.ok(queryLoop >= 0 && livePermissionMount > queryLoop, "permission card must mount inside the live query loop");
  assert.ok(confirmationResume > livePermissionMount, "same-Run confirmation handler must remain available to the live card");
  assert.doesNotMatch(views.slice(confirmationResume, views.indexOf("input.onkeydown", confirmationResume)), /\.abort\(\)/);
  assert.match(views, /permission_mode: this\.assistantPermissionMode/);
  assert.match(views, /当前任务全部允许/);
  assert.doesNotMatch(api, /cancelAssistantRun/);
  assert.doesNotMatch(views, /markdown\.empty\(\); await this\.markdown\.render/);
  assert.match(css, /\.la-message-markdown strong \{[\s\S]*display:\s*inline/);
  for (const label of ["上下文", "来源", "变更", "新会话", "搜索对话"]) assert.match(views, new RegExp(label));
  assert.match(css, /\.la-workspace--assistant/);
  assert.match(css, /grid-template-columns:\s*minmax\(620px, 1fr\) 352px/);
  assert.match(css, /\.la-live-trace/);
  assert.match(css, /@container \(max-width: 1420px\)[\s\S]*\.la-workspace--assistant \{ grid-template-columns: 72px minmax\(0, 1fr\); \}/);
  assert.match(css, /\.la-assistant-shell-v5 \.la-chat-toolbar__title \{ flex: 1 1 180px; min-width: 140px;/);
  assert.match(views, /assistantNetworkEnabled/);
  assert.match(views, /allow_network: this\.assistantNetworkEnabled/);
  assert.match(views, /networkToggleButton/);
  assert.match(views, /mode\.onclick = toggleNetwork/);
  assert.match(views, /aria-pressed/);
  assert.match(views, /添加内容或打开工具/);
  assert.match(views, /开启联网/);
  assert.match(views, /la-composer-model/);
  assert.match(views, /la-model-popover/);
  assert.match(views, /la-message-meta/);
  assert.match(views, /deepseek-color\.svg/);
  assert.match(views, /openai\.svg/);
  assert.match(views, /qwen-color\.svg/);
  assert.match(views, /renderModelBrand/);
  assert.match(views, /renderAssistantAvatar/);
  assert.match(views, /zhixu-assistant-avatar\.png/);
  assert.doesNotMatch(views, /iconButton\(header, "history"/);
  assert.doesNotMatch(views, /button\(header, "新会话"/);
  assert.match(views, /search_academic_sources/);
  assert.match(css, /\.la-assistant-shell-v7/);
  assert.match(css, /\.la-assistant-shell-v8/);
  assert.match(css, /\.la-assistant-shell-v8 \.la-unified-composer textarea:focus-visible[\s\S]*outline:\s*0 !important/);
  assert.match(css, /\.la-model-popover\[hidden\]/);
  assert.match(css, /User-message metadata belongs below the bubble/);
  assert.match(css, /\.la-message-avatar--zhixu/);
  assert.match(css, /\.la-composer-mode\.is-enabled/);
  assert.match(views, /paintSendButton/);
  assert.match(views, /la-composer-submit__arrow/);
  assert.match(views, /la-composer-submit__spinner/);
  assert.match(css, /\.la-composer-submit__spinner/);
  assert.match(css, /@keyframes la-spin/);
  assert.match(css, /\.la-composer-submit__arrow/);
  assert.match(views, /SEND_BUTTON_IDLE_VECTOR_DATA_URL/);
  assert.match(views, /data:image\/svg\+xml/);
  assert.doesNotMatch(css, /\.la-composer-submit__arrow\s*\{[^}]*clip-path/s);
  assert.match(css, /button\.la-composer-submit:not\(\.is-running\)[\s\S]*background:\s*transparent !important/);
  assert.match(css, /appearance:\s*none/);
  assert.match(css, /border-radius:\s*50% !important/);
  assert.match(css, /button\.la-composer-submit[\s\S]*width:\s*36px !important[\s\S]*height:\s*36px !important/);
  assert.match(css, /aspect-ratio:\s*1 \/ 1/);
  assert.match(css, /\.la-composer-submit\.is-running/);
  assert.match(views, /推理与执行过程/);
  assert.match(views, /保存在本地用于恢复，不会并入最终回答或未来模型上下文/);
  assert.match(views, /previousDetails\?\.open/);
  assert.match(views, /previousProviderReasoning\?\.open/);
  assert.match(views, /fallbackStatus = run\.status === "completed"/);
  assert.match(css, /\.la-live-trace__thinking/);
  assert.match(css, /\.la-provider-reasoning/);
  assert.match(views, /renderProviderReasoning/);
  assert.doesNotMatch(views, /复制思考/);
  assert.match(views, /支持深度推理协议/);
  assert.match(views, /assistantReasoningMode:\s*"auto"\s*\|\s*"deep"/);
  assert.match(views, /reasoning_mode:\s*this\.assistantReasoningMode/);
  assert.match(views, /深度思考/);
  assert.match(views, /提高推理预算，并要求证据与结果校验/);
  assert.match(css, /\.la-model-popover__reasoning/);
  assert.match(css, /\.la-composer-model\.is-deep/);
  assert.match(css, /\.la-assistant-source-card\.is-web/);
});
