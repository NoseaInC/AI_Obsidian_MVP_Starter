import type {DashboardData, LearningDirection, Recommendation, TodayPlan} from "./recommendations";

export type VerificationGrade = "A" | "B" | "C";
export type StudyWorkspaceMode = "recommendation" | "starting" | "learning" | "paused" | "quiz" | "completing" | "completed" | "error";
export type StudyAction = "start" | "started" | "pause" | "resume" | "open_quiz" | "leave_quiz" | "complete" | "completed" | "undo" | "fail" | "retry" | "exit";

export interface CandidateAdmissionResult {
  candidateId: string;
  grade: VerificationGrade;
  admitted: boolean;
  placement: "daily-plan" | "validated-direction" | "candidate-pool";
  reasons: string[];
  entityValid: boolean;
  canonicalTopicResolved: boolean;
  noveltyVerified: boolean;
  prerequisitesSatisfied: boolean;
  directSourcesAvailable: boolean;
  lessonBlueprintComplete: boolean;
  durationAppropriate: boolean;
  duplicateRisk: number;
}

export interface DailyPlanTask {
  recommendationId: string;
  minutes: number;
  state: "planned" | "in_progress" | "paused" | "completed" | "skipped";
  fixed: boolean;
  recommendation: Recommendation;
}

export interface DailyLayers {
  curriculumCandidates: Recommendation[];
  validatedDirections: LearningDirection[];
  dailyPlanItems: DailyPlanTask[];
}

export interface LessonSection {
  id: string;
  title: string;
  kind: "overview" | "definition" | "connection" | "example" | "summary";
  markdown: string;
  estimatedMinutes: number;
}

export interface LessonQuiz {
  id: string;
  question: string;
  options: string[];
  answerIndex: number;
  explanation: string;
}

export interface LessonBlueprint {
  version: number;
  recommendationId: string;
  title: string;
  goal: string;
  estimatedMinutes: number;
  sections: LessonSection[];
  quizzes: LessonQuiz[];
  prerequisites: string[];
  relatedNotes: Array<{title: string; path: string}>;
  learningPath: string[];
  sources: Array<{type: string; title: string; url?: string; path?: string}>;
}

export interface StudyWorkspaceState {
  mode: StudyWorkspaceMode;
  recommendationId: string;
  sessionId: string;
  sectionIndex: number;
  completedSectionIds: string[];
  quizAnswers: Record<string, number>;
  notes: string;
  error: string;
  startedAt: string;
  updatedAt: string;
}

export type StudyLayout = "wide" | "drawer" | "stacked";

export function studyLayoutForWidth(width: number): StudyLayout {
  if (width >= 1180) return "wide";
  if (width >= 760) return "drawer";
  return "stacked";
}

const normalize = (value: string): string => value.normalize("NFKC").toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");

export function evaluateCandidate(item: Recommendation): CandidateAdmissionResult {
  const grade = (item.verificationGrade ?? "C") as VerificationGrade;
  const verification = item.verification ?? {};
  const entityValid = item.title.trim().length >= 2 && item.title.length <= 120 && !/(Class 的|这个|那个|上面的|刚才的)/i.test(item.title);
  const canonicalTopicResolved = entityValid && normalize(item.title).length > 1;
  const noveltyVerified = Boolean(verification.noveltyVerified ?? item.candidate);
  const prerequisitesSatisfied = Boolean(verification.prerequisitesSatisfied ?? true);
  const directSourcesAvailable = Boolean(item.sourceBasis?.length);
  const lessonBlueprintComplete = Boolean(item.learningOutcomes?.length);
  const durationAppropriate = item.estimatedMinutes >= 5 && item.estimatedMinutes <= 25;
  const duplicateRisk = Math.max(0, Math.min(1, Number(verification.duplicateRisk ?? 0)));
  const facts = {entityValid, canonicalTopicResolved, noveltyVerified, prerequisitesSatisfied, directSourcesAvailable, lessonBlueprintComplete, durationAppropriate, duplicateRisk};
  const reasons: string[] = [];
  if (!item.candidate) reasons.push("不是课程候选");
  if (!item.sourceBasis?.length) reasons.push("缺少可核验来源");
  if ((item.confidence ?? 0) < .65) reasons.push("置信度不足");
  if (!item.learningOutcomes?.length) reasons.push("缺少可检验学习目标");
  if (!entityValid) reasons.push("未解析为稳定知识实体");
  if (!durationAppropriate) reasons.push("学习时长过长，需要先拆分");
  if (duplicateRisk >= .35) reasons.push("与现有知识重复风险过高");
  if (grade === "A" && item.candidate && !reasons.length) return {candidateId: item.id, grade, admitted: true, placement: "daily-plan", reasons: ["A 级机器验证通过"], ...facts};
  if (grade === "B" && item.candidate && entityValid && canonicalTopicResolved && lessonBlueprintComplete && durationAppropriate && duplicateRisk < .35) return {candidateId: item.id, grade, admitted: true, placement: "validated-direction", reasons: reasons.length ? reasons : ["来源有限，仅作为方向候选"], ...facts};
  return {candidateId: item.id, grade, admitted: false, placement: "candidate-pool", reasons: reasons.length ? reasons : ["未达到今日学习门槛"], ...facts};
}

