import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadRuntime() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-permission-e2e-"));
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

const organizationArguments = Object.freeze({
  title: "整理消息传递神经网络资料",
  moves: [{
    source_path: "01-Inbox/Research/MPNN-概览.md",
    target_path: "20-Knowledge/Drafts/图神经网络/资料/MPNN-概览.md",
  }],
  remove_empty_source_dirs: true,
});

const organizationContract = Object.freeze({
  name: "organize_vault_notes",
  description: "Atomically organize Markdown notes inside governed Vault roots",
  input_schema: {
    type: "object",
    properties: {
      title: {type: "string"},
      moves: {
        type: "array",
        items: {
          type: "object",
          properties: {
            source_path: {type: "string"},
            target_path: {type: "string"},
          },
          required: ["source_path", "target_path"],
          additionalProperties: false,
        },
      },
      remove_empty_source_dirs: {type: "boolean"},
    },
    required: ["title", "moves"],
    additionalProperties: false,
  },
  output_schema: {type: "object", additionalProperties: true},
  uses_network: false,
  mutates_state: true,
  timeout_seconds: 20,
  permission_level: "proposal",
  idempotent: true,
  cancellable: false,
  max_result_bytes: 64_000,
});

function emitToolCall(onEvent, id, name, args) {
  onEvent({type: "start"});
  onEvent({type: "tool_call_start", index: 0, id, name});
  onEvent({type: "tool_call_delta", index: 0, delta: JSON.stringify(args)});
  onEvent({
    type: "tool_call_end",
    index: 0,
    id,
    name,
    arguments: JSON.stringify(args),
  });
  onEvent({type: "done", finishReason: "toolUse"});
}

function emitText(onEvent, text) {
  onEvent({type: "start"});
  onEvent({type: "text_start"});
  onEvent({type: "text_delta", delta: text});
  onEvent({type: "text_end"});
  onEvent({type: "done", finishReason: "stop"});
}

function permissionResponse() {
  const move = organizationArguments.moves[0];
  return {
    ok: false,
    isError: true,
    error: {
      code: "task_organization_scope_required",
      message: "需要扩大当前 Run 的 Vault 整理范围",
      permissionRequest: {
        type: "vault_organization",
        toolName: "organize_vault_notes",
        summary: `${move.source_path} → ${move.target_path}`,
        organization: organizationArguments,
      },
    },
  };
}

function runtimeTransport({onExpanded, onTool, onModel}) {
  let authorization;
  return {
    async runtimeSession() { return {history: []}; },
    async registerTaskAuthorization(body) {
      authorization = structuredClone(body.taskAuthorization);
      return {taskAuthorization: authorization};
    },
    async expandTaskAuthorization(id, body) {
      assert.equal(id, authorization.id);
      await onExpanded?.(body, authorization);
      authorization.resourceScope = {
        ...authorization.resourceScope,
        allowAllRunCapabilities: body.mode === "all",
      };
      return {taskAuthorization: structuredClone(authorization)};
    },
    async appendRuntimeEvents(body) {
      return {runId: body.runId, lastEventSequence: body.events.at(-1)?.sequence ?? 0};
    },
    async toolContracts() { return {schemaVersion: 1, items: [organizationContract]}; },
    async callRuntimeTool(body) { return onTool(body); },
    async streamModelProxy(body, onEvent) { return onModel(body, onEvent); },
  };
}

