import type {DashboardData, Recommendation} from "./recommendations";
import {buildDailyLayers, todayPlanFromTasks, type DailyLayers} from "./study-workspace";

export const DAILY_SCHEMA_VERSION = 1;

export type DailyCategory = "review" | "learn" | "daily-knowledge" | "explore" | "source";
export type DailySort = "recommendation" | "due" | "mainline" | "short" | "easy" | "recent" | "confidence";
export type LearningEventType =
  | "recommendation_exposed"
  | "recommendation_clicked"
  | "recommendation_started"
  | "recommendation_completed"
  | "recommendation_snoozed"
  | "recommendation_added_to_plan"
  | "recommendation_feedback"
  | "recommendation_dismissed"
  | "study_session_started"
  | "study_session_paused"
  | "study_session_resumed"
  | "study_session_completed"
  | "study_session_abandoned"
  | "quiz_started"
  | "quiz_answered"
  | "quiz_completed"
  | "hint_opened"
  | "related_note_opened"
  | "source_opened"
  | "lesson_error_reported"
  | "plan_task_completed"
  | "plan_task_postponed"
  | "assistant_topic_revisited";

export interface LearningEvent {
  id: string;
  eventType: LearningEventType;
  subjectType: string;
  subjectId: string;
  topic?: string;
  domain?: string;
  recommendationId?: string;
  sessionId?: string;
  durationMs?: number;
  payload?: Record<string, unknown>;
  createdAt: string;
  schemaVersion: number;
}

export interface LearnerFeature {
  key: string;
  scope: string;
  value: unknown;
  confidence: number;
  evidenceCount: number;
  windowStart: string;
  windowEnd: string;
  updatedAt: string;
  source: "explicit" | "inferred";
}

export interface LearnerProfile {
  schemaVersion: number;
  eventCount: number;
  coveredDays: number;
  behaviorWeight: number;
  features: LearnerFeature[];
  explicitPreferences?: Record<string, unknown>;
  lastRebuiltAt?: string;
}

export interface DailyRankingContext {
  date: string;
  budgetMinutes: number;
  learnerProfile: LearnerProfile;
  mainlineRatio?: number;
  dailyKnowledgeQuota?: number;
}

export interface DailyDashboardRequest {
  dashboard: DashboardData;
  learnerProfile?: LearnerProfile;
  budgetMinutes?: number;
  sort?: DailySort;
  dailyKnowledgeQuota?: number;
}

export interface DailyDashboard extends DashboardData {
  schemaVersion: number;
  generatedAt: string;
  budgetMinutes: number;
  learnerProfile: LearnerProfile;
  rankedRecommendations: Recommendation[];
  categories: Record<DailyCategory, Recommendation[]>;
  layers: DailyLayers;
}

export interface DailyTransport {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body: unknown, idempotencyKey?: string): Promise<T>;
}

export interface DailyIntelligenceEngine {
  getDashboard(input: DailyDashboardRequest): Promise<DailyDashboard>;
  rankRecommendations(candidates: Recommendation[], context: DailyRankingContext, sort?: DailySort): Promise<Recommendation[]>;
  recordLearningEvent(event: LearningEvent): Promise<void>;
  getLearnerProfile(): Promise<LearnerProfile>;
  refreshCurriculumCandidates(request: Record<string, unknown>): Promise<Record<string, unknown>>;
  startStudySession(request: {recommendationId: string}): Promise<Record<string, unknown>>;
}

const EMPTY_PROFILE: LearnerProfile = {
  schemaVersion: DAILY_SCHEMA_VERSION,
  eventCount: 0,
  coveredDays: 0,
  behaviorWeight: 0,
  features: [],
};

export function categoryOf(item: Recommendation): DailyCategory {
  if (item.kind === "review") return "review";
  if (item.kind === "source") return "source";
  if (item.candidate && item.verificationGrade === "A") return "daily-knowledge";
  if (item.kind === "explore") return "explore";
  return "learn";
}

export function behaviorWeight(eventCount: number, coveredDays: number): number {
  if (eventCount < 20) return Math.min(.05, eventCount / 400);
  if (eventCount <= 100 || coveredDays < 7) return Math.min(.10, .05 + (eventCount - 20) / 1600);
  return .15;
}

