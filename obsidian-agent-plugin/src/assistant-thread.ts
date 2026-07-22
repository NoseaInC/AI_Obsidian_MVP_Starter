export const ASSISTANT_THREAD_SCHEMA_VERSION = 1;

export type AssistantTaskStatus =
  | "planning"
  | "running"
  | "waiting_user"
  | "completed"
  | "partially_completed"
  | "failed"
  | "cancelled";

export type AssistantTaskStepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface AssistantTaskStep {
  id: string;
  label: string;
  status: AssistantTaskStepStatus;
  detail?: string;
}

export interface AssistantTaskThread {
  schemaVersion: number;
  id: string;
  conversationId: string;
  userMessageId: string;
  title: string;
  intent: string;
  status: AssistantTaskStatus;
  progress: number;
  steps: AssistantTaskStep[];
  artifactGroupId?: string;
  error?: AssistantFailure;
  technical?: {
    correlationId?: string;
    runId?: string;
    modelProfileId?: string;
    retryOf?: string;
    errorCode?: string;
  };
  createdAt: string;
  updatedAt: string;
}

export interface AssistantFailureAction {
  id: "trusted-research" | "limited-guide" | "add-material" | "retry" | "change-model" | "edit-task";
  label: string;
  primary?: boolean;
}

export interface AssistantFailure {
  title: string;
  message: string;
  recoverable: boolean;
  partial: boolean;
  actions: AssistantFailureAction[];
  technicalCode?: string;
}

export interface AssistantArtifact {
  id: string;
  type: string;
  title: string;
  status: string;
  version?: number;
  conversationId?: string;
  sourceRunId?: string;
  payload?: Record<string, any>;
  summary?: Record<string, any>;
  versions?: Array<Record<string, any>>;
  createdAt?: string;
  updatedAt?: string;
}

export interface AssistantInspectorSource {
  id: string;
  title: string;
  kind: "pdf" | "vault_note" | "url" | "material";
  status: string;
  path?: string;
  url?: string;
}

export interface AssistantInspectorChange {
  id: string;
  title: string;
  status: string;
  path: string;
  summary: string;
  riskLevel: string;
  changeSetId?: string;
  actionId?: string;
  undoAvailable?: boolean;
}

export interface ArtifactGroup {
  schemaVersion: number;
  id: string;
  conversationId: string;
  taskThreadId: string;
  title: string;
  type: string;
  status: string;
  primaryArtifactId: string;
  childArtifactIds: string[];
  version: number;
  artifacts: AssistantArtifact[];
  createdAt: string;
  updatedAt: string;
}

export interface LearningPackView {
  title: string;
  minutes: number;
  domain: string;
  format: string;
  outcomes: string[];
  prerequisites: string[];
  sections: Array<{title: string; minutes: number}>;
  quizCount: number;
  quizLabel: string;
  sources: Array<{title: string; path?: string; url?: string}>;
}

const INTENT_LABELS: Record<string, string> = {
  ask_question: "回答你的问题",
  learn_topic: "构建入门学习包",
  research_topic: "整理可信资料与学习路径",
  capture_text: "整理原文并生成保存提案",
  organize_text: "整理原文并生成保存提案",
  import_material: "处理资料并生成学习提案",
  summarize_material: "处理资料并生成学习提案",
  create_study_plan: "生成学习计划",
  generate_quiz: "生成一份理解小测",
  continue_artifact_revision: "更新当前成果",
};

