import assert from "node:assert/strict";
import test from "node:test";
import {build} from "esbuild";

async function bundled() {
  const result = await build({
    entryPoints: [new URL("../src/run-pi-assistant-turn.ts", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "es2022",
    logLevel: "silent",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

test("shared Pi turn preserves local learning context and reduces the stream", async () => {
  const {runPiAssistantTurn} = await bundled();
  let preparedRequest;
  let queriedTurn;
  const runtime = {
    prepareTurn(request) {
      preparedRequest = request;
      return {request, payload: {kind: "prepared"}};
    },
    async *query(turn) {
      queriedTurn = turn;
      yield {type: "text", runId: "run-learning", conversationId: "conversation-learning", sequence: 1, content: "局部解释"};
      yield {type: "text", runId: "run-learning", conversationId: "conversation-learning", sequence: 2, content: "已完成"};
      yield {type: "done", runId: "run-learning", conversationId: "conversation-learning", sequence: 3, status: "completed"};
    },
  };
  const references = Array.from({length: 25}, (_, index) => ({kind: "vault_note", path: `note-${index}.md`, title: `Note ${index}`}));
  const updates = [];
  for await (const update of runPiAssistantTurn({
    runtime,
    message: "解释当前小节",
    conversationId: "conversation-learning",
    currentNote: "20-Knowledge/Core/topic.md",
    selection: "当前公式",
    references,
    context: {surface: "study_assistant", lessonVersion: "v3"},
    options: {allow_network: false, permission_mode: "ask"},
  })) updates.push(update);

  assert.equal(queriedTurn.request, preparedRequest);
  assert.deepEqual(preparedRequest.activeNote, {path: "20-Knowledge/Core/topic.md", selection: "当前公式"});
  assert.deepEqual(preparedRequest.options, {allow_network: false, permission_mode: "ask"});
  assert.match(preparedRequest.message, /<zhixu_task_context>/);
  const taskContext = JSON.parse(preparedRequest.message.match(/<zhixu_task_context>\n(.+)\n<\/zhixu_task_context>/s)[1]);
  assert.equal(taskContext.context.surface, "study_assistant");
  assert.equal(taskContext.context.lessonVersion, "v3");
  assert.equal(taskContext.references.length, 20);
  assert.equal(updates.at(-1).run.content, "局部解释已完成");
  assert.equal(updates.at(-1).run.status, "completed");
});

test("shared Pi turn projects a real applied Action Result", async () => {
  const {runPiAssistantTurn} = await bundled();
  const action = {
    state: "applied",
    actionId: "action-study-note",
    undoAvailable: true,
    files: [{path: "20-Knowledge/Drafts/study-note.md"}],
  };
  const runtime = {
    prepareTurn(request) { return {request, payload: {}}; },
    async *query() {
      yield {type: "tool_use", runId: "run-write", conversationId: "conversation-write", sequence: 1, id: "call-apply", name: "apply_vault_change", input: {planId: "plan-1"}, status: "running"};
      yield {type: "tool_result", runId: "run-write", conversationId: "conversation-write", sequence: 2, id: "call-apply", name: "apply_vault_change", result: {result: action}, summary: "草稿已写入", status: "completed"};
      yield {type: "done", runId: "run-write", conversationId: "conversation-write", sequence: 3, status: "completed"};
    },
  };
  let finalRun;
  for await (const update of runPiAssistantTurn({runtime, message: "生成学习笔记"})) finalRun = update.run;
  const applied = finalRun.toolCalls.find(call => call.tool === "apply_vault_change");
  assert.equal(applied.status, "completed");
  assert.deepEqual(applied.result.result, action);
  assert.equal(finalRun.status, "completed");
});
