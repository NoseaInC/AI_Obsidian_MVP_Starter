import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
import {readFile} from "node:fs/promises";

async function moduleUnderTest() {
  const result = await build({
    entryPoints: [new URL("../src/assistant-thread.ts", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node", target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

test("task thread exposes five human steps and hides technical failures by default", async () => {
  const mod = await moduleUnderTest();
  const task = mod.taskThreadFromRun({
    id: "run-1", status: "running", taskType: "runtime_task",
    created_at: "2026-07-14T10:00:00+08:00", updated_at: "2026-07-14T10:00:01+08:00",
  }, "conv-1", "msg-1", "PSM");
  assert.equal(task.steps.length, 5);
  assert.deepEqual(task.steps.map(item => item.label), [
    "检查已有知识", "检索可信来源", "生成适合当前水平的内容", "组织内容与练习", "生成可继续修改的成果",
  ]);
  const failure = mod.humanizeAssistantError("research_no_results", "research_no_results", true);
  assert.equal(failure.title, "本地资料中没有找到足够依据");
  assert.equal(failure.actions.length, 3);
  assert.equal(failure.actions[0].id, "trusted-research");
  const protectedWrite = mod.humanizeAssistantError("ValueError", "reviewed_core_read_only", true);
  assert.equal(protectedWrite.title, "正式知识受到保护");
  assert.equal(protectedWrite.actions[0].id, "retry");
  assert.equal(protectedWrite.technicalCode, "reviewed_core_read_only");
});

test("artifact grouping deduplicates results and keeps quiz inside one learning pack", async () => {
  const mod = await moduleUnderTest();
  const artifacts = [
    {id: "pack-1", type: "learning_pack", title: "PSM 入门学习包", status: "draft", version: 1, sourceRunId: "run-1", payload: {quizPreview: ["题目"]}},
    {id: "pack-duplicate", type: "learning_pack", title: "PSM 入门学习包", status: "draft", version: 1, sourceRunId: "run-1"},
    {id: "quiz-1", type: "quiz", title: "随堂小测", status: "draft", version: 1, sourceRunId: "run-1"},
  ];
  const groups = mod.groupAssistantArtifacts(artifacts, "conv-1", "task-1");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].primaryArtifactId, "pack-1");
  assert.equal(groups[0].artifacts.length, 1);
  assert.equal(mod.learningPackView(groups[0].artifacts[0]).quizCount, 1);
  assert.equal(mod.isTodayDuplicate({duplicate: true}), true);
});

test("backend artifact groups never absorb artifacts from older tasks", async () => {
  const mod = await moduleUnderTest();
  const artifacts = [
    {id: "current", type: "learning_pack", title: "当前成果", status: "draft"},
    {id: "old", type: "research_bundle", title: "旧成果", status: "draft"},
  ];
  const groups = mod.groupAssistantArtifacts(artifacts, "conv-1", "task-current", {
    id: "group-current", primaryArtifactId: "current", childArtifactIds: [], title: "当前成果",
  });
  assert.deepEqual(groups[0].artifacts.map(item => item.id), ["current"]);
});

test("a completed governed write is the primary assistant result", async () => {
  const mod = await moduleUnderTest();
  const artifacts = [
    {id: "proposal", type: "capture_proposal", title: "旧保存提案", status: "awaiting_confirmation", sourceRunId: "run-write"},
    {id: "plan", type: "organization_plan", title: "整理方案", status: "completed", sourceRunId: "run-write"},
    {id: "write", type: "write_result", title: "已写入 Delta Method", status: "completed", sourceRunId: "run-write", payload: {path: "20-Knowledge/Concepts/Delta Method.md", undoAvailable: true}},
  ];
  const groups = mod.groupAssistantArtifacts(artifacts, "conv-write", "task-write");
  assert.equal(groups[0].primaryArtifactId, "write");
});

test("assistant inspector derives real sources and pending change sets from conversation artifacts", async () => {
  const mod = await moduleUnderTest();
  const artifacts = [
    {id: "research", type: "research_bundle", title: "研究包", status: "draft", payload: {sources: [
      {id: "source-1", source_type: "local_vault", title: "Delta Method", metadata: {path: "20-Knowledge/Concepts/Delta Method.md"}},
    ]}},
    {id: "proposal", type: "capture_proposal", title: "整理提案", status: "awaiting_confirmation", payload: {
      change_set: {id: "brain-cs-1", title: "整理提案", preview: "1 个候选写入", writes: [{path: "01-Inbox/Delta.md", action: "create"}]},
      policy: {risk_level: "low"},
    }},
    {id: "change", type: "change_set", title: "整理提案", status: "awaiting_confirmation", payload: {changeSetId: "brain-cs-1", summary: "1 个候选写入"}},
  ];
  assert.deepEqual(mod.assistantInspectorSources(artifacts), [{
    id: "source-1", title: "Delta Method", kind: "vault_note", status: "local", path: "20-Knowledge/Concepts/Delta Method.md", url: undefined,
  }]);
  const changes = mod.assistantInspectorChanges(artifacts);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].changeSetId, "brain-cs-1");
  assert.equal(changes[0].path, "01-Inbox/Delta.md");
});

test("ordinary assistant visual contract is driven by Pi messages and Action Results", async () => {
  const views = await readFile(new URL("../src/views.ts", import.meta.url), "utf8");
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  for (const label of ["当前上下文", "相关笔记", "最近资料", "推荐动作", "当前理解", "最近对话信号"]) {
    assert.match(views, new RegExp(label));
  }
  assert.match(views, /runPiAssistantTurn/);
  assert.match(views, /buildAssistantMessageMetadata/);
  assert.match(views, /apply_vault_change/);
  assert.match(views, /undoAgentAction/);
  assert.match(css, /\.la-assistant-shell-v4/);
  assert.match(css, /grid-template-columns:\s*minmax\(560px, 1fr\) 300px/);
  for (const label of ["需要确认一个指代", "本会话不用于个性化", "Harness 校验并可撤销", "查看变化", "撤销"]) assert.match(views, new RegExp(label));
  assert.match(views, /openLinkText/);
  assert.match(views, /本地优先 · 低风险写入可撤销/);
  assert.doesNotMatch(views, /写入先审核/);
  assert.doesNotMatch(views, /latestMessage\?\.taskThreadId/);
  assert.doesNotMatch(views, /this\.renderAssistantTaskThread\(/);
  assert.doesNotMatch(views, /this\.renderAssistantArtifactGroup\(/);
});

test("assistant exposes private conversation controls and governed Vault actions", async () => {
  const views = await readFile(new URL("../src/views.ts", import.meta.url), "utf8");
  const main = await readFile(new URL("../main.ts", import.meta.url), "utf8");
  for (const route of ["/export", "/retain-summary", "?confirm=true"]) assert.match(views, new RegExp(route.replace(/[/?]/g, "\\$&")));
  for (const label of ["导出当前会话", "仅保留当前会话摘要", "删除当前会话"]) assert.match(views, new RegExp(label));
  for (const label of ["Vault 自治权限", "Agent 可以", "Agent 不会", "查看变化", "撤销", "清除本地对话"]) assert.match(main, new RegExp(label));
  assert.doesNotMatch(views + main, /window\.confirm|window\.prompt|window\.alert/);
});
