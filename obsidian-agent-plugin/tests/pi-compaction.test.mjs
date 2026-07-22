import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function load(target) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-compact-"));
  const outfile = path.join(directory, "bundle.cjs");
  await esbuild.build({
    entryPoints: [path.resolve(target)],
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

const LONG = "知识语义检索需要跨来源对齐概念，而不是机械拼接片段。".repeat(120);

function emitText(onEvent, text) {
  onEvent({type: "start"});
  onEvent({type: "text_start"});
  onEvent({type: "text_delta", delta: text});
  onEvent({type: "text_end"});
  onEvent({type: "done", finishReason: "stop"});
}

function emitToolCall(onEvent, id, name, args) {
  onEvent({type: "start"});
  onEvent({type: "tool_call_start", index: 0, id, name});
  onEvent({type: "tool_call_delta", index: 0, delta: JSON.stringify(args)});
  onEvent({type: "tool_call_end", index: 0, id, name, arguments: JSON.stringify(args)});
  onEvent({type: "done", finishReason: "toolUse"});
}

const organizationContract = Object.freeze({
  name: "organize_vault_notes",
  description: "Atomically organize Markdown notes",
  input_schema: {type: "object", properties: {title: {type: "string"}}, required: ["title"], additionalProperties: false},
  output_schema: {type: "object", additionalProperties: true},
  uses_network: false,
  mutates_state: true,
  timeout_seconds: 20,
  permission_level: "proposal",
  idempotent: true,
  cancellable: false,
  max_result_bytes: 64_000,
});

function permissionResponse() {
  return {
    ok: false,
    isError: true,
    error: {
      code: "task_organization_scope_required",
      message: "需要扩大当前 Run 的 Vault 整理范围",
      permissionRequest: {type: "vault_organization", toolName: "organize_vault_notes", summary: "整理资料", organization: {title: "整理资料"}},
    },
  };
}

// ---- Pure function tests (no runtime) ----

test("compaction never splits a tool call / tool result pair", async () => {
  const {module, dispose} = await load("src/core/runtime/pi/PiCompaction.ts");
  try {
    const user = {role: "user", content: "先读资料", timestamp: 1};
    const turns = [];
    for (let i = 0; i < 5; i += 1) {
      turns.push({role: "user", content: `问题 ${i}`, timestamp: i * 3});
      turns.push({role: "assistant", content: [{type: "text", text: `回答 ${i}`}], timestamp: i * 3 + 1});
    }
    const pairStartIndex = turns.length;
    turns.push({role: "user", content: "执行工具", timestamp: 100});
    turns.push({role: "assistant", content: [{type: "toolCall", id: "call-1", name: "t", arguments: {}}], timestamp: 101});
    turns.push({role: "toolResult", toolCallId: "call-1", content: [{type: "text", text: "ok"}], timestamp: 102});

    const result = module.compactAgentMessages(turns, {contextWindow: 10, reserve: 0, keepRecent: 0, force: true});
    assert.equal(result.compacted, true);
    const kept = result.messages.slice(1); // drop the summary
    const calls = new Set();
    const results = new Set();
    for (const m of kept) {
      if (m.role === "assistant") {
        for (const item of m.content) if (item.type === "toolCall") calls.add(item.id);
      } else if (m.role === "toolResult") {
        results.add(m.toolCallId);
      }
    }
    for (const id of calls) assert.ok(results.has(id), `tool call ${id} must stay with its result`);
    void user;
    void pairStartIndex;
  } finally {
    await dispose();
  }
});

test("summary is a runtime checkpoint, never a fake user turn", async () => {
  const {module, dispose} = await load("src/core/runtime/pi/PiCompaction.ts");
  try {
    const messages = [
      {role: "user", content: "目标是什么", timestamp: 1},
      {role: "assistant", content: [{type: "text", text: "解释"}], timestamp: 2},
    ];
    const sources = {
      messages,
      goal: "整理阅读笔记",
      activeNote: {path: "20-Knowledge/Notes/x.md", name: "x"},
      attachments: ["a.pdf"],
      sourcesRead: ["src-1"],
      completedActions: ["act-1"],
      pendingActions: ["act-2"],
      failedTools: ["bad-tool"],
      activeWorkspace: {workspaceId: "ws-1", projectPaths: ["p1"]},
      branchId: "run-1",
    };
    const result = module.compactAgentMessages(messages, {contextWindow: 10, reserve: 0, keepRecent: 0, force: true}, sources);
    assert.equal(result.compacted, true);
    assert.equal(result.messages[0].role, "assistant");
    assert.notEqual(result.messages[0].role, "user");
    const summaryText = Array.isArray(result.messages[0].content)
      ? result.messages[0].content.map(b => b.text ?? "").join("")
      : String(result.messages[0].content);
    assert.match(summaryText, /zhixu_runtime_checkpoint/);
    const state = result.entry.structuredState;
    assert.equal(state.goal, "整理阅读笔记");
    assert.equal(state.activeNote.path, "20-Knowledge/Notes/x.md");
    assert.deepEqual(state.attachments, ["a.pdf"]);
    assert.deepEqual(state.completedActions, ["act-1"]);
    assert.equal(state.activeWorkspace.workspaceId, "ws-1");
    assert.match(result.summary, /20-Knowledge\/Notes\/x.md/);
    assert.match(result.summary, /a.pdf/);
    assert.match(result.summary, /act-1/);
  } finally {
    await dispose();
  }
});

test("unresolved tool call defers compaction", async () => {
  const {module, dispose} = await load("src/core/runtime/pi/PiCompaction.ts");
  try {
    const messages = [
      {role: "user", content: "执行", timestamp: 1},
      {role: "assistant", content: [{type: "toolCall", id: "call-x", name: "t", arguments: {}}], timestamp: 2},
    ];
    const result = module.compactAgentMessages(messages, {contextWindow: 10, reserve: 0, keepRecent: 0, force: true});
    assert.equal(result.compacted, false);
    assert.equal(result.reason, "unresolved_tool_call");
    assert.equal(result.messages, messages);
  } finally {
    await dispose();
  }
});

// ---- Runtime integration tests ----

test("runtime compaction persists a recoverable checkpoint entry", async () => {
  const {module, dispose} = await load("src/core/runtime/PiAgentRuntime.ts");
  let call = 0;
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { return {ok: true, isError: false, content: {state: "applied"}}; },
    async streamModelProxy(body, onEvent) { emitText(onEvent, LONG); },
    saved: {},
    async saveCompactionCheckpoint(body) { this.saved[body.runId] = body; return {ok: true}; },
    async getCompactionCheckpoint(runId) { return {entry: this.saved[runId]?.entry ?? null}; },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    for (let i = 0; i < 4; i += 1) {
      for await (const _ of runtime.query(runtime.prepareTurn({message: LONG, conversationId: "compact-src", options: {maxTokens: 4000}}))) void _;
    }
    const runId = runtime.getConversationState().runId;
    const before = runtime.getConversationState();
    const chunk = await runtime.compact(runId, {contextWindow: 4000, keepRecent: 1000});
    assert.equal(chunk.type, "context_compacted");
    assert.ok(transport.saved[runId], "checkpoint entry was persisted");
    const entry = transport.saved[runId].entry;
    assert.equal(entry.structuredState.branchId, runId, "branch isolation via run id");
    assert.ok(entry.structuredState.activeWorkspace, "workspace captured in checkpoint");
    assert.ok(entry.structuredState.taskAuthorization, "authorization captured in checkpoint");
    // Restart reuses the entry
    const recovered = await transport.getCompactionCheckpoint(runId);
    assert.equal(recovered.entry.structuredState.branchId, runId);
    assert.equal(recovered.entry.tokensBefore, entry.tokensBefore);
    void before;
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("compaction is deferred while a permission is pending and the run stays intact", async () => {
  const {module, dispose} = await load("src/core/runtime/PiAgentRuntime.ts");
  let call = 0;
  let modelCalls = 0;
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { return call++ === 0 ? permissionResponse() : {ok: true, isError: false, content: {state: "applied"}}; },
    async streamModelProxy(body, onEvent) {
      modelCalls += 1;
      if (modelCalls === 1) emitToolCall(onEvent, "call-perm", "organize_vault_notes", {title: "整理资料"});
      else emitText(onEvent, LONG);
    },
    async saveCompactionCheckpoint() { return {ok: true}; },
    async getCompactionCheckpoint() { return {entry: null}; },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    let pendingRunId = null;
    let threw = null;
    const queryDone = (async () => {
      for await (const chunk of runtime.query(runtime.prepareTurn({message: "整理资料", conversationId: "compact-pending"}))) {
        if (chunk.type === "confirmation_required") {
          pendingRunId = chunk.runId;
          // While a permission is pending the run is still active, so compaction
          // must refuse rather than split the in-flight context.
          try {
            await runtime.compact(chunk.runId);
          } catch (error) {
            threw = error;
          }
          await runtime.confirm(chunk.runId, true, undefined, undefined, "__all__");
        }
      }
    })();
    await queryDone;
    assert.ok(pendingRunId, "permission became pending");
    assert.ok(threw, "compaction must not run while permission is pending");
    assert.match(String(threw?.message), /pi_compaction_requires_idle_run/);
    const state = runtime.getConversationState();
    assert.ok(Array.isArray(state.messages));
    assert.ok(state.messages.length >= 2, "original session messages intact after a deferred compaction");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});