const STATUS_MAP: Record<string, AssistantTaskStatus> = {
  created: "planning",
  understanding: "planning",
  planning: "planning",
  awaiting_authorization: "running",
  running: "running",
  verifying: "running",
  awaiting_confirmation: "waiting_user",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

export function assistantIntentLabel(intent: string, topic = ""): string {
  const base = INTENT_LABELS[intent] ?? "完成当前任务";
  return topic && ["learn_topic", "research_topic"].includes(intent) ? `${base}：${topic}` : base;
}

export function humanizeAssistantError(code = "", message = "", networkAllowed = true): AssistantFailure {
  const normalized = code.toLocaleLowerCase();
  const diagnostic = `${code} ${message}`.toLocaleLowerCase();
  if (diagnostic.includes("reviewed_core_read_only")) {
    return {
      title: "正式知识受到保护",
      message: "原笔记没有被覆盖。知序会把这部分改成独立更新建议，并保留同一任务中的其他草稿；重试即可继续。",
      recoverable: true,
      partial: true,
      actions: [{id: "retry", label: "按更新建议重试", primary: true}, {id: "edit-task", label: "修改任务"}],
      technicalCode: "reviewed_core_read_only",
    };
  }
  if (["research_no_results", "research_provider_unavailable"].includes(normalized)) {
    return {
      title: "本地资料中没有找到足够依据",
      message: networkAllowed
        ? "可以继续检索可信网页和论文，或先生成明确标注来源范围的概念导读。"
        : "网络检索当前关闭。你可以添加资料，或先生成明确标注来源范围的概念导读。",
      recoverable: true,
      partial: false,
      actions: [
        ...(networkAllowed ? [{id: "trusted-research" as const, label: "搜索可信网页和论文", primary: true}] : []),
        {id: "limited-guide", label: "先生成来源有限的概念导读", primary: !networkAllowed},
        {id: "add-material", label: "添加资料"},
      ],
      technicalCode: code,
    };
  }
  if (normalized.includes("model") || normalized.includes("authentication")) {
    return {
      title: "模型服务暂时不可用",
      message: "本地知识和已有资料仍可使用。你可以重试当前步骤，或切换已配置模型。",
      recoverable: true,
      partial: false,
      actions: [{id: "retry", label: "重试失败步骤", primary: true}, {id: "change-model", label: "切换模型"}],
      technicalCode: code,
    };
  }
  if (normalized.includes("attachment")) {
    return {
      title: "附件暂时无法读取",
      message: "原文件没有被修改。请重新选择文件，或改用本地路径导入。",
      recoverable: true,
      partial: false,
      actions: [{id: "add-material", label: "重新添加资料", primary: true}, {id: "edit-task", label: "修改任务"}],
      technicalCode: code,
    };
  }
  return {
    title: "这一步没有顺利完成",
    message: message && !/^[A-Za-z0-9_.:-]+$/.test(message) ? message : "已保留当前对话和已有成果，可以重试或修改任务后继续。",
    recoverable: true,
    partial: false,
    actions: [{id: "retry", label: "重试失败步骤", primary: true}, {id: "edit-task", label: "修改任务"}],
    technicalCode: code || undefined,
  };
}

export function taskThreadFromRun(run: any, conversationId: string, userMessageId = "", topic = "", networkAllowed = true): AssistantTaskThread {
  const status = STATUS_MAP[String(run?.status ?? "running")] ?? "running";
  const rawStatus = String(run?.status ?? "running");
  const stage = ({created: 0, understanding: 0, planning: 1, awaiting_authorization: 1, running: 2, verifying: 3, awaiting_confirmation: 4, completed: 5, failed: 3, cancelled: 2} as Record<string, number>)[rawStatus] ?? 2;
  const isFinalSuccess = ["completed", "waiting_user"].includes(status);
  const labels = [
    ["检查已有知识", "查找相关笔记和当前上下文"],
    ["检索可信来源", "按本地资料、学术来源和网络偏好逐级检索"],
    ["生成适合当前水平的内容", "根据目标和时间预算组织答案"],
    ["组织内容与练习", "合并重复结果并生成一份小测预览"],
    ["生成可继续修改的成果", "保留版本、来源与后续操作"],
  ];
  const steps: AssistantTaskStep[] = labels.map(([label, detail], index) => ({
    id: `${String(run?.id ?? "run")}-step-${index + 1}`,
    label,
    detail,
    status: isFinalSuccess || index < stage ? "completed" : status === "failed" && index === stage ? "failed" : status === "cancelled" && index >= stage ? "skipped" : index === stage ? "running" : "pending",
  }));
  const progress = Math.min(100, Math.max(5, isFinalSuccess ? 100 : Math.round((stage / labels.length) * 100)));
  const error = status === "failed" ? humanizeAssistantError(String(run?.error_code ?? ""), String(run?.error_message ?? ""), networkAllowed) : undefined;
  return {
    schemaVersion: ASSISTANT_THREAD_SCHEMA_VERSION,
    id: String(run?.task_thread?.id ?? run?.id ?? `task-${Date.now()}`),
    conversationId,
    userMessageId,
    title: String(run?.task_thread?.title ?? assistantIntentLabel(String(run?.primary_intent ?? ""), topic)),
    intent: String(run?.primary_intent ?? "unknown"),
    status: run?.task_thread?.status ?? status,
    progress: Number(run?.task_thread?.progress ?? progress),
    steps: Array.isArray(run?.task_thread?.steps) && run.task_thread.steps.length
      ? run.task_thread.steps.map((item: any, index: number) => ({id: String(item.id ?? `${run.id}-step-${index}`), label: String(item.userFacingLabel ?? item.label ?? "处理任务"), detail: item.detail ? String(item.detail) : undefined, status: String(item.status ?? "pending") as AssistantTaskStepStatus}))
      : steps,
    artifactGroupId: run?.task_thread?.artifactGroupId,
    error,
    technical: {correlationId: run?.correlation_id, runId: run?.id, modelProfileId: run?.model_profile_id, retryOf: run?.retry_of, errorCode: run?.error_code},
    createdAt: String(run?.created_at ?? new Date().toISOString()),
    updatedAt: String(run?.updated_at ?? new Date().toISOString()),
  };
}

const PRIMARY_PRIORITY = ["write_result", "update_suggestion", "organization_plan", "learning_pack", "material", "research_bundle", "capture_proposal", "learning_plan", "knowledge_gap", "change_set", "quiz"];

export function groupAssistantArtifacts(artifacts: AssistantArtifact[], conversationId: string, taskThreadId: string, backendGroup?: any): ArtifactGroup[] {
  if (backendGroup?.id) {
    const groupIds = new Set([String(backendGroup.primaryArtifactId ?? ""), ...(backendGroup.childArtifactIds ?? []).map(String)]);
    const scopedArtifacts = artifacts.filter(item => groupIds.has(item.id));
    return [{
      schemaVersion: Number(backendGroup.schemaVersion ?? 1), id: String(backendGroup.id), conversationId,
      taskThreadId, title: String(backendGroup.title || "任务成果"), type: String(backendGroup.type || "assistant-result"),
      status: String(backendGroup.status || "ready"), primaryArtifactId: String(backendGroup.primaryArtifactId || ""),
      childArtifactIds: [...(backendGroup.childArtifactIds ?? [])].map(String), version: Number(backendGroup.version ?? 1),
      artifacts: scopedArtifacts, createdAt: String(backendGroup.createdAt ?? ""), updatedAt: String(backendGroup.updatedAt ?? ""),
    }];
  }
  const unique = new Map<string, AssistantArtifact>();
  for (const artifact of artifacts) {
    const key = `${artifact.type}:${artifact.title.trim().toLocaleLowerCase()}`;
    const previous = unique.get(key);
    if (!previous || Number(artifact.version ?? 1) > Number(previous.version ?? 1)) unique.set(key, artifact);
  }
  const values = [...unique.values()];
  if (values.some(item => item.type === "learning_pack")) {
    for (const [key, artifact] of unique) if (artifact.type === "quiz") unique.delete(key);
  }
  const deduped = [...unique.values()];
  if (!deduped.length) return [];
  const primary = PRIMARY_PRIORITY.map(type => deduped.find(item => item.type === type)).find(Boolean) ?? deduped[0];
  const createdAt = String(primary.createdAt ?? new Date().toISOString());
  const updatedAt = String(primary.updatedAt ?? createdAt);
  return [{
    schemaVersion: ASSISTANT_THREAD_SCHEMA_VERSION,
    id: `group-${primary.sourceRunId ?? taskThreadId}`,
    conversationId,
    taskThreadId,
    title: primary.title,
    type: primary.type,
    status: primary.status,
    primaryArtifactId: primary.id,
    childArtifactIds: deduped.filter(item => item.id !== primary.id).map(item => item.id),
    version: Math.max(...deduped.map(item => Number(item.version ?? 1))),
    artifacts: deduped,
    createdAt,
    updatedAt,
  }];
}

export function assistantInspectorSources(artifacts: AssistantArtifact[]): AssistantInspectorSource[] {
  const sources = new Map<string, AssistantInspectorSource>();
  for (const artifact of artifacts) {
    const payload = artifact.payload ?? artifact.summary ?? {};
    const candidates = [payload.sources, payload.bundle?.sources, payload.evidence]
      .filter(Array.isArray).flat() as Array<Record<string, any>>;
    for (const source of candidates) {
      const sourceType = String(source.source_type ?? source.sourceType ?? source.kind ?? "material");
      const path = String(source.path ?? source.metadata?.path ?? "");
      const url = String(source.canonical_url ?? source.url ?? "");
      const title = String(source.title ?? source.displayName ?? "") || path || url || "本地来源";
      const kind: AssistantInspectorSource["kind"] = sourceType.includes("pdf") ? "pdf"
        : sourceType.includes("vault") || Boolean(path) ? "vault_note"
          : sourceType.includes("url") || Boolean(url) ? "url" : "material";
      const id = String(source.id ?? path ?? url ?? title);
      sources.set(id, {id, title, kind, status: String(source.status ?? "local"), path: path || undefined, url: url || undefined});
    }
  }
  return [...sources.values()];
}

export function assistantInspectorChanges(artifacts: AssistantArtifact[]): AssistantInspectorChange[] {
  const changes = new Map<string, AssistantInspectorChange>();
  for (const artifact of artifacts) {
    const payload = artifact.payload ?? artifact.summary ?? {};
    const embedded = payload.change_set ?? payload.changeSet;
    const changeSetId = String(payload.changeSetId ?? embedded?.id ?? "");
    const isWriteArtifact = artifact.type === "change_set" || artifact.type === "capture_proposal" || artifact.type === "write_result";
    if (!isWriteArtifact || (!changeSetId && artifact.type !== "write_result")) continue;
    const writes = (embedded?.writes ?? payload.writes ?? payload.proposed_notes ?? []) as Array<Record<string, any>>;
    const path = String(payload.path ?? writes[0]?.path ?? "");
    const key = changeSetId || String(payload.actionId ?? artifact.id);
    const previous = changes.get(key);
    changes.set(key, {
      id: artifact.id,
      title: String(embedded?.title ?? artifact.title ?? "Change Set"),
      status: String(artifact.status ?? embedded?.state ?? "awaiting_confirmation"),
      path: path || previous?.path || "受控知识目标",
      summary: String(payload.summary ?? embedded?.preview ?? previous?.summary ?? "等待用户确认"),
      riskLevel: String(payload.riskLevel ?? payload.policy?.risk_level ?? previous?.riskLevel ?? "low"),
      changeSetId: changeSetId || previous?.changeSetId,
      actionId: String(payload.actionId ?? previous?.actionId ?? "") || undefined,
      undoAvailable: Boolean(payload.undoAvailable ?? previous?.undoAvailable),
    });
  }
  return [...changes.values()];
}

export function learningPackView(artifact: AssistantArtifact): LearningPackView {
  const payload = artifact.payload ?? artifact.summary ?? {};
  const outcomes = payload.learningOutcomes ?? payload.outcomes ?? payload.learning_sequence ?? ["理解核心直觉", "知道适用条件", "识别常见误区"];
  const prerequisites = payload.prerequisites ?? payload.evidence?.map((item: any) => item.title) ?? ["基础统计概念"];
  const sections = payload.sections ?? payload.contentStructure ?? [
    {title: "背景与直觉", minutes: 4}, {title: "核心概念", minutes: 3}, {title: "方法流程", minutes: 3}, {title: "实践小结", minutes: 2},
  ];
  const quiz = payload.quizPreview ?? payload.quiz_preview ?? [];
  return {
    title: artifact.title,
    minutes: Number(payload.estimatedMinutes ?? payload.estimated_minutes ?? 12),
    domain: String(payload.domain ?? "学习主题"),
    format: String(payload.format ?? "微课"),
    outcomes: [...outcomes].map(String).slice(0, 4),
    prerequisites: [...prerequisites].map((item: any) => String(item?.title ?? item)).slice(0, 4),
    sections: [...sections].map((item: any) => ({title: String(item?.title ?? item), minutes: Number(item?.minutes ?? 3)})).slice(0, 5),
    quizCount: Math.max(1, Array.isArray(quiz) ? quiz.length : Number(quiz?.count ?? 1)),
    quizLabel: "用于检验理解程度",
    sources: [...(payload.sources ?? payload.evidence ?? [])].map((item: any) => ({title: String(item?.title ?? "来源"), path: item?.path, url: item?.url})).slice(0, 5),
  };
}

export function isTodayDuplicate(result: any): boolean {
  return Boolean(result?.duplicate || result?.code === "today_duplicate_task");
}
