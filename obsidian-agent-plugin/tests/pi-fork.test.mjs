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
  const transport = {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) { return {taskAuthorization: structuredClone(body.taskAuthorization)}; },
    async expandTaskAuthorization() { return {taskAuthorization: {}}; },
    async appendRuntimeEvents(body) { return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0}; },
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
    for await (const _ of runtime.query(runtime.prepareTurn({message: "first", conversationId: "src"}))) void _;
    // Source turn 2 introduces a message that must NOT leak into the fork
    for await (const _ of runtime.query(runtime.prepareTurn({message: "second", conversationId: "src"}))) void _;

    const srcRunId = runtime.getConversationState().runId;
    const forkState = await runtime.fork(srcRunId, 2);
    assert.equal(forkState.forkedFromSequence, 2);

    // Continue the fork: its model must see S1 but never FUTURE-SENTINEL
    for await (const _ of runtime.query(runtime.prepareTurn({message: "fork-continue", conversationId: forkState.conversationId}))) void _;
    // Resume the source: its model must still see FUTURE-SENTINEL (branch intact)
    for await (const _ of runtime.query(runtime.prepareTurn({message: "src-resume", conversationId: "src"}))) void _;

    const forkTurnContext = modelContexts[2];
    const srcResumeContext = modelContexts[3];
    assert.match(forkTurnContext, /S1/);
    assert.ok(!forkTurnContext.includes("FUTURE-SENTINEL"), "fork must not see the future");
    assert.ok(srcResumeContext.includes("FUTURE-SENTINEL"), "source branch must remain intact");
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
    const forkState = await runtime.fork(srcRunId, 1);
    assert.equal(forkState.forkedFromSequence, 1);
    // Continuing the fork registers its fresh authorization.
    for await (const _ of runtime.query(runtime.prepareTurn({message: "fork branch", conversationId: forkState.conversationId}))) void _;

    const forkAuth = authorized.at(-1);
    assert.equal(authorized.length, 2, "source and fork register separately");
    assert.notEqual(forkAuth.id, authorized[0].id, "fork must mint a new authorization");
    assert.equal(authorized[0].networkPolicy, "allow", "source had network authorization");
    assert.equal(forkAuth.resourceScope.allowAllRunCapabilities, false, "fork must not inherit allow_all");
    assert.equal(forkAuth.networkPolicy, "deny", "fork must not inherit network authorization");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});
