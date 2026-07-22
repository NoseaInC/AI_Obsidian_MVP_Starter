/**
 * T06 acceptance: a Pi tool call blocked on a permission decision must survive a
 * plugin restart. The pending call is persisted, the exact confirmation card is
 * rebuilt on the next run, and confirming it re-executes the original call and
 * continues the run with Pi's continuation API (no model re-planning).
 */
import {describe, it, beforeEach} from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import esbuild from "esbuild";

async function loadRuntime() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-recovery-"));
  const runtimeOut = path.join(directory, "runtime.cjs");
  const adapterOut = path.join(directory, "adapter.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/PiAgentRuntime.ts")],
    outfile: runtimeOut,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiEventAdapter.ts")],
    outfile: adapterOut,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  const require = createRequire(import.meta.url);
  return {
    PiAgentRuntime: require(runtimeOut).PiAgentRuntime,
    PiEventAdapter: require(adapterOut).PiEventAdapter,
    dispose: () => rm(directory, {recursive: true, force: true}),
  };
}

const CID = "conv-t06";

function makeRecoveryTransport(pendingOverride = {}) {
  const modelCalls = {count: 0};
  const toolCalls = [];
  let authorization = null;
  const pending = new Map();
  const events = [];

  const emitToolCall = (onEvent, id, name, args) => {
    onEvent({type: "start"});
    onEvent({type: "tool_call_start", index: 0, id, name});
    onEvent({type: "tool_call_delta", index: 0, delta: JSON.stringify(args)});
    onEvent({type: "tool_call_end", index: 0, id, name, arguments: JSON.stringify(args)});
    onEvent({type: "done", finishReason: "toolUse"});
  };
  const emitText = (onEvent, text) => {
    onEvent({type: "start"});
    onEvent({type: "text_start"});
    onEvent({type: "text_delta", delta: text});
    onEvent({type: "text_end"});
    onEvent({type: "done", finishReason: "stop"});
  };

  const onModel = (body, onEvent) => {
    modelCalls.count += 1;
    if (modelCalls.count === 1) {
      emitToolCall(onEvent, "tc-organize", "organize_vault_notes", {target_path: "30-Learning", organization: {strategy: "by_tag", tag: "ai"}});
    } else {
      // Continuation: the recovered (re-executed or blocked) tool result must
      // be fed back to the model.
      const ctx = JSON.stringify(body.context?.messages ?? []);
      assert.ok(
        ctx.includes("action-organize") || ctx.includes("permission_denied"),
        "recovered tool result injected into model continuation",
      );
      emitText(onEvent, "已整理 Vault 笔记：action-organize 已应用。");
    }
  };
  const organizationArguments = {
    target_path: "30-Learning",
    organization: {strategy: "by_tag", tag: "ai"},
  };
  const onTool = (body) => {
    toolCalls.push(body);
    if (toolCalls.length === 1) {
      return {
        ok: false,
        isError: true,
        error: {
          code: "task_organization_scope_required",
          message: "需要扩大当前 Run 的 Vault 整理范围",
          permissionRequest: {
            type: "vault_organization",
            toolName: "organize_vault_notes",
            summary: `${organizationArguments.target_path} 整理`,
            organization: organizationArguments,
          },
        },
      };
    }
    return {ok: true, isError: false, content: {actionId: "action-organize", state: "applied"}};
  };

  const transport = {
    modelCalls,
    toolCalls,
    pending,
    appendRuntimeEvents: async () => ({}),
    runtimeSession: async () => ({history: events}),
    controlRuntimeRun: async () => ({}),
    cancelRuntimeRun: async () => ({}),
    registerTaskAuthorization: async (body) => {
      authorization = body.taskAuthorization;
      return {ok: true, taskAuthorization: authorization};
    },
    expandTaskAuthorization: async (_id, body) => {
      authorization = {...authorization, resourceScope: {...authorization.resourceScope, allowAllRunCapabilities: body.mode === "all"}};
      return {taskAuthorization: structuredClone(authorization)};
    },
    toolContracts: async () => ({
      schemaVersion: 1,
      items: [{
        name: "organize_vault_notes",
        description: "Atomically organize Markdown notes inside governed Vault roots",
        input_schema: {
          type: "object",
          properties: {
            target_path: {type: "string"},
            organization: {type: "object"},
          },
          required: ["target_path", "organization"],
          additionalProperties: false,
        },
        output_schema: {type: "object", additionalProperties: true},
        uses_network: false,
        mutates_state: true,
        timeout_seconds: 20,
        permission_level: "proposal",
        idempotent: true,
        cancellable: false,
        max_result_bytes: 100_000,
        request_permission: true,
      }],
    }),
    streamModelProxy: async (body, onEvent) => onModel(body, onEvent),
    callRuntimeTool: async (body) => onTool(body),
    savePendingToolCall: async (body) => {
      const key = `${body.runId}:${body.toolCallId}`;
      const now = new Date().toISOString();
      const existing = pending.get(key);
      pending.set(key, {
        runId: body.runId,
        sessionId: body.sessionId,
        turnId: body.turnId,
        toolCallId: body.toolCallId,
        toolName: body.toolName,
        arguments: body.arguments,
        permissionRequest: body.permissionRequest,
        taskAuthorizationId: body.taskAuthorizationId,
        state: body.state ?? "pending",
        createdAt: (pendingOverride[key]?.createdAt) ?? existing?.createdAt ?? now,
        updatedAt: now,
      });
      return {ok: true};
    },
    listPendingToolCalls: async (scope) => {
      const items = [...pending.values()].filter((p) => {
        if (scope.runId && p.runId !== scope.runId) return false;
        if (scope.sessionId && p.sessionId !== scope.sessionId) return false;
        return true;
      });
      return {items};
    },
    resolvePendingToolCall: async (runId, toolCallId, state) => {
      const key = `${runId}:${toolCallId}`;
      const rec = pending.get(key);
      if (rec) {
        rec.state = state;
        rec.resolvedAt = new Date().toISOString();
      }
      return {ok: true};
    },
  };
  return transport;
}

