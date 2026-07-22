import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadGuard() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "zhixu-stallguard-"));
  const out = path.join(dir, "guard.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiStallGuard.ts")],
    outfile: out,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  const require = createRequire(import.meta.url);
  return {module: require(out), dispose: () => rm(dir, {recursive: true, force: true})};
}

async function loadAdapter() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "zhixu-piadapter-"));
  const out = path.join(dir, "adapter.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiToolAdapter.ts")],
    outfile: out,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  const require = createRequire(import.meta.url);
  return {module: require(out), dispose: () => rm(dir, {recursive: true, force: true})};
}

const identity = {
  runId: "pi-run-1",
  turnId: "pi-turn-1",
  conversationId: "conversation-1",
  sourceMessageId: "msg-1",
  taskAuthorization: {id: "ta-1", networkPolicy: "deny"},
};

const readContract = {
  name: "read_only_tool",
  description: "read",
  input_schema: {type: "object", properties: {value: {type: "string"}}, required: ["value"], additionalProperties: false},
  uses_network: false,
  mutates_state: false,
  timeout_seconds: 5,
  permission_level: "read_only",
  idempotent: true,
  cancellable: false,
  max_result_bytes: 64_000,
};

const writeIdempotentContract = {
  name: "write_idempotent",
  description: "write idempotent",
  input_schema: {type: "object", properties: {value: {type: "string"}}, required: ["value"], additionalProperties: false},
  uses_network: false,
  mutates_state: true,
  timeout_seconds: 5,
  permission_level: "proposal",
  idempotent: true,
  cancellable: false,
  max_result_bytes: 64_000,
};

const writeNonIdempotentContract = {
  name: "write_non_idempotent",
  description: "write non-idempotent",
  input_schema: {type: "object", properties: {value: {type: "string"}}, required: ["value"], additionalProperties: false},
  uses_network: false,
  mutates_state: true,
  timeout_seconds: 5,
  permission_level: "proposal",
  idempotent: false,
  cancellable: false,
  max_result_bytes: 64_000,
};

const readArgs = {toolCallId: "c1", name: "read_only_tool", args: {value: "x"}, mutatesState: false, idempotent: true, permissionLevel: "read_only"};

test("read-only repeat reuses cached observation without re-execution", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  const r1 = g.beforeTool(readArgs);
  assert.ok(!r1.cached);
  g.remember(r1.key, {folders: ["20-Knowledge"]});
  const r2 = g.beforeTool({...readArgs, toolCallId: "c2"});
  assert.ok(r2.cached, "second read-only call should reuse cached observation");
  assert.equal(r2.duplicate, undefined);
  dispose();
});

test("non-idempotent mutating repeat is intercepted as duplicate", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  const a = {toolCallId: "c1", name: "write_non_idempotent", args: {value: "x"}, mutatesState: true, idempotent: false, permissionLevel: "proposal"};
  const r1 = g.beforeTool(a);
  assert.ok(!r1.cached && !r1.duplicate);
  g.remember(r1.key, {actionId: "a1"});
  const r2 = g.beforeTool({...a, toolCallId: "c2"});
  assert.ok(r2.duplicate, "equivalent repeat must be intercepted");
  assert.equal(r2.cached, undefined);
  const obs = g.duplicateObservation("write_non_idempotent");
  assert.equal(obs.code, "duplicate_non_idempotent_tool_call");
  assert.equal(obs.status, "blocked");
  dispose();
});

test("idempotent mutating tool always re-executes, never caches", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  const a = {toolCallId: "c1", name: "write_idempotent", args: {value: "x"}, mutatesState: true, idempotent: true, permissionLevel: "proposal"};
  const r1 = g.beforeTool(a);
  g.remember(r1.key, {actionId: "a1"});
  const r2 = g.beforeTool({...a, toolCallId: "c1"});
  assert.ok(!r2.cached && !r2.duplicate, "idempotent mutating must re-execute");
  dispose();
});

