import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve("src");
const files = directory => fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
  const target = path.join(directory, entry.name);
  return entry.isDirectory() ? files(target) : entry.isFile() && entry.name.endsWith(".ts") ? [target] : [];
});
const matching = (directory, pattern) => files(directory).filter(file => pattern.test(fs.readFileSync(file, "utf8"))).map(file => path.relative(process.cwd(), file));

test("core runtime is independent from feature and DOM layers", () => {
  assert.deepEqual(matching(path.join(root, "core"), /from\s+["'][^"']*features\//), []);
  assert.deepEqual(matching(path.join(root, "core", "runtime"), /\b(?:HTMLElement|document\.|window\.)/), []);
});

test("frontend contains no Python runtime imports or PydanticAI implementation", () => {
  assert.deepEqual(matching(root, /(?:from\s+["'][^"']*\.py["']|\bpydantic_ai\b)/), []);
});

test("tool trace renderer consumes only normalized AgentChunk contracts", () => {
  const renderer = fs.readFileSync(path.join(root, "features", "chat", "ToolTraceRenderer.ts"), "utf8");
  assert.match(renderer, /AgentChunk/);
  assert.doesNotMatch(renderer, /AssistantStreamEvent|PydanticAgentRuntime|AgentClient/);
});

test("main assistant no longer references the legacy coordinator", () => {
  const service = fs.readFileSync(path.resolve("..", "agent", "core", "service.py"), "utf8");
  assert.doesNotMatch(service, /AssistantRunCoordinator/);
  assert.doesNotMatch(service, /deterministic-tools/);
});

test("provider and routing writes pass through the unified SettingsService", () => {
  const views = fs.readFileSync(path.join(root, "views.ts"), "utf8");
  const settings = fs.readFileSync(path.join(root, "core", "settings", "SettingsService.ts"), "utf8");
  assert.doesNotMatch(views, /client\.(?:post|patch|delete)\([^\n]*(?:model-profiles|model-routing)/);
  assert.match(settings, /class SettingsService/);
  assert.match(settings, /updateModelRoute/);
  assert.match(settings, /saveModelProfile/);
});

test("inline edit is a registered CodeMirror snapshot-checked decoration", () => {
  const controller = fs.readFileSync(path.join(root, "features", "inline-edit", "InlineEditController.ts"), "utf8");
  const main = fs.readFileSync(path.resolve("main.ts"), "utf8");
  for (const primitive of ["StateEffect", "StateField", "Decoration", "WidgetType"]) assert.match(controller, new RegExp(primitive));
  assert.match(controller, /documentSnapshot/);
  assert.match(controller, /selectedText/);
  assert.match(controller, /原文已变化，请重新生成。/);
  assert.match(main, /registerEditorExtension\(inlineEditExtension\)/);
});

test("Pydantic runtime exposes durable lifecycle capabilities", () => {
  const runtime = fs.readFileSync(path.join(root, "core", "runtime", "PydanticAgentRuntime.ts"), "utf8");
  for (const capability of ["reconnect", "resume", "fork", "cancel", "compact", "regenerate"]) {
    assert.match(runtime, new RegExp(`${capability}: true`));
  }
  assert.match(runtime, /lastEventSequence/);
  assert.match(runtime, /checkpointId/);
  assert.match(runtime, /forkedFromSequence/);
});
