import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadRuntime() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-runtime-"));
  const outfile = path.join(directory, "runtime.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/PiAgentRuntime.ts")],
    outfile,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  const require = createRequire(import.meta.url);
  return {module: require(outfile), dispose: () => rm(directory, {recursive: true, force: true})};
}

test("Pi owns an observation-driven multi-turn tool loop", async () => {
  const {module, dispose} = await loadRuntime();
  let modelRequests = 0;
  let toolRequests = 0;
  const persisted = [];
  const transport = {
    async registerTaskAuthorization(body) {
      assert.match(body.taskAuthorization.runId, /^pi-run-/);
      return {taskAuthorization: body.taskAuthorization};
    },
    async appendRuntimeEvents(body) {
      persisted.push(...body.events);
      return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0};
    },
    async toolContracts() {
      return {
        schemaVersion: 1,
        items: [{
          name: "get_vault_overview",
          description: "Read a safe Vault overview",
          input_schema: {type: "object", properties: {}, additionalProperties: false},
          output_schema: {type: "object", additionalProperties: true},
          uses_network: false,
          mutates_state: false,
          timeout_seconds: 20,
          permission_level: "read_only",
          idempotent: true,
          cancellable: false,
          max_result_bytes: 16000,
        }],
      };
    },
    async callRuntimeTool(body) {
      toolRequests += 1;
      assert.equal(body.toolName, "get_vault_overview");
      assert.match(body.runId, /^pi-run-/);
      return {ok: true, isError: false, content: {folders: ["20-Knowledge"]}};
    },
    async streamModelProxy(body, onEvent) {
      modelRequests += 1;
      onEvent({type: "start"});
      if (modelRequests === 1) {
        onEvent({type: "tool_call_start", index: 0, id: "call-1", name: "get_vault_overview"});
        onEvent({type: "tool_call_delta", index: 0, delta: "{}"});
        onEvent({type: "tool_call_end", index: 0, id: "call-1", name: "get_vault_overview", arguments: "{}"});
        onEvent({type: "usage", usage: {prompt_tokens: 10, completion_tokens: 3, total_tokens: 13}});
        onEvent({type: "done", finishReason: "toolUse"});
      } else {
        assert.ok(body.context.messages.some(message => message.role === "toolResult"));
        onEvent({type: "text_start"});
        onEvent({type: "text_delta", delta: "Vault 中有 20-Knowledge。"});
        onEvent({type: "text_end"});
        onEvent({type: "usage", usage: {prompt_tokens: 20, completion_tokens: 8, total_tokens: 28}});
        onEvent({type: "done", finishReason: "stop"});
      }
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const turn = runtime.prepareTurn({message: "查看 Vault", conversationId: "conversation-1"});
    const chunks = [];
    for await (const chunk of runtime.query(turn)) chunks.push(chunk);
    assert.equal(modelRequests, 2);
    assert.equal(toolRequests, 1);
    assert.deepEqual(chunks.filter(item => item.type === "tool_use").map(item => item.status), ["running"]);
    assert.deepEqual(chunks.filter(item => item.type === "tool_result").map(item => item.status), ["completed"]);
    assert.equal(chunks.filter(item => item.type === "text").map(item => item.content).join(""), "Vault 中有 20-Knowledge。");
    assert.equal(chunks.at(-1).type, "done");
    assert.deepEqual(persisted, chunks);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("Pi restores durable history and exposes only provider-returned thinking as reasoning chunks", async () => {
  const {module, dispose} = await loadRuntime();
  let restoredContext = "";
  const persisted = [];
  const transport = {
    async runtimeSession() {
      return {
        schemaVersion: 2,
        history: [
          {id: "old-user", role: "user", content: "旧问题", createdAt: "2026-07-20T08:00:00Z"},
          {id: "old-assistant", role: "assistant", content: "旧回答", createdAt: "2026-07-20T08:00:01Z"},
        ],
      };
    },
    async registerTaskAuthorization(body) { return {taskAuthorization: body.taskAuthorization}; },
    async appendRuntimeEvents(body) { persisted.push(...body.events); return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(body, onEvent) {
      restoredContext = JSON.stringify(body.context.messages);
      onEvent({type: "start"});
      onEvent({type: "thinking_start", contentIndex: 0});
      onEvent({type: "thinking_delta", contentIndex: 0, delta: "供应商原始推理"});
      onEvent({type: "thinking_end", contentIndex: 0});
      onEvent({type: "text_start"});
      onEvent({type: "text_delta", delta: "恢复成功"});
      onEvent({type: "text_end"});
      onEvent({type: "done", finishReason: "stop"});
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "继续", conversationId: "conversation-restored"}))) chunks.push(chunk);
    assert.match(restoredContext, /旧问题/);
    assert.match(restoredContext, /旧回答/);
    assert.doesNotMatch(restoredContext, /供应商原始推理/);
    assert.equal(chunks.filter(item => item.type === "text").map(item => item.content).join(""), "恢复成功");
    const reasoning = chunks.filter(item => item.type === "reasoning");
    assert.deepEqual(reasoning.map(item => item.phase), ["started", "delta", "completed"]);
    assert.equal(reasoning.map(item => item.content).join(""), "供应商原始推理");
    assert.match(JSON.stringify(persisted), /供应商原始推理/);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("Pi fails closed when the model proxy ends without a terminal event", async () => {
  const {module, dispose} = await loadRuntime();
  const persisted = [];
  const transport = {
    async registerTaskAuthorization(body) { return {taskAuthorization: body.taskAuthorization}; },
    async appendRuntimeEvents(body) { persisted.push(...body.events); return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(_body, onEvent) { onEvent({type: "start"}); },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "不会永久等待", conversationId: "conversation-eof"}))) chunks.push(chunk);
    assert.equal(chunks.at(-1).type, "error");
    assert.match(chunks.at(-1).content, /未收到完成事件/);
    assert.equal(runtime.getConversationState().status, "failed");
  } finally {
    await dispose();
  }
});

test("Pi forwards the selected profile model instead of its construction placeholder", async () => {
  const {module, dispose} = await loadRuntime();
  let requestedModel = "";
  const transport = {
    async registerTaskAuthorization(body) { return {taskAuthorization: body.taskAuthorization}; },
    async appendRuntimeEvents(body) { return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(body, onEvent) {
      requestedModel = body.model;
      onEvent({type: "start"});
      onEvent({type: "text_start"});
      onEvent({type: "text_delta", delta: "ok"});
      onEvent({type: "text_end"});
      onEvent({type: "done", finishReason: "stop"});
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const turn = runtime.prepareTurn({message: "test", conversationId: "conversation-model", model: "deepseek-v4-pro"});
    for await (const _chunk of runtime.query(turn)) { /* consume */ }
    assert.equal(requestedModel, "deepseek-v4-pro");
  } finally {
    await dispose();
  }
});

test("Pi resets stall budgets for every turn in a long-lived conversation", async () => {
  const {module, dispose} = await loadRuntime();
  const originalNow = Date.now;
  let clock = Date.parse("2026-07-20T08:00:00Z");
  Date.now = () => clock;
  let requests = 0;
  const transport = {
    async registerTaskAuthorization(body) { return {taskAuthorization: body.taskAuthorization}; },
    async appendRuntimeEvents(body) { return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(_body, onEvent) {
      requests += 1;
      onEvent({type: "start"});
      onEvent({type: "text_start"});
      onEvent({type: "text_delta", delta: `turn-${requests}`});
      onEvent({type: "text_end"});
      onEvent({type: "done", finishReason: "stop"});
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const first = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "first", conversationId: "conversation-long"}))) first.push(chunk);
    clock += 31 * 60_000;
    const second = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({message: "second", conversationId: "conversation-long"}))) second.push(chunk);
    assert.equal(requests, 2);
    assert.equal(second.filter(item => item.type === "text").map(item => item.content).join(""), "turn-2");
    assert.equal(second.at(-1).type, "done");
    runtime.cleanup();
  } finally {
    Date.now = originalNow;
    await dispose();
  }
});