test("a new unique observation resets consecutive no-progress", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  const r1 = g.beforeTool(readArgs);
  g.remember(r1.key, {v: 1});
  assert.equal(g.progress.consecutiveNoProgress, 0);
  const c2 = g.beforeTool({...readArgs, toolCallId: "c2"});
  g.repeated(c2.cached);
  assert.equal(g.progress.consecutiveNoProgress, 1);
  const c3 = g.beforeTool({...readArgs, toolCallId: "c3"});
  const rep = g.repeated(c3.cached);
  assert.equal(rep.stallGuard, "no_new_information");
  assert.equal(g.progress.consecutiveNoProgress, 2);
  g.remember("other:key", {v: 2});
  assert.equal(g.progress.consecutiveNoProgress, 0);
  assert.equal(g.progress.uniqueObservationCount, 2);
  dispose();
});

test("reset clears budget and progress counters for a new Turn", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  g.beforeModelRequest();
  const r = g.beforeTool(readArgs);
  g.remember(r.key, {v: 1});
  g.reset();
  assert.equal(g.progress.modelRequests, 0);
  assert.equal(g.progress.toolCalls, 0);
  assert.equal(g.progress.consecutiveNoProgress, 0);
  assert.equal(g.progress.uniqueObservationCount, 0);
  dispose();
});

test("six consecutive no-progress events trigger safe termination", async () => {
  const {module: {PiStallGuard}, dispose} = await loadGuard();
  const g = new PiStallGuard();
  const r = g.beforeTool(readArgs);
  g.remember(r.key, {v: 1});
  const stages = [];
  for (let i = 0; i < 5; i++) {
    const c = g.beforeTool({...readArgs, toolCallId: `c${i + 2}`});
    stages.push(g.repeated(c.cached).stallGuard);
  }
  assert.deepEqual(stages, [
    "reused_existing_observation",
    "no_new_information",
    "no_new_information",
    "stall_replan_required",
    "stall_replan_required",
  ]);
  const c6 = g.beforeTool({...readArgs, toolCallId: "c7"});
  assert.throws(() => g.repeated(c6.cached), /stall_guard_safe_termination/);
  dispose();
});

test("read-only repeat does not re-invoke the backend through the adapter", async () => {
  const {module: {createPiTools}, dispose} = await loadAdapter();
  const {module: {PiStallGuard}} = await loadGuard();
  let calls = 0;
  const transport = {
    callRuntimeTool: async (body) => {
      calls += 1;
      return {ok: true, isError: false, content: {value: body.arguments.value}};
    },
  };
  const g = new PiStallGuard();
  const tools = createPiTools([readContract], identity, transport, g);
  const tool = tools.find((t) => t.name === "read_only_tool");
  await tool.execute("call-1", {value: "x"}, undefined);
  const r2 = await tool.execute("call-2", {value: "x"}, undefined);
  assert.equal(calls, 1);
  const parsed = JSON.parse(r2.content[0].text);
  assert.equal(parsed.stallGuard, "reused_existing_observation");
  assert.equal(parsed.value, "x");
  dispose();
});

test("non-idempotent write repeat is intercepted and does not hit backend", async () => {
  const {module: {createPiTools}, dispose} = await loadAdapter();
  const {module: {PiStallGuard}} = await loadGuard();
  let calls = 0;
  const transport = {
    callRuntimeTool: async () => {
      calls += 1;
      return {ok: true, isError: false, content: {actionId: "a1"}};
    },
  };
  const g = new PiStallGuard();
  const tools = createPiTools([writeNonIdempotentContract], identity, transport, g);
  const tool = tools.find((t) => t.name === "write_non_idempotent");
  await tool.execute("call-1", {value: "x"}, undefined);
  const r2 = await tool.execute("call-2", {value: "x"}, undefined);
  assert.equal(calls, 1);
  const parsed = JSON.parse(r2.content[0].text);
  assert.equal(parsed.code, "duplicate_non_idempotent_tool_call");
  assert.equal(parsed.status, "blocked");
  dispose();
});

test("idempotent write re-executes through the backend each time (includes authorization recovery)", async () => {
  const {module: {createPiTools}, dispose} = await loadAdapter();
  const {module: {PiStallGuard}} = await loadGuard();
  let calls = 0;
  const transport = {
    callRuntimeTool: async () => {
      calls += 1;
      return {ok: true, isError: false, content: {actionId: "a1"}};
    },
  };
  const g = new PiStallGuard();
  const tools = createPiTools([writeIdempotentContract], identity, transport, g);
  const tool = tools.find((t) => t.name === "write_idempotent");
  await tool.execute("call-1", {value: "x"}, undefined);
  await tool.execute("call-1", {value: "x"}, undefined);
  assert.equal(calls, 2);
  dispose();
});