test("permission card pauses and resumes the exact organize call in the same Run", async () => {
  const {module, dispose} = await loadRuntime();
  let modelCalls = 0;
  const toolCalls = [];
  const expansions = [];
  const transport = runtimeTransport({
    async onExpanded(body, authorization) {
      expansions.push(structuredClone(body));
      assert.equal(body.runId, authorization.runId);
      assert.equal(body.mode, "once");
      assert.equal(body.organization.moves.length, 1);
      assert.equal(body.organization.moves[0].source_path, organizationArguments.moves[0].source_path);
      assert.equal(body.organization.moves[0].target_path, organizationArguments.moves[0].target_path);
      assert.equal(body.organization.remove_empty_source_dirs, true);
    },
    onTool(body) {
      toolCalls.push(structuredClone(body));
      return toolCalls.length === 1
        ? permissionResponse()
        : {ok: true, isError: false, content: {actionId: "action-organize", state: "applied"}};
    },
    onModel(body, onEvent) {
      modelCalls += 1;
      if (modelCalls === 1) {
        emitToolCall(onEvent, "call-organize", "organize_vault_notes", organizationArguments);
      } else {
        assert.match(JSON.stringify(body.context.messages), /action-organize/);
        emitText(onEvent, "资料已整理到嵌套目录，事务验证通过。");
      }
    },
  });
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const turn = runtime.prepareTurn({message: "整理 MPNN 资料", conversationId: "conversation-permission"});
    const chunks = [];
    for await (const chunk of runtime.query(turn)) {
      chunks.push(chunk);
      if (chunk.type === "confirmation_required") {
        assert.equal(runtime.getConversationState().status, "waiting_confirmation");
        assert.equal(chunk.confirmation.tool_name, "organize_vault_notes");
        assert.match(chunk.confirmation.summary, /扩大当前 Run/);
        assert.equal(chunks.some(item => item.type === "done" || item.type === "error"), false);
        for await (const resolution of runtime.confirm(chunk.runId, true)) chunks.push(resolution);
      }
    }
    assert.equal(expansions.length, 1);
    assert.equal(toolCalls.length, 2);
    assert.equal(toolCalls[0].runId, toolCalls[1].runId);
    assert.equal(toolCalls[0].toolCallId, "call-organize");
    assert.equal(toolCalls[1].toolCallId, "call-organize");
    assert.equal(chunks.filter(item => item.type === "confirmation_required").length, 1);
    assert.equal(chunks.filter(item => item.type === "tool_result").at(-1)?.status, "completed");
    assert.equal(chunks.filter(item => item.type === "text").map(item => item.content).join(""), "资料已整理到嵌套目录，事务验证通过。");
    assert.equal(chunks.at(-1).type, "done");
    assert.equal(chunks.at(-1).status, "completed");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("denying inline permission returns a blocked Observation and lets the same Run finish", async () => {
  const {module, dispose} = await loadRuntime();
  let modelCalls = 0;
  let toolCalls = 0;
  let expansions = 0;
  const transport = runtimeTransport({
    onExpanded() { expansions += 1; },
    onTool() {
      toolCalls += 1;
      return permissionResponse();
    },
    onModel(body, onEvent) {
      modelCalls += 1;
      if (modelCalls === 1) {
        emitToolCall(onEvent, "call-denied", "organize_vault_notes", organizationArguments);
      } else {
        assert.match(JSON.stringify(body.context.messages), /user_denied_permission/);
        emitText(onEvent, "已取消整理，Vault 没有变化。");
      }
    },
  });
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({
      message: "整理资料，但先请求权限",
      conversationId: "conversation-denial",
    }))) {
      chunks.push(chunk);
      if (chunk.type === "confirmation_required") {
        for await (const resolution of runtime.confirm(chunk.runId, false)) chunks.push(resolution);
      }
    }
    assert.equal(expansions, 0);
    assert.equal(toolCalls, 1);
    assert.equal(modelCalls, 2);
    assert.equal(chunks.filter(item => item.type === "tool_result").at(-1)?.status, "blocked");
    assert.equal(chunks.filter(item => item.type === "text").map(item => item.content).join(""), "已取消整理，Vault 没有变化。");
    assert.equal(chunks.at(-1).type, "done");
    assert.equal(chunks.at(-1).status, "completed");
    runtime.cleanup();
  } finally {
    await dispose();
  }
});

test("allow-all expands safe capabilities without a card and remains scoped to one Run", async () => {
  const {module, dispose} = await loadRuntime();
  let modelCalls = 0;
  let toolCalls = 0;
  const expansions = [];
  const registered = [];
  const transport = runtimeTransport({
    onExpanded(body) { expansions.push(structuredClone(body)); },
    onTool() {
      toolCalls += 1;
      return toolCalls === 1
        ? permissionResponse()
        : {ok: true, content: {actionId: "action-auto-authorized"}};
    },
    onModel(_body, onEvent) {
      modelCalls += 1;
      if (modelCalls === 1) emitToolCall(onEvent, "call-auto", "organize_vault_notes", organizationArguments);
      else emitText(onEvent, "本 Run 内自动授权的安全整理已完成。");
    },
  });
  const originalRegister = transport.registerTaskAuthorization.bind(transport);
  transport.registerTaskAuthorization = async body => {
    registered.push(structuredClone(body.taskAuthorization));
    return originalRegister(body);
  };
  try {
    const runtime = new module.PiAgentRuntime(transport);
    const chunks = [];
    for await (const chunk of runtime.query(runtime.prepareTurn({
      message: "自动整理资料",
      conversationId: "conversation-allow-all",
      options: {permission_mode: "allow_all"},
    }))) chunks.push(chunk);
    assert.equal(registered[0].resourceScope.allowAllRunCapabilities, true);
    assert.equal(chunks.some(item => item.type === "confirmation_required"), false);
    assert.equal(expansions.length, 1);
    assert.equal(expansions[0].mode, "all");
    assert.equal(toolCalls, 2);
    assert.equal(chunks.at(-1).status, "completed");

    const next = runtime.prepareTurn({message: "新一轮", conversationId: "conversation-new-run"});
    assert.notEqual(next.payload.identity.runId, registered[0].runId);
    assert.notEqual(next.payload.identity.taskAuthorization.resourceScope.allowAllRunCapabilities, true);
    runtime.cleanup();
  } finally {
    await dispose();
  }
});