function featureNumber(profile: LearnerProfile, key: string, scope = "global"): number | undefined {
  const inferred = profile.features.find(item => item.key === key && item.scope === scope && item.source === "inferred");
  const explicit = profile.features.find(item => item.key === key && item.scope === scope && item.source === "explicit");
  const value = explicit?.value ?? inferred?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function scoreRecommendation(item: Recommendation, context: DailyRankingContext): number {
  const due = item.kind === "review" ? (item.dueState === "overdue" ? 100 : 82) : 15;
  const route = item.route === "mainline" ? 100 : 55;
  const gap = item.gapScore ?? (item.candidate ? 88 : item.mastery === undefined ? 50 : Math.max(10, 100 - item.mastery * 22));
  const domainCompletion = featureNumber(context.learnerProfile, "topic_completion_rate", item.domain);
  const behavior = item.behaviorScore ?? (domainCompletion === undefined ? 50 : Math.max(0, 100 - domainCompletion * 100));
  const conversation = item.conversationScore ?? 50;
  const prerequisite = item.prerequisites.length === 0 ? 85 : item.mastery !== undefined && item.mastery >= 2 ? 90 : 70;
  const timeFit = item.estimatedMinutes <= context.budgetMinutes ? 100 : Math.max(15, context.budgetMinutes / Math.max(1, item.estimatedMinutes) * 100);
  const interest = item.favorite ? 100 : featureNumber(context.learnerProfile, "domain_interest", item.domain) ?? 55;
  const novelty = item.candidate ? 100 : item.kind === "learn" ? 70 : 45;
  // This is the single deterministic Today authority. Models can propose
  // candidates and explanations, but cannot change the final local weights.
  const weights = {
    due: .18,
    route: .17,
    gap: .18,
    conversation: .12,
    behavior: .12,
    prerequisite: .10,
    timeFit: .06,
    interest: .04,
    novelty: .03,
  };
  return Math.round((
    due * weights.due + route * weights.route + gap * weights.gap + conversation * weights.conversation + behavior * weights.behavior +
    prerequisite * weights.prerequisite + timeFit * weights.timeFit + interest * weights.interest + novelty * weights.novelty
  ) * 100) / 100;
}

export function rankDailyRecommendations(candidates: Recommendation[], context: DailyRankingContext, sort: DailySort = "recommendation"): Recommendation[] {
  const today = new Date(`${context.date}T12:00:00`);
  const weekend = today.getDay() === 0 || today.getDay() === 6;
  const dailyQuota = Math.max(0, Math.min(weekend ? 2 : 1, context.dailyKnowledgeQuota ?? (weekend ? 2 : 1)));
  const exploreQuota = weekend ? 2 : 1;
  const enriched = candidates.map(item => ({...item, dailyScore: scoreRecommendation(item, context)}));
  enriched.sort((a, b) => {
    if (sort === "due") return Number(b.dueState === "overdue") - Number(a.dueState === "overdue") || b.dailyScore! - a.dailyScore!;
    if (sort === "mainline") return Number(b.route === "mainline") - Number(a.route === "mainline") || b.dailyScore! - a.dailyScore!;
    if (sort === "short") return a.estimatedMinutes - b.estimatedMinutes || b.dailyScore! - a.dailyScore!;
    if (sort === "easy") return (a.difficultyScore ?? 2) - (b.difficultyScore ?? 2) || b.dailyScore! - a.dailyScore!;
    if (sort === "recent") return String(b.generatedAt ?? "").localeCompare(String(a.generatedAt ?? "")) || b.dailyScore! - a.dailyScore!;
    if (sort === "confidence") return (b.confidence ?? 0) - (a.confidence ?? 0) || b.dailyScore! - a.dailyScore!;
    return b.dailyScore! - a.dailyScore! || a.estimatedMinutes - b.estimatedMinutes || a.title.localeCompare(b.title, "zh-CN");
  });
  let dailyCount = 0;
  let exploreCount = 0;
  return enriched.filter(item => {
    const category = categoryOf(item);
    if (category === "daily-knowledge") return dailyCount++ < dailyQuota;
    if (category === "explore") return exploreCount++ < exploreQuota;
    return true;
  });
}

export function groupDailyRecommendations(items: Recommendation[]): Record<DailyCategory, Recommendation[]> {
  const groups: Record<DailyCategory, Recommendation[]> = {review: [], learn: [], "daily-knowledge": [], explore: [], source: []};
  for (const item of items) groups[categoryOf(item)].push(item);
  return groups;
}

function eventId(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `learning-${random}`;
}

export function createLearningEvent(type: LearningEventType, subject: {type: string; id: string; recommendation?: Recommendation; sessionId?: string}, payload: Record<string, unknown> = {}): LearningEvent {
  return {
    id: eventId(), eventType: type, subjectType: subject.type, subjectId: subject.id,
    topic: subject.recommendation?.title, domain: subject.recommendation?.domain,
    recommendationId: subject.recommendation?.id, sessionId: subject.sessionId,
    payload, createdAt: new Date().toISOString(), schemaVersion: DAILY_SCHEMA_VERSION,
  };
}

export class LearningEventBuffer {
  private pending = new Map<string, LearningEvent>();
  private timer: number | undefined;
  private flushing: Promise<void> | null = null;

  constructor(private append: (events: LearningEvent[]) => Promise<void>, private intervalMs = 2500, private threshold = 8) {}

  record(event: LearningEvent): void {
    this.pending.set(event.id, event);
    if (this.pending.size >= this.threshold) void this.flush();
    else if (this.timer === undefined) this.timer = globalThis.setTimeout(() => void this.flush(), this.intervalMs) as unknown as number;
  }

  async flush(): Promise<void> {
    if (this.flushing) return this.flushing;
    if (this.timer !== undefined) { globalThis.clearTimeout(this.timer); this.timer = undefined; }
    const batch = [...this.pending.values()];
    if (!batch.length) return;
    this.flushing = this.append(batch).then(() => {
      for (const item of batch) this.pending.delete(item.id);
    }).finally(() => { this.flushing = null; });
    return this.flushing;
  }

  async dispose(): Promise<void> {
    if (this.timer !== undefined) { globalThis.clearTimeout(this.timer); this.timer = undefined; }
    await this.flush();
  }

  size(): number { return this.pending.size; }
}

export class LocalDailyIntelligenceEngine implements DailyIntelligenceEngine {
  private events: LearningEventBuffer;

  constructor(private transport: DailyTransport, private trackingEnabled: boolean | (() => boolean) = true) {
    this.events = new LearningEventBuffer(events => this.transport.post("/learning/events", {events, schemaVersion: DAILY_SCHEMA_VERSION}, `events-${events[0]?.id ?? "empty"}`));
  }

  async getDashboard(input: DailyDashboardRequest): Promise<DailyDashboard> {
    const profile = input.learnerProfile ?? EMPTY_PROFILE;
    const budget = input.budgetMinutes ?? input.dashboard.todayPlan?.budgetMinutes ?? (new Date(`${input.dashboard.date}T12:00:00`).getDay() % 6 === 0 ? 210 : 25);
    const ranked = await this.rankRecommendations(input.dashboard.recommendations, {date: input.dashboard.date, budgetMinutes: budget, learnerProfile: profile, mainlineRatio: .7, dailyKnowledgeQuota: input.dailyKnowledgeQuota}, input.sort);
    const layers = buildDailyLayers(input.dashboard);
    const scoreLookup = new Map(ranked.map(item => [item.id, item]));
    const dailyPlanItems = layers.dailyPlanItems.map(task => ({...task, recommendation: scoreLookup.get(task.recommendationId) ?? task.recommendation}));
    const visible = dailyPlanItems.map(item => ({...item.recommendation, estimatedMinutes: item.minutes, dailyPlanState: item.state}));
    return {
      ...input.dashboard,
      todayPlan: todayPlanFromTasks(input.dashboard.todayPlan, dailyPlanItems),
      schemaVersion: DAILY_SCHEMA_VERSION,
      generatedAt: new Date().toISOString(),
      budgetMinutes: budget,
      learnerProfile: profile,
      rankedRecommendations: visible,
      categories: groupDailyRecommendations(visible),
      layers: {...layers, dailyPlanItems},
    };
  }

  async rankRecommendations(candidates: Recommendation[], context: DailyRankingContext, sort: DailySort = "recommendation"): Promise<Recommendation[]> {
    return rankDailyRecommendations(candidates, context, sort);
  }

  async recordLearningEvent(event: LearningEvent): Promise<void> {
    const enabled = typeof this.trackingEnabled === "function" ? this.trackingEnabled() : this.trackingEnabled;
    if (enabled) this.events.record(event);
  }

  async getLearnerProfile(): Promise<LearnerProfile> {
    const response = await this.transport.get<{profile: LearnerProfile}>("/learning/profile");
    return response.profile;
  }

  async refreshCurriculumCandidates(request: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.transport.post("/curriculum/refresh", request);
  }

  async startStudySession(request: {recommendationId: string}): Promise<Record<string, unknown>> {
    return this.transport.post("/study-sessions", {recommendation_id: request.recommendationId});
  }

  async dispose(): Promise<void> { await this.events.dispose(); }
  pendingEventCount(): number { return this.events.size(); }
}
