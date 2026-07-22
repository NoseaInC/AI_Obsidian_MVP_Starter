import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadTransport() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "zhixu-pimodel-"));
  const out = path.join(dir, "model.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiModelTransport.ts")],
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
  profileId: "p1",
  model: "m1",
  sessionId: "s1",
  runId: "r1",
  conversationId: "c1",
  turnId: "t1",
  sourceMessageId: "m1",
  taskAuthorization: {id: "ta1", networkPolicy: "deny"},
};
const model = {
  id: "m1",
  name: "m1",
  api: "openai-completions",
  provider: "openai",
  baseUrl: "",
  reasoning: false,
  input: ["text"],
  cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0},
  contextWindow: 128_000,
  maxTokens: 3000,
};
const context = [];

const TEST_TIMEOUTS = {firstEventMs: 50, idleMs: 100, idleDeepMs: 300, hardMs: 500, hardDeepMs: 1500};

function delay(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(t);
      resolve();
    }, {once: true});
  });
}

function waitAbort(signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    signal.addEventListener("abort", () => resolve(), {once: true});
  });
}

async function collect(stream, timeoutMs = 3000) {
  const events = [];
  const iter = (async () => {
    for await (const ev of stream) {
      events.push(ev);
      if (ev.type === "done" || ev.type === "error") break;
    }
  })();
  await Promise.race([iter, new Promise((_, reject) => setTimeout(() => reject(new Error("collect timeout")), timeoutMs))]);
  return events;
}

function terminalCode(events) {
  const terminal = events.find((e) => e.type === "done" || e.type === "error");
  return terminal ? terminal.code : undefined;
}

test("no first packet triggers model_first_event_timeout and one terminal", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {streamModelProxy: async (_req, _emit, signal) => { await waitAbort(signal); }};
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(terminalCode(events), "model_first_event_timeout");
  assert.equal(events.filter((e) => e.type === "error").length, 1);
  dispose();
});

test("stall after first packet triggers model_idle_timeout", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {streamModelProxy: async (_req, emit, signal) => { emit({type: "start"}); await waitAbort(signal); }};
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(terminalCode(events), "model_idle_timeout");
  dispose();
});

test("continuous deltas within idle budget complete without timeout", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "start"});
      for (let i = 0; i < 5; i += 1) {
        await delay(30, signal);
        if (signal.aborted) return;
        emit({type: "text_delta", delta: "a"});
      }
      await delay(30, signal);
      if (signal.aborted) return;
      emit({type: "done", finishReason: "stop"});
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(events.find((e) => e.type === "error"), undefined);
  assert.equal(terminalCode(events), undefined);
  assert.ok(events.some((e) => e.type === "done"));
  dispose();
});

test("exceeding hard deadline triggers model_request_deadline_exceeded", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "start"});
      let i = 0;
      while (!signal.aborted) {
        await delay(40, signal);
        if (signal.aborted) return;
        emit({type: "text_delta", delta: String(i += 1)});
      }
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(terminalCode(events), "model_request_deadline_exceeded");
  dispose();
});

test("done emits exactly one terminal and no secondary error", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "start"});
      await delay(20, signal);
      emit({type: "text_delta", delta: "hi"});
      await delay(20, signal);
      emit({type: "done", finishReason: "stop"});
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(events.filter((e) => e.type === "done" || e.type === "error").length, 1);
  assert.equal(events.find((e) => e.type === "error"), undefined);
  dispose();
});

test("user abort triggers model_request_aborted and one terminal", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {streamModelProxy: async (_req, emit, signal) => { emit({type: "start"}); await waitAbort(signal); }};
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const userAbort = new AbortController();
  const stream = t.stream(identity, model, context, {signal: userAbort.signal}, undefined);
  const collected = collect(stream);
  await delay(30, userAbort.signal);
  userAbort.abort();
  const events = await collected;
  assert.equal(terminalCode(events), "model_request_aborted");
  dispose();
});

test("thinking stall triggers model_idle_timeout", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {streamModelProxy: async (_req, emit, signal) => { emit({type: "thinking_start"}); await waitAbort(signal); }};
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(terminalCode(events), "model_idle_timeout");
  dispose();
});

test("tool call deltas reset idle and only time out after the gap", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "tool_call_start", index: 0, id: "t1", name: "x"});
      for (let i = 0; i < 5; i += 1) {
        await delay(30, signal);
        if (signal.aborted) return;
        emit({type: "tool_call_delta", index: 0, delta: "a"});
      }
      await waitAbort(signal);
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {}, undefined));
  assert.equal(terminalCode(events), "model_idle_timeout");
  dispose();
});

test("deep-thinking mode uses the larger idle budget", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  // 200ms gap would exceed normal idle (100ms) but is within deep idle (300ms).
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "thinking_start"});
      await delay(200, signal);
      if (signal.aborted) return;
      emit({type: "done", finishReason: "stop"});
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {reasoning: true}, undefined));
  assert.equal(events.find((e) => e.type === "error"), undefined);
  assert.ok(events.some((e) => e.type === "done"));
  dispose();
});

test("normal mode times out on the same 200ms thinking gap", async () => {
  const {module: {PiModelTransport}, dispose} = await loadTransport();
  const transport = {
    streamModelProxy: async (_req, emit, signal) => {
      emit({type: "thinking_start"});
      await delay(200, signal);
      if (signal.aborted) return;
      emit({type: "done", finishReason: "stop"});
    },
  };
  const t = new PiModelTransport(transport, TEST_TIMEOUTS);
  const events = await collect(t.stream(identity, model, context, {reasoning: false}, undefined));
  assert.equal(terminalCode(events), "model_idle_timeout");
  dispose();
});
