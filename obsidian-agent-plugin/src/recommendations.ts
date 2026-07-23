import {isActiveExplicitWorkflowJob} from "./explicit-workflow-ui";

export type RecommendationKind = "review" | "learn" | "explore" | "source";
export type RecommendationSort = "smart" | "due" | "mainline" | "short" | "recent" | "explore";

export interface KnowledgeReference { title: string; path: string; }
export interface MicroConcept { id: string; title: string; explanation: string; estimatedMinutes: number; candidate: boolean; }
export interface Recommendation {
  id: string; title: string; kind: RecommendationKind; estimatedMinutes: number; score: number;
  reason: string; reasonDetails: string[]; prerequisites: KnowledgeReference[]; relatedNotes: KnowledgeReference[];
  microConcepts: MicroConcept[]; quizPreview?: {question: string; answerHint: string}; sourcePath?: string;
  domain: string; route: "mainline" | "branch"; dueState?: "overdue" | "due" | "upcoming";
  mastery?: number; actions: string[]; favorite?: boolean; preparedId?: string;
  candidate?: boolean; candidateKind?: string; generatedAt?: string;
  confidence?: number; verificationGrade?: "A" | "B" | "C"; verificationScore?: number;
  sourceBasis?: Array<{type: string; title: string; url?: string; path?: string; retrievedAt?: string}>;
  behaviorBasis?: string[]; learningOutcomes?: string[]; verification?: Record<string, unknown>;
  gapScore?: number; behaviorScore?: number; difficultyScore?: number; dailyScore?: number;
  conversationScore?: number;
  direction?: boolean;
  horizon?: "near" | "route" | "exploration";
  priority?: "高优先级" | "中优先级" | "探索性";
  confidenceLabel?: "高置信" | "来源有限";
  noveltyBasis?: string;
  isNewKnowledge?: boolean;
  sourceQuality?: string;
  dailyPlanState?: "planned" | "in_progress" | "paused" | "completed" | "skipped";
  candidateRootId?: string;
  splitPart?: number;
  splitTotal?: number;
}

export interface LearningDirection {
  id: string; title: string; horizon: "near" | "route" | "exploration";
  why: string[]; connections: string[]; noveltyBasis: string; prerequisites: string[];
  estimatedMinutes: number; difficulty: string; route: "mainline" | "branch";
  sourceQuality: string; priority: "高优先级" | "中优先级" | "探索性";
  confidenceLabel: "高置信" | "来源有限"; isNewKnowledge: boolean;
}

export interface TodayPlan {
  date: string; version: number; budgetMinutes: number; totalMinutes: number;
  items: Array<{recommendationId: string; minutes: number; state: string; fixed: boolean; recommendation?: Recommendation}>;
}

export interface DashboardData {
  date: string;
  recommendations: Recommendation[];
  summary: {suggested_minutes: number; completed_minutes: number; review_count: number; learn_count: number; prepared_count: number; review_count_pending: number; failed_count: number; active_job_count: number};
  active_jobs: any[]; failed_jobs: any[];
  todayPlan?: TodayPlan;
  directions?: LearningDirection[];
  todayConstraints?: {date?: string; noFormula?: boolean};
  recentAdjustment?: {actionId: string; type: string; beforeMinutes: number; afterMinutes: number; undoAvailable: boolean};
  runtime?: {ranking?: string; persistence?: string; modelConfigured?: boolean; provider?: string | null};
}

export const KIND_LABEL: Record<RecommendationKind, string> = {review: "复习", learn: "学习", explore: "探索", source: "资料"};

export function selectRecommendations(items: Recommendation[], query: string, kind: RecommendationKind | "all", sort: RecommendationSort): Recommendation[] {
  const needle = query.trim().toLocaleLowerCase();
  const filtered = items.filter(item => (kind === "all" || item.kind === kind) && (!needle || `${item.title} ${item.reason} ${item.domain}`.toLocaleLowerCase().includes(needle)));
  return [...filtered].sort((a, b) => {
    if (sort === "due") return Number(b.dueState === "overdue") - Number(a.dueState === "overdue") || b.score - a.score;
    if (sort === "mainline") return Number(b.route === "mainline") - Number(a.route === "mainline") || b.score - a.score;
    if (sort === "short") return a.estimatedMinutes - b.estimatedMinutes || b.score - a.score;
    if (sort === "explore") return Number(b.kind === "explore") - Number(a.kind === "explore") || b.score - a.score;
    return b.score - a.score || a.estimatedMinutes - b.estimatedMinutes;
  });
}

export function activeJobs(jobs: any[]): any[] { return jobs.filter(job => isActiveExplicitWorkflowJob(String(job.state ?? ""))); }
export function pendingBundles(bundles: any[]): any[] { return bundles.filter(bundle => bundle.state === "prepared"); }
export function readableJobTitle(job: any): string {
  const pdf = String(job.payload?.pdf ?? "");
  if (job.kind === "prepare-pdf" && pdf) return `处理 ${pdf.split("/").pop()?.replace(/\.pdf$/i, "")}`;
  return ({quiz: "生成短测", "learning-plan": "生成学习计划", "expand-idea": "展开灵感"} as Record<string, string>)[job.kind] ?? "Agent 任务";
}
