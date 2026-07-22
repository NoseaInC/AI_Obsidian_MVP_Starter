import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function loadAdapter() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-tool-obs-"));
  const outfile = path.join(directory, "adapter.cjs");
  await esbuild.build({
    entryPoints: [path.resolve("src/core/runtime/pi/PiToolAdapter.ts")],
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

const contract = (max_result_bytes, name = "read_note_excerpt") => ({name, max_result_bytes});

test("70,000 CJK char result stays valid JSON, never a half string", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const content = {text: "中".repeat(70_000)};
  const out = adapter.serializeToolObservation(contract(64_000), {content});
  assert.ok(!out.includes("truncated by plugin"));
  const parsed = JSON.parse(out);
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, "tool_result_exceeds_transport_budget");
  assert.equal(parsed.status, "partial");
  assert.equal(typeof parsed.resultBytes, "number");
  assert.equal(parsed.maxResultBytes, 64_000);
  dispose();
});

test("long note preserves next_offset for model to continue reading", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const content = {
    content: "x".repeat(1000),
    offset: 0,
    next_offset: 12_000,
    total_chars: 50_000,
    truncated: true,
    path: "20-Knowledge/Concepts/x.md",
  };
  const out = adapter.serializeToolObservation(contract(200_000), {content});
  assert.ok(!out.includes("truncated by plugin"));
  const parsed = JSON.parse(out);
  assert.equal(parsed.offset, 0);
  assert.equal(parsed.next_offset, 12_000);
  assert.equal(parsed.total_chars, 50_000);
  assert.equal(parsed.truncated, true);
  assert.equal(parsed.path, "20-Knowledge/Concepts/x.md");
  dispose();
});

test("large array result is not cut in the middle", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const items = Array.from({length: 2000}, (_, i) => ({id: i, v: "y".repeat(50)}));
  const out = adapter.serializeToolObservation(contract(500_000), {content: {items}});
  const parsed = JSON.parse(out);
  assert.equal(parsed.items.length, 2000);
  assert.equal(parsed.items[1999].id, 1999);
  assert.equal(parsed.items[1999].v, "y".repeat(50));
  dispose();
});

test("result near contract upper bound passes intact", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  // 3 bytes per CJK; 33300 chars => 3*33300 + 11 surrounding chars = 99911 < 100000.
  const content = {text: "中".repeat(33_300)};
  const out = adapter.serializeToolObservation(contract(100_000), {content});
  assert.ok(!("code" in JSON.parse(out)) || JSON.parse(out).ok !== false);
  const parsed = JSON.parse(out);
  assert.equal(parsed.text.length, 33_300);
  assert.notEqual(parsed.code, "tool_result_exceeds_transport_budget");
  dispose();
});

test("oversized result returns recoverable partial observation, no crash", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const content = {text: "中".repeat(50_000)}; // ~150000 bytes > 100000
  const out = adapter.serializeToolObservation(contract(100_000), {content});
  assert.ok(!out.includes("truncated by plugin"));
  const parsed = JSON.parse(out);
  assert.equal(parsed.ok, false);
  assert.equal(parsed.status, "partial");
  assert.equal(parsed.code, "tool_result_exceeds_transport_budget");
  assert.equal(parsed.maxResultBytes, 100_000);
  assert.ok(parsed.continuation && typeof parsed.continuation === "object");
  dispose();
});

test("truncation marker string is gone from the source behavior", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const out = adapter.serializeToolObservation(contract(64_000), {content: {value: "正常结果"}});
  assert.ok(!out.includes("truncated by plugin"));
  assert.deepEqual(JSON.parse(out), {value: "正常结果"});
  dispose();
});

test("continuation extracts only real pagination fields (nextCursor)", async () => {
  const {module: adapter, dispose} = await loadAdapter();
  const content = {
    folders: Array.from({length: 5000}, (_, i) => `20-Knowledge/${i}`),
    nextCursor: "cursor-abc",
    truncated: true,
  };
  const out = adapter.serializeToolObservation(contract(1000), {content});
  const parsed = JSON.parse(out);
  assert.equal(parsed.code, "tool_result_exceeds_transport_budget");
  assert.equal(parsed.continuation.nextCursor, "cursor-abc");
  assert.equal(parsed.continuation.truncated, true);
  // Never fabricates a position that was not present.
  assert.equal(parsed.continuation.next_offset, undefined);
  dispose();
});