export function deduplicateCandidates(items: Recommendation[]): Recommendation[] {
  const seen = new Set<string>();
  return items.filter(item => {
    const key = `${normalize(item.title)}|${normalize(item.domain)}`;
    if (!key.replace("|", "") || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function splitOversizedCandidate(item: Recommendation, maximumMinutes = 25): Recommendation[] {
  if (item.estimatedMinutes <= maximumMinutes) return [item];
  const parts = Math.max(2, Math.ceil(item.estimatedMinutes / maximumMinutes));
  const outcomes = item.learningOutcomes?.length ? item.learningOutcomes : item.reasonDetails.length ? item.reasonDetails : [item.reason];
  return Array.from({length: parts}, (_, index) => ({
    ...item,
    id: `${item.id}-part-${index + 1}`,
    title: `${item.title} · ${index + 1}/${parts}`,
    estimatedMinutes: Math.ceil(item.estimatedMinutes / parts),
    learningOutcomes: [outcomes[index % outcomes.length]],
    reason: `${item.reason}（已拆为可在一次学习会话内完成的第 ${index + 1} 部分）`,
  }));
}

export function buildDailyLayers(dashboard: DashboardData): DailyLayers {
  const recommendations = deduplicateCandidates(dashboard.recommendations);
  const lookup = new Map(recommendations.map(item => [item.id, item]));
  const curriculumCandidates = recommendations.filter(item => item.candidate);
  const promoted = curriculumCandidates.filter(item => evaluateCandidate(item).placement === "validated-direction" || (item.splitPart ?? 1) > 1).map(item => ({
    id: item.id, title: item.title, horizon: "route" as const, why: item.reasonDetails.length ? item.reasonDetails : [item.reason],
    connections: item.relatedNotes.map(value => value.title), noveltyBasis: item.noveltyBasis ?? "课程候选的后续学习阶梯",
    prerequisites: item.prerequisites.map(value => value.title), estimatedMinutes: item.estimatedMinutes,
    difficulty: (item.difficultyScore ?? 2) >= 3 ? "hard" : "medium", route: item.route,
    sourceQuality: item.sourceQuality ?? (item.sourceBasis?.length ? "可追溯来源" : "来源有限"), priority: "中优先级" as const,
    confidenceLabel: item.verificationGrade === "A" ? "高置信" as const : "来源有限" as const, isNewKnowledge: true,
  }));
  const validatedDirections = [...(dashboard.directions ?? []), ...promoted];
  const dailyPlanItems: DailyPlanTask[] = [];
  for (const planned of dashboard.todayPlan?.items ?? []) {
    const recommendation = planned.recommendation ?? lookup.get(planned.recommendationId);
    if (!recommendation) continue;
    if (recommendation.direction) continue;
    if ((recommendation.splitPart ?? 1) > 1) continue;
    if (recommendation.candidate && !evaluateCandidate(recommendation).admitted) continue;
    if (recommendation.candidate && evaluateCandidate(recommendation).placement !== "daily-plan") continue;
    dailyPlanItems.push({
      recommendationId: planned.recommendationId,
      minutes: Math.max(1, Number(planned.minutes) || recommendation.estimatedMinutes),
      state: normalizePlanState(planned.state),
      fixed: Boolean(planned.fixed),
      recommendation: {...recommendation, estimatedMinutes: Math.max(1, Number(planned.minutes) || recommendation.estimatedMinutes)},
    });
  }
  return {curriculumCandidates, validatedDirections, dailyPlanItems};
}

function normalizePlanState(value: string): DailyPlanTask["state"] {
  if (["in_progress", "paused", "completed", "skipped"].includes(value)) return value as DailyPlanTask["state"];
  return "planned";
}

export function createLessonBlueprint(item: Recommendation): LessonBlueprint {
  const outcomes = item.learningOutcomes?.length ? item.learningOutcomes : [`解释「${item.title}」的直觉、定义、成立条件与一个典型应用。`];
  const prerequisites = item.prerequisites.map(value => value.title);
  const connections = item.relatedNotes.map(value => value.title);
  const concepts = item.microConcepts.map(value => value.title);
  const minutes = Math.max(5, item.estimatedMinutes);
  const weights = [1, 2, 2, 2, 1];
  const allocated = weights.map(weight => Math.max(1, Math.round(minutes * weight / 8)));
  const formula = item.title.includes("影响函数")
    ? "\n\n$$\nIF(x;T,F)=\\lim_{\\epsilon\\to0}\\frac{T((1-\\epsilon)F+\\epsilon\\delta_x)-T(F)}{\\epsilon}\n$$\n\n它描述在分布 $F$ 中加入极小权重的点 $x$ 时，统计泛函 $T$ 的一阶变化。"
    : item.title.includes("倾向得分") ? "\n\n倾向得分写作 $e(x)=P(T=1\\mid X=x)$；它是处理分配机制的条件概率，不是结果预测。" : "";
  const sections: LessonSection[] = [
    {id: "why", title: `为什么需要${item.title}`, kind: "overview", estimatedMinutes: allocated[0], markdown: `## 本节学习目标\n\n${outcomes.map(value => `- ${value}`).join("\n")}\n\n${item.reason}`},
    {id: "definition", title: "直观定义", kind: "definition", estimatedMinutes: allocated[1], markdown: `## 核心定义\n\n**${item.title}** 是当前学习路线中的一个可检验知识单元。先抓住直觉，再核对成立条件。\n\n${item.reasonDetails.map(value => `- ${value}`).join("\n") || "- 从定义、条件和边界三个层次理解。"}${formula}`},
    {id: "connection", title: `与${prerequisites[0] ?? connections[0] ?? "已有知识"}的关系`, kind: "connection", estimatedMinutes: allocated[2], markdown: `## 知识连接\n\n前置知识：${prerequisites.join("、") || "无需额外前置"}。\n\n相关笔记：${connections.join("、") || "当前尚无正式相关笔记"}。\n\n${concepts.length ? `需要补齐：${concepts.join("、")}。` : "重点区分概念本身、识别条件与估计方法。"}`},
    {id: "example", title: "一个简单例子", kind: "example", estimatedMinutes: allocated[3], markdown: `## 应用检查\n\n用一个你熟悉的场景回答：\n\n1. 「${item.title}」解决什么问题？\n2. 它依赖哪些条件？\n3. 条件失败时会得到什么误导性结论？`},
    {id: "summary", title: "本节总结", kind: "summary", estimatedMinutes: allocated[4], markdown: `## 小结\n\n完成后，你应当能够：\n\n${outcomes.map(value => `- ${value}`).join("\n")}\n\n> 本课程内容不会自动写入 reviewed/core 笔记。`},
  ];
  const question = item.quizPreview?.question ?? `关于「${item.title}」，哪一项最能检查你是否理解了它的适用边界？`;
  return {
    version: 1, recommendationId: item.id, title: item.title, goal: outcomes[0], estimatedMinutes: minutes, sections,
    quizzes: [{id: "checkpoint", question, options: ["能复述名称", "能说明定义、条件与反例", "看过相关笔记", "记住一个术语"], answerIndex: 1, explanation: item.quizPreview?.answerHint ?? "掌握概念需要同时说明定义、成立条件和边界。"}],
    prerequisites, relatedNotes: item.relatedNotes, learningPath: [...prerequisites, item.title, ...concepts].filter(Boolean), sources: item.sourceBasis ?? [],
  };
}

export function initialStudyState(recommendationId: string): StudyWorkspaceState {
  const now = new Date().toISOString();
  return {mode: "recommendation", recommendationId, sessionId: "", sectionIndex: 0, completedSectionIds: [], quizAnswers: {}, notes: "", error: "", startedAt: "", updatedAt: now};
}

export function reduceStudyState(state: StudyWorkspaceState, action: StudyAction, payload: Partial<StudyWorkspaceState> = {}): StudyWorkspaceState {
  const allowed: Record<StudyWorkspaceMode, StudyAction[]> = {
    recommendation: ["start"], starting: ["started", "fail", "exit"], learning: ["pause", "open_quiz", "complete", "fail", "exit"],
    paused: ["resume", "exit", "fail"], quiz: ["leave_quiz", "pause", "complete", "fail", "exit"], completing: ["completed", "fail"],
    completed: ["undo", "exit"], error: ["retry", "exit"],
  };
  if (!allowed[state.mode].includes(action)) throw new Error(`invalid_study_transition:${state.mode}:${action}`);
  const mode: Record<StudyAction, StudyWorkspaceMode> = {
    start: "starting", started: "learning", pause: "paused", resume: "learning", open_quiz: "quiz", leave_quiz: "learning",
    complete: "completing", completed: "completed", undo: "learning", fail: "error", retry: "starting", exit: "recommendation",
  };
  const now = new Date().toISOString();
  return {...state, ...payload, mode: mode[action], error: action === "retry" || action === "exit" ? "" : (payload.error ?? state.error), startedAt: action === "started" && !state.startedAt ? now : state.startedAt, updatedAt: now};
}

export function todayPlanFromTasks(plan: TodayPlan | undefined, tasks: DailyPlanTask[]): TodayPlan | undefined {
  if (!plan) return undefined;
  return {...plan, totalMinutes: tasks.filter(item => item.state !== "skipped").reduce((sum, item) => sum + item.minutes, 0), items: tasks.map(item => ({recommendationId: item.recommendationId, minutes: item.minutes, state: item.state, fixed: item.fixed, recommendation: item.recommendation}))};
}
