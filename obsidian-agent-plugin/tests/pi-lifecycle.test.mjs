import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function bundle(entry) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-life-"));
  const outfile = path.join(directory, "module.cjs");
  await esbuild.build({entryPoints: [path.resolve(entry)], outfile, bundle: true, format: "cjs", platform: "node", target: "node22", logLevel: "silent"});
  return {module: createRequire(import.meta.url)(outfile), dispose: () => rm(directory, {recursive: true, force: true})};
}

function baseTransport(overrides = {}) {
  const persisted = [];
  const controls = [];
  return {
    persisted,
    controls,
    async registerTaskAuthorization(body) { return {taskAuthorization: body.taskAuthorization}; },
    async appendRuntimeEvents(body) { await new Promise(resolve => setTimeout(resolve, 2)); persisted.push(...body.events); return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async runtimeEvents(_run, after) { return {items: persisted.filter(item => item.sequence > after)}; },
    async runtimeSession() { return {session: {entries: []}}; },
    async runtimeSessionProjection() { throw new Error("pi_session_not_found"); },
    async controlRuntimeRun(_run, type, text) { controls.push({type, text}); return {control: {type}}; },
    async cancelRuntimeRun(runId) { controls.push({type: "cancel", runId}); return {status: "cancelled"}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async callRuntimeTool() { throw new Error("unexpected tool"); },
    ...overrides,
  };
}

test("events are persisted before the consumer observes them and reconnect uses durable events", async () => {
  const {module, dispose} = await bundle("src/core/runtime/PiAgentRuntime.ts");
  const transport = baseTransport({
    async streamModelProxy(_body, onEvent) {
      onEvent({type: "start"}); onEvent({type: "text_start"}); onEvent({type: "text_delta", delta: "已持久化"}); onEvent({type: "text_end"}); onEvent({type: "done", finishReason: "stop"});
    },
  });
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "回答", conversationId: "persist-conversation"}))) {
      assert.ok(transport.persisted.some(item => item.sequence === chunk.sequence));
      chunks.push(chunk);
    }
    const runId = chunks[0].runId;
    const reconnected = await runtime.reconnect(runId, 0);
    assert.deepEqual(reconnected, chunks);
  } finally { await dispose(); }
});

test("steering and follow-up are queued in the live Pi loop", async () => {
  const {module, dispose} = await bundle("src/core/runtime/PiAgentRuntime.ts");
  let request = 0;
  let release;
  const started = new Promise(resolve => { release = resolve; });
  const contexts = [];
  let unblock;
  const blocked = new Promise(resolve => { unblock = resolve; });
  const transport = baseTransport({
    async streamModelProxy(body, onEvent) {
      request += 1; contexts.push(body.context.messages);
      if (request === 1) { release(); await blocked; }
      onEvent({type: "start"}); onEvent({type: "text_start"}); onEvent({type: "text_delta", delta: `turn-${request}`}); onEvent({type: "text_end"}); onEvent({type: "done", finishReason: "stop"});
    },
  });
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const turn = runtime.prepareTurn({message: "开始", conversationId: "control-conversation"});
    const collecting = (async () => { const result = []; for await (const chunk of runtime.query(turn)) result.push(chunk); return result; })();
    await started;
    const runId = runtime.getConversationState().runId;
    await runtime.steer(runId, "只看统计推断");
    await runtime.followUp(runId, "完成后给三道题");
    unblock();
    await collecting;
    assert.deepEqual(transport.controls.slice(0, 2), [{type: "steering", text: "只看统计推断"}, {type: "follow_up", text: "完成后给三道题"}]);
    assert.ok(contexts.some(messages => JSON.stringify(messages).includes("只看统计推断")));
    assert.ok(contexts.some(messages => JSON.stringify(messages).includes("完成后给三道题")));
  } finally { await dispose(); }
});

test("token compaction cuts only at a complete user turn", async () => {
  const {module, dispose} = await bundle("src/core/runtime/pi/PiCompaction.ts");
  try {
    const messages = [];
    for (let index = 0; index < 18; index += 1) {
      messages.push({role: "user", content: `目标 ${index} ${"甲".repeat(1200)}`, timestamp: index});
      messages.push({role: "assistant", content: [{type: "toolCall", id: `call-${index}`, name: "search_vault", arguments: {query: String(index)}}], api: "x", provider: "x", model: "x", usage: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}}, stopReason: "toolUse", timestamp: index});
      messages.push({role: "toolResult", toolCallId: `call-${index}`, toolName: "search_vault", content: [{type: "text", text: "result"}], isError: false, timestamp: index});
    }
    const result = module.compactAgentMessages(messages, {contextWindow: 6000, reserve: 1000, keepRecent: 1800, force: true});
    assert.equal(result.compacted, true);
    assert.equal(result.messages[0].role, "assistant", "summary is a runtime checkpoint, never a fake user turn");
    assert.notEqual(result.messages[0].role, "user");
    const summaryText = Array.isArray(result.messages[0].content)
      ? result.messages[0].content.map(b => b.text ?? "").join("")
      : String(result.messages[0].content);
    assert.match(summaryText, /zhixu_runtime_checkpoint/);
    assert.equal(result.messages[1].role, "user");
  } finally { await dispose(); }
});

test("fork creates a new authorization lineage without mutating the source branch", async () => {
  const {module, dispose} = await bundle("src/core/runtime/PiAgentRuntime.ts");
  const registered = [];
  const transport = baseTransport({
    async registerTaskAuthorization(body) { registered.push(body.taskAuthorization); return {taskAuthorization: body.taskAuthorization}; },
    async runtimeForkProjection(runId, body) {
      return {
        sourceRunId: runId,
        sessionId: "source-conversation",
        mode: body.mode ?? "fork",
        requestedSequence: body.sequence ?? null,
        resolvedForkEntryId: "entry-source-1",
        resolvedForkSequence: body.sequence ?? 1,
        completedActionIds: [],
        projection: {
          sessionId: "source-conversation",
          leafId: "entry-source-1",
          branchId: runId,
          entries: [],
          messages: [{role: "user", content: "source", timestamp: 1}],
          focus: {},
          attachments: [],
          activeActions: [],
          pending: null,
          compaction: null,
          schemaVersion: 1,
        },
        schemaVersion: 1,
      };
    },
    async streamModelProxy(_body, onEvent) {
      onEvent({type: "start"}); onEvent({type: "text_start"}); onEvent({type: "text_delta", delta: "source"}); onEvent({type: "text_end"}); onEvent({type: "done", finishReason: "stop"});
    },
  });
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "source", conversationId: "source-conversation"}))) chunks.push(chunk);
    const sourceRunId = chunks[0].runId;
    const sourceState = runtime.getConversationState();
    const fork = await runtime.fork(sourceRunId, 1);
    assert.notEqual(fork.conversationId, sourceState.conversationId);
    assert.notEqual(fork.runId, sourceRunId);
    assert.equal(fork.parentRunId, sourceRunId);
    assert.equal(fork.forkedFromSequence, 1);
    assert.equal(fork.resolvedForkEntryId, "entry-source-1");
    assert.equal(registered.length, 1);
    assert.equal(registered[0].runId, sourceRunId);
  } finally { await dispose(); }
});
