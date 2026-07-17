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

test("cancel keeps partial output and changes only a running run", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "message.delta", {delta: "已返回部分"}));
  state = mod.cancelledAssistantRun(state);
  assert.equal(state.status, "cancelled");
  assert.equal(state.content, "已返回部分");
  assert.equal(mod.cancelledAssistantRun({...state, status: "completed"}).status, "completed");
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

test("inline confirmation is not mistaken for an applied write", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "inline.confirmation.required", {confirmation: {run_id: "run-1", proposal_id: "cs-1", title: "修改", summary: "1 个文件", risk_level: "medium", writes: [], actions: ["confirm", "reject"]}}));
  state = mod.reduceAssistantStream(state, event(3, "run.waiting_confirmation", {proposalId: "cs-1"}));
  assert.equal(state.proposalRequired, true);
  assert.equal(state.status, "waiting_confirmation");
});

test("completed message metadata survives the stream without a full view refresh", async () => {
  const mod = await moduleUnderTest();
  let state = mod.initialAssistantLiveRun();
  state = mod.reduceAssistantStream(state, event(1, "run.started"));
  state = mod.reduceAssistantStream(state, event(2, "message.delta", {delta: "- **一致性**：定义"}));
  state = mod.reduceAssistantStream(state, event(3, "message.completed", {
    message: {id: "msg-final", role: "assistant", content: "- **一致性**：定义", createdAt: "2026-07-16T20:00:00+08:00"},
  }));
  state = mod.reduceAssistantStream(state, event(4, "run.completed"));
  assert.equal(state.completedMessage.id, "msg-final");
  assert.equal(state.completedMessage.content, state.content);
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

test("assistant production surface uses stream endpoint, stop and three inspector tabs", async () => {
  const [views, api, css] = await Promise.all([
    readFile(new URL("../src/views.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
    readFile(new URL("../styles.css", import.meta.url), "utf8"),
  ]);
  assert.match(api, /\/assistant\/stream/);
  assert.match(api, /response\.body\.getReader/);
  assert.match(views, /停止生成/);
  assert.match(views, /reduceAssistantStream/);
  assert.match(views, /paintAssistantLiveTrace\(activeTrace, this\.assistantLiveRun\)/);
  assert.match(views, /regenerateMessageId/);
  assert.match(views, /编辑后重发/);
  assert.match(views, /重新生成/);
  assert.match(views, /ProgressiveAssistantMarkdown/);
  assert.match(views, /PydanticAgentRuntime/);
  assert.match(views, /resumeInlineConfirmation/);
  assert.match(api, /cancelAssistantRun/);
  assert.doesNotMatch(views, /markdown\.empty\(\); await this\.markdown\.render/);
  assert.match(css, /\.la-message-markdown strong \{[\s\S]*display:\s*inline/);
  for (const label of ["上下文", "来源", "变更", "新会话", "搜索对话"]) assert.match(views, new RegExp(label));
  assert.match(css, /\.la-workspace--assistant/);
  assert.match(css, /grid-template-columns:\s*minmax\(620px, 1fr\) 352px/);
  assert.match(css, /\.la-live-trace/);
  assert.match(css, /@container \(max-width: 1420px\)[\s\S]*\.la-workspace--assistant \{ grid-template-columns: 72px minmax\(0, 1fr\); \}/);
  assert.match(css, /\.la-assistant-shell-v5 \.la-chat-toolbar__title \{ flex: 1 1 180px; min-width: 140px;/);
});
