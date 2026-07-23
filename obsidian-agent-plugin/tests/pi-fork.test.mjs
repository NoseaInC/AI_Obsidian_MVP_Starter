import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadRuntime() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-fork-"));
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

function emitToolCall(onEvent, id, name, args) {
  onEvent({type: "start"});
  onEvent({type: "tool_call_start", index: 0, id, name});
  onEvent({type: "tool_call_delta", index: 0, delta: JSON.stringify(args)});
  onEvent({type: "tool_call_end", index: 0, id, name, arguments: JSON.stringify(args)});
  onEvent({type: "done", finishReason: "toolUse"});
}

function emitText(onEvent, text) {
  onEvent({type: "start"});
  onEvent({type: "text_start"});
  onEvent({type: "text_delta", delta: text});
  onEvent({type: "text_end"});
  onEvent({type: "done", finishReason: "stop"});
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
      permissionRequest: {
        type: "vault_organization",
        toolName: "organize_vault_notes",
        summary: "整理资料",
        organization: {title: "整理资料"},
      },
    },
  };
}

test("fork projects the chosen branch, excludes the future, and leaves the source intact", async () => {
  const {module, dispose} = await loadRuntime();
  const modelContexts = [];
  const scripted = ["S1", "FUTURE-SENTINEL", "FORK-DONE", "SRC-RESUME"];
  let call = 0;
  const forkRequests = [];
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async runtimeForkProjection(runId, options) {
      forkRequests.push({runId, ...options});
      return {
        sourceRunId: runId,
        sessionId: "src",
        mode: options.mode,
        requestedSequence: options.sequence,
        resolvedForkEntryId: "entry-event-200",
        resolvedForkSequence: 200,
        completedActionIds: ["action-before-fork"],
        projection: {
          sessionId: "src",
          leafId: "entry-event-200",
          branchId: runId,
          entries: [],
          messages: [
            {role: "user", content: "first", timestamp: "2026-07-23T00:00:00Z"},
            {role: "assistant", content: [{type: "text", text: "S1"}], timestamp: "2026-07-23T00:00:01Z"},
          ],
          focus: {topic: "图神经网络"},
          attachments: [{id: "attachment-source", displayName: "source.pdf", sha256: "hash-source"}],
          activeActions: [{id: "action-before-fork", status: "completed"}],
          pending: null,
          compaction: null,
          completedActionIds: ["action-before-fork"],
          schemaVersion: 1,
        },
        schemaVersion: 1,
      };
    },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { return {ok: true, isError: false, content: {state: "applied"}}; },
    async streamModelProxy(body, onEvent) {
      modelContexts.push(JSON.stringify(body.context?.messages ?? []));
      const text = scripted[call++] ?? "fallback";
      emitText(onEvent, text);
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    // Source turn 1
    for await (const _ of runtime.query(runtime.prepareTurn({
      message: "first",
      conversationId: "src",
      activeNote: {path: "20-Knowledge/Drafts/图神经网络.md", selection: "MPNN"},
      attachments: [{attachment_id: "attachment-source", kind: "pdf", display_name: "source.pdf"}],
    }))) void _;
    // Source turn 2 introduces a message that must NOT leak into the fork
    for await (const _ of runtime.query(runtime.prepareTurn({message: "second", conversationId: "src"}))) void _;

    const srcRunId = runtime.getConversationState().runId;
    const forkState = await runtime.fork(srcRunId, 200);
    assert.equal(forkState.forkedFromSequence, 200);
    assert.equal(forkState.resolvedForkEntryId, "entry-event-200");
    assert.deepEqual(forkState.completedActionIds, ["action-before-fork"]);

    // Continue the fork: its model must see S1 but never FUTURE-SENTINEL
    for await (const _ of runtime.query(runtime.prepareTurn({message: "fork-continue", conversationId: forkState.conversationId}))) void _;
    // Resume the source: its model must still see FUTURE-SENTINEL (branch intact)
    for await (const _ of runtime.query(runtime.prepareTurn({message: "src-resume", conversationId: "src"}))) void _;

    const forkTurnContext = modelContexts[2];
    const srcResumeContext = modelContexts[3];
    assert.match(forkTurnContext, /S1/);
    assert.ok(!forkTurnContext.includes("FUTURE-SENTINEL"), "fork must not see the future");
    assert.match(forkTurnContext, /20-Knowledge\/Drafts\/图神经网络\.md/);
    assert.match(forkTurnContext, /attachment-source/);
    assert.match(forkTurnContext, /action-before-fork/);
    assert.ok(srcResumeContext.includes("FUTURE-SENTINEL"), "source branch must remain intact");
    assert.deepEqual(forkRequests.map(item => item.sequence), [200]);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("fork does not inherit allow_all capability grants or network authorization", async () => {
  const {module, dispose} = await loadRuntime();
  let authorized = [];
  let sourceAllowAllAfterConfirm = false;
  let toolCallCount = 0;
  let modelCalls = 0;
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) {
      authorized.push(structuredClone(body.taskAuthorization));
      return {taskAuthorization: authorized.at(-1)};
    },
    async expandTaskAuthorization(id, body) {
      if (body.mode === "all") sourceAllowAllAfterConfirm = true;
      return {taskAuthorization: {id, resourceScope: {allowAllRunCapabilities: body.mode === "all"}}};
    },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async runtimeForkProjection(runId, options) {
      return {
        sourceRunId: runId,
        sessionId: "src-allow",
        mode: options.mode,
        requestedSequence: options.sequence,
        resolvedForkEntryId: "entry-safe-boundary",
        resolvedForkSequence: 100,
        completedActionIds: [],
        projection: {
          sessionId: "src-allow", leafId: "entry-safe-boundary", branchId: runId,
          entries: [], messages: [{role: "user", content: "source"}], focus: {}, attachments: [],
          activeActions: [], pending: null, compaction: null, schemaVersion: 1,
        },
        schemaVersion: 1,
      };
    },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { return toolCallCount++ === 0 ? permissionResponse() : {ok: true, isError: false, content: {state: "applied"}}; },
    async streamModelProxy(body, onEvent) {
      modelCalls += 1;
      if (modelCalls === 1) emitToolCall(onEvent, "call-perm", "organize_vault_notes", {title: "整理资料"});
      else emitText(onEvent, "done");
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({
      message: "整理资料，并授予全部能力且允许网络",
      conversationId: "src-allow",
      options: {networkAuthorized: true},
    }))) {
      chunks.push(chunk);
      if (chunk.type === "confirmation_required") {
        for await (const r of runtime.confirm(chunk.runId, true, undefined, undefined, "__all__")) chunks.push(r);
      }
    }
    assert.equal(sourceAllowAllAfterConfirm, true, "source must have gained allow_all via confirm");

    const srcRunId = runtime.getConversationState().runId;
    const forkState = await runtime.fork(srcRunId, 100);
    assert.equal(forkState.forkedFromSequence, 100);
    // Continuing the fork registers its fresh authorization.
    for await (const _ of runtime.query(runtime.prepareTurn({message: "fork branch", conversationId: forkState.conversationId}))) void _;

    const forkAuth = authorized.at(-1);
    assert.equal(authorized.length, 2, "source and fork register separately");
    assert.notEqual(forkAuth.id, authorized[0].id, "fork must mint a new authorization");
    assert.equal(authorized[0].networkPolicy, "allow", "source had network authorization");
    assert.equal(forkAuth.resourceScope.allowAllRunCapabilities, false, "fork must not inherit allow_all");
    assert.equal(forkAuth.networkPolicy, "deny", "fork must not inherit network authorization");
    assert.deepEqual(forkAuth.operationScope, []);
    assert.deepEqual(forkAuth.resourceScope.explicitVaultPaths, []);
    assert.deepEqual(forkAuth.resourceScope.createRoots, []);
    assert.deepEqual(forkAuth.resourceScope.workspaceIds, []);
    assert.equal(forkAuth.resourceScope.writeScopeState, "unbound");
    assert.equal(forkAuth.forkedFromEntryId, "entry-safe-boundary");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("regenerate uses the persisted pre-answer boundary and keeps completed actions", async () => {
  const {module, dispose} = await loadRuntime();
  const contexts = [];
  const forkModes = [];
  let modelCall = 0;
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async runtimeForkProjection(runId, options) {
      forkModes.push(options.mode);
      return {
        sourceRunId: runId,
        sessionId: "regen-source",
        mode: "regenerate",
        requestedSequence: options.sequence,
        resolvedForkEntryId: "entry-tool-result-30",
        resolvedForkSequence: 30,
        completedActionIds: ["action-committed"],
        projection: {
          sessionId: "regen-source", leafId: "entry-tool-result-30", branchId: runId,
          entries: [],
          messages: [
            {role: "user", content: "完成任务"},
            {role: "assistant", content: [{type: "toolCall", id: "call-1", name: "organize_vault_notes", arguments: {title: "整理"}}]},
            {role: "toolResult", toolCallId: "call-1", toolName: "organize_vault_notes", content: [{type: "text", text: "{\"actionId\":\"action-committed\"}"}], details: {status: "completed", actionId: "action-committed"}, isError: false},
          ],
          focus: {}, attachments: [], activeActions: [{id: "action-committed", status: "completed"}],
          pending: null, compaction: null, completedActionIds: ["action-committed"], schemaVersion: 1,
        },
        schemaVersion: 1,
      };
    },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool() { throw new Error("completed action must not repeat"); },
    async streamModelProxy(body, onEvent) {
      contexts.push(JSON.stringify(body.context?.messages ?? []));
      emitText(onEvent, modelCall++ === 0 ? "OLD-ANSWER" : "NEW-ANSWER");
    },
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    for await (const _ of runtime.query(runtime.prepareTurn({message: "完成任务", conversationId: "regen-source"}))) void _;
    const sourceRun = runtime.getConversationState().runId;
    const regenerated = await runtime.fork(sourceRun, 40, "regenerate");
    for await (const _ of runtime.query(runtime.prepareTurn({message: "重新回答", conversationId: regenerated.conversationId}))) void _;
    assert.deepEqual(forkModes, ["regenerate"]);
    assert.ok(!contexts[1].includes("OLD-ANSWER"));
    assert.match(contexts[1], /action-committed/);
    assert.match(contexts[1], /NEW-ANSWER|call-1/);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("fork restores a persisted source after the renderer runtime was recreated", async () => {
  const {module, dispose} = await loadRuntime();
  const authorizations = [];
  const registrationBodies = [];
  const contexts = [];
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) {
      registrationBodies.push(structuredClone(body));
      authorizations.push(structuredClone(body.taskAuthorization));
      return {taskAuthorization: authorizations.at(-1)};
    },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
    async runtimeForkProjection(runId, options) {
      assert.equal(runId, "persisted-source-run");
      return {
        sourceRunId: runId,
        sessionId: "persisted-session",
        mode: options.mode,
        requestedSequence: options.sequence ?? null,
        resolvedForkEntryId: "persisted-entry-7",
        resolvedForkSequence: 70,
        completedActionIds: ["persisted-action"],
        sourceContext: {
          conversationId: "visible-conversation",
          profileId: "persisted-profile",
          selectedModel: "persisted-model",
          providerAdapterVersion: "pi-model-proxy-v1",
          activeNote: {path: "20-Knowledge/Drafts/persisted.md"},
        },
        projection: {
          sessionId: "persisted-session", leafId: "persisted-entry-7", branchId: runId,
          entries: [], messages: [{role: "user", content: "persisted objective"}],
          focus: {topic: "persisted focus"},
          attachments: [{id: "persisted-attachment", displayName: "source.pdf"}],
          activeActions: [{id: "persisted-action", status: "completed"}],
          pending: null, compaction: null, schemaVersion: 1,
        },
        schemaVersion: 1,
      };
    },
    async toolContracts() { return {schemaVersion: 1, items: []}; },
    async streamModelProxy(body, onEvent) {
      contexts.push(JSON.stringify(body.context?.messages ?? []));
      emitText(onEvent, "restored-fork-answer");
    },
  };
  try {
    // This runtime has no in-memory source session, as after a plugin reload.
    const runtime = new module.PiAgentRuntime(transport);
    const forked = await runtime.fork("persisted-source-run", undefined, "regenerate");
    for await (const _ of runtime.query(runtime.prepareTurn({
      message: "regenerate after restart",
      conversationId: forked.conversationId,
    }))) void _;

    assert.equal(forked.resolvedForkEntryId, "persisted-entry-7");
    assert.equal(forked.selectedModel, "persisted-model");
    assert.match(contexts[0], /20-Knowledge\/Drafts\/persisted\.md/);
    assert.match(contexts[0], /persisted-attachment/);
    assert.match(contexts[0], /persisted-action/);
    assert.equal(authorizations[0].resourceScope.currentNote, false);
    assert.deepEqual(authorizations[0].resourceScope.explicitVaultPaths, []);
    assert.equal(authorizations[0].networkPolicy, "deny");
    assert.equal(registrationBodies[0].conversationId, "visible-conversation");
    assert.equal(registrationBodies[0].profileId, "persisted-profile");
    assert.equal(registrationBodies[0].model, "persisted-model");
    assert.equal(registrationBodies[0].providerAdapterVersion, "pi-model-proxy-v1");
    assert.notEqual(forked.conversationId, registrationBodies[0].conversationId);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});