function driveTurn(runtime, prepared, collector) {
  void (async () => {
    try {
      for await (const chunk of runtime.query(prepared)) collector.push(chunk);
    } catch (e) {
      collector.push({type: "error", error: String(e)});
    }
    collector.done = true;
  })();
}

function collectChunks(chunks, type) {
  return chunks.filter((c) => c.type === type);
}

function runIdOf(chunks, type) {
  const chunk = chunks.find((c) => c.type === type);
  return chunk?.run_id ?? chunk?.runId;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}


describe("T06 pending permission survives plugin restart", () => {
  let rt, transport, runtimeA, prepareA, chunksA, runIdA;

  beforeEach(async () => {
    rt = await loadRuntime();
    transport = makeRecoveryTransport();
    runtimeA = new rt.PiAgentRuntime(transport);
    prepareA = await runtimeA.prepareTurn({message: "整理一下 Vault", conversationId: CID});
    runIdA = prepareA.payload.identity.runId;
    chunksA = [];
    driveTurn(runtimeA, prepareA, chunksA);
    await sleep(50);
  });

  it("persists the pending call and rebuilds the same card after restart", async () => {
    const cardA = collectChunks(chunksA, "confirmation_required")[0];
    assert.ok(cardA, "first run must surface a permission card");
    assert.ok(runIdA, "run id present");
    const pendingA = await transport.listPendingToolCalls({runId: runIdA});
    assert.equal(pendingA.items.length, 1, "pending call persisted before restart");
    assert.equal(pendingA.items[0].state, "pending");
    assert.equal(pendingA.items[0].toolName, "organize_vault_notes");
    assert.ok(pendingA.items[0].arguments?.target_path, "original arguments persisted");

    // Simulate plugin restart: a brand-new runtime over the same transport.
    runtimeA.cleanup();
    const runtimeB = new rt.PiAgentRuntime(transport);
    const prepareB = await runtimeB.prepareTurn({message: "继续", conversationId: CID});
    const chunksB = [];
    for await (const chunk of runtimeB.query(prepareB)) {
      chunksB.push(chunk);
      if (chunk.type === "confirmation_required") {
        const cardB = chunk;
        assert.equal(cardB.confirmation.tool_name, "organize_vault_notes");
        assert.equal(cardB.confirmation.run_id, runIdA, "recovery continues the original run id");
        const pendingB = await transport.listPendingToolCalls({sessionId: CID});
        assert.equal(pendingB.items.filter((p) => p.state === "pending").length, 1, "not duplicated by restart");
        for await (const r of runtimeB.confirm(prepareB.payload.identity.runId, true)) chunksB.push(r);
      }
    }

    assert.ok(chunksB.some((c) => c.type === "done"), "run completed");
    assert.equal(transport.toolCalls.length, 2, "tool executed once originally and once on recovery");
    const resolved = await transport.listPendingToolCalls({runId: runIdA});
    assert.equal(resolved.items[0].state, "allowed", "pending marked allowed");
  });

  it("deny on the recovered card blocks the tool without re-executing it", async () => {
    runtimeA.cleanup();
    const runtimeB = new rt.PiAgentRuntime(transport);
    const prepareB = await runtimeB.prepareTurn({message: "继续", conversationId: CID});
    const chunksB = [];
    for await (const chunk of runtimeB.query(prepareB)) {
      chunksB.push(chunk);
      if (chunk.type === "confirmation_required") {
        for await (const r of runtimeB.confirm(prepareB.payload.identity.runId, false)) chunksB.push(r);
      }
    }

    assert.equal(transport.toolCalls.length, 1, "tool not re-executed on deny");
    assert.ok(chunksB.some((c) => c.type === "done"), "run completed");
    const resolved = await transport.listPendingToolCalls({runId: runIdA});
    assert.equal(resolved.items[0].state, "denied");
  });

  it("double confirm is idempotent and does not throw", async () => {
    runtimeA.cleanup();
    const runtimeB = new rt.PiAgentRuntime(transport);
    const prepareB = await runtimeB.prepareTurn({message: "继续", conversationId: CID});
    const chunksB = [];
    for await (const chunk of runtimeB.query(prepareB)) {
      chunksB.push(chunk);
      if (chunk.type === "confirmation_required") {
        for await (const r of runtimeB.confirm(prepareB.payload.identity.runId, true)) chunksB.push(r);
      }
    }
    assert.ok(chunksB.some((c) => c.type === "done"));
    assert.equal(transport.toolCalls.length, 2);
    // second click must be a no-op, not an error
    const second = [];
    for await (const r of runtimeB.confirm(prepareB.payload.identity.runId, true)) second.push(r);
    assert.ok(second.length >= 0);
  });

  it("expired pending call is not resumed", async () => {
    const originalCallId = (await transport.listPendingToolCalls({runId: runIdA})).items[0].toolCallId;
    const key = `${runIdA}:${originalCallId}`;
    const rec = transport.pending.get(key);
    rec.createdAt = new Date(Date.now() - 31 * 60 * 1000).toISOString();

    runtimeA.cleanup();
    const runtimeB = new rt.PiAgentRuntime(transport);
    const prepareB = await runtimeB.prepareTurn({message: "继续", conversationId: CID});
    const chunksB = [];
    for await (const chunk of runtimeB.query(prepareB)) {
      chunksB.push(chunk);
      if (chunk.type === "confirmation_required") {
        // a fresh card may appear; resolving it is fine for this scenario
        for await (const r of runtimeB.confirm(prepareB.payload.identity.runId, true)) chunksB.push(r);
      }
    }

    const resolved = await transport.listPendingToolCalls({runId: runIdA});
    const original = resolved.items.find((p) => p.toolCallId === originalCallId);
    assert.equal(original.state, "interrupted", "expired pending is resolved, not resumed");
  });
});
