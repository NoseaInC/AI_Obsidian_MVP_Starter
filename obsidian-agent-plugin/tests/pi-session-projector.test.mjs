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
  return {projectForkMessages: mod.projectForkMessages, dispose: () => rm(directory, {recursive: true, force: true})};
}

const loaded = await loadProjector();
const {projectForkMessages} = loaded;
const dispose = loaded.dispose;

function msg(role, extra = {}) {
  return {role, content: role === "toolResult" ? [{type: "text", text: "r"}] : "x", ...extra};
}

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
