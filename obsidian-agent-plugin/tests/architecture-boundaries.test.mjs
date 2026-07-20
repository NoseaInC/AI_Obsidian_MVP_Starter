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
const backendRoot = path.resolve("..", "agent");
const pythonFiles = directory => fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
  const target = path.join(directory, entry.name);
  if (entry.name === "__pycache__" || entry.name === "tests") return [];
  return entry.isDirectory() ? pythonFiles(target) : entry.isFile() && entry.name.endsWith(".py") ? [target] : [];
});

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

test("only provider-returned thinking crosses the runtime as a typed reasoning AgentChunk", () => {
  const read = relative => fs.readFileSync(path.resolve(relative), "utf8");
  const types = read("src/core/runtime/types.ts");
  const adapter = read("src/core/runtime/pi/PiEventAdapter.ts");
  const transport = read("src/core/runtime/pi/PiModelTransport.ts");
  const stream = read("src/assistant-stream.ts");
  assert.match(transport, /thinking_delta/);
  assert.match(adapter, /never synthesizes reasoning/);
  assert.match(adapter, /thinking_start/);
  assert.match(adapter, /thinking_delta/);
  assert.match(adapter, /thinking_end/);
  assert.match(types, /type:\s*"reasoning"/);
  assert.match(stream, /reasoning\.started/);
  assert.match(stream, /reasoning\.delta/);
  assert.match(stream, /reasoning\.completed/);
  assert.match(stream, /reasoningBlocks/);
});

test("main assistant no longer references the legacy coordinator", () => {
  const service = fs.readFileSync(path.resolve("..", "agent", "core", "service.py"), "utf8");
  assert.doesNotMatch(service, /AssistantRunCoordinator/);
  assert.doesNotMatch(service, /deterministic-tools/);
  const views = fs.readFileSync(path.join(root, "views.ts"), "utf8");
  assert.doesNotMatch(views, /renderBrainRun|\/brain\/|brain-change-sets/);
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

test("Pi runtime exposes durable lifecycle capabilities", () => {
  const runtime = fs.readFileSync(path.join(root, "core", "runtime", "PiAgentRuntime.ts"), "utf8");
  for (const capability of ["reconnect", "resume", "fork", "cancel", "compact", "regenerate"]) {
    assert.match(runtime, new RegExp(`${capability}: true`));
  }
  assert.match(runtime, /lastEventSequence/);
  assert.match(runtime, /checkpointId/);
  assert.match(runtime, /forkedFromSequence/);
});

test("Pi is the only production runtime registered by the main assistant", () => {
  const views = fs.readFileSync(path.join(root, "views.ts"), "utf8");
  assert.match(views, /register\("pi-agent"/);
  assert.match(views, /new PiAgentRuntime/);
  assert.doesNotMatch(views, /PydanticAgentRuntime/);
});

test("Python production boundary has no retired Agent loop or keyword intent router", () => {
  const sources = pythonFiles(backendRoot).map(file => fs.readFileSync(file, "utf8")).join("\n");
  for (const token of ["pydantic_ai", "AssistantRunCoordinator", "PydanticAgentRuntime", "BrainOrchestrator", "IntentRouter", "classify_intent", "INTENT_SCHEMA", "decide_assistant_outcome", "classify_assistant_intent", "SAVE_MARKERS", "_WRITE_TOKENS"]) {
    assert.doesNotMatch(sources, new RegExp(token));
  }
});

test("primary navigation has no standalone approval or review inbox", () => {
  const views = fs.readFileSync(path.join(root, "views.ts"), "utf8");
  assert.match(views, /type MainTab = "today" \| "sources" \| "plan" \| "assistant"/);
  assert.doesNotMatch(views, /this\.navStat\(stats, "需要你确认"/);
});
