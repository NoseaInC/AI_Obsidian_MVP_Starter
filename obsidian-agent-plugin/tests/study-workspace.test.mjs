import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {build} from "esbuild";

async function moduleUnderTest() {
  const result = await build({entryPoints: [new URL("../src/study-workspace.ts", import.meta.url).pathname], bundle: true, write: false, format: "esm", platform: "node", target: "es2022"});
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

function random(seedText) {
  let seed = [...seedText].reduce((value, char) => (value * 33 + char.charCodeAt(0)) >>> 0, 2166136261);
  return () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 2 ** 32; };
}

const base = {
  reasonDetails: ["区分定义与边界"], prerequisites: [], relatedNotes: [], microConcepts: [], domain: "因果推断", route: "mainline", actions: [],
  reason: "连接当前学习路线", estimatedMinutes: 8, score: 70, kind: "learn", learningOutcomes: ["解释定义与适用边界"],
};

test("three daily layers never leak B/C candidates or directions into the execution plan", async () => {
  const mod = await moduleUnderTest();
  const seed = process.env.LA_TEST_SEED || "daily-plan-1000"; const rng = random(seed);
  for (let run = 0; run < 1000; run += 1) {
    const recommendations = Array.from({length: 8 + Math.floor(rng() * 24)}, (_, index) => {
      const grade = ["A", "B", "C"][Math.floor(rng() * 3)]; const candidate = rng() > .45;
      return {...base, id: `r-${run}-${index}`, title: `主题 ${run}-${index}`, candidate, direction: !candidate && rng() > .88, verificationGrade: grade,
        sourceBasis: grade === "A" ? [{type: "local_knowledge", title: "来源"}] : [], confidence: grade === "A" ? .9 : .6};
    });
    const todayItems = recommendations.map(item => ({recommendationId: item.id, minutes: item.estimatedMinutes, state: "planned", fixed: false, recommendation: item}));
    const layers = mod.buildDailyLayers({date: "2026-07-15", recommendations, summary: {}, active_jobs: [], failed_jobs: [], todayPlan: {date: "2026-07-15", version: 1, budgetMinutes: 25, totalMinutes: 25, items: todayItems}, directions: []});
    for (const task of layers.dailyPlanItems) {
      assert.equal(Boolean(task.recommendation.direction), false, `seed=${seed} run=${run}`);
      if (task.recommendation.candidate) assert.equal(task.recommendation.verificationGrade, "A", `seed=${seed} run=${run}`);
    }
  }
});

test("study state machine survives 10,000 valid actions and rejects illegal transitions", async () => {
  const mod = await moduleUnderTest();
  const seed = process.env.LA_TEST_SEED || "study-actions-10000"; const rng = random(seed);
  const valid = {
    recommendation: ["start"], starting: ["started", "fail", "exit"], learning: ["pause", "open_quiz", "complete", "fail", "exit"], paused: ["resume", "exit", "fail"],
    quiz: ["leave_quiz", "pause", "complete", "fail", "exit"], completing: ["completed", "fail"], completed: ["undo", "exit"], error: ["retry", "exit"],
  };
  let state = mod.initialStudyState("rec");
  for (let index = 0; index < 10_000; index += 1) {
    const actions = valid[state.mode]; const action = actions[Math.floor(rng() * actions.length)];
    state = mod.reduceStudyState(state, action, action === "started" ? {sessionId: "session"} : {});
    assert.ok(valid[state.mode], `seed=${seed} index=${index} mode=${state.mode}`);
  }
  assert.throws(() => mod.reduceStudyState(mod.initialStudyState("x"), "complete"), /invalid_study_transition/);
});

test("1,000 lesson blueprints remain bounded, source-aware and splittable", async () => {
  const mod = await moduleUnderTest();
  const seed = process.env.LA_TEST_SEED || "lesson-1000"; const rng = random(seed);
  for (let index = 0; index < 1000; index += 1) {
    const minutes = 5 + Math.floor(rng() * 116);
    const item = {...base, id: `lesson-${index}`, title: `课程 ${index}`, estimatedMinutes: minutes, sourceBasis: [{type: "local_knowledge", title: `来源 ${index}`, path: `20-Knowledge/${index}.md`}], quizPreview: {question: "哪项正确？", answerHint: "检查边界"}};
    const split = mod.splitOversizedCandidate(item, 25);
    assert.ok(split.every(part => part.estimatedMinutes <= 25), `seed=${seed} index=${index}`);
    for (const part of split) {
      const lesson = mod.createLessonBlueprint(part);
      assert.equal(lesson.sections.length, 5);
      assert.equal(lesson.quizzes.length, 1);
      assert.equal(lesson.sources[0].title, `来源 ${index}`);
      assert.match(lesson.sections.at(-1).markdown, /不会自动写入 reviewed\/core/);
    }
  }
});

test("responsive classification is stable at ten fixed and one hundred random widths", async () => {
  const mod = await moduleUnderTest();
  const fixed = [320, 480, 759, 760, 859, 980, 1179, 1180, 1440, 1920];
  for (const width of fixed) assert.ok(["wide", "drawer", "stacked"].includes(mod.studyLayoutForWidth(width)));
  const seed = process.env.LA_TEST_SEED || "widths-100"; const rng = random(seed);
  for (let index = 0; index < 100; index += 1) {
    const width = 260 + Math.floor(rng() * 1900); const layout = mod.studyLayoutForWidth(width);
    assert.equal(layout, width >= 1180 ? "wide" : width >= 760 ? "drawer" : "stacked", `seed=${seed} width=${width}`);
  }
});

test("noise and unresolved B candidates stay out of validated directions", async () => {
  const mod = await moduleUnderTest();
  const noise = {...base, id: "noise", title: "这个 Class 的可迁移应用", candidate: true, verificationGrade: "B", confidence: .8, sourceBasis: [], learningOutcomes: ["理解"]};
  const admission = mod.evaluateCandidate(noise);
  assert.equal(admission.admitted, false);
  assert.equal(admission.placement, "candidate-pool");
  assert.ok(admission.reasons.some(value => value.includes("知识实体")));
});

test("paused study sessions refresh the dashboard and expose an explicit resume action", async () => {
  const views = await readFile(new URL("../src/views.ts", import.meta.url), "utf8");
  assert.match(views, /item\.dailyPlanState === "paused" \|\| item\.dailyPlanState === "in_progress"/);
  assert.match(views, /resumable \? "继续学习" : "开始学习"/);
  assert.match(views, /this\.studyState = initialStudyState\(item\.id\);[\s\S]*await this\.refresh\(\);/);
});
