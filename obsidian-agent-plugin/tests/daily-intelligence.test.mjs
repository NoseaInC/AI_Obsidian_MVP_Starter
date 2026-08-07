import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";

async function moduleUnderTest() {
  const result = await build({
    entryPoints: [new URL("../src/daily-intelligence.ts", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node", target: "es2022",
  });
  const code = result.outputFiles[0].text;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

const base = {
  reasonDetails: [], prerequisites: [], relatedNotes: [], microConcepts: [], domain: "因果推断",
  route: "mainline", actions: [], reason: "连接当前路线", estimatedMinutes: 8, score: 50,
};

test("daily ranking is deterministic, due-first and enforces weekday knowledge quota", async () => {
  const mod = await moduleUnderTest();
  const profile = {schemaVersion: 1, eventCount: 0, coveredDays: 0, behaviorWeight: 0, features: []};
  const candidates = [
    {...base, id: "due", title: "到期复习", kind: "review", dueState: "overdue", mastery: 2},
    {...base, id: "new-1", title: "新知识一", kind: "explore", candidate: true, verificationGrade: "A", confidence: .92},
    {...base, id: "new-2", title: "新知识二", kind: "explore", candidate: true, verificationGrade: "B", confidence: .8},
    {...base, id: "c", title: "未验证内容", kind: "explore", candidate: true, verificationGrade: "C"},
  ];
  const context = {date: "2026-07-14", budgetMinutes: 25, learnerProfile: profile};
  const first = mod.rankDailyRecommendations(candidates, context);
  const second = mod.rankDailyRecommendations(candidates, context);
  assert.deepEqual(first.map(item => item.id), second.map(item => item.id));
  assert.equal(first[0].id, "due");
  assert.equal(first.filter(item => mod.categoryOf(item) === "daily-knowledge").length, 1);
  assert.equal(mod.categoryOf(candidates[3]), "explore");
});

test("cold start behavior maturity and explicit preference priority stay bounded", async () => {
  const mod = await moduleUnderTest();
  assert.equal(mod.behaviorMaturity(0, 0), 0);
  assert.ok(mod.behaviorMaturity(19, 2) < 0.5);
  assert.ok(mod.behaviorMaturity(80, 3) < 0.7);
  assert.ok(mod.behaviorMaturity(101, 7) > 0.7);
  const profile = {schemaVersion: 1, eventCount: 50, coveredDays: 3, behaviorWeight: .08, features: [
    {key: "domain_interest", scope: "因果推断", value: 0, confidence: .8, evidenceCount: 50, windowStart: "2026-07-01", windowEnd: "2026-07-14", updatedAt: "2026-07-14", source: "inferred"},
    {key: "domain_interest", scope: "因果推断", value: 100, confidence: 1, evidenceCount: 1, windowStart: "2026-07-14", windowEnd: "2026-07-14", updatedAt: "2026-07-14", source: "explicit"},
  ]};
  const score = mod.scoreRecommendation({...base, id: "x", title: "显式偏好", kind: "learn"}, {date: "2026-07-14", budgetMinutes: 25, learnerProfile: profile});
  assert.ok(score > 50);
});

test("goal alignment contributes a fixed twelve-percent and Today plan owns visible order", async () => {
  const mod = await moduleUnderTest();
  const profile = {schemaVersion: 1, eventCount: 0, coveredDays: 0, behaviorWeight: 0, features: [], explicitGoals: ["数据分析秋招统计复习"]};
  const context = {date: "2026-07-14", budgetMinutes: 25, learnerProfile: profile};
  const low = mod.scoreRecommendationDetailed({...base, id: "low", title: "无关内容", kind: "learn", learnerSignals: {goal_alignment: 0}}, context);
  const high = mod.scoreRecommendationDetailed({...base, id: "high", title: "统计复习相关", kind: "learn", learnerSignals: {goal_alignment: 1}}, context);
  assert.equal(Math.round(high.score - low.score), 12);
  assert.ok(high.topFactors.some(f => f.factor === "goal_alignment"));
  const engine = new mod.LocalDailyIntelligenceEngine({get: async () => ({profile}), post: async () => ({})}, false);
  const dashboard = await engine.getDashboard({dashboard: {
    date: "2026-07-14", recommendations: [
      {...base, id: "score-first", title: "分数更高", kind: "learn", gapScore: 100},
      {...base, id: "plan-first", title: "计划优先", kind: "learn", gapScore: 0},
    ],
    summary: {suggested_minutes: 16, completed_minutes: 0, review_count: 0, learn_count: 2, prepared_count: 0, review_count_pending: 0, failed_count: 0, active_job_count: 0},
    active_jobs: [], failed_jobs: [],
    todayPlan: {date: "2026-07-14", version: 1, budgetMinutes: 18, totalMinutes: 16, items: [
      {recommendationId: "plan-first", minutes: 8, state: "planned", fixed: false},
      {recommendationId: "score-first", minutes: 8, state: "planned", fixed: false},
    ]},
  }});
  assert.equal(dashboard.budgetMinutes, 18);
  assert.deepEqual(dashboard.rankedRecommendations.map(item => item.id), ["plan-first", "score-first"]);
  await engine.dispose();
});

test("learning event buffer deduplicates, batches and flushes without blocking UI", async () => {
  const mod = await moduleUnderTest();
  const writes = [];
  const buffer = new mod.LearningEventBuffer(async events => { writes.push(events); }, 60_000, 10);
  const event = {id: "same", eventType: "recommendation_exposed", subjectType: "recommendation", subjectId: "r", createdAt: new Date().toISOString(), schemaVersion: 1};
  buffer.record(event); buffer.record(event);
  assert.equal(buffer.size(), 1);
  await buffer.flush();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].length, 1);
  assert.equal(buffer.size(), 0);
  await buffer.dispose();
});

test("daily UI source contains four-column structure, tabs and direct study actions", async () => {
  const {readFile} = await import("node:fs/promises");
  const views = await readFile(new URL("../src/views.ts", import.meta.url), "utf8");
  const study = await readFile(new URL("../src/study-workspace.ts", import.meta.url), "utf8");
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  for (const label of ["必须复习", "下一步学习", "每日新知识", "探索", "资料任务", "为什么推荐", "你已经知道", "这次要补齐", "学习目标", "小测试预览", "依据来源"]) assert.match(views, new RegExp(label));
  for (const action of ["稍后", "加入明天", "加入周末", "不感兴趣", "太难", "开始学习"]) assert.match(views, new RegExp(action));
  assert.match(css, /grid-template-columns:\s*248px minmax\(340px, 378px\) minmax\(520px, 1fr\)/);
  assert.match(css, /\.la-daily-detail__actions/);
  assert.match(css, /overflow: hidden !important/);
  for (const value of ["可能的下一步", "为什么适合", "新颖性", "来源质量", "今日安排已调整", "撤销"]) assert.match(views, new RegExp(value));
  assert.match(css, /\.la-direction-card/);
  for (const value of ["正在准备学习工作台", "当前进度", "随堂小测", "相关知识点", "完成本节学习"]) assert.match(views, new RegExp(value));
  assert.match(study, /直观定义/);
  assert.match(css, /\.la-study-workspace/);
});
