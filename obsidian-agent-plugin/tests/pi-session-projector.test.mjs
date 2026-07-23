import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadProjector() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-projector-"));
  const outfile = path.join(directory, "projector.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiSessionProjector.ts")],
    outfile,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  const require = createRequire(import.meta.url);
  const mod = require(outfile);
  return {
    projectPiSessionMessages: mod.projectPiSessionMessages,
    dispose: () => rm(directory, {recursive: true, force: true}),
  };
}

const loaded = await loadProjector();
const {projectPiSessionMessages} = loaded;
const dispose = loaded.dispose;

function projection(messages, extra = {}) {
  return {
    sessionId: "s1", leafId: "e9", branchId: "r1", entries: [], messages,
    focus: {}, attachments: [], activeActions: [], pending: null, compaction: null,
    schemaVersion: 1, ...extra,
  };
}

test("persisted User → Tool Call → Tool Result → Assistant restores exact Pi shapes", () => {
  const restored = projectPiSessionMessages(projection([
    {role: "user", content: "查找笔记", timestamp: "2026-07-23T00:00:00Z"},
    {role: "assistant", content: [
      {type: "text", text: "先搜索。"},
      {type: "toolCall", id: "call-1", name: "search_vault", arguments: {query: "因果"}},
    ], timestamp: "2026-07-23T00:00:01Z"},
    {role: "toolResult", toolCallId: "call-1", toolName: "search_vault", content: [{type: "text", text: "{\"ok\":true}"}], details: {status: "completed", actionId: "a1"}, isError: false, timestamp: "2026-07-23T00:00:02Z"},
    {role: "assistant", content: [{type: "text", text: "完整回答"}], timestamp: "2026-07-23T00:00:03Z"},
  ]));
  assert.deepEqual(restored.map(item => item.role), ["user", "assistant", "toolResult", "assistant"]);
  assert.deepEqual(restored[1].content[1], {type: "toolCall", id: "call-1", name: "search_vault", arguments: {query: "因果"}});
  assert.equal(restored[2].toolCallId, restored[1].content[1].id);
  assert.equal(restored[2].toolName, restored[1].content[1].name);
  assert.equal(restored[2].details.actionId, "a1");
  assert.equal(restored[3].content[0].text, "完整回答");
});

test("blocked and failed results survive with their typed status", () => {
  const restored = projectPiSessionMessages(projection([
    {role: "assistant", content: [
      {type: "toolCall", id: "blocked", name: "write_note", arguments: {}},
      {type: "toolCall", id: "failed", name: "search_vault", arguments: {}},
    ]},
    {role: "toolResult", toolCallId: "blocked", toolName: "write_note", content: [{type: "text", text: "blocked"}], details: {status: "blocked", code: "user_denied_permission"}, isError: false},
    {role: "toolResult", toolCallId: "failed", toolName: "search_vault", content: [{type: "text", text: "failed"}], details: {status: "failed", code: "search_failed"}, isError: true},
  ]));
  assert.equal(restored[1].details.status, "blocked");
  assert.equal(restored[1].isError, false);
  assert.equal(restored[2].details.status, "failed");
  assert.equal(restored[2].isError, true);
});

test("orphaned calls become interrupted results and orphaned results are dropped", () => {
  const restored = projectPiSessionMessages(projection([
    {role: "toolResult", toolCallId: "no-call", toolName: "x", content: [{type: "text", text: "unsafe"}]},
    {role: "assistant", content: [{type: "toolCall", id: "orphan", name: "search_vault", arguments: {query: "x"}}]},
  ]));
  assert.deepEqual(restored.map(item => item.role), ["assistant", "toolResult"]);
  assert.equal(restored[1].toolCallId, "orphan");
  assert.equal(restored[1].details.code, "tool_call_interrupted_by_restart");
});

test("a persisted pending record keeps its exact Tool Call unresolved for R04 recovery", () => {
  const restored = projectPiSessionMessages(projection([
    {role: "assistant", content: [{type: "toolCall", id: "pending-1", name: "organize_vault_notes", arguments: {moves: []}}]},
  ], {
    pending: {toolCallId: "pending-1", toolName: "organize_vault_notes", state: "pending"},
  }));
  assert.deepEqual(restored.map(item => item.role), ["assistant"]);
  assert.deepEqual(restored[0].content[0], {
    type: "toolCall",
    id: "pending-1",
    name: "organize_vault_notes",
    arguments: {moves: []},
  });
});

test("reasoning blocks never enter restored context; controls and checkpoints do", () => {
  const restored = projectPiSessionMessages(projection([
    {role: "assistant", content: [
      {type: "thinking", thinking: "private chain"},
      {type: "text", text: "public answer"},
    ]},
    {role: "custom", customType: "steering", content: "只看主线", display: true},
  ], {
    compaction: {tokensBefore: 100, structuredState: {goal: "continue"}, createdAt: "2026-07-23T00:00:00Z"},
  }));
  assert.doesNotMatch(JSON.stringify(restored), /private chain/);
  assert.equal(restored[0].content[0].text, "public answer");
  assert.equal(restored[1].customType, "steering");
  assert.equal(restored[2].role, "assistant");
  assert.match(restored[2].content[0].text, /zhixu_runtime_checkpoint/);
  assert.match(restored[2].content[0].text, /continue/);
});

process.on("exit", () => dispose().catch(() => {}));
