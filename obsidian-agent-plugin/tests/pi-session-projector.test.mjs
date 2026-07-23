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
    projectForkMessages: mod.projectForkMessages,
    projectPiSessionMessages: mod.projectPiSessionMessages,
    dispose: () => rm(directory, {recursive: true, force: true}),
  };
}

const loaded = await loadProjector();
const {projectForkMessages, projectPiSessionMessages} = loaded;
const dispose = loaded.dispose;

function msg(role, extra = {}) {
  return {role, content: role === "toolResult" ? [{type: "text", text: "r"}] : "x", ...extra};
}

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
  assert.equal(restored[2].role, "compactionSummary");
  assert.match(restored[2].summary, /continue/);
});

test("fork without sequence keeps the entire transcript", () => {
  const messages = [msg("user"), msg("assistant"), msg("user"), msg("assistant")];
  const out = projectForkMessages(messages, undefined, "fork");
  assert.equal(out.length, 4);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user", "assistant"]);
});

test("fork before a future turn excludes later messages", () => {
  const messages = [msg("user"), msg("assistant"), msg("user"), msg("assistant", {future: true})];
  const out = projectForkMessages(messages, 3, "fork");
  assert.equal(out.length, 3);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user"]);
  assert.ok(!out.some(m => m.future));
});

test("fork on an assistant tool call drops the orphaned call, never splitting the pair", () => {
  const messages = [
    msg("user"),
    msg("assistant"),
    msg("user"),
    msg("assistant", {toolCall: {id: "c1", name: "t", arguments: {}}}),
    msg("toolResult"),
    msg("assistant", {future: true}),
  ];
  const out = projectForkMessages(messages, 4, "fork");
  assert.equal(out.length, 3);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user"]);
  assert.ok(!out.some(m => m.future));
});

test("fork after a tool result keeps the complete tool pair", () => {
  const messages = [
    msg("user"),
    msg("assistant"),
    msg("user"),
    msg("assistant", {toolCall: {id: "c1", name: "t", arguments: {}}}),
    msg("toolResult"),
    msg("assistant", {future: true}),
  ];
  const out = projectForkMessages(messages, 5, "fork");
  assert.equal(out.length, 5);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user", "assistant", "toolResult"]);
  assert.ok(!out.some(m => m.future));
});

test("regenerate at the final answer hides the old answer", () => {
  const messages = [
    msg("user"),
    msg("assistant"),
    msg("user"),
    msg("assistant", {toolCall: {id: "c1", name: "t", arguments: {}}}),
    msg("toolResult"),
    msg("assistant", {oldAnswer: true}),
  ];
  const out = projectForkMessages(messages, 6, "regenerate");
  assert.equal(out.length, 5);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user", "assistant", "toolResult"]);
  assert.ok(!out.some(m => m.oldAnswer));
});

test("regenerate at a tool call keeps the pair up to the tool result", () => {
  const messages = [
    msg("user"),
    msg("assistant"),
    msg("user"),
    msg("assistant", {toolCall: {id: "c1", name: "t", arguments: {}}}),
    msg("toolResult"),
    msg("assistant", {oldAnswer: true}),
  ];
  const out = projectForkMessages(messages, 4, "regenerate");
  assert.equal(out.length, 3);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant", "user"]);
});

test("fork on an ordinary answer keeps that answer", () => {
  const messages = [msg("user", {content: "u1"}), msg("assistant", {content: "a1"}), msg("user", {future: true})];
  const out = projectForkMessages(messages, 2, "fork");
  assert.equal(out.length, 2);
  assert.equal(out[1].content, "a1");
});

process.on("exit", () => dispose().catch(() => {}));
