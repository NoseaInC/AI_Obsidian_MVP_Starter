import assert from "node:assert/strict";
import {mkdtemp, readFile, rm} from "node:fs/promises";
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

test("runtime compaction uses the persisted projection leaf, never the session id", async () => {
  const source = await readFile("src/core/runtime/PiAgentRuntime.ts", "utf8");
  assert.match(source, /currentLeafId: projection\.leafId/);
  assert.doesNotMatch(source, /currentLeafId:\s*session\.identity\.sessionId/);
  assert.match(source, /transformContext: async messages => messages/);
});

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
      {role: "user", content: "目标是什么", timestamp: 1, metadata: {entryId: "entry-user", entryStartId: "entry-user", entryEndId: "entry-user"}},
      {role: "assistant", content: [{type: "text", text: "解释"}], timestamp: 2, metadata: {entryId: "entry-answer", entryStartId: "entry-answer", entryEndId: "entry-answer"}},
    ];
    const sources = {
      messages,
      goal: "整理阅读笔记",
      activeNote: {path: "20-Knowledge/Notes/x.md", name: "x"},
      conversationFocus: {activeTopic: "语义检索"},
      activeSelectionReference: "selection:20-Knowledge/Notes/x.md",
      attachments: [{id: "attachment-1", displayName: "a.pdf"}],
      sourcesRead: [{id: "src-1", observationReference: "obs-1"}],
      completedActions: [{id: "act-1", status: "completed"}],
      pendingActions: [{id: "act-2", status: "pending"}],
      failedTools: [{tool: "bad-tool", status: "failed"}],
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
    assert.equal(state.attachments[0].id, "attachment-1");
    assert.equal(state.completedActions[0].id, "act-1");
    assert.equal(state.activeWorkspace.workspaceId, "ws-1");
    assert.equal(result.entry.cutEntryId, "entry-user");
    assert.equal(result.entry.keptFromEntryId, "entry-answer");
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

test("pending typed question defers compaction", async () => {
  const {module, dispose} = await load("src/core/runtime/pi/PiCompaction.ts");
  try {
    const messages = [
      {role: "user", content: "需要澄清", timestamp: 1},
      {role: "custom", customType: "question_required", content: "选择范围", details: {status: "pending"}, timestamp: 2},
    ];
    const result = module.compactAgentMessages(messages, {contextWindow: 10, reserve: 0, keepRecent: 0, force: true});
    assert.equal(result.compacted, false);
    assert.equal(result.reason, "pending_question");
    assert.equal(result.messages, messages);
  } finally {
    await dispose();
  }
});

test("runtime defers compaction for an in-memory pending question before projection", async () => {
  const source = await readFile("src/core/runtime/PiAgentRuntime.ts", "utf8");
  const originalCheck = source.indexOf("hasPendingQuestion(original)");
  const projectionLoad = source.indexOf("runtimeSessionProjection(\n        session.identity.sessionId", originalCheck);
  assert.ok(originalCheck > 0, "runtime checks its exact in-memory transcript");
  assert.ok(projectionLoad > originalCheck, "pending question is checked before loading/replacing from projection");
  assert.match(source.slice(originalCheck, projectionLoad), /context_compaction_deferred/);
});

// ---- Runtime integration tests ----

test("runtime compaction persists a recoverable checkpoint entry", async () => {
  const {module, dispose} = await load("src/core/runtime/PiAgentRuntime.ts");
  let call = 0;
  let saveCalls = 0;
  const modelContexts = [];
  const transport = {
    async runtimeSession() { return {history: []}; },
    async runtimeSessionProjection(sessionId, options = {}) {
      if (!options.runId) {
        if (!this.restoreProjection) throw new Error("pi_session_not_found");
        const saved = Object.values(this.saved)[0];
        return {
          sessionId, leafId: `checkpoint-entry-${saved.runId}`, branchId: saved.runId,
          entries: [],
          messages: [{
            role: "compactionSummary", summary: saved.summary,
            tokensBefore: saved.entry.tokensBefore, timestamp: Date.now(),
            metadata: {entryId: `checkpoint-entry-${saved.runId}`},
          }],
          focus: {}, attachments: [], activeActions: [], pending: null,
          compaction: {...saved.entry, summary: saved.summary, checkpointEntryId: `checkpoint-entry-${saved.runId}`},
          compactionState: saved.entry.structuredState, schemaVersion: 1,
        };
      }
      const messages = [];
      for (let index = 0; index < 4; index += 1) {
        messages.push({
          role: "user", content: `${LONG}-${index}`, timestamp: index * 2,
          metadata: {entryId: `entry-user-${index}`, entryStartId: `entry-user-${index}`, entryEndId: `entry-user-${index}`},
        });
        messages.push({
          role: "assistant", content: [{type: "text", text: `${LONG}-${index}`}], timestamp: index * 2 + 1,
          metadata: {entryId: `entry-answer-${index}`, entryStartId: `entry-answer-${index}`, entryEndId: `entry-answer-${index}`},
        });
      }
      return {
        sessionId, leafId: "entry-answer-3", branchId: options.runId, entries: [], messages,
        focus: {activeTopic: {title: "语义检索"}},
        attachments: [{id: "attachment-1", displayName: "paper.pdf", sha256: "hash-1"}],
        activeActions: [{id: "action-1", status: "completed", undoState: {available: true}}],
        pending: null, compaction: null,
        compactionState: {
          goal: "完整恢复目标",
          explicitConstraints: ["只使用本地引用"],
          conversationFocus: {activeTopic: {title: "语义检索"}},
          activeNote: {path: "20-Knowledge/Drafts/current.md"},
          activeSelectionReference: "selection:current",
          attachments: [{id: "attachment-1", displayName: "paper.pdf", sha256: "hash-1"}],
          sourcesRead: [{tool: "read_note_excerpt", observationReference: "obs-1"}],
          completedActions: [{id: "action-1", status: "completed"}],
          pendingActions: [], failedTools: [],
          activeWorkspace: {workspaceId: "workspace-1", workspaceIds: ["workspace-1"], projectPaths: ["project"]},
          taskBranch: {branchId: options.runId, parentRunId: "parent-run", forkedFromEntryId: "parent-entry"},
          taskAuthorization: {id: "auth-summary", networkPolicy: "deny", operationScope: []},
          currentLeafId: "entry-answer-3", branchId: options.runId,
          actionIds: ["action-1"], undoState: {actions: [{id: "action-1", available: true}]},
        },
        schemaVersion: 1,
      };
    },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { return {ok: true, isError: false, content: {state: "applied"}}; },
    async streamModelProxy(body, onEvent) { modelContexts.push(JSON.stringify(body.context?.messages ?? [])); emitText(onEvent, LONG); },
    saved: {},
    restoreProjection: false,
    async saveCompactionCheckpoint(body) {
      saveCalls += 1;
      this.saved[body.runId] = body;
      return {ok: true, checkpoint: {...body.entry, summary: body.summary, checkpointEntryId: `checkpoint-entry-${body.runId}`}};
    },
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
    assert.equal(entry.structuredState.currentLeafId, "entry-answer-3", "real persisted leaf captured");
    assert.equal(entry.structuredState.activeNote.path, "20-Knowledge/Drafts/current.md");
    assert.equal(entry.structuredState.attachments[0].id, "attachment-1");
    assert.equal(entry.structuredState.completedActions[0].id, "action-1");
    assert.equal(entry.structuredState.undoState.actions[0].available, true);
    assert.equal(entry.structuredState.taskBranch.forkedFromEntryId, "parent-entry");
    assert.ok(!entry.cutEntryId.startsWith("entry-1"), "cut id is a real projection Entry id, not a message index");
    // Restart reuses the entry
    const recovered = await transport.getCompactionCheckpoint(runId);
    assert.equal(recovered.entry.structuredState.branchId, runId);
    assert.equal(recovered.entry.tokensBefore, entry.tokensBefore);
    void before;
    runtime.cleanup();

    transport.restoreProjection = true;
    const restarted = new module.PiAgentRuntime(transport);
    for await (const _ of restarted.query(restarted.prepareTurn({message: "重启后继续", conversationId: "compact-src"}))) void _;
    assert.match(modelContexts.at(-1), /完整恢复目标/);
    assert.equal(saveCalls, 1, "restart reuses the durable checkpoint instead of generating another summary");
    restarted.cleanup();
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
    async expandTaskAuthorization() { return {taskAuthorization: {resourceScope: {allowAllRunCapabilities: false}}}; },
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
    let deferred = null;
    const queryDone = (async () => {
      for await (const chunk of runtime.query(runtime.prepareTurn({message: "整理资料", conversationId: "compact-pending"}))) {
        if (chunk.type === "confirmation_required") {
          pendingRunId = chunk.runId;
          // While a permission is pending the run is still active, so compaction
          // must refuse rather than split the in-flight context.
          deferred = await runtime.compact(chunk.runId);
          // Consume the async generator so confirm() code actually executes.
          for await (const _ of runtime.confirm(chunk.runId, true, undefined, undefined, "__all__")) void _;
        }
      }
    })();
    await queryDone;
    assert.ok(pendingRunId, "permission became pending");
    assert.ok(deferred, "compact returned a result while permission is pending");
    assert.equal(deferred.type, "notice");
    assert.equal(deferred.code, "context_compaction_deferred");
    const state = runtime.getConversationState();
    assert.ok(state, "conversation state exists after deferred compaction");
    assert.equal(state.status, "completed", "run completed normally after a deferred compaction");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("checkpoint persistence failure leaves the exact in-memory history intact", async () => {
  const {module, dispose} = await load("src/core/runtime/PiAgentRuntime.ts");
  const contexts = [];
  let modelCall = 0;
  const persistedMessages = [
    {role: "user", content: "first", metadata: {entryId: "entry-u1", entryStartId: "entry-u1", entryEndId: "entry-u1"}},
    {role: "assistant", content: [{type: "text", text: `ORIGINAL-SENTINEL-${LONG}`}], metadata: {entryId: "entry-a1", entryStartId: "entry-a1", entryEndId: "entry-a1"}},
    {role: "user", content: `second-${LONG}`, metadata: {entryId: "entry-u2", entryStartId: "entry-u2", entryEndId: "entry-u2"}},
    {role: "assistant", content: [{type: "text", text: LONG}], metadata: {entryId: "entry-a2", entryStartId: "entry-a2", entryEndId: "entry-a2"}},
    {role: "user", content: `third-${LONG}`, metadata: {entryId: "entry-u3", entryStartId: "entry-u3", entryEndId: "entry-u3"}},
    {role: "assistant", content: [{type: "text", text: LONG}], metadata: {entryId: "entry-a3", entryStartId: "entry-a3", entryEndId: "entry-a3"}},
  ];
  const transport = {
    async runtimeSessionProjection(sessionId, options = {}) {
      if (!options.runId) throw new Error("pi_session_not_found");
      return {
        sessionId, leafId: "entry-a3", branchId: options.runId, entries: [],
        messages: persistedMessages, focus: {}, attachments: [], activeActions: [], pending: null,
        compaction: null,
        compactionState: {goal: "keep history", currentLeafId: "entry-a3", branchId: options.runId},
        schemaVersion: 1,
      };
    },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(body, onEvent) {
      contexts.push(JSON.stringify(body.context?.messages ?? []));
      emitText(onEvent, modelCall++ === 0 ? `ORIGINAL-SENTINEL-${LONG}` : LONG);
    },
    async saveCompactionCheckpoint() { throw new Error("disk unavailable"); },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    for await (const _ of runtime.query(runtime.prepareTurn({message: "first", conversationId: "failure-src"}))) void _;
    const runId = runtime.getConversationState().runId;
    const result = await runtime.compact(runId, {contextWindow: 1000, keepRecent: 200});
    assert.equal(result.type, "notice");
    assert.equal(result.code, "context_compaction_skipped");
    assert.match(result.content, /disk unavailable/);
    for await (const _ of runtime.query(runtime.prepareTurn({message: "after failure", conversationId: "failure-src"}))) void _;
    assert.match(contexts[1], /ORIGINAL-SENTINEL/, "failed persistence must not replace Agent state");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("automatic threshold compaction commits the prior Run before the next turn", async () => {
  const {module, dispose} = await load("src/core/runtime/PiAgentRuntime.ts");
  const order = [];
  const transport = {
    async runtimeSessionProjection(sessionId, options = {}) {
      if (!options.runId) throw new Error("pi_session_not_found");
      return {
        sessionId, leafId: "entry-a3", branchId: options.runId, entries: [],
        messages: [
          {role: "user", content: LONG, metadata: {entryId: "entry-u1", entryStartId: "entry-u1", entryEndId: "entry-u1"}},
          {role: "assistant", content: [{type: "text", text: LONG}], metadata: {entryId: "entry-a1", entryStartId: "entry-a1", entryEndId: "entry-a1"}},
          {role: "user", content: LONG, metadata: {entryId: "entry-u2", entryStartId: "entry-u2", entryEndId: "entry-u2"}},
          {role: "assistant", content: [{type: "text", text: LONG}], metadata: {entryId: "entry-a2", entryStartId: "entry-a2", entryEndId: "entry-a2"}},
          {role: "user", content: LONG, metadata: {entryId: "entry-u3", entryStartId: "entry-u3", entryEndId: "entry-u3"}},
          {role: "assistant", content: [{type: "text", text: LONG}], metadata: {entryId: "entry-a3", entryStartId: "entry-a3", entryEndId: "entry-a3"}},
        ],
        focus: {}, attachments: [], activeActions: [], pending: null, compaction: null,
        compactionState: {goal: "auto", currentLeafId: "entry-a3", branchId: options.runId},
        schemaVersion: 1,
      };
    },
    async modelCapabilities() {
      return {profiles: [{id: "", capabilities: {contextWindow: 3000, maxOutputTokens: 500}}]};
    },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) {
      if (body.events.some(event => event.type === "context_compacted")) order.push("event");
      return {lastEventSequence: body.events.at(-1)?.sequence ?? 0};
    },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(_body, onEvent) { emitText(onEvent, "done"); },
    async saveCompactionCheckpoint(body) {
      order.push("checkpoint");
      return {ok: true, checkpoint: {...body.entry, summary: body.summary, checkpointEntryId: "checkpoint-auto"}};
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    for await (const _ of runtime.query(runtime.prepareTurn({message: "turn one", conversationId: "auto-src"}))) void _;
    for await (const _ of runtime.query(runtime.prepareTurn({message: "turn two", conversationId: "auto-src"}))) void _;
    assert.deepEqual(order, ["checkpoint", "event"]);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});
