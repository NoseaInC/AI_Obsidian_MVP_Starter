import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadSessionTree() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "zhixu-sessiontree-"));
  const out = path.join(dir, "tree.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiSessionTree.ts")],
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

test("partial turn shows its assistant text in the tree", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "text", runId: "r1", turnId: "t1", content: "段落一。"},
    {type: "text", runId: "r1", turnId: "t1", content: "段落二。"},
  ]);
  assert.equal(tree.turns.length, 1);
  assert.equal(tree.turns[0].status, "partial");
  assert.equal(tree.turns[0].assistant, "段落一。段落二。");
  dispose();
});

test("blocked tool call is visible in the tree", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "text", runId: "r1", turnId: "t1", content: "x"},
    {type: "tool_call_start", runId: "r1", turnId: "t1", id: "call-1", name: "organize_vault_notes", args: {target: "20-Knowledge"}},
    {type: "tool_result", runId: "r1", turnId: "t1", id: "call-1", status: "blocked", summary: "需要授权"},
    {type: "done", runId: "r1", turnId: "t1", finishReason: "stop"},
  ]);
  assert.equal(tree.turns[0].toolCalls.length, 1);
  assert.equal(tree.turns[0].toolCalls[0].name, "organize_vault_notes");
  assert.equal(tree.turns[0].toolResults.length, 1);
  assert.equal(tree.turns[0].toolResults[0].status, "blocked");
  dispose();
});

test("aborted turn retains produced content", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "text", runId: "r1", turnId: "t1", content: "已产出部分。"},
    {type: "error", runId: "r1", turnId: "t1", status: "cancelled", code: "model_request_aborted"},
  ]);
  assert.equal(tree.turns[0].status, "cancelled");
  assert.equal(tree.turns[0].assistant, "已产出部分。");
  assert.equal(tree.turns[0].stopReason, "model_request_aborted");
  dispose();
});

test("reasoning blocks are projected", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "thinking_delta", runId: "r1", turnId: "t1", thinking: "让我先梳理。"},
    {type: "thinking_delta", runId: "r1", turnId: "t1", thinking: "再看结构。"},
    {type: "text", runId: "r1", turnId: "t1", content: "结论。"},
    {type: "done", runId: "r1", turnId: "t1", finishReason: "stop"},
  ]);
  assert.deepEqual(tree.turns[0].reasoning, ["让我先梳理。", "再看结构。"]);
  dispose();
});

test("usage is accumulated across the turn", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "usage", runId: "r1", turnId: "t1", usage: {promptTokens: 10, completionTokens: 5, totalTokens: 15}},
    {type: "usage", runId: "r1", turnId: "t1", usage: {promptTokens: 20, completionTokens: 8, totalTokens: 28}},
    {type: "done", runId: "r1", turnId: "t1", finishReason: "stop"},
  ]);
  assert.equal(tree.turns[0].usage.promptTokens, 30);
  assert.equal(tree.turns[0].usage.completionTokens, 13);
  assert.equal(tree.turns[0].usage.totalTokens, 43);
  dispose();
});

test("tree is serializable and free of undefined leakage", async () => {
  const {module: {projectSessionTree}, dispose} = await loadSessionTree();
  const tree = projectSessionTree([
    {type: "tool_call_start", runId: "r1", turnId: "t1", id: "call-1", name: "search", args: {q: "x"}},
    {type: "tool_result", runId: "r1", turnId: "t1", id: "call-1", status: "ok"},
    {type: "done", runId: "r1", turnId: "t1", finishReason: "stop"},
  ]);
  const round = JSON.parse(JSON.stringify(tree));
  assert.equal(round.serializable, true);
  assert.equal(typeof round.turns[0].toolResults[0].id, "string");
  // A tool result with no summary must not leak an explicit undefined key.
  assert.equal("summary" in round.turns[0].toolResults[0], false);
  dispose();
});
