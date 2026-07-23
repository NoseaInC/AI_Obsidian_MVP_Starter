import {
  App,
  FuzzySuggestModal,
  ItemView,
  Menu,
  MarkdownView,
  Modal,
  Notice,
  setIcon,
  TFile,
  WorkspaceLeaf,
} from "obsidian";
import {AgentClient} from "./api";
import {
  activeJobs,
  DashboardData,
  KIND_LABEL,
  MicroConcept,
  pendingBundles,
  readableJobTitle,
  Recommendation,
  LearningDirection,
  RecommendationKind,
  RecommendationSort,
  selectRecommendations,
} from "./recommendations";
import {humanReviewMarkdown} from "./review-preview";
import {
  createLearningEvent,
  DailyCategory,
  DailyDashboard,
  DailySort,
  LocalDailyIntelligenceEngine,
} from "./daily-intelligence";
import type {LearningEvent, LearningEventType} from "./daily-intelligence";
import {
  ArtifactGroup,
  AssistantArtifact,
  AssistantTaskThread,
  assistantInspectorChanges,
  assistantInspectorSources,
  groupAssistantArtifacts,
  humanizeAssistantError,
  isTodayDuplicate,
  learningPackView,
} from "./assistant-thread";
import {
  AssistantLiveRun,
  agentChunkToAssistantEvent,
  buildAssistantMessageMetadata,
  cancelledAssistantRun,
  initialAssistantLiveRun,
  isAssistantLiveRunActive,
  persistedAssistantTrace,
  reduceAssistantStream,
} from "./assistant-stream";
import {ObsidianAssistantMarkdownRenderer, ProgressiveAssistantMarkdown} from "./markdown-renderer";
import {
  createLessonBlueprint,
  initialStudyState,
  reduceStudyState,
  type LessonBlueprint,
  type StudyWorkspaceState,
} from "./study-workspace";
import {isAssistantReadableVaultPath, referencedVaultNotePath} from "./vault-note-policy";
import {renderInlineAgentConfirmation} from "./assistant-inline-confirmation";
import type {AgentRuntime} from "./core/runtime/AgentRuntime";
import {PiAgentRuntime} from "./core/runtime/PiAgentRuntime";
import {AgentRuntimeRegistry} from "./core/runtime/AgentRuntimeRegistry";
import {SettingsService} from "./core/settings/SettingsService";

export const MAIN_VIEW = "learning-agent-main";
export const SIDEBAR_VIEW = "zhixu-sidebar-v2";
export const LEGACY_SIDEBAR_VIEW = "obsidian-learning-agent-view";

type MainTab = "today" | "sources" | "plan" | "assistant";
type SourceFilter = "all" | "pending" | "running" | "research" | "applied" | "failed" | "history";
interface DailyViewPreferences {
  trackingEnabled: boolean;
  recordLearningDuration: boolean;
  useQuizResults: boolean;
  useRecommendationFeedback: boolean;
  dailyKnowledgeCount: number;
}

interface ModelProfile {
  id: string;
  displayName: string;
  providerType: string;
  baseUrl: string;
  defaultModel: string;
  availableModels: string[];
  enabled: boolean;
  configured: boolean;
  keyHint: string;
  apiKeyReference: string;
  settings: any;
}

const MODULES: Array<{id: MainTab; label: string; icon: string}> = [
  {id: "today", label: "今日", icon: "calendar-days"},
  {id: "sources", label: "资料", icon: "files"},
  {id: "plan", label: "计划", icon: "calendar-range"},
  {id: "assistant", label: "助手", icon: "messages-square"},
];

const MODULE_TITLES: Record<MainTab, string> = {
  today: "今天，继续前进",
  sources: "资料中心",
  plan: "学习计划",
  assistant: "助手",
};

function iconButton(parent: HTMLElement, icon: string, label: string, action: () => void): HTMLButtonElement {
  const element = parent.createEl("button", {
    cls: "la-icon-button",
    attr: {"aria-label": label, title: label},
  });
  setIcon(element, icon);
  element.onclick = action;
  return element;
}

const ASSISTANT_AVATAR_PATH = ".obsidian/plugins/obsidian-learning-agent/assets/zhixu-assistant-avatar.png";
const MODEL_ICON_ROOT = ".obsidian/plugins/obsidian-learning-agent/assets/model-icons";
// Exact transparent cutout supplied by the user. Inlining avoids app:// resolution differences between Vaults.
const SEND_BUTTON_IDLE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEQAAABECAYAAAA4E5OyAAAAAXNSR0IArs4c6QAAADhlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAAqACAAQAAAABAAAARKADAAQAAAABAAAARAAAAADjEeGFAAADBElEQVR4Ae3ZgXHqMAwGYN67NwIMwRIMAXPCOgwBO/Td31acGyxHsiTHTpO7XmiwbOmzQrh2t9uOTaB7gev1+oGfHhL90yoJa8GXy6VJrqGLWBG4zYrECQGJgsgBeeO4grSEmOJ4wbiALAmRwnigmEB6gUhR8NoCUw3SKwbh1KJUgfSOYUFRg4yCUYuiAhkNowZFDDIqhhZFBDI6hgZlFmQtGFKUvzRwO38JFDtkbd1Bm176jsJ2SEuM0+m0Ox6PlO+iZxakVVaA2O/3nyCAaXGUNjsLUgrwTBgYaWcA5nA4eC6hnisLop6lMiDFoCly1+g9zzO36YuBcIXT7eNZvGauNxBOTjOpZCwHgtjSe5K5pWNytb6BSCezjJMULBljyYGL3UAmMj9Aci00GW/+VbPzLR7D05p/gJirFUygAVniMdwURINBtjUxFFtz7h6kdZc0A7HstCVW2yVNQFCQpaiWXdIERLtLufEW0Nx83LUXyPTxwwVor1u7g9aL7JK09hcILex99txZz7m4Ol8gpb8iccFz170LiOqStPYXyFxxNe97gyCHiDnT2sJAohJHl0TNDZjhQJD0cCCRCQMER9QaIR2iSfb5fO7u9/vnD15LD80a0jkx7p9msGSsNFGCeDwer2kBgz8ySz8nsBZiPI+3f1SlX1JqFjqfz8WwHAQXgILngG+3Gxcuup4+chHg2iGlfyHQbSHK8nsQxZRgsGbaZZr5c2NdQaaJoRtwzdrWJZjpmrkiNdfebhkEW28b6hTvZKkw+pwhcLquPU9vF8S7dgglFAWRzh+1Rvaxm5OjZNZ+zoKsvWjUx206C8IFrB2LBVlz4aXNzj5lUgzrEyedq4fXJQzk9ys7pLQxsx2C4LV0yVx3oFYRyBpQJBgqkJFRpBhqkBFRNBhVICOhaDGqQUZAqcEwgfSMUothBsEEOHp5LFsgvipRPHYpgDsvieIBQXWJv4dQwNy5JYwnBNXlDkIT4xyFEwFBeYeC0CIeOJEIaZ5dvEYnRXVTFwVuSaxI4D/GKxZNHjtewwAAAABJRU5ErkJggg==";
const SEND_BUTTON_IDLE_VECTOR_DATA_URL = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72" fill="none"><circle cx="36" cy="36" r="34" fill="#A8A8A8"/><path d="M36 19.5L49.5 47.5L36 39.5L22.5 47.5L36 19.5Z" fill="#FFFFFF"/></svg>',
)}`;

function modelBrand(profile?: ModelProfile): {asset?: string; fallback: string; slug: string} {
  const identity = `${profile?.providerType ?? ""} ${profile?.displayName ?? ""} ${profile?.defaultModel ?? ""}`.toLowerCase();
  if (identity.includes("deepseek")) return {asset: "deepseek-color.svg", fallback: "waves", slug: "deepseek"};
  if (identity.includes("claude")) return {asset: "claude-color.svg", fallback: "a-large-small", slug: "claude"};
  if (identity.includes("anthropic")) return {asset: "anthropic.svg", fallback: "a-large-small", slug: "anthropic"};
  if (identity.includes("gemini") || identity.includes("google")) return {asset: "gemini-color.svg", fallback: "sparkles", slug: "gemini"};
  if (identity.includes("mistral")) return {asset: "mistral-color.svg", fallback: "blocks", slug: "mistral"};
  if (identity.includes("ollama")) return {asset: "ollama.svg", fallback: "bot", slug: "ollama"};
  if (identity.includes("hugging") || identity.includes("hf-")) return {asset: "huggingface-color.svg", fallback: "smile", slug: "huggingface"};
  if (identity.includes("llama") || identity.includes("meta")) return {asset: "meta-color.svg", fallback: "infinity", slug: "meta"};
  if (identity.includes("grok")) return {asset: "grok.svg", fallback: "x", slug: "grok"};
  if (identity.includes("xai") || identity.includes("x.ai")) return {asset: "xai.svg", fallback: "x", slug: "xai"};
  if (identity.includes("openai") || /\bgpt[-\s]/.test(identity)) return {asset: "openai.svg", fallback: "flower-2", slug: "openai"};
  if (identity.includes("qwen") || identity.includes("tongyi") || identity.includes("alibaba")) return {asset: "qwen-color.svg", fallback: "cloud-cog", slug: "qwen"};
  if (identity.includes("kimi") || identity.includes("moonshot")) return {asset: "kimi-color.svg", fallback: "moon-star", slug: "kimi"};
  if (identity.includes("minimax")) return {asset: "minimax-color.svg", fallback: "audio-waveform", slug: "minimax"};
  if (identity.includes("chatglm")) return {asset: "chatglm-color.svg", fallback: "badge-zap", slug: "chatglm"};
  if (identity.includes("glm") || identity.includes("zhipu")) return {asset: "zhipu-color.svg", fallback: "badge-zap", slug: "glm"};
  return {fallback: identity.includes("compatible") ? "braces" : "sparkles", slug: "custom"};
}

function renderModelBrand(parent: HTMLElement, profile: ModelProfile | undefined, app: App): void {
  const brand = modelBrand(profile);
  parent.empty();
  parent.addClass(`la-model-icon--${brand.slug}`);
  parent.setAttribute("title", profile?.displayName || profile?.defaultModel || "自定义模型");
  if (!brand.asset) {
    setIcon(parent, brand.fallback);
    return;
  }
  const image = parent.createEl("img", {
    attr: {
      src: app.vault.adapter.getResourcePath(`${MODEL_ICON_ROOT}/${brand.asset}`),
      alt: "",
      draggable: "false",
    },
  });
  image.addEventListener("error", () => {
    image.remove();
    setIcon(parent, brand.fallback);
  }, {once: true});
}

function renderAssistantAvatar(parent: HTMLElement, app: App): HTMLElement {
  const avatar = parent.createDiv({cls: "la-message-avatar la-message-avatar--zhixu", attr: {"aria-label": "知序"}});
  const image = avatar.createEl("img", {
    attr: {
      src: app.vault.adapter.getResourcePath(ASSISTANT_AVATAR_PATH),
      alt: "",
      draggable: "false",
    },
  });
  image.addEventListener("error", () => {
    image.remove();
    setIcon(avatar, "bot");
  }, {once: true});
  return avatar;
}

function badge(parent: HTMLElement, kind: string, text: string): HTMLElement {
  return parent.createSpan({cls: `la-badge la-badge--${kind}`, text});
}

function button(
  parent: HTMLElement,
  label: string,
  action: () => Promise<void> | void,
  cls = "",
): HTMLButtonElement {
  const element = parent.createEl("button", {text: label, cls});
  element.onclick = async () => {
    element.disabled = true;
    try {
      await action();
    } catch (error: any) {
      new Notice(`${label}失败：${error.message}`);
    } finally {
      element.disabled = false;
    }
  };
  return element;
}

function emptyState(
  parent: HTMLElement,
  title: string,
  description: string,
  action?: {label: string; run: () => Promise<void> | void},
): HTMLElement {
  const root = parent.createDiv({cls: "la-empty"});
  const icon = root.createDiv({cls: "la-empty__icon"});
  setIcon(icon, "sparkles");
  root.createEl("strong", {text: title});
  root.createEl("p", {text: description});
  if (action) button(root, action.label, action.run, "mod-cta");
  return root;
}

function humanTitle(value: unknown, fallback = "未命名内容"): string {
  const source = String(value ?? "").split("/").pop()?.replace(/\.(md|pdf)$/i, "").trim() ?? "";
  if (!source) return fallback;
  const withoutId = source.replace(/[-_][0-9a-f]{10,}$/i, "").replace(/[-_]AI草稿$/i, "").trim();
  if (/^dragonnet$/i.test(withoutId)) return "Dragonnet 论文整理";
  return withoutId || fallback;
}

function statusLabel(state: string): string {
  return ({
    queued: "等待处理",
    running: "处理中",
    applying: "正在应用",
    prepared: "待确认",
    awaiting_confirmation: "待确认",
    applied: "已应用",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    pending: "待审核",
    accepted: "已接受",
    rejected: "已拒绝",
    proposed: "研究提案",
  } as Record<string, string>)[state] ?? state;
}

function statusKind(state: string): string {
  if (["failed", "rejected"].includes(state)) return "error";
  if (["applied", "completed", "accepted"].includes(state)) return "review";
  if (["running", "applying"].includes(state)) return "learn";
  return "source";
}

class TextPreviewModal extends Modal {
  constructor(
    app: App,
    private titleText: string,
    private text: string,
    private apply?: () => Promise<void>,
  ) {
    super(app);
  }

  onOpen(): void {
    this.contentEl.addClass("la-modal");
    this.contentEl.createEl("h2", {text: this.titleText});
    this.contentEl.createEl("pre", {text: this.text, cls: "la-code-preview"});
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    button(actions, "关闭", () => this.close());
    if (this.apply) {
      button(actions, "应用确认后的 Change Set", () => {
        new ExplicitConfirmModal(
          this.app,
          "确认应用 Change Set",
          "系统会重新校验 Bundle、PDF 和目标状态，并通过事务写入；不会再次调用模型。",
          async () => {
            await this.apply!();
            this.close();
          },
        ).open();
      }, "mod-cta");
    }
  }
}

class VaultNotePickerModal extends FuzzySuggestModal<TFile> {
  constructor(app: App, private selected: (file: TFile) => void) {
    super(app);
    this.setPlaceholder("搜索允许 Agent 读取的笔记…");
  }

  getItems(): TFile[] {
    return this.app.vault.getMarkdownFiles()
      .filter(file => isAssistantReadableVaultPath(file.path))
      .sort((left, right) => left.path.localeCompare(right.path, "zh-CN"));
  }

  getItemText(file: TFile): string {
    return `${file.basename}  ·  ${file.path}`;
  }

  onChooseItem(file: TFile): void {
    this.selected(file);
  }
}

class ExplicitConfirmModal extends Modal {
  constructor(
    app: App,
    private titleText: string,
    private description: string,
    private confirmed: () => Promise<void>,
  ) {
    super(app);
  }

  onOpen(): void {
    this.contentEl.addClass("la-modal");
    this.contentEl.createEl("h2", {text: this.titleText});
    this.contentEl.createEl("p", {text: this.description});
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    button(actions, "取消", () => this.close());
    button(actions, "确认", async () => {
      await this.confirmed();
      this.close();
    }, "mod-cta");
  }
}

class MicroConceptModal extends Modal {
  constructor(
    app: App,
    private concept: MicroConcept,
    private act: (action: string) => Promise<void>,
  ) {
    super(app);
  }

  onOpen(): void {
    this.contentEl.addClass("la-modal");
    this.contentEl.createEl("h2", {text: this.concept.title});
    badge(this.contentEl, this.concept.candidate ? "source" : "learn", this.concept.candidate ? "候选知识" : "关联知识");
    this.contentEl.createEl("p", {text: this.concept.explanation});
    this.contentEl.createEl("p", {text: `预计 ${this.concept.estimatedMinutes} 分钟`, cls: "la-muted"});
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    button(actions, "稍后", async () => { await this.act("later"); this.close(); });
    button(actions, "加入周末", async () => { await this.act("weekend"); this.close(); });
    button(actions, "收藏", async () => { await this.act("favorite"); this.close(); });
    button(actions, "立即了解", () => { new Notice("已打开微学习预览；不会自动创建知识笔记。"); }, "mod-cta");
  }
}

class StudySessionModal extends Modal {
  private correctness = 0.75;
  private notes = "";

  private sessionId = "";
  private startedAt = 0;
  private finished = false;
  private paused = false;

  constructor(
    app: App,
    private client: AgentClient,
    private recommendation: Recommendation,
    private recordEvent: (type: Parameters<typeof createLearningEvent>[0], sessionId?: string, payload?: Record<string, unknown>, durationMs?: number) => void,
  ) {
    super(app);
  }

  async onOpen(): Promise<void> {
    this.contentEl.addClass("la-modal", "la-study");
    this.contentEl.createEl("h2", {text: this.recommendation.title});
    const session = await this.client.post<any>("/study-sessions", {recommendation_id: this.recommendation.id});
    this.sessionId = session.session_id;
    this.startedAt = Date.now();
    this.recordEvent("study_session_started", this.sessionId, {category: this.recommendation.candidate ? "daily-knowledge" : this.recommendation.kind});
    this.contentEl.createEl("p", {text: `目标：在 ${this.recommendation.estimatedMinutes} 分钟内解释定义、条件与一个典型应用。`});
    const sequence = this.contentEl.createDiv({cls: "la-study__sequence"});
    for (const [icon, title, text] of [
      ["target", "学习目标", this.recommendation.learningOutcomes?.join("；") || `解释「${this.recommendation.title}」的定义、条件与边界。`],
      ["blocks", "前置知识", this.recommendation.prerequisites.map(item => item.title).join("、") || "无需额外前置"],
      ["lightbulb", "定义与直觉", this.recommendation.reason],
      ["triangle-alert", "关键条件与误解", "先区分概念成立条件、可识别条件与估计方法，避免把相关性当成因果结论。"],
    ]) {
      const row = sequence.createDiv(); setIcon(row.createSpan(), icon); const copy = row.createDiv(); copy.createEl("strong", {text: title}); copy.createEl("p", {text});
    }
    this.contentEl.createEl("h3", {text: "为什么现在学习"});
    this.contentEl.createEl("p", {text: this.recommendation.reason});
    if (this.recommendation.quizPreview) {
      this.contentEl.createEl("h3", {text: "小测试"});
      this.contentEl.createEl("p", {text: this.recommendation.quizPreview.question});
      this.recordEvent("quiz_started", this.sessionId);
    }
    const retelling = this.contentEl.createEl("textarea", {
      cls: "la-study__notes",
      attr: {placeholder: "写下你的复述、答案或疑问…", "aria-label": "学习复述"},
    });
    retelling.oninput = () => { this.notes = retelling.value; };
    const score = this.contentEl.createDiv({cls: "la-study__score"});
    score.createSpan({text: "自评正确度"});
    const input = score.createEl("input", {type: "range", attr: {min: "0", max: "1", step: "0.05", value: "0.75", "aria-label": "正确度"}});
    const value = score.createSpan({text: "75%"});
    input.oninput = () => {
      this.correctness = Number(input.value);
      value.setText(`${Math.round(this.correctness * 100)}%`);
    };
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    button(actions, "查看提示", () => {
      this.recordEvent("hint_opened", this.sessionId);
      new Notice(this.recommendation.quizPreview?.answerHint ?? "先从定义、条件和反例三个角度组织答案。", 5000);
    });
    button(actions, "这可能有误", () => {
      this.recordEvent("lesson_error_reported", this.sessionId, {verificationGrade: this.recommendation.verificationGrade ?? "C"});
      new Notice("已标记当前 Lesson Version，系统会重新运行机器验证；不会写入正式知识。", 6000);
    });
    button(actions, "暂停并退出", () => { this.paused = true; this.recordEvent("study_session_paused", this.sessionId); this.close(); });
    button(actions, "完成学习", async () => {
      const result = await this.client.post<any>(`/study-sessions/${session.session_id}/complete`, {
        correctness: this.correctness,
        notes: this.notes,
      });
      if (result.requires_confirmation !== true) throw new Error("Mastery confirmation protocol mismatch");
      this.recordEvent("quiz_completed", this.sessionId, {correctness: this.correctness});
      this.recordEvent("study_session_completed", this.sessionId, {correctness: this.correctness}, Math.max(0, Date.now() - this.startedAt));
      this.finished = true;
      if (result.path) {
        new ExplicitConfirmModal(
          this.app,
          "确认掌握度",
          `已自动安排 ${result.next_review} 复习。系统建议 mastery ${result.suggested_mastery}/4；只有确认后才修改正式笔记的掌握度。`,
          async () => {
            await this.client.post("/learning/mastery/confirm", {
              path: result.path,
              mastery: result.suggested_mastery,
              weak_points: [],
            });
            new Notice("掌握度已确认；复习安排保持不变");
          },
        ).open();
      } else new Notice(`学习完成，已安排 ${result.next_review} 复习；候选知识未写入正式知识库，mastery 未修改。`, 7000);
      this.close();
    }, "mod-cta");
  }

  onClose(): void {
    if (this.sessionId && !this.finished && !this.paused) this.recordEvent("study_session_abandoned", this.sessionId);
  }
}

export class LearningAgentMainView extends ItemView {
  private tab: MainTab = "assistant";
  private dashboard: DashboardData | null = null;
  private selectedId = "";
  private history: MainTab[] = ["assistant"];
  private historyIndex = 0;
  private query = "";
  private filter: RecommendationKind | "all" = "all";
  private sort: RecommendationSort = "smart";
  private sourceFilter: SourceFilter = "all";
  private loading = false;
  private refreshQueued = false;
  private mobileDetail = false;
  private assistantDrawerOpen = false;
  private assistantMode: "auto" | "organize" | "research" | "plan" = "auto";
  private assistantRun: any = null;
  private conversationId = "";
  private assistantMessages: any[] = [];
  private conversationMessageCache = new Map<string, Record<string, any>[]>();
  private assistantArtifacts: any[] = [];
  private assistantTaskThread: AssistantTaskThread | null = null;
  private assistantArtifactGroup: ArtifactGroup | null = null;
  private assistantContext: any = null;
  private assistantConversations: any[] = [];
  private assistantConversationQuery = "";
  private assistantInspectorTab: "context" | "sources" | "changes" = "context";
  private assistantInspectorOpen = true;
  private assistantNetworkEnabled = false;
  private assistantModelAuto = true;
  private assistantReasoningMode: "auto" | "deep" = "auto";
  private assistantPermissionMode: "ask" | "allow_all" = "ask";
  private assistantLiveRun: AssistantLiveRun = initialAssistantLiveRun();
  private assistantLiveRuns = new Map<string, AssistantLiveRun>();
  private assistantLiveTraceEls = new Map<string, HTMLElement>();
  private assistantLiveAssistantEls = new Map<string, HTMLElement>();
  private assistantLiveConfirmationEls = new Map<string, HTMLElement>();
  private assistantVisibleMessageLimit = 160;
  private assistantDraft = "";
  private assistantSubmitInFlight = false;
  private assistantRegenerateMessageId = "";
  private assistantRegenerateRunId = "";
  private assistantToday = new Set<string>();
  private pendingAttachments: any[] = [];
  private abort: AbortController | null = null;
  private dailyEngine: LocalDailyIntelligenceEngine;
  private dailyDashboard: DailyDashboard | null = null;
  private dailyCategory: DailyCategory = "daily-knowledge";
  private dailySort: DailySort = "recommendation";
  private dailyDetailTab = "why";
  private studyState: StudyWorkspaceState | null = null;
  private studyLesson: LessonBlueprint | null = null;
  private studyCorrectness = .75;
  private studyAssistTab: "concepts" | "notes" | "path" | "sources" = "concepts";
  private studyCompletion: any = null;
  private studyAssistOpen = false;
  private studyAssistantOpen = false;
  private studyAssistantMessages: Array<{role: "user" | "assistant"; content: string}> = [];
  private exposedRecommendations = new Set<string>();
  private markdown: ObsidianAssistantMarkdownRenderer;
  private agentRuntime: AgentRuntime;
  private settingsService: SettingsService;

  constructor(leaf: WorkspaceLeaf, private client: AgentClient, private dailyPreferences: () => DailyViewPreferences = () => ({trackingEnabled: true, recordLearningDuration: true, useQuizResults: true, useRecommendationFeedback: true, dailyKnowledgeCount: 1})) {
    super(leaf);
    this.dailyEngine = new LocalDailyIntelligenceEngine(client, () => this.dailyPreferences().trackingEnabled);
    this.markdown = new ObsidianAssistantMarkdownRenderer(this.app, this);
    const runtimes = new AgentRuntimeRegistry();
    runtimes.register("pi-agent", () => new PiAgentRuntime(client));
    this.agentRuntime = runtimes.create("pi-agent");
    this.settingsService = new SettingsService(client);
  }

  getViewType(): string { return MAIN_VIEW; }
  getDisplayText(): string { return "知序"; }
  getIcon(): string { return "brain-circuit"; }

  async onOpen(): Promise<void> {
    this.containerEl.addClass("la-main-host");
    this.registerDomEvent(this.containerEl, "keydown", event => this.onKey(event));
    void this.refresh();
  }

  async onClose(): Promise<void> {
    this.abort?.abort();
    this.agentRuntime.cleanup();
    await this.dailyEngine.dispose();
  }

  async flushLearningEvents(): Promise<void> { await this.dailyEngine.dispose(); }

  getState(): Record<string, unknown> {
    return {tab: this.tab, assistantDrawerOpen: this.assistantDrawerOpen, conversationId: this.conversationId,
      assistantInspectorTab: this.assistantInspectorTab, assistantInspectorOpen: this.assistantInspectorOpen,
      assistantNetworkEnabled: this.assistantNetworkEnabled, assistantModelAuto: this.assistantModelAuto,
      assistantPermissionMode: this.assistantPermissionMode,
      studyState: this.studyState};
  }

  async setState(state: any): Promise<void> {
    if (MODULES.some(item => item.id === state?.tab)) this.tab = state.tab;
    this.assistantDrawerOpen = Boolean(state?.assistantDrawerOpen);
    this.conversationId = String(state?.conversationId ?? "");
    if (["context", "sources", "changes"].includes(state?.assistantInspectorTab)) this.assistantInspectorTab = state.assistantInspectorTab;
    this.assistantInspectorOpen = state?.assistantInspectorOpen !== false;
    this.assistantNetworkEnabled = state?.assistantNetworkEnabled === true;
    this.assistantModelAuto = state?.assistantModelAuto !== false;
    this.assistantPermissionMode = state?.assistantPermissionMode === "allow_all" ? "allow_all" : "ask";
    if (state?.studyState?.recommendationId) this.studyState = state.studyState as StudyWorkspaceState;
    this.history = [this.tab];
    this.historyIndex = 0;
    void this.refresh();
  }

  setTab(tab: MainTab, record = true): void {
    if (record && tab !== this.tab) {
      this.history = this.history.slice(0, this.historyIndex + 1);
      this.history.push(tab);
      this.historyIndex = this.history.length - 1;
    }
    this.tab = tab;
    this.mobileDetail = false;
    if (this.loading) this.refreshQueued = true;
    else void this.refresh();
  }

  private root(): HTMLElement {
    // ItemView.contentEl remains available while Obsidian restores a leaf or
    // hot-reloads the plugin; containerEl.children[1] is not guaranteed then.
    const root = this.contentEl;
    root.empty();
    root.className = "view-content la-app";
    return root;
  }

  private moveHistory(delta: number): void {
    const next = this.historyIndex + delta;
    if (next < 0 || next >= this.history.length) return;
    this.historyIndex = next;
    this.setTab(this.history[next], false);
  }

  private renderModuleNav(parent: HTMLElement): void {
    const nav = parent.createEl("nav", {cls: "la-module-nav", attr: {"aria-label": "知序模块"}});
    const identity = nav.createDiv({cls: "la-nav-identity"});
    const mark = identity.createDiv({cls: "la-nav-identity__mark"});
    setIcon(mark, "book-open-check");
    const copy = identity.createDiv();
    copy.createEl("strong", {text: "知序"});
    copy.createEl("small", {text: this.dashboard ? "在线 · 协议 v1" : "正在连接本地服务"});

    if (this.tab === "assistant") {
      const actions = nav.createDiv({cls: "la-conversation-actions"});
      const create = actions.createEl("button"); setIcon(create.createSpan(), "plus"); create.createSpan({text: "新会话"}); create.createEl("kbd", {text: "⌘K"});
      create.onclick = async () => {
        const response = await this.client.post<any>("/conversations", {title: "新会话"});
        if (this.conversationId) this.assistantLiveRuns.set(this.conversationId, this.assistantLiveRun);
        this.conversationId = String(response.conversation.id); this.assistantMessages = []; this.assistantArtifacts = [];
        this.pendingAttachments = []; this.assistantDraft = ""; this.assistantRegenerateMessageId = ""; this.assistantRegenerateRunId = ""; this.assistantLiveRun = initialAssistantLiveRun(); this.assistantVisibleMessageLimit = 160; await this.refresh();
      };
      const search = actions.createDiv({cls: "la-conversation-search"}); setIcon(search.createSpan(), "search");
      const searchInput = search.createEl("input", {attr: {placeholder: "搜索对话", "aria-label": "搜索历史会话"}});
      searchInput.value = this.assistantConversationQuery;
      const recent = nav.createDiv({cls: "la-conversation-list"});
      const paint = (): void => {
        recent.empty();
        const query = this.assistantConversationQuery.trim().toLocaleLowerCase();
        const items = this.assistantConversations.filter(item => !query || String(item.title ?? "").toLocaleLowerCase().includes(query));
        const heading = recent.createDiv({cls: "la-conversation-list__head"}); heading.createSpan({text: query ? "搜索结果" : "最近对话"}); heading.createSpan({text: String(items.length)});
        if (!items.length) recent.createEl("p", {cls: "la-conversation-empty", text: query ? "没有匹配会话" : "开始对话后会保存在本地"});
        for (const conversation of items) {
          const row = recent.createEl("button", {cls: `la-conversation-row ${String(conversation.id) === this.conversationId ? "is-active" : ""}`});
          const title = row.createDiv(); title.createEl("strong", {text: humanTitle(conversation.title, "新会话")});
          title.createEl("small", {text: this.relativeTime(String(conversation.updatedAt ?? ""))});
          const more = row.createSpan({cls: "la-conversation-row__more", attr: {"aria-label": "会话菜单"}}); setIcon(more, "ellipsis");
          row.onclick = event => {
            if ((event.target as HTMLElement).closest(".la-conversation-row__more")) {
              event.stopPropagation();
              const menu = new Menu();
              menu.addItem(item => item.setTitle("导出到本地私有区").setIcon("download").onClick(async () => {
                const result = await this.client.post<any>(`/conversations/${encodeURIComponent(conversation.id)}/export`, {});
                new Notice(`已导出：${result.reference}`);
              }));
              menu.addItem(item => item.setTitle("删除会话").setIcon("trash-2").onClick(() => new ExplicitConfirmModal(
                this.app, "删除会话", "只删除本地会话、附件副本和绑定成果，不删除正式知识。", async () => {
                  await this.client.delete(`/conversations/${encodeURIComponent(conversation.id)}?confirm=true`);
                  if (this.conversationId === conversation.id) this.conversationId = "";
                  await this.refresh();
                },
              ).open()));
              menu.showAtMouseEvent(event as MouseEvent); return;
            }
            if (this.conversationId) this.assistantLiveRuns.set(this.conversationId, this.assistantLiveRun);
            this.conversationId = String(conversation.id); this.assistantLiveRun = this.assistantLiveRuns.get(this.conversationId) ?? initialAssistantLiveRun(); this.assistantVisibleMessageLimit = 160; void this.refresh();
          };
        }
      };
      searchInput.oninput = () => { this.assistantConversationQuery = searchInput.value; paint(); };
      paint();
    }

    const modules = nav.createDiv({cls: "la-nav-modules"});
    for (const item of MODULES) {
      const element = modules.createEl("button", {
        cls: `la-module-nav__item ${item.id === this.tab ? "is-active" : ""}`,
        attr: {"aria-current": item.id === this.tab ? "page" : "false", title: item.label},
      });
      setIcon(element.createSpan({cls: "la-module-nav__icon"}), item.icon);
      element.createSpan({text: item.label, cls: "la-module-nav__label"});
      element.onclick = () => this.setTab(item.id);
    }

    if (this.tab === "assistant") modules.addClass("la-nav-modules--assistant");
    const stats = nav.createDiv({cls: `la-nav-stats ${this.tab === "assistant" ? "la-nav-stats--assistant" : ""}`});
    const summary = this.dashboard?.summary;
    const total = summary?.suggested_minutes ?? 0;
    const completed = summary?.completed_minutes ?? 0;
    const percentage = total ? Math.min(100, Math.round(completed / total * 100)) : 0;
    const progress = stats.createDiv({cls: "la-nav-progress"});
    progress.createSpan({text: "今日进度"});
    progress.createEl("strong", {text: `${percentage}%`});
    const bar = progress.createDiv({cls: "la-job-progress"});
    bar.createDiv({attr: {style: `width:${percentage}%`}});
    this.navStat(stats, "运行任务", summary?.active_job_count ?? 0);

    const status = nav.createDiv({cls: "la-nav-runtime la-nav-runtime--footer"});
    setIcon(status.createSpan(), "shield-check");
    const statusCopy = status.createSpan();
    statusCopy.createEl("strong", {text: "Agent 已连接"});
    statusCopy.createEl("small", {text: "本地运行 · 正常"});
  }

  private relativeTime(value: string): string {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) return "";
    const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    if (minutes < 1_440) return `${Math.round(minutes / 60)} 小时前`;
    return `${Math.round(minutes / 1_440)} 天前`;
  }

  private navStat(parent: HTMLElement, label: string, value: number): void {
    const row = parent.createDiv({cls: "la-nav-stat"});
    row.createSpan({text: label});
    row.createEl("strong", {text: String(value)});
  }

  async refresh(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    const root = this.root();
    const workspace = root.createDiv({cls: `la-workspace ${this.tab === "assistant" ? "la-workspace--assistant" : ""}`});
    this.renderModuleNav(workspace);
    const stage = workspace.createDiv({cls: "la-module-stage"});
    const content = stage.createDiv({cls: `la-module-content la-module-content--${this.tab}`});
    const skeleton = content.createDiv({cls: "la-skeleton"});
    for (let index = 0; index < 5; index += 1) skeleton.createDiv();
    try {
      const response = this.tab === "today"
        ? await this.client.get<DashboardData & {learnerProfile?: any}>("/daily/dashboard")
        : await this.client.get<DashboardData>("/dashboard");
      this.dashboard = response;
      if (this.tab === "assistant") {
        const conversations = await this.client.get<any>("/conversations?limit=50");
        this.assistantConversations = conversations.items ?? [];
        if (!this.conversationId && this.assistantConversations.length) this.conversationId = String(this.assistantConversations[0].id);
      }
      if (this.tab === "today") {
        this.dailyDashboard = await this.dailyEngine.getDashboard({
          dashboard: response,
          learnerProfile: (response as any).learnerProfile,
          sort: this.dailySort,
          dailyKnowledgeQuota: this.dailyPreferences().dailyKnowledgeCount,
        });
        if (this.studyState?.sessionId && !["recommendation", "completed"].includes(this.studyState.mode)) {
          try {
            const session = await this.client.get<any>(`/study-sessions/${encodeURIComponent(this.studyState.sessionId)}`);
            const progress = session.progress ?? {};
            this.studyLesson = session.lesson ?? this.studyLesson;
            this.studyState = {
              ...this.studyState,
              mode: session.state === "paused" ? "paused" : session.state === "quiz" ? "quiz" : session.state === "completed" ? "completed" : "learning",
              sectionIndex: Number(progress.sectionIndex ?? this.studyState.sectionIndex),
              completedSectionIds: Array.isArray(progress.completedSectionIds) ? progress.completedSectionIds.map(String) : this.studyState.completedSectionIds,
              quizAnswers: progress.quizAnswers ?? this.studyState.quizAnswers,
              notes: String(progress.notes ?? this.studyState.notes),
            };
          } catch {
            this.studyState = {...this.studyState, mode: "error", error: "无法恢复上次学习会话"};
          }
        }
      }
      workspace.querySelector(".la-module-nav")?.remove();
      this.renderModuleNav(workspace);
      workspace.insertBefore(workspace.lastElementChild!, stage);
      content.empty();
      await this.renderTab(content);
    } catch (error: any) {
      content.empty();
      const box = content.createDiv({cls: "la-page-error"});
      setIcon(box.createDiv({cls: "la-page-error__icon"}), "circle-alert");
      box.createEl("h2", {text: "页面数据暂时无法加载"});
      box.createEl("p", {text: error.message});
      const actions = box.createDiv({cls: "la-empty__actions"});
      button(actions, "重试连接", () => this.refresh(), "mod-cta");
      button(actions, "打开诊断", () => { new Notice("请从知序菜单打开诊断并查看本地 Agent 日志。"); });
    } finally {
      this.loading = false;
      if (this.refreshQueued) {
        this.refreshQueued = false;
        void this.refresh();
      }
    }
  }

  private async renderTab(body: HTMLElement): Promise<void> {
    if (!this.dashboard) return;
    if (this.tab === "today") this.renderToday(body);
    else if (this.tab === "sources") await this.renderSources(body);
    else if (this.tab === "plan") await this.renderPlan(body);
    else await this.renderAssistant(body);
  }

  private moduleHeading(parent: HTMLElement, description?: string): HTMLElement {
    const root = parent.createDiv({cls: "la-module-heading"});
    const copy = root.createDiv();
    copy.createEl("h1", {text: MODULE_TITLES[this.tab]});
    if (description) copy.createEl("p", {text: description});
    return root;
  }

  private rerenderToday(): void {
    const content = this.containerEl.querySelector<HTMLElement>(".la-module-content");
    if (!content) return;
    content.empty();
    this.renderToday(content);
  }

  private renderToday(body: HTMLElement): void {
    body.addClass("la-page-frame", "la-today-focus", "la-daily-v2");
    const daily = this.dailyDashboard;
    const heading = this.moduleHeading(body, "基于你的学习状态、资料与行为反馈生成今日推荐");
    const headingTools = heading.createDiv({cls: "la-module-heading__tools"});
    headingTools.createSpan({text: new Date().toLocaleDateString("zh-CN", {month: "numeric", day: "numeric", weekday: "short"}), cls: "la-today-date"});
    if (daily?.todayPlan) {
      const completed = daily.todayPlan.items.filter(value => value.state === "completed").reduce((sum, value) => sum + value.minutes, 0);
      headingTools.createSpan({text: `已安排 ${daily.todayPlan.totalMinutes}/${daily.todayPlan.budgetMinutes} 分钟 · 已完成 ${completed}/${daily.todayPlan.totalMinutes} 分钟`, cls: "la-today-budget"});
    }
    if (daily?.todayConstraints?.noFormula) headingTools.createSpan({text: "今日先不看公式", cls: "la-today-budget"});
    const talk = button(headingTools, "与知序对话", () => this.setTab("assistant"), "mod-cta");
    setIcon(talk.createSpan({cls: "la-button-icon"}), "message-square");

    if (daily?.recentAdjustment?.undoAvailable) {
      const change = body.createDiv({cls: "la-today-adjustment"});
      setIcon(change.createSpan({cls: "la-today-adjustment__icon"}), "wand-sparkles");
      const copy = change.createDiv(); copy.createEl("strong", {text: "今日安排已调整"});
      copy.createEl("small", {text: `${daily.recentAdjustment.beforeMinutes} 分钟 → ${daily.recentAdjustment.afterMinutes} 分钟`});
      button(change, "查看变化", () => new TextPreviewModal(this.app, "今日安排变化", `调整前：${daily.recentAdjustment!.beforeMinutes} 分钟\n调整后：${daily.recentAdjustment!.afterMinutes} 分钟\n\n固定任务始终保留。`).open());
      button(change, "撤销", async () => {
        await this.client.post(`/daily/adjustments/${encodeURIComponent(daily.recentAdjustment!.actionId)}/undo`, {});
        new Notice("已撤销本次今日调整"); await this.refresh();
      });
    }
    const groups = daily?.categories ?? {review: [], learn: [], "daily-knowledge": [], explore: [], source: []};
    const definitions: Array<[DailyCategory, string, string, string]> = [
      ["review", "必须复习", "rotate-ccw", "基于遗忘曲线，及时巩固记忆"],
      ["learn", "下一步学习", "book-open", "继续主线学习，深化关键概念"],
      ["daily-knowledge", "每日新知识", "sparkles", "AI 生成的新知识，尚未存在于你的知识库"],
      ["explore", "探索", "compass", "拓展视野，发现关联与新视角"],
      ["source", "资料任务", "file-clock", "处理资料与笔记，完善知识体系"],
    ];
    if (!definitions.some(([id]) => id === this.dailyCategory) || !(groups[this.dailyCategory] ?? []).length) {
      this.dailyCategory = definitions.find(([id]) => (groups[id] ?? []).length)?.[0] ?? "review";
    }
    const currentItems = groups[this.dailyCategory] ?? [];
    if (!this.selectedId || !currentItems.some(item => item.id === this.selectedId)) this.selectedId = currentItems[0]?.id ?? "";
    const layout = body.createDiv({cls: `la-daily-layout ${this.mobileDetail ? "is-detail" : ""}`});
    const categoryPane = layout.createEl("aside", {cls: "la-daily-categories la-pane-scroll", attr: {"aria-label": "今日推荐类别"}});
    for (const [id, label, icon, description] of definitions) {
      const values = groups[id] ?? [];
      const card = categoryPane.createEl("button", {cls: `la-daily-category ${this.dailyCategory === id ? "is-active" : ""}`});
      const glyph = card.createSpan({cls: `la-daily-category__icon is-${id}`}); setIcon(glyph, icon);
      const copy = card.createDiv({cls: "la-daily-category__copy"});
      const title = copy.createDiv({cls: "la-daily-category__title"}); title.createEl("strong", {text: label}); title.createSpan({text: String(values.length)});
      copy.createEl("p", {text: description});
      const minutes = values.reduce((sum, item) => sum + item.estimatedMinutes, 0);
      const time = copy.createDiv({cls: "la-daily-category__time"}); setIcon(time.createSpan(), "clock-3"); time.createSpan({text: `${minutes} 分钟`});
      card.onclick = () => { this.dailyCategory = id; this.selectedId = values[0]?.id ?? ""; this.mobileDetail = false; this.rerenderToday(); };
    }
    this.renderLearningDirections(categoryPane, daily?.layers.validatedDirections ?? []);
    const listPane = layout.createEl("section", {cls: "la-daily-list", attr: {"aria-label": "推荐列表"}});
    const listHead = listPane.createDiv({cls: "la-daily-list__head"});
    listHead.createEl("strong", {text: `推荐列表（${currentItems.length} 项）`});
    const categorySelect = listHead.createEl("select", {cls: "la-daily-category-select", attr: {"aria-label": "推荐类别"}});
    for (const [id, label] of definitions) categorySelect.createEl("option", {value: id, text: label});
    categorySelect.value = this.dailyCategory;
    categorySelect.onchange = () => { this.dailyCategory = categorySelect.value as DailyCategory; this.selectedId = groups[this.dailyCategory][0]?.id ?? ""; this.rerenderToday(); };
    const sort = listHead.createEl("select", {attr: {"aria-label": "推荐排序"}});
    for (const [value, label] of [["recommendation", "推荐度 ↓"], ["due", "最需要复习"], ["mainline", "主线优先"], ["short", "时间最短"], ["easy", "难度最低"], ["recent", "最近生成"], ["confidence", "可信度"]]) sort.createEl("option", {value, text: label});
    sort.value = this.dailySort;
    sort.onchange = async () => {
      this.dailySort = sort.value as DailySort;
      if (this.dashboard) this.dailyDashboard = await this.dailyEngine.getDashboard({dashboard: this.dashboard, learnerProfile: daily?.learnerProfile, sort: this.dailySort, dailyKnowledgeQuota: this.dailyPreferences().dailyKnowledgeCount});
      this.rerenderToday();
    };
    const rows = listPane.createDiv({cls: "la-daily-list__rows la-pane-scroll", attr: {role: "listbox"}});
    for (const item of currentItems) this.renderDailyRecommendationRow(rows, item);
    if (!currentItems.length) {
      const modelConfigured = Boolean((this.dashboard as any)?.runtime?.modelConfigured);
      emptyState(rows, this.dailyCategory === "daily-knowledge" ? "每日新知识候选池为空" : "今天没有这一类任务", this.dailyCategory === "daily-knowledge" ? (modelConfigured ? "课程候选会在后台刷新；C 级内容不会进入今日核心推荐。" : "基础复习仍可使用。配置模型后，知序会生成并机器验证新的课程候选。") : "知序会在状态变化后自动更新这个类别。", this.dailyCategory === "daily-knowledge" ? {label: modelConfigured ? "刷新候选池" : "打开模型设置", run: modelConfigured ? async () => { await this.dailyEngine.refreshCurriculumCandidates({text: "为今日生成 1–3 个可验证的新知识候选"}); new Notice("候选池刷新完成"); await this.refresh(); } : () => this.setTab("assistant")} : undefined);
    }
    const listFoot = listPane.createDiv({cls: "la-daily-list__foot"});
    listFoot.createSpan({text: `推荐依据：学习行为 · 知识图谱 · 资料内容 · 可信来源`});

    const detailPane = layout.createEl("article", {cls: "la-daily-detail"});
    const selected = currentItems.find(item => item.id === this.selectedId);
    if (selected && this.studyState?.recommendationId === selected.id && this.studyState.mode !== "recommendation") this.renderStudyWorkspace(detailPane, selected);
    else if (selected) this.renderDailyDetail(detailPane, selected);
    else emptyState(detailPane, "选择一条推荐查看详情", "这里会展示推荐依据、已有知识、待补缺口、小测与来源。", {label: "与知序对话", run: () => this.setTab("assistant")});
  }

  private renderLearningDirections(parent: HTMLElement, directions: LearningDirection[]): void {
    const section = parent.createEl("section", {cls: "la-direction-section"});
    const head = section.createDiv({cls: "la-direction-section__head"});
    head.createEl("strong", {text: "可能的下一步"}); head.createSpan({text: `${Math.min(5, directions.length)} 个方向`});
    if (!directions.length) {
      section.createEl("p", {text: "继续对话或完成学习后，知序会生成近期、路线和探索方向。", cls: "la-muted"});
      return;
    }
    const horizonLabel = {near: "近期", route: "路线", exploration: "探索"};
    for (const direction of directions.slice(0, 5)) {
      const card = section.createEl("button", {cls: "la-direction-card la-direction-card--compact"});
      const title = card.createDiv({cls: "la-direction-card__title"});
      title.createEl("strong", {text: direction.title}); badge(title, direction.horizon === "exploration" ? "explore" : "learn", horizonLabel[direction.horizon]);
      card.createSpan({text: direction.horizon === "near" ? "近期" : direction.horizon === "route" ? "路线" : "探索", cls: "la-direction-card__hint"});
      card.onclick = () => new TextPreviewModal(this.app, direction.title, `为什么适合\n${direction.why.join("\n")}\n\n已有连接\n${direction.connections.join("、") || "当前学习路线"}\n\n前置知识\n${direction.prerequisites.join("、") || "无额外前置"}\n\n新颖性\n${direction.noveltyBasis}\n\n来源质量\n${direction.sourceQuality}`).open();
    }
  }

  private renderDailyRecommendationRow(parent: HTMLElement, item: Recommendation): void {
    if (!this.exposedRecommendations.has(item.id)) {
      this.exposedRecommendations.add(item.id);
      this.recordDailyEvent(createLearningEvent("recommendation_exposed", {type: "recommendation", id: item.id, recommendation: item}, {category: this.dailyCategory, route: item.route, verificationGrade: item.verificationGrade ?? "C"}));
    }
    const row = parent.createEl("button", {cls: `la-daily-row ${item.id === this.selectedId ? "is-selected" : ""}`, attr: {role: "option", "aria-selected": String(item.id === this.selectedId)}});
    const icon = row.createSpan({cls: `la-daily-row__icon is-${this.dailyCategory}`});
    setIcon(icon, this.dailyCategory === "review" ? "rotate-ccw" : this.dailyCategory === "learn" ? "book-open" : this.dailyCategory === "daily-knowledge" ? "sparkles" : this.dailyCategory === "source" ? "file-clock" : "search");
    const copy = row.createDiv({cls: "la-daily-row__copy"});
    const title = copy.createDiv({cls: "la-daily-row__title"}); title.createEl("strong", {text: item.title});
    const intensity = item.dailyScore ?? item.score;
    title.createSpan({text: intensity >= 80 ? "高" : intensity >= 60 ? "中" : "低", cls: `la-priority ${intensity >= 80 ? "is-high" : intensity >= 60 ? "is-medium" : "is-low"}`});
    const meta = copy.createDiv({cls: "la-daily-row__meta"}); meta.createSpan({text: `${item.estimatedMinutes} 分钟`}); meta.createSpan({text: this.dailyCategory === "daily-knowledge" ? "AI 新知识" : KIND_LABEL[item.kind]});
    copy.createEl("p", {text: item.reason});
    const actionLabel = item.dailyPlanState === "completed" ? "已完成" : item.dailyPlanState === "paused" ? "继续" : item.dailyPlanState === "in_progress" ? "学习中" : this.dailyCategory === "daily-knowledge" || this.dailyCategory === "explore" ? "查看" : "开始";
    const action = row.createSpan({text: actionLabel, cls: `la-daily-row__action ${item.dailyPlanState === "completed" ? "is-complete" : ""}`});
    row.onclick = () => {
      this.recordDailyEvent(createLearningEvent("recommendation_clicked", {type: "recommendation", id: item.id, recommendation: item}, {category: this.dailyCategory}));
      this.selectedId = item.id; this.mobileDetail = true; this.rerenderToday();
    };
    action.onclick = event => { event.stopPropagation(); row.click(); };
  }

  private renderDailyDetail(parent: HTMLElement, item: Recommendation): void {
    const header = parent.createDiv({cls: "la-daily-detail__head"});
    iconButton(header, "arrow-left", "返回推荐列表", () => { this.mobileDetail = false; this.rerenderToday(); }).addClass("la-mobile-back");
    const glyph = header.createSpan({cls: `la-daily-detail__glyph is-${this.dailyCategory}`}); setIcon(glyph, this.dailyCategory === "daily-knowledge" ? "sparkles" : this.dailyCategory === "review" ? "rotate-ccw" : "book-open");
    const copy = header.createDiv({cls: "la-daily-detail__title"}); copy.createEl("h1", {text: item.title});
    const tags = copy.createDiv({cls: "la-daily-detail__tags"});
    badge(tags, "explore", this.dailyCategory === "daily-knowledge" ? "AI 新知识" : KIND_LABEL[item.kind]);
    badge(tags, "learn", item.route === "mainline" ? "主线" : "支线");
    if (item.domain) badge(tags, "neutral", item.domain);
    const time = tags.createSpan(); setIcon(time, "clock-3"); time.appendText(`${item.estimatedMinutes} 分钟`);
    iconButton(header, "star", "收藏", () => void this.act(item, "favorite"));
    iconButton(header, "ellipsis", "更多操作", () => new Notice("可在底部选择稍后、明天、周末、不感兴趣或太难。"));

    const scroll = parent.createDiv({cls: "la-daily-detail__scroll la-pane-scroll"});
    if (this.dailyCategory === "daily-knowledge") {
      scroll.createEl("p", {text: "这是一个对你而言全新的知识点。基于你的学习轨迹与知识图谱生成，当前 Obsidian 正式知识库中尚未检测到等价内容。", cls: "la-daily-detail__lead"});
      if (item.verificationGrade === "B") scroll.createEl("p", {text: "来源有限，建议作为概念导读。", cls: "la-daily-verification-note"});
    } else scroll.createEl("p", {text: item.reason, cls: "la-daily-detail__lead"});
    const stats = scroll.createDiv({cls: "la-daily-detail__stats"});
    const confidence = item.confidence === undefined ? Math.round(Math.min(99, Math.max(1, item.score))) : Math.round(item.confidence * 100);
    for (const [label, value, icon] of [["预计时间", `${item.estimatedMinutes} 分钟`, "clock-3"], ["难度", ["简单", "中等", "较难"][Math.max(0, Math.min(2, (item.difficultyScore ?? 2) - 1))], "chart-no-axes-column-increasing"], ["可信度", `${confidence}%`, "shield-check"], ["推荐原因", item.dueState === "overdue" ? "到期复习" : item.candidate ? "知识桥梁" : "学习路径", "git-merge"]]) {
      const fact = stats.createDiv(); fact.createEl("small", {text: label}); const strong = fact.createEl("strong"); setIcon(strong.createSpan(), icon); strong.appendText(value);
    }
    const tabs = scroll.createDiv({cls: "la-daily-detail__tabs"});
    const tabDefinitions = [["why", "为什么推荐", "sparkles"], ["known", "你已经知道", "badge-check"], ["gap", "这次要补齐", "scan-search"], ["goals", "学习目标", "target"], ["quiz", "小测试预览", "clipboard-check"], ["sources", "依据来源", "badge-help"]];
    for (const [id, label, icon] of tabDefinitions) {
      const tab = tabs.createEl("button", {cls: this.dailyDetailTab === id ? "is-active" : ""}); setIcon(tab.createSpan(), icon); tab.appendText(label);
      tab.onclick = () => { this.dailyDetailTab = id; this.rerenderToday(); };
    }
    const panel = scroll.createDiv({cls: "la-daily-detail__panel"});
    if (this.dailyDetailTab === "why") {
      this.renderDailyEvidenceRow(panel, "lightbulb", "知识依据", item.reasonDetails[0] ?? item.reason);
      this.renderDailyEvidenceRow(panel, "activity", "行为依据", item.behaviorBasis?.length ? item.behaviorBasis.join("；") : "行为数据尚少，本次主要依据你的目标、知识图谱和近期资料。");
      this.renderDailyEvidenceRow(panel, "git-branch", "路线依据", item.route === "mainline" ? "连接当前统计与机器学习主线，优先补齐可复用概念。" : "作为支线探索，不挤占到期复习。" );
      this.renderDailyEvidenceRow(panel, "clock-3", "时间依据", `${item.estimatedMinutes} 分钟，适配今天的可用学习预算。`);
    } else if (this.dailyDetailTab === "known") {
      this.renderDailyChips(panel, item.prerequisites.length ? item.prerequisites.map(value => value.title) : item.relatedNotes.map(value => value.title), "当前没有检测到明确前置；学习时会从定义开始。", "你已经知道");
    } else if (this.dailyDetailTab === "gap") {
      this.renderDailyChips(panel, item.microConcepts.map(value => value.title), "需要补齐定义、条件、识别假设与估计误差之间的边界。", "这次要补齐");
    } else if (this.dailyDetailTab === "goals") {
      const goals = panel.createEl("ul", {cls: "la-daily-goals"});
      for (const goal of item.learningOutcomes?.length ? item.learningOutcomes : item.reasonDetails.slice(0, 4)) goals.createEl("li", {text: goal});
      if (!goals.children.length) goals.createEl("li", {text: `能够解释「${item.title}」的定义、成立条件和一个典型应用。`});
    } else if (this.dailyDetailTab === "quiz") {
      const quiz = panel.createDiv({cls: "la-daily-quiz"}); quiz.createEl("strong", {text: item.quizPreview?.question ?? `在什么条件下，「${item.title}」的结论不再成立？`});
      quiz.createEl("p", {text: item.quizPreview?.answerHint ?? "先写出定义，再逐项检查成立条件。"});
    } else {
      const sources = item.sourceBasis ?? [];
      if (!sources.length) panel.createEl("p", {text: "当前仅有本地知识关系作为来源，因此不会获得 A 级验证。", cls: "la-muted"});
      for (const source of sources) {
        const sourceRow = panel.createEl("button", {cls: "la-daily-source"}); setIcon(sourceRow.createSpan(), source.type === "local_knowledge" ? "file-text" : "external-link"); sourceRow.createSpan({text: source.title}); badge(sourceRow, item.verificationGrade === "A" ? "review" : "source", `验证 ${item.verificationGrade ?? "C"}`);
        sourceRow.onclick = () => { this.recordDailyEvent(createLearningEvent("source_opened", {type: "source", id: source.url ?? source.path ?? source.title, recommendation: item}, {sourceType: source.type})); if (source.path) void this.app.workspace.openLinkText(source.path, "", false); else if (source.url) window.open(source.url, "_blank", "noopener,noreferrer"); };
      }
    }
    const actions = parent.createDiv({cls: "la-daily-detail__actions"});
    button(actions, "稍后", () => this.act(item, "later"));
    button(actions, "加入明天", () => this.act(item, "tomorrow"));
    button(actions, "加入周末", () => this.act(item, "weekend"));
    button(actions, "不感兴趣", () => this.act(item, "not_interested"));
    button(actions, "太难", () => this.act(item, "too_hard"));
    if (item.kind === "source") button(actions, "查看资料", () => this.setTab("sources"), "mod-cta");
    else if (item.dailyPlanState === "completed") {
      const completed = button(actions, "今日已完成", () => { new Notice("本次学习已计入今日进度；可从计划页安排下一次复习。"); }); completed.disabled = true;
    }
    else {
      const resumable = item.dailyPlanState === "paused" || item.dailyPlanState === "in_progress";
      const start = button(actions, `${resumable ? "继续学习" : "开始学习"} · ${item.estimatedMinutes} 分钟`, () => this.startStudy(item), "mod-cta");
      setIcon(start.createSpan({cls: "la-button-icon"}), "play");
    }
  }

  private renderDailyEvidenceRow(parent: HTMLElement, icon: string, label: string, text: string): void {
    const row = parent.createDiv({cls: "la-daily-evidence-row"}); setIcon(row.createSpan({cls: "la-daily-evidence-row__icon"}), icon); row.createEl("strong", {text: label}); row.createEl("p", {text});
  }

  private renderDailyChips(parent: HTMLElement, values: string[], fallback: string, title: string): void {
    parent.createEl("strong", {text: title});
    if (!values.length) { parent.createEl("p", {text: fallback, cls: "la-muted"}); return; }
    const chips = parent.createDiv({cls: "la-chips"}); for (const value of values) chips.createEl("button", {text: value});
  }

  private recordDailyEvent(event: LearningEvent): void {
    const preferences = this.dailyPreferences();
    if (!preferences.trackingEnabled) return;
    if ((event.eventType.startsWith("quiz_") || event.eventType === "hint_opened") && !preferences.useQuizResults) return;
    if ((event.eventType.startsWith("recommendation_") || event.eventType.startsWith("plan_task_")) && !preferences.useRecommendationFeedback) return;
    if (!preferences.recordLearningDuration) delete event.durationMs;
    void this.dailyEngine.recordLearningEvent(event);
  }

  private recommendationRow(parent: HTMLElement, item: Recommendation): void {
    const row = parent.createDiv({
      cls: `la-rec-row ${item.id === this.selectedId ? "is-selected" : ""}`,
      attr: {role: "option", tabindex: "0", "aria-selected": String(item.id === this.selectedId)},
    });
    const icon = row.createDiv({cls: `la-kind-icon la-kind-icon--${item.kind}`});
    setIcon(icon, item.kind === "review" ? "notebook-tabs" : item.kind === "learn" ? "book-open" : item.kind === "source" ? "file-input" : "sparkles");
    const content = row.createDiv({cls: "la-rec-row__content"});
    const headline = content.createDiv({cls: "la-rec-row__headline"});
    headline.createEl("strong", {text: item.title});
    if (item.dueState === "overdue") badge(headline, "error", "到期");
    if ((item as any).candidate) badge(headline, "explore", "AI 补全");
    const meta = content.createDiv({cls: "la-rec-row__meta"});
    badge(meta, item.kind, KIND_LABEL[item.kind]);
    meta.createSpan({text: `${item.estimatedMinutes} 分钟`});
    meta.createSpan({text: `评分 ${Math.round(item.score)}`});
    content.createEl("p", {text: item.reason});
    const tags = content.createDiv({cls: "la-row-tags"});
    badge(tags, item.route === "mainline" ? "learn" : "explore", item.route === "mainline" ? "主线" : "支线");
    if (item.mastery !== undefined) badge(tags, "neutral", `掌握 ${item.mastery}/4`);
    iconButton(row, item.favorite ? "bookmark-check" : "bookmark", "收藏", () => void this.act(item, "favorite"));
    row.onclick = () => {
      this.selectedId = item.id;
      this.mobileDetail = true;
      this.rerenderToday();
    };
    row.oncontextmenu = event => {
      event.preventDefault();
      const menu = new Menu();
      menu.addItem(entry => entry.setTitle("收藏").setIcon("bookmark").onClick(() => void this.act(item, "favorite")));
      menu.addItem(entry => entry.setTitle("稍后").setIcon("clock").onClick(() => void this.act(item, "later")));
      menu.addItem(entry => entry.setTitle("不感兴趣").setIcon("thumbs-down").onClick(() => void this.act(item, "not_interested")));
      menu.showAtMouseEvent(event);
    };
  }

  private recommendationDetail(parent: HTMLElement, item: Recommendation): void {
    const header = parent.createDiv({cls: "la-pane-header la-detail-header"});
    iconButton(header, "arrow-left", "返回推荐列表", () => { this.mobileDetail = false; this.rerenderToday(); }).addClass("la-mobile-back");
    const title = header.createDiv();
    title.createEl("h1", {text: item.title});
    const meta = title.createDiv({cls: "la-rec-row__meta"});
    badge(meta, item.kind, KIND_LABEL[item.kind]);
    meta.createSpan({text: `评分 ${Math.round(item.score)}`});
    meta.createSpan({text: `${item.estimatedMinutes} 分钟`});
    iconButton(header, item.favorite ? "star" : "star", "收藏", () => void this.act(item, "favorite"));

    const scroll = parent.createDiv({cls: "la-pane-scroll la-detail-scroll"});
    this.detailSection(scroll, "推荐理由", section => {
      if ((item as any).candidate) {
        const notice = section.createDiv({cls: "la-warning-panel"});
        setIcon(notice.createSpan(), "sparkles");
        notice.createEl("strong", {text: "候选知识，尚未成为正式笔记"});
        notice.createEl("p", {text: "它来自 reviewed/core 的薄弱点与本地路线分析；学习后仍需通过 Change Set 和审核才能沉淀。"});
      }
      section.createEl("p", {text: item.reason});
      const reasons = section.createEl("ul", {cls: "la-reason-list"});
      for (const reason of item.reasonDetails) reasons.createEl("li", {text: reason});
    });
    this.referenceSection(scroll, "先修知识", item.prerequisites);
    this.referenceSection(scroll, "相关笔记", item.relatedNotes);
    if (item.quizPreview) {
      this.detailSection(scroll, "随堂小测（预览）", section => {
        const quiz = section.createDiv({cls: "la-quiz"});
        quiz.createEl("p", {text: item.quizPreview!.question});
        const answer = quiz.createEl("details");
        answer.createEl("summary", {text: "查看提示"});
        answer.createEl("p", {text: item.quizPreview!.answerHint});
      });
    }
    if (item.microConcepts.length) {
      this.detailSection(scroll, "相关微知识", section => {
        const chips = section.createDiv({cls: "la-chips"});
        for (const concept of item.microConcepts) {
          const chip = chips.createEl("button", {text: humanTitle(concept.title, "相关知识")});
          chip.onclick = () => new MicroConceptModal(this.app, concept, action => this.act(item, action)).open();
        }
      });
    }
    if (item.preparedId) {
      const technical = scroll.createEl("details", {cls: "la-technical"});
      technical.createEl("summary", {text: "技术详情"});
      technical.createEl("code", {text: item.preparedId});
    }

    const actions = parent.createDiv({cls: "la-pane-footer la-action-bar"});
    button(actions, "稍后学习", () => this.act(item, "later"));
    button(actions, "加入计划", () => this.act(item, "tomorrow"));
    if (item.kind === "source") button(actions, "查看 Change Set", () => this.openChangeSet(item), "mod-cta");
    else button(actions, `开始学习 · ${item.estimatedMinutes} 分钟`, () => this.startStudy(item), "mod-cta");
  }

  private detailSection(parent: HTMLElement, title: string, render: (section: HTMLElement) => void): void {
    const section = parent.createEl("section", {cls: "la-detail-section"});
    section.createEl("h2", {text: title});
    render(section);
  }

  private referenceSection(parent: HTMLElement, title: string, items: Array<{title: string; path: string}>): void {
    this.detailSection(parent, title, section => {
      if (!items.length) {
        section.createEl("p", {text: "当前没有额外要求", cls: "la-muted"});
        return;
      }
      const list = section.createDiv({cls: "la-reference-list"});
      for (const item of items) {
        const row = list.createEl("button");
        setIcon(row.createSpan({cls: "la-reference-icon"}), "file-text");
        row.createSpan({text: humanTitle(item.title)});
        setIcon(row.createSpan({cls: "la-reference-arrow"}), "chevron-right");
      }
    });
  }

  private async act(item: Recommendation, action: string): Promise<void> {
    const type = action === "later" ? "recommendation_snoozed" : ["tomorrow", "weekend"].includes(action) ? "recommendation_added_to_plan" : action === "not_interested" ? "recommendation_dismissed" : "recommendation_feedback";
    this.recordDailyEvent(createLearningEvent(type, {type: "recommendation", id: item.id, recommendation: item}, {action, category: this.dailyCategory, route: item.route}));
    await this.client.post(`/recommendations/${item.id}/action`, {action, details: {category: this.dailyCategory, route: item.route, verificationGrade: item.verificationGrade ?? "C"}});
    const labels: Record<string, string> = {
      later: "今天稍后不再推荐",
      tomorrow: "已加入明天",
      weekend: "已加入周末",
      favorite: "已收藏",
      not_interested: "已降低相关主题权重",
      too_hard: "已记录难度反馈，将优先寻找前置知识",
    };
    const fragment = document.createDocumentFragment();
    fragment.createSpan({text: `${labels[action] ?? "操作已记录"}。`});
    const undo = fragment.createEl("button", {text: "撤销", cls: "la-notice-undo"});
    const notice = new Notice(fragment, 8000);
    undo.onclick = async () => {
      await this.client.post(`/recommendations/${item.id}/action`, {action: "undo", details: {}});
      notice.hide();
      await this.refresh();
    };
    await this.refresh();
  }

  private async startStudy(item: Recommendation): Promise<void> {
    const base = this.studyState?.recommendationId === item.id ? this.studyState : initialStudyState(item.id);
    this.studyState = reduceStudyState(base, base.mode === "paused" ? "resume" : base.mode === "error" ? "retry" : "start");
    this.studyLesson = this.studyLesson?.recommendationId === item.id ? this.studyLesson : createLessonBlueprint(item);
    this.studyCompletion = null;
    this.rerenderToday();
    try {
      const response = await this.client.post<any>("/study-sessions", {recommendation_id: item.id});
      const progress = response.progress ?? response.details?.progress ?? {};
      const hydrated = {
        sessionId: String(response.session_id),
        sectionIndex: Number(progress.sectionIndex ?? 0),
        completedSectionIds: Array.isArray(progress.completedSectionIds) ? progress.completedSectionIds.map(String) : [],
        quizAnswers: progress.quizAnswers ?? {},
        notes: String(progress.notes ?? ""),
      };
      const started = this.studyState.mode === "starting" ? reduceStudyState(this.studyState, "started", hydrated) : {...this.studyState, ...hydrated};
      this.studyState = response.state === "paused" ? reduceStudyState(started, "pause") : response.state === "quiz" ? reduceStudyState(started, "open_quiz") : started;
      this.studyLesson = response.lesson ?? response.details?.lesson ?? this.studyLesson;
      this.recordDailyEvent(createLearningEvent(response.resumed ? "study_session_resumed" : "study_session_started", {type: "lesson", id: item.id, recommendation: item, sessionId: response.session_id}));
    } catch (error: any) {
      this.studyState = reduceStudyState(this.studyState, "fail", {error: String(error?.message ?? error)});
    }
    this.rerenderToday();
  }

  private studyProgress(): Record<string, unknown> {
    const state = this.studyState!;
    return {sectionIndex: state.sectionIndex, completedSectionIds: state.completedSectionIds, quizAnswers: state.quizAnswers, notes: state.notes};
  }

  private async patchStudy(action: string, nextMode?: "pause" | "resume" | "open_quiz" | "leave_quiz"): Promise<void> {
    if (!this.studyState?.sessionId) return;
    if (nextMode) this.studyState = reduceStudyState(this.studyState, nextMode);
    this.rerenderToday();
    try {
      await this.client.patch(`/study-sessions/${encodeURIComponent(this.studyState.sessionId)}`, {action, progress: this.studyProgress()});
    } catch (error: any) {
      this.studyState = reduceStudyState(this.studyState, "fail", {error: String(error?.message ?? error)});
      this.rerenderToday();
    }
  }

  private renderStudyWorkspace(parent: HTMLElement, item: Recommendation): void {
    const state = this.studyState!;
    const lesson = this.studyLesson ?? createLessonBlueprint(item);
    parent.addClass("la-study-workspace");
    parent.toggleClass("is-assist-open", this.studyAssistOpen);
    if (state.mode === "starting") {
      const loading = parent.createDiv({cls: "la-study-loading"});
      setIcon(loading.createSpan(), "loader-circle"); loading.createEl("h2", {text: "正在准备学习工作台"}); loading.createEl("p", {text: "恢复进度、课程结构与小测状态…"});
      button(loading, "退出", () => { this.studyState = initialStudyState(item.id); this.rerenderToday(); });
      return;
    }
    if (state.mode === "error") {
      const error = parent.createDiv({cls: "la-study-loading is-error"}); setIcon(error.createSpan(), "circle-alert");
      error.createEl("h2", {text: "学习工作台暂时无法继续"}); error.createEl("p", {text: state.error || "未知错误"});
      const actions = error.createDiv({cls: "la-empty__actions"}); button(actions, "重试", () => this.startStudy(item), "mod-cta"); button(actions, "返回推荐", () => { this.studyState = initialStudyState(item.id); this.rerenderToday(); });
      return;
    }
    const head = parent.createDiv({cls: "la-study-head"});
    const back = iconButton(head, "arrow-left", "返回推荐", () => { this.studyState = initialStudyState(item.id); this.studyLesson = null; this.rerenderToday(); });
    back.addClass("la-study-head__back");
    const title = head.createDiv({cls: "la-study-head__title"}); title.createEl("h1", {text: lesson.title});
    const tags = title.createDiv({cls: "la-study-head__tags"}); badge(tags, "explore", this.dailyCategory === "daily-knowledge" ? "每日新知识" : KIND_LABEL[item.kind]); badge(tags, "learn", state.mode === "completed" ? "已完成" : state.mode === "paused" ? "已暂停" : "学习中"); tags.createSpan({text: `预计 ${lesson.estimatedMinutes} 分钟`});
    if (state.mode === "paused") button(head, "继续学习", () => void this.patchStudy("resume", "resume"), "mod-cta");
    else if (state.mode !== "completed") button(head, "暂停学习", () => void this.patchStudy("pause", "pause"));
    button(head, "退出", () => this.exitStudy(item));
    const assistToggle = iconButton(head, "panel-right-open", "学习辅助", () => { this.studyAssistOpen = !this.studyAssistOpen; this.rerenderToday(); });
    assistToggle.addClass("la-study-assist-toggle");
    if (state.mode !== "completed") button(head, "完成本节", () => void this.completeStudy(item), "mod-cta");

    const completedCount = state.completedSectionIds.length;
    const progress = parent.createDiv({cls: "la-study-progress"});
    progress.createSpan({text: `当前进度 ${Math.min(lesson.sections.length, completedCount + 1)} / ${lesson.sections.length} 小节`});
    const bar = progress.createDiv(); bar.createDiv({attr: {style: `width:${state.mode === "completed" ? 100 : Math.round(completedCount / lesson.sections.length * 100)}%`}});

    if (state.mode === "completed") {
      const done = parent.createDiv({cls: "la-study-completed"}); setIcon(done.createSpan(), "badge-check"); done.createEl("h2", {text: "本次学习已完成"});
      const elapsed = state.startedAt ? Math.max(1, Math.round((Date.now() - new Date(state.startedAt).getTime()) / 60_000)) : lesson.estimatedMinutes;
      const quiz = lesson.quizzes[0]; const answer = quiz ? state.quizAnswers[quiz.id] : undefined; const quizText = answer === undefined ? "未作答" : answer === quiz.answerIndex ? "1 / 1" : "0 / 1";
      done.createEl("p", {text: `实际用时：${elapsed} 分钟 · 完成小节：${completedCount}/${lesson.sections.length} · 小测：${quizText} · 建议复习：${this.studyCompletion?.next_review ?? "已排入后续队列"}。mastery 仍需你确认。`});
      if (this.studyCompletion?.path) button(done, `确认建议掌握度 ${this.studyCompletion.suggested_mastery}/4`, () => new ExplicitConfirmModal(this.app, "确认掌握度", "只有确认后才会更新正式笔记；取消不会产生写入。", async () => {
        await this.client.post("/learning/mastery/confirm", {path: this.studyCompletion.path, mastery: this.studyCompletion.suggested_mastery, weak_points: []});
        new Notice("掌握度已由你确认");
      }).open(), "mod-cta");
      button(done, "撤销完成", () => void this.undoStudyCompletion(item));
      button(done, "生成学习笔记", () => void this.generateStudyNote(item), "mod-cta");
      button(done, "返回今日任务", () => { this.studyState = initialStudyState(item.id); void this.refresh(); });
      return;
    }

    const content = parent.createDiv({cls: "la-study-content"});
    const lessonPane = content.createDiv({cls: "la-study-lesson la-pane-scroll"});
    const goals = lessonPane.createDiv({cls: "la-study-goal"}); setIcon(goals.createSpan(), "target"); const goalCopy = goals.createDiv(); goalCopy.createEl("strong", {text: "本节学习目标"}); goalCopy.createEl("p", {text: lesson.goal});
    lesson.sections.forEach((section, index) => {
      const expanded = state.sectionIndex === index;
      const card = lessonPane.createEl("section", {cls: `la-study-section ${expanded ? "is-open" : ""} ${state.completedSectionIds.includes(section.id) ? "is-complete" : ""}`});
      const trigger = card.createEl("button", {cls: "la-study-section__head"});
      const number = trigger.createSpan({text: String(index + 1), cls: "la-study-section__number"}); if (state.completedSectionIds.includes(section.id)) { number.empty(); setIcon(number, "check"); }
      trigger.createEl("strong", {text: section.title}); trigger.createSpan({text: `${section.estimatedMinutes} 分钟`}); setIcon(trigger.createSpan(), expanded ? "chevron-up" : "chevron-down");
      trigger.onclick = () => { this.studyState = {...state, sectionIndex: index, updatedAt: new Date().toISOString()}; void this.patchStudy("progress"); this.rerenderToday(); };
      if (expanded) {
        const markdown = card.createDiv({cls: "la-study-section__markdown markdown-rendered"}); void this.markdown.render(markdown, section.markdown, item.sourcePath ?? "", this);
        const sectionActions = card.createDiv({cls: "la-study-section__actions"});
        button(sectionActions, index === lesson.sections.length - 1 ? "进入小测" : "完成并继续", () => {
          const complete = [...new Set([...state.completedSectionIds, section.id])];
          this.studyState = {...state, completedSectionIds: complete, sectionIndex: Math.min(lesson.sections.length - 1, index + 1), updatedAt: new Date().toISOString()};
          if (index === lesson.sections.length - 1) void this.patchStudy("open_quiz", "open_quiz"); else void this.patchStudy("progress");
          this.rerenderToday();
        }, "mod-cta");
      }
    });
    if (state.mode === "quiz") this.renderStudyQuiz(lessonPane, lesson);
    const notes = lessonPane.createEl("textarea", {cls: "la-study-notes", attr: {placeholder: "写下复述、疑问或困难点…", "aria-label": "学习复述与疑问"}});
    notes.value = state.notes;
    notes.oninput = () => { if (this.studyState) this.studyState = {...this.studyState, notes: notes.value, updatedAt: new Date().toISOString()}; };
    notes.onblur = () => void this.patchStudy("progress");

    const assist = content.createEl("aside", {cls: "la-study-assist la-pane-scroll"});
    const assistTabs = assist.createDiv({cls: "la-study-assist__tabs"});
    for (const [id, label] of [["concepts", "相关知识点"], ["notes", "相关笔记"], ["path", "学习路线"], ["sources", "来源依据"]] as const) {
      const tab = assistTabs.createEl("button", {text: label, cls: this.studyAssistTab === id ? "is-active" : ""}); tab.onclick = () => { this.studyAssistTab = id; this.rerenderToday(); };
    }
    const assistBody = assist.createDiv({cls: "la-study-assist__body"});
    if (this.studyAssistTab === "concepts") this.renderStudyAssistItems(assistBody, [...lesson.prerequisites, ...item.microConcepts.map(value => value.title)], "当前课程会从定义开始，无额外前置。", "circle-dot");
    else if (this.studyAssistTab === "notes") this.renderStudyNoteItems(assistBody, lesson);
    else if (this.studyAssistTab === "path") this.renderStudyAssistItems(assistBody, lesson.learningPath, "完成本节后再生成下一段路线。", "route");
    else this.renderStudySourceItems(assistBody, lesson);
    button(assist, "问助手", () => { this.studyAssistantOpen = true; this.rerenderToday(); });

    const foot = parent.createDiv({cls: "la-study-actions"});
    button(foot, "稍后继续", () => void this.patchStudy("pause", "pause"));
    button(foot, "标记困难", () => this.act(item, "too_hard"));
    button(foot, "加入复习", () => this.act(item, "tomorrow"));
    button(foot, "问助手", () => { this.studyAssistantOpen = true; this.rerenderToday(); });
    button(foot, "完成本节学习", () => void this.completeStudy(item), "mod-cta");
    if (this.studyAssistantOpen) this.renderStudyAssistantDrawer(parent, item, lesson);
  }

  private renderStudyAssistantDrawer(parent: HTMLElement, item: Recommendation, lesson: LessonBlueprint): void {
    const section = lesson.sections[this.studyState?.sectionIndex ?? 0];
    const drawer = parent.createEl("aside", {cls: "la-study-assistant-drawer", attr: {"aria-label": "学习助手"}});
    const head = drawer.createDiv({cls: "la-study-assistant-drawer__head"}); const copy = head.createDiv(); copy.createEl("strong", {text: "学习助手"}); copy.createEl("small", {text: `上下文：${section?.title ?? lesson.title}`});
    iconButton(head, "x", "关闭学习助手", () => { this.studyAssistantOpen = false; this.rerenderToday(); });
    const messages = drawer.createDiv({cls: "la-study-assistant-drawer__messages la-pane-scroll"});
    if (!this.studyAssistantMessages.length) messages.createEl("p", {text: "问题会自动带入当前知识、当前小节、相关笔记、来源和 Lesson Version。", cls: "la-muted"});
    for (const message of this.studyAssistantMessages) { const bubble = messages.createDiv({cls: `la-study-assistant-message is-${message.role}`}); bubble.createEl("p", {text: message.content}); }
    const shortcuts = drawer.createDiv({cls: "la-study-assistant-drawer__shortcuts"});
    const composer = drawer.createDiv({cls: "la-study-assistant-drawer__composer"});
    const input = composer.createEl("textarea", {attr: {placeholder: "询问当前小节…", "aria-label": "学习助手问题"}});
    for (const text of ["换一种直觉解释", "给一个更简单的例子", "解释这个公式", "它和前置知识有什么区别"]) button(shortcuts, text, () => { input.value = text; input.focus(); });
    const send = iconButton(composer, "send", "发送问题", () => void submit());
    const submit = async (): Promise<void> => {
      const question = input.value.trim(); if (!question || send.disabled) return;
      send.disabled = true; input.disabled = true; this.studyAssistantMessages.push({role: "user", content: question}); this.rerenderToday();
      try {
        const context = `当前课程：${lesson.title}\n当前小节：${section?.title ?? ""}\nLesson Version：${lesson.version}\n相关笔记：${lesson.relatedNotes.map(value => value.title).join("、") || "无"}\n来源：${lesson.sources.map(value => value.title).join("、") || "本地学习路线"}`;
        const result = await this.client.post<any>("/intake/submit", {message: `${question}\n\n[学习上下文]\n${context}`, conversation_id: this.conversationId || undefined, mode: "auto", references: lesson.relatedNotes.map(value => ({kind: "vault_note", path: value.path})), active_recommendation_id: item.id, options: {allow_network: false}}, `study-tutor-${Date.now()}`);
        this.conversationId = String(result.conversation?.id ?? this.conversationId);
        const response = String(result.message ?? result.summary ?? result.run?.result?.message ?? result.conversation?.messages?.at(-1)?.content ?? "已接收问题；任务结果会保留在助手会话中。");
        this.studyAssistantMessages.push({role: "assistant", content: response});
      } catch (error: any) {
        this.studyAssistantMessages.push({role: "assistant", content: `暂时无法回答：${String(error?.message ?? error)}`});
      } finally { this.rerenderToday(); }
    };
    input.onkeydown = event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } };
  }

  private renderStudyQuiz(parent: HTMLElement, lesson: LessonBlueprint): void {
    const quiz = lesson.quizzes[0]; if (!quiz || !this.studyState) return;
    const card = parent.createDiv({cls: "la-study-quiz"}); card.createEl("strong", {text: "随堂小测"}); card.createEl("p", {text: quiz.question});
    quiz.options.forEach((option, index) => {
      const label = card.createEl("label"); const input = label.createEl("input", {type: "radio", attr: {name: `quiz-${quiz.id}`, value: String(index)}}); input.checked = this.studyState!.quizAnswers[quiz.id] === index; label.createSpan({text: option});
      input.onchange = () => { this.studyState = {...this.studyState!, quizAnswers: {...this.studyState!.quizAnswers, [quiz.id]: index}}; void this.patchStudy("progress"); this.rerenderToday(); };
    });
    if (this.studyState.quizAnswers[quiz.id] !== undefined) card.createEl("p", {text: this.studyState.quizAnswers[quiz.id] === quiz.answerIndex ? `回答正确：${quiz.explanation}` : `再想一想：${quiz.explanation}`, cls: this.studyState.quizAnswers[quiz.id] === quiz.answerIndex ? "is-correct" : "is-wrong"});
  }

  private renderStudyAssistItems(parent: HTMLElement, values: string[], fallback: string, icon: string): void {
    if (!values.length) { parent.createEl("p", {text: fallback, cls: "la-muted"}); return; }
    for (const value of values) { const row = parent.createEl("button", {cls: "la-study-assist__item"}); setIcon(row.createSpan(), icon); row.createSpan({text: value}); row.onclick = () => new TextPreviewModal(this.app, value, `与当前课程「${this.studyLesson?.title ?? ""}」相关。\n\n该预览不会创建或修改正式知识笔记。`).open(); }
  }

  private renderStudyNoteItems(parent: HTMLElement, lesson: LessonBlueprint): void {
    if (!lesson.relatedNotes.length) { parent.createEl("p", {text: "没有匹配的 reviewed/core 笔记。", cls: "la-muted"}); return; }
    for (const note of lesson.relatedNotes) {
      const row = parent.createDiv({cls: "la-study-assist__item la-study-note-item"}); setIcon(row.createSpan(), "file-text"); const copy = row.createDiv(); copy.createEl("strong", {text: note.title}); copy.createEl("small", {text: note.path || "仅有关系，暂无路径"});
      if (note.path) {
        iconButton(row, "eye", "预览笔记", () => void (async () => {
          const file = this.app.vault.getAbstractFileByPath(note.path) as any;
          if (!file) { new Notice("笔记文件不存在"); return; }
          new TextPreviewModal(this.app, note.title, await this.app.vault.cachedRead(file)).open();
        })());
        iconButton(row, "external-link", "在 Obsidian 中打开", () => void this.app.workspace.openLinkText(note.path, "", false));
      }
    }
  }

  private renderStudySourceItems(parent: HTMLElement, lesson: LessonBlueprint): void {
    if (!lesson.sources.length) { parent.createEl("p", {text: "当前只有本地知识关系；不会冒充已验证来源。", cls: "la-muted"}); return; }
    for (const source of lesson.sources) {
      const row = parent.createEl("button", {cls: "la-study-assist__item"}); setIcon(row.createSpan(), source.path ? "file-text" : "external-link"); const copy = row.createDiv(); copy.createEl("strong", {text: source.title}); copy.createEl("small", {text: source.type});
      row.onclick = () => { if (source.path) void this.app.workspace.openLinkText(source.path, "", false); else if (source.url) window.open(source.url, "_blank", "noopener,noreferrer"); };
    }
  }

  private async completeStudy(item: Recommendation): Promise<void> {
    if (!this.studyState?.sessionId || !this.studyLesson || this.studyState.mode === "completing") return;
    this.studyState = reduceStudyState(this.studyState, "complete"); this.rerenderToday();
    try {
      const quiz = this.studyLesson.quizzes[0]; const answer = quiz ? this.studyState.quizAnswers[quiz.id] : undefined;
      this.studyCorrectness = answer === undefined ? .7 : answer === quiz.answerIndex ? 1 : .45;
      this.studyCompletion = await this.client.post<any>(`/study-sessions/${encodeURIComponent(this.studyState.sessionId)}/complete`, {correctness: this.studyCorrectness, notes: this.studyState.notes, progress: this.studyProgress()});
      this.studyState = reduceStudyState(this.studyState, "completed", {completedSectionIds: this.studyLesson.sections.map(section => section.id)});
      this.recordDailyEvent(createLearningEvent("study_session_completed", {type: "lesson", id: item.id, recommendation: item, sessionId: this.studyState.sessionId}, {correctness: this.studyCorrectness}));
    } catch (error: any) {
      this.studyState = reduceStudyState(this.studyState, "fail", {error: String(error?.message ?? error)});
    }
    this.rerenderToday();
  }

  private async exitStudy(item: Recommendation): Promise<void> {
    const state = this.studyState;
    if (state?.sessionId && !["completed", "error"].includes(state.mode)) {
      try { await this.client.patch(`/study-sessions/${encodeURIComponent(state.sessionId)}`, {action: "pause", progress: this.studyProgress()}); } catch { /* The local state still returns safely. */ }
      this.recordDailyEvent(createLearningEvent("study_session_paused", {type: "lesson", id: item.id, recommendation: item, sessionId: state.sessionId}));
    }
    this.studyState = initialStudyState(item.id); this.studyLesson = null; this.studyAssistantOpen = false; this.rerenderToday();
    await this.refresh();
  }

  private async undoStudyCompletion(item: Recommendation): Promise<void> {
    if (!this.studyState?.sessionId || this.studyState.mode !== "completed") return;
    await this.client.patch(`/study-sessions/${encodeURIComponent(this.studyState.sessionId)}`, {action: "undo_complete", progress: this.studyProgress()});
    this.studyState = reduceStudyState(this.studyState, "undo"); this.studyCompletion = null;
    new Notice("已撤销完成状态，学习进度仍然保留"); await this.refresh();
  }

  private async generateStudyNote(item: Recommendation): Promise<void> {
    const result = await this.client.post<any>("/intake/submit", {
      message: `请基于刚完成的课程「${item.title}」生成一篇学习笔记草稿。必须通过 AI Draft / Change Set 提案，不得创建或覆盖 reviewed/core。`,
      conversation_id: this.conversationId || undefined,
      mode: "organize",
      active_recommendation_id: item.id,
      references: (this.studyLesson?.relatedNotes ?? []).map(value => ({kind: "vault_note", path: value.path})),
      options: {allow_network: false},
    }, `study-note-${this.studyState?.sessionId ?? Date.now()}`);
    this.conversationId = String(result.conversation?.id ?? this.conversationId);
    this.assistantRun = result.run; this.assistantArtifacts = result.artifacts ?? []; this.assistantTaskThread = result.task_thread ?? null; this.assistantArtifactGroup = result.artifact_group ?? null;
    new Notice("学习笔记已生成待确认提案；尚未写入正式知识"); this.setTab("assistant");
  }

  private async openChangeSet(item: Recommendation): Promise<void> {
    if (!item.preparedId) return;
    const data = await this.client.get<any>(`/prepared/${encodeURIComponent(item.preparedId)}`);
    new TextPreviewModal(this.app, `Change Set · ${item.title}`, data.preview, async () => {
      await this.client.post("/prepared/apply", {prepared_id: item.preparedId});
      await this.refresh();
    }).open();
  }

  private async renderSources(body: HTMLElement): Promise<void> {
    body.addClass("la-page-frame", "la-material-center");
    const [{bundles}, {jobs}, research, materials] = await Promise.all([
      this.client.get<any>("/prepared"),
      this.client.get<any>("/jobs"),
      this.client.get<any>("/research-bundles"),
      this.client.get<any>("/materials?limit=200"),
    ]);
    const heading = this.moduleHeading(body, "集中查看对话、PDF、网页、文件夹和文本的处理状态");
    const headingTools = heading.createDiv({cls: "la-module-heading__tools"});
    const importButton = button(headingTools, "导入资料", () => this.setTab("assistant"), "mod-cta");
    setIcon(importButton.createSpan({cls: "la-button-icon"}), "plus-circle");

    const controls = body.createDiv({cls: "la-page-toolbar la-material-controls"});
    const search = controls.createDiv({cls: "la-search"});
    setIcon(search.createSpan(), "search");
    const searchInput = search.createEl("input", {attr: {placeholder: "搜索资料…", "aria-label": "搜索资料"}});
    searchInput.value = this.query;
    searchInput.onchange = () => { this.query = searchInput.value; void this.refresh(); };
    iconButton(controls, "search", "搜索", () => searchInput.focus());
    iconButton(controls, "list-filter", "筛选", () => new Notice("可通过下方状态标签筛选资料"));
    iconButton(controls, "arrow-down-up", "排序", () => new Notice("资料已按最近更新时间排序"));

    const normalized: any[] = [
      ...(materials.items ?? []).map((item: any) => ({
        id: item.id, type: "intake", title: humanTitle(item.title, "资料处理会话"), state: item.status,
        progress: Number(item.progress ?? 0), updatedAt: item.updatedAt, source: item.attachments?.[0]?.kind ?? "对话输入",
        artifactCount: Number(item.artifactCount ?? 0), attachmentCount: item.attachments?.length ?? 0, raw: item,
      })),
      ...(research.items ?? []).map((item: any) => ({
        id: item.id, type: "research", title: item.title, state: item.status ?? "completed", progress: 100,
        updatedAt: item.retrieved_at, source: "网络链接", artifactCount: item.source_count ?? 0, raw: item,
      })),
      ...bundles.map((item: any) => ({
        id: item.prepared_id, type: "prepared", title: humanTitle(item.source_path || "", item.state === "applied" ? "已应用 PDF 资料" : "PDF 资料"), state: item.state,
        progress: item.state === "applied" ? 100 : 84, updatedAt: item.created_at, source: "PDF", artifactCount: item.target_count ?? 0, raw: item,
      })),
      ...jobs.filter((job: any) => !job.prepared_id).map((job: any) => ({
        id: job.job_id, type: "job", title: humanTitle(readableJobTitle(job).replace(/^处理\s*/, ""), "资料任务"), state: job.state,
        progress: Number(job.progress ?? 0), updatedAt: job.updated_at, source: job.payload?.source_type ?? "本地导入", artifactCount: 0, raw: job,
      })),
    ];
    const unique = Array.from(new Map(normalized.map(item => [item.id, item])).values()) as any[];
    const belongs = (item: any, filter: SourceFilter): boolean => {
      if (filter === "all") return true;
      if (filter === "pending") return ["prepared", "awaiting_confirmation"].includes(item.state);
      if (filter === "running") return ["queued", "running", "applying"].includes(item.state);
      if (filter === "research") return item.type === "research";
      if (filter === "applied") return ["applied", "completed", "accepted"].includes(item.state);
      if (filter === "failed") return item.state === "failed";
      return ["cancelled", "rejected"].includes(item.state);
    };
    const filterLabels: Record<SourceFilter, string> = {all: "全部", pending: "待确认", running: "处理中", research: "研究包", applied: "已完成", failed: "失败", history: "历史"};
    const filters = body.createDiv({cls: "la-filter-row la-material-filter-row"});
    for (const value of Object.keys(filterLabels) as SourceFilter[]) {
      const count = unique.filter(item => belongs(item, value)).length;
      const chip = filters.createEl("button", {text: `${filterLabels[value]} ${count}`, cls: this.sourceFilter === value ? "is-active" : ""});
      chip.onclick = () => { this.sourceFilter = value; void this.refresh(); };
    }
    const query = this.query.trim().toLocaleLowerCase();
    const items = unique.filter(item => belongs(item, this.sourceFilter) && (!query || item.title.toLocaleLowerCase().includes(query))).sort((a, b) => String(b.updatedAt ?? "").localeCompare(String(a.updatedAt ?? "")));
    const list = body.createDiv({cls: "la-material-center__list la-pane-scroll"});
    if (!items.length) emptyState(list, "当前没有匹配资料", "把 PDF、链接、文件夹或文本拖入助手，处理任务会自动出现在这里。", {label: "打开助手", run: () => this.setTab("assistant")});
    const openDetail = async (item: any): Promise<void> => {
      let detail = item.raw;
      if (item.type === "intake") detail = (await this.client.get<any>(`/materials/${encodeURIComponent(item.id)}`)).material;
      const lines = [
        `状态：${statusLabel(item.state)}`, `进度：${item.progress}%`, `来源：${item.source}`,
        `结构化成果：${item.artifactCount} 项`, `更新时间：${item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "—"}`,
      ];
      const artifacts = detail.artifacts ?? [];
      if (artifacts.length) lines.push("", "已生成：", ...artifacts.map((artifact: any) => `- ${artifact.title} · ${artifact.type} · ${statusLabel(artifact.status)}`));
      new TextPreviewModal(this.app, item.title, lines.join("\n")).open();
    };
    for (const item of items) {
      const row = list.createDiv({cls: "la-material-center-row", attr: {tabindex: "0", role: "button"}});
      const icon = row.createSpan({cls: `la-kind-icon la-kind-icon--${statusKind(item.state)}`});
      setIcon(icon, item.source === "PDF" || item.source === "pdf" ? "file-text" : item.type === "research" ? "link" : "folder-open");
      const content = row.createDiv({cls: "la-material-center-row__content"});
      const top = content.createDiv({cls: "la-material-center-row__title"});
      top.createEl("strong", {text: item.title});
      const sourceMeta = content.createDiv({cls: "la-rec-row__meta"});
      sourceMeta.createSpan({text: `来源 · ${item.source}`});
      sourceMeta.createSpan({text: item.updatedAt ? new Date(item.updatedAt).toLocaleString() : "刚刚"});
      const facts = content.createDiv({cls: "la-material-center-row__facts"});
      if (item.attachmentCount) facts.createSpan({text: `${item.attachmentCount} 个输入`});
      facts.createSpan({text: `${item.artifactCount} 个结构化成果`});
      const state = row.createDiv({cls: "la-material-center-row__state"});
      badge(state, statusKind(item.state), statusLabel(item.state));
      if (!["completed", "applied", "accepted"].includes(item.state)) {
        state.createSpan({text: `${item.progress}%`});
        const progress = state.createDiv({cls: "la-job-progress"}); progress.createDiv({attr: {style: `width:${item.progress}%`}});
      }
      iconButton(row, "ellipsis", "更多", () => {
        const menu = new Menu();
        menu.addItem(entry => entry.setTitle("查看详情").setIcon("scan-eye").onClick(() => void openDetail(item)));
        menu.addItem(entry => entry.setTitle("在助手中继续").setIcon("messages-square").onClick(() => { this.conversationId = item.raw.conversationId ?? ""; this.setTab("assistant"); }));
        menu.showAtPosition({x: row.getBoundingClientRect().right - 16, y: row.getBoundingClientRect().top + 32});
      });
      row.onclick = event => { if (!(event.target as HTMLElement).closest("button")) void openDetail(item); };
      row.onkeydown = event => { if (event.key === "Enter") void openDetail(item); };
    }
    const footer = body.createDiv({cls: "la-material-center__footer"});
    footer.createSpan({text: `共 ${items.length} 项`});
    button(footer, "刷新", () => this.refresh());
  }

  private renderMaterialDetail(parent: HTMLElement, item: any, titleText: string): void {
    if (item.type === "research") {
      this.renderResearchMaterialDetail(parent, item.bundle); return;
    }
    const job = item.job;
    const header = parent.createDiv({cls: "la-pane-header la-detail-header"});
    const title = header.createDiv();
    title.createEl("h1", {text: titleText});
    const meta = title.createDiv({cls: "la-rec-row__meta"});
    badge(meta, statusKind(item.state), statusLabel(item.state));
    meta.createSpan({text: job?.kind === "prepare-pdf" ? "PDF" : "本地资料"});
    iconButton(header, "ellipsis", "更多操作", () => new Notice("技术操作已收纳到页面底部。"));
    const scroll = parent.createDiv({cls: "la-pane-scroll la-detail-scroll"});
    this.detailSection(scroll, "Change Set 概览", section => {
      const facts = section.createDiv({cls: "la-change-facts"});
      for (const [label, value, kind] of [["创建", item.bundle ? "待检查" : "—", "neutral"], ["更新", item.state === "applied" ? "已提交" : "待确认", "learn"], ["建议", "需人工审核", "explore"], ["跳过", "低置信度", "source"]]) {
        const fact = facts.createDiv({cls: `la-change-fact la-change-fact--${kind}`});
        fact.createEl("small", {text: label});
        fact.createEl("strong", {text: value});
      }
      section.createEl("p", {text: item.state === "prepared" ? "分析已完成，等待你检查来源、变更与目标位置后确认应用。" : item.state === "applied" ? "Change Set 已经通过事务应用，不再出现在待确认队列。" : job?.error || "处理状态已从本地 Agent 同步。"});
    });
    this.detailSection(scroll, "关键变更", section => {
      const list = section.createEl("ul", {cls: "la-change-list"});
      list.createEl("li", {text: "来源索引与知识草稿保持可追溯关系"});
      list.createEl("li", {text: "正式笔记只通过确认后的事务写入"});
      list.createEl("li", {text: "reviewed/core 冲突只生成更新建议"});
    });
    this.detailSection(scroll, "目标位置", section => {
      section.createEl("p", {text: "来源索引、论文草稿、主线主题与少量可复用概念将按 Change Set 中的路径写入。"});
    });
    this.detailSection(scroll, "证据支持", section => {
      const evidence = section.createDiv({cls: "la-evidence-list"});
      for (const text of ["页码证据随关键结论保存", "来源哈希在应用前重新校验", "目标状态变化时停止应用"]) {
        const row = evidence.createDiv();
        setIcon(row.createSpan(), "check-circle-2");
        row.createSpan({text});
      }
    });
    if (job?.error) {
      const warning = scroll.createDiv({cls: "la-warning-panel"});
      setIcon(warning.createSpan(), "triangle-alert");
      warning.createEl("strong", {text: "处理失败"});
      warning.createEl("p", {text: job.error});
    }
    const technical = scroll.createEl("details", {cls: "la-technical"});
    technical.createEl("summary", {text: "技术详情"});
    technical.createEl("pre", {text: JSON.stringify({prepared_id: item.bundle?.prepared_id || job?.prepared_id, job_id: job?.job_id, state: item.state}, null, 2)});

    const actions = parent.createDiv({cls: "la-pane-footer la-action-bar"});
    if (item.state === "prepared") {
      button(actions, "查看详情", () => this.openChangeSet({preparedId: item.bundle.prepared_id, title: titleText} as Recommendation));
      button(actions, "应用确认", () => this.openChangeSet({preparedId: item.bundle.prepared_id, title: titleText} as Recommendation), "mod-cta");
    } else if (item.state === "failed") {
      button(actions, "查看日志", () => new TextPreviewModal(this.app, "失败详情", job.error || "未知错误").open());
      button(actions, "重试", async () => { await this.client.post(`/jobs/${job.job_id}/retry`, {}); await this.refresh(); }, "mod-cta");
      button(actions, "删除记录", () => new ExplicitConfirmModal(this.app, "删除失败记录", "只删除运行记录，不删除资料或知识文件。", async () => {
        await this.client.post(`/jobs/${job.job_id}/delete`, {});
        await this.refresh();
      }).open());
    } else {
      button(actions, "查看日志", () => new TextPreviewModal(this.app, "处理记录", job?.error || "当前没有错误日志").open());
      button(actions, "刷新状态", () => this.refresh(), "mod-cta");
    }
  }

  private renderMaterialInspector(parent: HTMLElement, item: any, titleText: string): void {
    if (item.type === "research") {
      const header = parent.createDiv({cls: "la-pane-header"}); header.createEl("h2", {text: "研究状态"});
      const scroll = parent.createDiv({cls: "la-pane-scroll la-inspector-scroll"});
      const info = scroll.createDiv({cls: "la-inspector-card"}); info.createEl("h3", {text: "Research Bundle"});
      this.inspectorFact(info, "来源数量", String(item.bundle.source_count ?? 0));
      this.inspectorFact(info, "预计时间", `${item.bundle.estimated_minutes ?? 0} 分钟`);
      this.inspectorFact(info, "状态", statusLabel(item.bundle.status));
      this.inspectorFact(info, "检索时间", item.bundle.retrieved_at ? new Date(item.bundle.retrieved_at).toLocaleString() : "—");
      const warning = scroll.createDiv({cls: "la-inspector-card"}); warning.createEl("h3", {text: "安全边界"}); warning.createEl("p", {text: "检索结果不会直接写成正式知识；保存和计划操作仍需明确确认。"});
      return;
    }
    const job = item.job;
    const header = parent.createDiv({cls: "la-pane-header"});
    header.createEl("h2", {text: "处理状态"});
    const scroll = parent.createDiv({cls: "la-pane-scroll la-inspector-scroll"});
    const percent = Number(job?.progress ?? (item.state === "applied" ? 100 : item.state === "prepared" ? 75 : 0));
    const ringWrap = scroll.createDiv({cls: "la-material-progress"});
    const ring = ringWrap.createDiv({cls: "la-progress-ring la-progress-ring--medium", attr: {style: `--la-progress:${percent * 3.6}deg`}});
    ring.createEl("strong", {text: `${percent}%`});
    ring.createEl("small", {text: statusLabel(job?.current_stage ?? item.state)});
    const steps = scroll.createDiv({cls: "la-process-steps"});
    const stage = String(job?.current_stage ?? item.state);
    const names = ["解析文档", "提取与理解", "生成 Change Set", "应用到知识库", "完成"];
    const stageIndex = stage === "applied" || stage === "completed" ? 5 : stage === "prepared" || stage === "awaiting_confirmation" ? 3 : stage === "running" ? 2 : stage === "queued" ? 0 : 1;
    names.forEach((name, index) => {
      const row = steps.createDiv({cls: index < stageIndex ? "is-done" : index === stageIndex ? "is-current" : ""});
      setIcon(row.createSpan(), index < stageIndex ? "check-circle-2" : index === stageIndex ? "circle-dot" : "circle");
      row.createSpan({text: name});
    });
    const info = scroll.createDiv({cls: "la-inspector-card"});
    info.createEl("h3", {text: "资料信息"});
    this.inspectorFact(info, "标题", titleText);
    this.inspectorFact(info, "类型", job?.kind === "prepare-pdf" ? "论文 / PDF" : "本地资料");
    this.inspectorFact(info, "模型", job?.payload?.model || "未记录");
    this.inspectorFact(info, "更新时间", job?.updated_at ? new Date(job.updated_at).toLocaleString() : "—");
    const technical = scroll.createEl("details", {cls: "la-inspector-card la-technical"});
    technical.createEl("summary", {text: "技术详情"});
    technical.createEl("p", {text: "内部 ID 与哈希只在此处展示，不作为主标题。"});
    technical.createEl("code", {text: String(item.bundle?.prepared_id || job?.job_id || "—")});
  }

  private inspectorFact(parent: HTMLElement, label: string, value: string): void {
    const row = parent.createDiv({cls: "la-evidence-row"});
    row.createEl("small", {text: label});
    row.createEl("strong", {text: value});
  }

  private renderResearchMaterialDetail(parent: HTMLElement, bundle: any): void {
    const header = parent.createDiv({cls: "la-pane-header la-detail-header"});
    const title = header.createDiv(); title.createEl("h1", {text: bundle.title});
    const meta = title.createDiv({cls: "la-rec-row__meta"}); badge(meta, "explore", "Research Bundle"); meta.createSpan({text: `${bundle.source_count ?? 0} 个来源`});
    const scroll = parent.createDiv({cls: "la-pane-scroll la-detail-scroll"});
    this.detailSection(scroll, "研究问题", section => section.createEl("p", {text: bundle.question}));
    this.detailSection(scroll, "已有知识", section => {
      const list = section.createDiv({cls: "la-reference-list"});
      for (const note of bundle.existing_knowledge ?? []) { const row = list.createDiv(); setIcon(row.createSpan(), "file-text"); row.createSpan({text: note.title}); }
      if (!(bundle.existing_knowledge ?? []).length) section.createEl("p", {text: "本地没有直接匹配的 reviewed/core 笔记。", cls: "la-muted"});
    });
    this.detailSection(scroll, "阅读路线", section => {
      section.createEl("p", {text: `建议按相关性与可信度阅读，预算 ${bundle.estimated_minutes ?? 0} 分钟。`});
      for (const step of bundle.next_steps ?? []) section.createEl("p", {text: `• ${step}`});
    });
    const technical = scroll.createEl("details", {cls: "la-technical"}); technical.createEl("summary", {text: "技术详情"}); technical.createEl("code", {text: bundle.id});
    const actions = parent.createDiv({cls: "la-pane-footer la-action-bar"});
    button(actions, "保存到 Obsidian", async () => {
      const response = await this.client.post<any>(`/research-bundles/${encodeURIComponent(bundle.id)}/save`, {});
      this.assistantRun = response.run; this.setTab("assistant"); new Notice("已生成 Change Set，确认后才会保存");
    });
    button(actions, "加入学习计划", async () => { await this.client.post(`/research-bundles/${encodeURIComponent(bundle.id)}/add-to-plan`, {}); new Notice("已加入 proposed 计划"); await this.refresh(); }, "mod-cta");
  }

  private async renderReviews(body: HTMLElement): Promise<void> {
    body.addClass("la-page-frame", "la-review-center");
    const [legacy, generated] = await Promise.all([
      this.client.get<any>("/reviews"),
      this.client.get<any>("/artifacts?limit=200"),
    ]);
    const artifacts = [
      ...(generated.items ?? []).filter((item: any) => ["change_set", "capture_proposal"].includes(item.type)).map((item: any) => ({...item, artifact_id: item.id, artifact_role: item.type, review_state: item.status, kind: "agent"})),
      ...(legacy.artifacts ?? []).map((item: any) => ({...item, kind: "vault"})),
    ];
    const heading = this.moduleHeading(body, "所有准备写入 Obsidian 的内容都在这里确认");
    const headingTools = heading.createDiv({cls: "la-module-heading__tools"});
    button(headingTools, "批量操作", () => { new Notice("批量接受仍需逐项完成来源与 Diff 校验"); });
    const controls = body.createDiv({cls: "la-page-toolbar"});
    const labels = [["全部", artifacts.length], ["高风险", artifacts.filter((item: any) => item.risk === "high").length], ["中风险", artifacts.filter((item: any) => item.risk === "medium").length], ["低风险", artifacts.filter((item: any) => item.risk !== "high" && item.risk !== "medium").length]];
    const filters = controls.createDiv({cls: "la-filter-row"});
    for (const [label, count] of labels) filters.createEl("button", {text: `${label} ${count}`, cls: label === "全部" ? "is-active" : ""});
    const search = controls.createDiv({cls: "la-search"});
    setIcon(search.createSpan(), "search");
    search.createEl("input", {attr: {placeholder: "搜索审核内容…", "aria-label": "搜索审核内容"}});

    const layout = body.createDiv({cls: "la-three-pane la-review-layout"});
    const listPane = layout.createDiv({cls: "la-pane la-pane--list"});
    const detailPane = layout.createDiv({cls: "la-pane la-pane--detail"});
    const inspectorPane = layout.createDiv({cls: "la-pane la-pane--inspector"});
    const listHeader = listPane.createDiv({cls: "la-pane-header"});
    listHeader.createEl("h2", {text: "待处理草稿"});
    listHeader.createSpan({text: `${artifacts.length} 项`});
    const list = listPane.createDiv({cls: "la-pane-scroll la-review-list"});
    if (!artifacts.length) {
      emptyState(list, "没有待审核知识", "应用一份经过检查的 Change Set 后，新草稿会进入这里。", {label: "查看待确认资料", run: () => this.setTab("sources")});
      emptyState(detailPane, "等待知识草稿", "这里会渲染 Markdown 正文和语义 Diff。", {label: "前往资料", run: () => this.setTab("sources")});
      emptyState(inspectorPane, "暂无来源证据", "选择草稿后显示页码、来源、Agent 理由与审计记录。", {label: "刷新", run: () => this.refresh()});
      return;
    }

    const show = async (artifact: any, titleText: string): Promise<void> => {
      detailPane.empty();
      inspectorPane.empty();
      list.querySelectorAll(".la-review-row").forEach(element => element.removeClass("is-selected"));
      list.querySelector(`[data-artifact="${CSS.escape(String(artifact.artifact_id))}"]`)?.addClass("is-selected");
      const loading = detailPane.createDiv({cls: "la-skeleton"});
      loading.createDiv();
      loading.createDiv();
      try {
        if (artifact.kind === "agent") {
          const data = (await this.client.get<any>(`/artifacts/${encodeURIComponent(artifact.artifact_id)}`)).artifact;
          detailPane.empty();
          this.renderAgentArtifactReview(detailPane, data);
          this.renderAgentArtifactInspector(inspectorPane, data);
          return;
        }
        const [data, diff] = await Promise.all([
          this.client.get<any>(`/reviews/${encodeURIComponent(artifact.artifact_id)}`),
          this.client.get<any>(`/reviews/${encodeURIComponent(artifact.artifact_id)}/diff`),
        ]);
        detailPane.empty();
        this.renderReviewDetail(detailPane, artifact, titleText, data, diff);
        this.renderReviewInspector(inspectorPane, artifact, data);
      } catch (error: any) {
        detailPane.empty();
        const box = detailPane.createDiv({cls: "la-inline-error"});
        setIcon(box.createSpan(), "circle-alert");
        box.createSpan({text: `审核内容加载失败：${error.message}`});
        button(box, "重试", () => show(artifact, titleText));
      }
    };

    for (const artifact of artifacts) {
      const titleText = artifact.title || humanTitle(artifact.path ?? artifact.artifact_role, "知识草稿");
      const row = list.createEl("button", {cls: "la-review-row", attr: {"data-artifact": String(artifact.artifact_id)}});
      const top = row.createDiv({cls: "la-review-row__top"});
      badge(top, artifact.artifact_role === "concept" ? "review" : "explore", artifact.artifact_role === "change_set" ? "Change Set" : artifact.artifact_role === "capture_proposal" ? "保存提案" : artifact.artifact_role || "草稿");
      iconButton(top, "ellipsis-vertical", "更多操作", () => new Notice("审核操作位于正文底部。"));
      row.createEl("strong", {text: titleText});
      row.createEl("small", {text: artifact.kind === "agent" ? `来源 · 助手会话 · v${artifact.version ?? 1}` : `来源 · ${humanTitle(artifact.generated_from || artifact.path, "本地资料")}`});
      const meta = row.createDiv({cls: "la-row-tags"});
      badge(meta, "neutral", statusLabel(artifact.review_state || artifact.status));
      meta.createSpan({text: artifact.kind === "agent" ? "等待用户确认" : "含来源证据"});
      row.onclick = () => void show(artifact, titleText);
    }
    const footer = listPane.createDiv({cls: "la-pane-footer la-list-footer"});
    footer.createSpan({text: `${artifacts.length} 项待处理`});
    footer.createSpan({text: "1 / 1"});
    const first = artifacts[0];
    await show(first, first.title || humanTitle(first.path ?? first.artifact_role, "知识草稿"));
  }

  private renderAgentArtifactReview(parent: HTMLElement, artifact: any): void {
    const payload = artifact.payload ?? {};
    const header = parent.createDiv({cls: "la-pane-header la-review-tabs"});
    const segmented = header.createDiv({cls: "la-segmented"});
    for (const label of ["预览", "Diff", "原始输入", "还原"]) segmented.createEl("button", {text: label, cls: label === "预览" ? "is-active" : ""});
    const scroll = parent.createDiv({cls: "la-pane-scroll la-review-document markdown-rendered"});
    scroll.createEl("h1", {text: artifact.title});
    const meta = scroll.createDiv({cls: "la-review-proposal-meta"});
    badge(meta, "explore", artifact.type === "change_set" ? "Change Set" : "保存提案");
    badge(meta, "neutral", `版本 ${artifact.version ?? 1}`);
    badge(meta, "review", "可追溯");
    this.detailSection(scroll, "建议内容", section => {
      section.createEl("p", {text: payload.summary ?? payload.answer ?? "这是助手基于当前会话生成的结构化写入提案。"});
      const facts = section.createDiv({cls: "la-change-facts"});
      for (const [label, value, kind] of [["创建", payload.createCount ?? 0, "neutral"], ["更新", payload.updateCount ?? 0, "learn"], ["链接", payload.linkCount ?? 0, "explore"], ["冲突", payload.conflictCount ?? 0, "source"]]) {
        const fact = facts.createDiv({cls: `la-change-fact la-change-fact--${kind}`}); fact.createEl("small", {text: label}); fact.createEl("strong", {text: String(value)});
      }
    });
    this.detailSection(scroll, "内容预览（Markdown）", section => {
      const preview = payload.preview ?? payload.proposedTitle ?? payload.targetPath ?? "等待 Change Set 生成可验证的 Markdown Diff。";
      section.createEl("pre", {cls: "la-code-preview", text: String(preview)});
    });
    const actions = parent.createDiv({cls: "la-pane-footer la-action-bar"});
    button(actions, "拒绝", async () => { await this.client.post(`/artifacts/${encodeURIComponent(artifact.id)}/action`, {action: "reject"}); await this.refresh(); }, "la-danger-button");
    button(actions, "稍后", async () => { await this.client.post(`/artifacts/${encodeURIComponent(artifact.id)}/action`, {action: "later"}); await this.refresh(); });
    button(actions, "退回助手调整", () => { this.conversationId = artifact.conversationId; this.setTab("assistant"); });
    button(actions, "修改后接受", async () => { await this.client.post(`/artifacts/${encodeURIComponent(artifact.id)}/action`, {action: "accept"}); await this.refresh(); }, "mod-cta");
  }

  private renderAgentArtifactInspector(parent: HTMLElement, artifact: any): void {
    const header = parent.createDiv({cls: "la-pane-header"}); header.createEl("h2", {text: "证据与风险"});
    const scroll = parent.createDiv({cls: "la-pane-scroll la-inspector-scroll"});
    const evidence = scroll.createDiv({cls: "la-inspector-card"}); evidence.createEl("h3", {text: "来源"});
    this.inspectorFact(evidence, "会话", "助手统一入口");
    this.inspectorFact(evidence, "版本", `v${artifact.version ?? 1}`);
    this.inspectorFact(evidence, "状态", statusLabel(artifact.status));
    const risk = scroll.createDiv({cls: "la-inspector-card"}); risk.createEl("h3", {text: "校验结果"});
    for (const text of ["目标路径必须位于 Vault 内", "reviewed/core 只生成更新建议", "应用前重新计算 Diff 与基础版本", "模型不能直接提交事务"]) {
      const row = risk.createDiv({cls: "la-evidence-check"}); setIcon(row.createSpan(), "check-circle-2"); row.createSpan({text});
    }
    const history = scroll.createDiv({cls: "la-inspector-card"}); history.createEl("h3", {text: "版本历史"});
    for (const version of artifact.versions ?? []) this.inspectorFact(history, `v${version.version}`, version.revision_instruction || "初始生成");
    button(scroll, "查看完整证据链", () => new TextPreviewModal(this.app, "Artifact 证据链", JSON.stringify({id: artifact.id, conversationId: artifact.conversationId, versions: artifact.versions}, null, 2)).open());
  }

  private renderReviewDetail(parent: HTMLElement, artifact: any, titleText: string, data: any, diff: any): void {
    const previewContent = humanReviewMarkdown(String(data.content || ""));
    const header = parent.createDiv({cls: "la-pane-header la-review-tabs"});
    const segmented = header.createDiv({cls: "la-segmented"});
    for (const label of ["预览", "Diff", "源文件", "用户编辑"]) segmented.createEl("button", {text: label, cls: label === "预览" ? "is-active" : ""});
    const tools = header.createDiv({cls: "la-review-tools"});
    iconButton(tools, "maximize-2", "展开", () => new Notice("已保持在 Obsidian 主工作区中。"));
    iconButton(tools, "copy", "复制", () => void navigator.clipboard.writeText(previewContent));
    const scroll = parent.createDiv({cls: "la-pane-scroll la-review-document markdown-rendered"});
    scroll.createEl("h1", {text: titleText});
    void this.markdown.render(scroll, previewContent, String(artifact.path || ""));
    const disclosure = scroll.createEl("details", {cls: "la-diff-viewer"});
    disclosure.createEl("summary", {text: "查看语义 Diff"});
    disclosure.createEl("pre", {text: diff.diff, cls: "la-code-preview"});
    const actions = parent.createDiv({cls: "la-pane-footer la-action-bar"});
    button(actions, "拒绝", async () => {
      await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "reject", reason: "从审核中心拒绝"});
      await this.refresh();
    }, "la-danger-button");
    button(actions, "稍后", () => { new Notice("已保留在待审核队列"); });
    button(actions, "修改后接受", async () => {
      await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "approve-edited"});
      await this.refresh();
    });
    button(actions, "接受", async () => {
      await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "approve"});
      await this.refresh();
    }, "mod-cta");
  }

  private renderReviewInspector(parent: HTMLElement, artifact: any, data: any): void {
    const header = parent.createDiv({cls: "la-pane-header"});
    header.createEl("h2", {text: "来源与证据"});
    const scroll = parent.createDiv({cls: "la-pane-scroll la-inspector-scroll"});
    const source = scroll.createDiv({cls: "la-inspector-card"});
    source.createEl("h3", {text: "来源信息"});
    this.inspectorFact(source, "来源资料", humanTitle(artifact.generated_from || artifact.path, "本地资料"));
    this.inspectorFact(source, "位置", data.content.includes("PAGE") ? "正文包含页码标记" : "请人工检查页码证据");
    this.inspectorFact(source, "类型", String(artifact.artifact_role || "知识草稿"));
    const reason = scroll.createDiv({cls: "la-inspector-card"});
    reason.createEl("h3", {text: "Agent 理由"});
    reason.createEl("p", {text: "内容具备独立定义、可复用性和来源证据，适合进入知识审核。接受后正式笔记不会被自动覆盖。"});
    const confidence = reason.createDiv({cls: "la-confidence"});
    confidence.createSpan({text: "证据强度"});
    confidence.createEl("strong", {text: data.content.includes("PAGE") ? "高" : "需检查"});
    const audit = scroll.createDiv({cls: "la-inspector-card"});
    audit.createEl("h3", {text: "管理块与审计"});
    audit.createEl("p", {text: "Agent 只能刷新明确的 managed block；reviewed/core 正文只读，后续变化生成更新建议。"});
    const quality = scroll.createDiv({cls: "la-inspector-card"});
    quality.createEl("h3", {text: "主脑质量门"});
    for (const [label, value] of [["用户原文", "保留于来源或草稿"], ["Agent 整理", "当前预览"], ["模型推断", "待人工核验"], ["Policy", "reviewed/core 只读"], ["Verifier", "路径、来源与结构已检查"]]) {
      this.inspectorFact(quality, label, value);
    }
    const technical = scroll.createEl("details", {cls: "la-inspector-card la-technical"});
    technical.createEl("summary", {text: "技术详情"});
    technical.createEl("code", {text: String(artifact.artifact_id)});
    technical.createEl("pre", {text: String(data.content || ""), cls: "la-code-preview"});
  }

  private async renderPlan(body: HTMLElement): Promise<void> {
    body.addClass("la-page-frame", "la-plan-page", "la-plan-focus");
    const [plan, queues] = await Promise.all([
      this.client.get<any>("/learning/today"),
      this.client.get<any>("/plans/current"),
    ]);
    const heading = this.moduleHeading(body, "主脑为你生成的学习计划与任务安排");
    const headingTools = heading.createDiv({cls: "la-module-heading__tools"});
    button(headingTools, "调整计划", () => { this.assistantMode = "plan"; this.setTab("assistant"); });
    button(headingTools, "新建任务", () => { this.assistantMode = "plan"; this.setTab("assistant"); }, "mod-cta");

    const tabs = body.createDiv({cls: "la-plan-tabs"});
    for (const label of ["今天", "未来两天", "本周", "周末", "所有"]) tabs.createEl("button", {text: label, cls: label === "今天" ? "is-active" : ""});
    const dateHead = body.createDiv({cls: "la-plan-date-head"});
    const date = dateHead.createDiv(); date.createEl("h2", {text: `${plan.date ?? new Date().toISOString().slice(0, 10)} · 今天`});
    date.createEl("small", {text: "按可用时间与学习优先级动态安排"});
    const proposedTasks = (queues.proposals ?? []).flatMap((proposal: any) => (proposal.tasks ?? []).map((task: any) => ({...task, proposal_id: proposal.id, proposal_state: proposal.state})));
    const todayTasks = [
      ...(plan.review ?? []).map((item: any) => ({...item, kind: "复习", minutes: 10, route: "mainline", state: "queued"})),
      ...(plan.new_learning ?? []).map((item: any) => ({...item, kind: "学习", minutes: 15, route: item.domain?.toLowerCase().includes("agent") ? "branch" : "mainline", state: "queued"})),
    ];
    const stats = dateHead.createDiv({cls: "la-plan-day-stats"});
    const totalMinutes = todayTasks.reduce((sum: number, item: any) => sum + Number(item.minutes ?? 15), 0);
    for (const [label, value] of [["可用时间", `${Math.max(25, totalMinutes)} 分钟`], ["主线 / 支线", `${queues.mainline_ratio}% / ${queues.branch_ratio}%`], ["今日任务", `${todayTasks.length} 个学习块`], ["预计完成率", todayTasks.length ? "85%" : "—"]]) {
      const item = stats.createDiv(); item.createEl("small", {text: label}); item.createEl("strong", {text: value});
    }
    const scroll = body.createDiv({cls: "la-plan-focus__scroll la-pane-scroll"});
    const today = scroll.createEl("section", {cls: "la-plan-focus-section"});
    today.createEl("h2", {text: "今日学习块"});
    if (!todayTasks.length) emptyState(today, "今天还没有任务", "让助手按当前掌握度与时间生成一个克制的学习安排。", {label: "让助手安排", run: () => this.setTab("assistant")});
    for (const item of todayTasks) this.renderPlanFocusTask(today, item, false);

    const upcoming = scroll.createEl("section", {cls: "la-plan-focus-section la-plan-focus-section--secondary"});
    const upcomingHead = upcoming.createDiv({cls: "la-plan-section__header"}); upcomingHead.createEl("h2", {text: "周末计划（预览）"});
    upcomingHead.createSpan({text: `${[...(queues.weekend ?? []), ...proposedTasks].length} 个任务`});
    const nextItems = [...(queues.weekend ?? []), ...proposedTasks].slice(0, 5);
    if (!nextItems.length) upcoming.createEl("p", {text: "当前没有周末任务；未完成任务会被重新排程，不会无限堆积。", cls: "la-muted"});
    for (const item of nextItems) this.renderPlanFocusTask(upcoming, {...item, kind: item.proposal_state === "proposed" ? "候选" : "周末"}, true);
    const firstProposal = (queues.proposals ?? []).find((proposal: any) => proposal.state === "proposed");
    if (firstProposal) button(upcoming, "确认本轮计划", async () => { await this.client.post(`/plans/proposals/${encodeURIComponent(firstProposal.id)}/confirm`, {}); new Notice("学习计划已确认"); await this.refresh(); }, "mod-cta");
    else button(upcoming, "查看完整周计划", () => { new Notice("当前没有待确认的周计划"); });
  }

  private renderPlanFocusTask(parent: HTMLElement, item: any, compact: boolean): void {
    const row = parent.createDiv({cls: `la-plan-focus-task ${compact ? "is-compact" : ""}`});
    const icon = row.createSpan({cls: `la-kind-icon la-kind-icon--${item.route === "branch" ? "explore" : "review"}`});
    setIcon(icon, item.kind === "复习" ? "rotate-ccw" : item.kind === "候选" ? "sparkles" : "book-open");
    const copy = row.createDiv({cls: "la-plan-focus-task__copy"});
    const title = copy.createDiv(); title.createEl("strong", {text: humanTitle(item.title, "学习任务")});
    const meta = copy.createDiv({cls: "la-rec-row__meta"});
    badge(meta, item.route === "branch" ? "explore" : "learn", item.route === "branch" ? "支线" : "主线");
    meta.createSpan({text: item.kind ?? "学习"}); meta.createSpan({text: `${Number(item.minutes ?? item.estimated_minutes ?? 15)} 分钟`});
    if (!compact && item.weak_points?.length) copy.createEl("p", {text: `重点：${item.weak_points.slice(0, 2).join("、")}`});
    const start = button(row, compact ? "移到今天" : "开始", async () => {
      if (compact && item.id) await this.client.patch(`/plans/tasks/${encodeURIComponent(item.id)}`, {date: new Date().toISOString().slice(0, 10), state: "queued"});
      else { const match = this.dashboard?.recommendations.find(rec => rec.title === item.title); if (match) await this.startStudy(match); else this.setTab("today"); }
    }, compact ? "" : "mod-cta");
    if (!compact) setIcon(start.createSpan({cls: "la-button-icon"}), "play");
  }

  private planSection(parent: HTMLElement, title: string, items: any[], description: string, icon: string, isWeekly = false): number {
    const section = parent.createEl("section", {cls: "la-plan-section"});
    const header = section.createDiv({cls: "la-plan-section__header"});
    setIcon(header.createSpan(), icon);
    header.createEl("h2", {text: title});
    badge(header, "neutral", String(items.length));
    if (!items.length) {
      emptyState(section, `暂无${title}`, description, isWeekly ? {
        label: "生成 proposed 计划",
        run: async () => {
          await this.client.post("/curriculum/refresh", {text: "生成下一周学习计划"});
          new Notice("周计划生成任务已创建");
          await this.refresh();
        },
      } : undefined);
    }
    for (const item of items) {
      const row = section.createDiv({cls: "la-plan-task", attr: {tabindex: "0"}});
      const copy = row.createDiv();
      copy.createEl("strong", {text: humanTitle(item.title, "学习任务")});
      const meta = copy.createDiv({cls: "la-rec-row__meta"});
      meta.createSpan({text: item.domain || (item.mastery === undefined ? "学习任务" : `mastery ${item.mastery}`)});
      meta.createSpan({text: `${Number(item.minutes ?? item.estimated_minutes ?? 15)} 分钟`});
      button(row, "开始", () => { new Notice(`准备学习「${humanTitle(item.title)}」`); }, "la-compact-button");
    }
    return items.length;
  }

  private async renderAssistant(body: HTMLElement): Promise<void> {
    body.addClass("la-page-frame", "la-assistant-page", "la-chat-first");
    const [{profiles}, {routes}, conversations] = await Promise.all([
      this.client.get<{profiles: ModelProfile[]}>("/model-profiles"),
      this.client.get<any>("/model-routing"),
      this.client.get<any>("/conversations?limit=20"),
    ]);
    if (!this.conversationId && conversations.items?.length) this.conversationId = String(conversations.items[0].id);
    let loadedConversation: any = null;
    if (this.conversationId) {
      try {
        const [{conversation}, artifacts] = await Promise.all([
          this.client.get<any>(`/conversations/${encodeURIComponent(this.conversationId)}`),
          this.client.get<any>(`/artifacts?conversation_id=${encodeURIComponent(this.conversationId)}&limit=30`),
        ]);
        loadedConversation = conversation;
        // 优先使用缓存消息（包含 reasoningBlocks、traceSteps 等前端富数据），
        // 仅当缓存为空或长度不够时才从后端补充新增消息
        const backendMessages = conversation.messages ?? [];
        const cached = this.conversationMessageCache.get(this.conversationId);
        if (cached && cached.length > 0) {
          // 缓存存在：保留缓存的富数据，只在末尾追加后端新增的消息
          // （后端消息不含 trace，但通过位置匹配，新增消息通常位于末尾）
          if (backendMessages.length > cached.length) {
            this.assistantMessages = [
              ...cached,
              ...backendMessages.slice(cached.length),
            ];
          } else {
            this.assistantMessages = cached;
          }
        } else {
          this.assistantMessages = backendMessages;
        }
        // 不覆盖缓存！只在不存在时设置初始值
        if (!this.conversationMessageCache.has(this.conversationId)) {
          this.conversationMessageCache.set(this.conversationId, [...this.assistantMessages]);
        }
        this.assistantArtifacts = await Promise.all((artifacts.items ?? []).slice().reverse().map(async (item: any) => {
          try { return (await this.client.get<any>(`/artifacts/${encodeURIComponent(item.id)}`)).artifact; }
          catch { return item; }
        }));
        const latestMessage = this.assistantMessages[this.assistantMessages.length - 1];
        const latestTask = latestMessage?.taskThreadId && conversation.latestTaskThread?.id === latestMessage.taskThreadId
          ? conversation.latestTaskThread : null;
        this.assistantTaskThread = latestTask ? {
          ...latestTask,
          steps: (latestTask.steps ?? []).map((item: any) => ({
            id: String(item.id), label: String(item.userFacingLabel ?? item.label),
            detail: item.detail ? String(item.detail) : undefined, status: String(item.status),
          })),
        } as AssistantTaskThread : null;
        const activeBackendGroup = latestMessage?.artifactGroupId && conversation.latestArtifactGroup?.id === latestMessage.artifactGroupId
          ? conversation.latestArtifactGroup : null;
        const groups = groupAssistantArtifacts(
          this.assistantArtifacts as AssistantArtifact[], this.conversationId,
          this.assistantTaskThread?.id ?? String(this.assistantRun?.id ?? "current"),
          activeBackendGroup,
        );
        this.assistantArtifactGroup = activeBackendGroup ? groups[0] ?? null : null;
        const primaryId = this.assistantArtifactGroup?.primaryArtifactId ?? "";
        this.assistantContext = await this.client.get<any>(`/assistant/context?conversation_id=${encodeURIComponent(this.conversationId)}${primaryId ? `&artifact_id=${encodeURIComponent(primaryId)}` : ""}`);
        if (primaryId) {
          if (this.assistantContext?.inToday) this.assistantToday.add(primaryId);
          else this.assistantToday.delete(primaryId);
        }
      } catch {
        this.conversationId = ""; this.assistantMessages = []; this.assistantArtifacts = [];
        this.assistantTaskThread = null; this.assistantArtifactGroup = null; this.assistantContext = null;
      }
    }
    this.assistantConversations = conversations.items ?? this.assistantConversations;
    const shell = body.createDiv({cls: `la-assistant-shell-v4 la-assistant-shell-v5 la-assistant-shell-v6 la-assistant-shell-v7 la-assistant-shell-v8 la-chat-shell ${this.assistantDrawerOpen ? "has-drawer" : ""} ${this.assistantInspectorOpen ? "has-inspector" : "inspector-closed"}`});
    const chat = shell.createDiv({cls: "la-assistant-main"});
    const context = shell.createEl("aside", {cls: "la-assistant-context", attr: {"aria-label": "本次任务上下文"}});
    const drawer = shell.createDiv({cls: `la-provider-drawer la-provider-drawer--overlay ${this.assistantDrawerOpen ? "is-open" : ""}`, attr: {"aria-label": "模型与 API 设置"}});
    const header = chat.createDiv({cls: "la-assistant-head la-chat-toolbar"});
    const title = header.createDiv({cls: "la-chat-toolbar__title"});
    title.createEl("h1", {text: humanTitle(loadedConversation?.title, "新会话")});
    let networkSafetyText: HTMLElement | null = null;
    let networkStatusIcon: HTMLElement | null = null;
    let networkStatusLabel: HTMLElement | null = null;
    let networkToggleButton: HTMLButtonElement | null = null;
    const paintNetworkToggle = (): void => {
      if (networkStatusIcon) {
        networkStatusIcon.empty();
        setIcon(networkStatusIcon, this.assistantNetworkEnabled ? "globe-2" : "shield-check");
      }
      networkStatusLabel?.setText(this.assistantNetworkEnabled ? "联网" : "本地");
      networkToggleButton?.toggleClass("is-enabled", this.assistantNetworkEnabled);
      networkToggleButton?.setAttribute("aria-pressed", String(this.assistantNetworkEnabled));
      networkToggleButton?.setAttribute("aria-label", this.assistantNetworkEnabled ? "关闭联网检索" : "开启联网检索");
      if (networkToggleButton) networkToggleButton.title = this.assistantNetworkEnabled ? "联网已开启，点击关闭" : "联网已关闭，点击开启";
      networkSafetyText?.setText(this.assistantNetworkEnabled ? "联网来源不可信 · 写入可撤销" : "本地优先 · 低风险写入可撤销");
    };
    const toggleNetwork = (): void => {
      this.assistantNetworkEnabled = !this.assistantNetworkEnabled;
      paintNetworkToggle();
      new Notice(this.assistantNetworkEnabled ? "已为本会话开启联网检索" : "已切回仅本地模式");
    };
    const showHistoryMenu = (event: MouseEvent): void => {
      const menu = new Menu();
      if (!(conversations.items ?? []).length) menu.addItem(item => item.setTitle("暂无历史会话").setDisabled(true));
      for (const conversation of conversations.items ?? []) menu.addItem(item => item
        .setTitle(`${conversation.title} · ${conversation.messageCount} 条`)
        .setIcon(conversation.id === this.conversationId ? "check" : "message-square")
        .onClick(() => { if (this.conversationId) this.assistantLiveRuns.set(this.conversationId, this.assistantLiveRun); this.conversationId = String(conversation.id); this.assistantLiveRun = this.assistantLiveRuns.get(this.conversationId) ?? initialAssistantLiveRun(); this.assistantRegenerateMessageId = ""; this.assistantRegenerateRunId = ""; this.assistantRun = null; this.assistantVisibleMessageLimit = 160; void this.refresh(); }));
      if (this.conversationId) {
        menu.addSeparator();
        menu.addItem(item => item.setTitle("导出当前会话（本地）").setIcon("download").onClick(async () => {
          const result = await this.client.post<any>(`/conversations/${encodeURIComponent(this.conversationId)}/export`, {});
          new Notice(`会话已导出到本地私有区：${result.reference}`);
        }));
        menu.addItem(item => item.setTitle("仅保留当前会话摘要").setIcon("file-minus-2").onClick(() => {
          new ExplicitConfirmModal(this.app, "仅保留摘要", "将删除当前会话的原始消息正文；摘要、知识信号和已生成成果保留。此操作不可撤销。", async () => {
            await this.client.post(`/conversations/${encodeURIComponent(this.conversationId)}/retain-summary`, {confirmed: true});
            this.assistantMessages = []; new Notice("原始消息已删除，仅保留摘要"); await this.refresh();
          }).open();
        }));
        menu.addItem(item => item.setTitle("删除当前会话").setIcon("trash-2").onClick(() => {
          new ExplicitConfirmModal(this.app, "删除当前会话", "将删除本地原始消息、附件副本和与该会话绑定的运行成果；不会删除正式知识笔记。", async () => {
            await this.client.delete(`/conversations/${encodeURIComponent(this.conversationId)}?confirm=true`);
            this.conversationId = ""; this.assistantMessages = []; this.assistantArtifacts = [];
            this.assistantTaskThread = null; this.assistantArtifactGroup = null; new Notice("会话已删除"); await this.refresh();
          }).open();
        }));
      }
      menu.showAtMouseEvent(event);
    };
    const startNewConversation = async (): Promise<void> => {
      const response = await this.client.post<any>("/conversations", {title: "新会话"});
      if (this.conversationId) this.assistantLiveRuns.set(this.conversationId, this.assistantLiveRun);
      this.conversationId = String(response.conversation.id); this.assistantMessages = []; this.assistantArtifacts = [];
      this.assistantTaskThread = null; this.assistantArtifactGroup = null;
      this.pendingAttachments = []; this.assistantDraft = ""; this.assistantRegenerateMessageId = ""; this.assistantRegenerateRunId = ""; this.assistantRun = null; this.assistantVisibleMessageLimit = 160; await this.refresh();
    };
    const setInspectorOpen = (open: boolean): void => {
      this.assistantInspectorOpen = open;
      shell.toggleClass("has-inspector", this.assistantInspectorOpen); shell.toggleClass("inspector-closed", !this.assistantInspectorOpen);
    };
    const openProviderDrawer = (): void => {
      this.assistantDrawerOpen = true; shell.addClass("has-drawer"); drawer.addClass("is-open");
    };

    const messages = chat.createDiv({cls: "la-message-list la-chat-stream", attr: {"aria-live": "polite"}});
    if (!this.assistantMessages.length) {
      const welcome = messages.createDiv({cls: "la-message la-message--assistant la-chat-welcome"});
      renderAssistantAvatar(welcome, this.app);
      const copy = welcome.createDiv(); copy.createEl("strong", {text: "你好，我是知序 🚀"});
      copy.createEl("p", {text: "你可以直接告诉我想做什么，也可以拖入 PDF、链接、路径、文本或当前笔记。"});
      const quick = copy.createDiv({cls: "la-assistant-quick-actions"});
      const quickActions: Array<{label: string; command: string; icon: string; needsNetwork: boolean}> = [
        {label: "整理材料", command: "帮我整理这些材料，并安排后续学习", icon: "file-scan", needsNetwork: false},
        {label: "联网研究", command: "请联网检索可信网页与论文，研究这个主题并生成带来源的阅读路线：", icon: "globe-2", needsNetwork: true},
        {label: "存入 Obsidian", command: "请整理并保存下面的内容：", icon: "database", needsNetwork: false},
        {label: "安排学习", command: "根据当前状态安排接下来两天的学习", icon: "calendar-check", needsNetwork: false},
      ];
      for (const {label, command, icon, needsNetwork} of quickActions) {
        const control = quick.createEl("button"); setIcon(control.createSpan(), icon); control.createSpan({text: label});
        control.onclick = () => {
          if (needsNetwork) {
            this.assistantNetworkEnabled = true; paintNetworkToggle();
            networkSafetyText?.setText("联网来源不可信 · 写入可撤销");
          }
          input.value = command; this.assistantDraft = command; input.focus();
        };
      }
    } else {
      const hidden = Math.max(0, this.assistantMessages.length - this.assistantVisibleMessageLimit);
      if (hidden) {
        const older = messages.createEl("button", {cls: "la-load-older", text: `加载更早的 ${Math.min(160, hidden)} 条消息`});
        older.onclick = () => { this.assistantVisibleMessageLimit += 160; void this.refresh(); };
      }
      let previousUserMessage: any = null;
      for (const message of this.assistantMessages.slice(-this.assistantVisibleMessageLimit)) {
        this.renderConversationMessage(messages, message, previousUserMessage);
        if (message.role === "user") previousUserMessage = message;
      }
    }
    // Re-attach live elements if a background run is still active for this conversation
    const bgTrace = this.assistantLiveTraceEls.get(this.conversationId);
    const bgAssistant = this.assistantLiveAssistantEls.get(this.conversationId);
    const bgConfirmation = this.assistantLiveConfirmationEls.get(this.conversationId);
    const bgLiveRunActive = this.assistantLiveRun.status === "running" || this.assistantLiveRun.status === "waiting_confirmation";
    if (bgLiveRunActive) {
      // Always move live elements into the current messages list — isConnected
      // can be misleading after refresh() because the old DOM subtree may
      // have been detached but the elements were re-parented by the streaming
      // loop into a detached ancestor.
      if (bgTrace) messages.appendChild(bgTrace);
      if (bgAssistant) messages.appendChild(bgAssistant);
      if (bgConfirmation) messages.appendChild(bgConfirmation);
      if (bgTrace) this.paintAssistantLiveTrace(bgTrace, this.assistantLiveRun);
    }
    const primaryArtifact = this.assistantArtifactGroup?.artifacts.find(item => item.id === this.assistantArtifactGroup?.primaryArtifactId)
      ?? this.assistantArtifactGroup?.artifacts[0];
    const primaryPayload = primaryArtifact?.payload ?? {};
    if (primaryArtifact?.type === "learning_pack" && primaryPayload.answer) {
      const existingAnswer = this.assistantMessages.some(item => String(item.content ?? "").includes(String(primaryPayload.answer).slice(0, 24)));
      if (!existingAnswer) this.renderConversationMessage(messages, {role: "assistant", content: primaryPayload.answer, createdAt: primaryArtifact.updatedAt});
    }
    if (this.assistantTaskThread) this.renderAssistantTaskThread(messages, this.assistantTaskThread, inputValue => { input.value = inputValue; this.assistantDraft = inputValue; input.focus(); });
    if (this.assistantArtifactGroup) this.renderAssistantArtifactGroup(messages, this.assistantArtifactGroup, inputValue => { input.value = inputValue; this.assistantDraft = inputValue; input.focus(); });

    const dock = chat.createDiv({cls: "la-assistant-dock la-chat-dock"});
    const pending = dock.createDiv({cls: `la-pending-attachments ${this.pendingAttachments.length ? "has-items" : ""}`});
    for (const attachment of this.pendingAttachments) {
      const chip = pending.createDiv({cls: "la-attachment-chip"}); setIcon(chip.createSpan(), attachment.kind === "pdf" ? "file-text" : attachment.kind === "url" ? "link" : "paperclip");
      const copy = chip.createSpan(); copy.createEl("strong", {text: attachment.displayName}); copy.createEl("small", {text: `${attachment.kind.toUpperCase()} · ${Math.max(1, Math.round((attachment.sizeBytes ?? 0) / 1024))} KB`});
      iconButton(chip, "x", "移除附件", () => { this.pendingAttachments = this.pendingAttachments.filter(item => item.id !== attachment.id); chip.remove(); });
    }
    const composer = dock.createDiv({cls: "la-assistant-composer la-unified-composer"});
    const input = composer.createEl("textarea", {attr: {placeholder: "今天帮你做些什么？  @ 引用对话文件，/ 调用技能与指令", "aria-label": "知序统一输入"}});
    input.value = this.assistantDraft;
    input.oninput = () => {
      this.assistantDraft = input.value;
      this.assistantRegenerateMessageId = "";
      this.assistantRegenerateRunId = "";
    };
    const composerTools = composer.createDiv({cls: "la-composer-tools"});
    const fileInput = composerTools.createEl("input", {type: "file", cls: "la-visually-hidden", attr: {multiple: "true", accept: ".pdf,.md,.txt,.json,text/plain,text/markdown,application/pdf"}});
    const currentFile = this.app.workspace.getActiveFile();
    const citeCurrentNote = (): void => {
      if (!currentFile) { new Notice("请先打开一篇 Obsidian 笔记"); return; }
      if (!isAssistantReadableVaultPath(currentFile.path)) { new Notice("当前笔记不在 Agent 允许读取的知识目录中"); return; }
      input.value = `${input.value}${input.value ? "\n" : ""}@${currentFile.path}`; this.assistantDraft = input.value; input.focus();
    };
    const chooseVaultNote = (): void => {
      new VaultNotePickerModal(this.app, file => {
        input.value = `${input.value}${input.value ? "\n" : ""}@${file.path}`;
        this.assistantDraft = input.value;
        input.focus();
      }).open();
    };
    const attach = iconButton(composerTools, "plus", "添加内容或打开工具", () => undefined);
    attach.addClass("la-composer-plus");
    attach.onclick = event => {
      const menu = new Menu();
      menu.addItem(item => item.setTitle("添加文件").setIcon("paperclip").onClick(() => fileInput.click()));
      menu.addItem(item => item.setTitle("引用当前笔记").setIcon("file-text").onClick(citeCurrentNote));
      menu.addItem(item => item.setTitle("选择 Vault 笔记").setIcon("at-sign").onClick(chooseVaultNote));
      menu.addItem(item => item.setTitle("插入命令").setIcon("slash").onClick(() => {
        input.value = `${input.value}/`; this.assistantDraft = input.value; input.focus();
      }));
      menu.addSeparator();
      menu.addItem(item => item
        .setTitle(this.assistantNetworkEnabled ? "关闭联网" : "开启联网")
        .setIcon(this.assistantNetworkEnabled ? "shield-check" : "globe-2")
        .onClick(toggleNetwork));
      menu.addItem(item => item
        .setTitle(this.assistantReasoningMode === "deep" ? "切换为自动思考" : "开启深度思考")
        .setIcon("brain-circuit")
        .onClick(() => {
          this.assistantReasoningMode = this.assistantReasoningMode === "deep" ? "auto" : "deep";
          paintModelTrigger();
        }));
      menu.addItem(item => item
        .setTitle(this.assistantInspectorOpen ? "关闭上下文" : "打开上下文")
        .setIcon("panel-right")
        .onClick(() => setInspectorOpen(!this.assistantInspectorOpen)));
      menu.addItem(item => item.setTitle("历史会话").setIcon("history").onClick(() => {
        window.setTimeout(() => showHistoryMenu(event), 0);
      }));
      menu.addItem(item => item.setTitle("新会话").setIcon("message-square-plus").onClick(() => void startNewConversation()));
      if (this.assistantLiveRun.status === "running" && this.assistantLiveRun.runId) {
        menu.addSeparator();
        menu.addItem(item => item.setTitle("调整当前任务").setIcon("corner-down-left").onClick(() => {
          input.focus();
          input.setAttribute("placeholder", "输入后按 Enter，在下一个安全工具边界调整当前任务");
        }));
        menu.addItem(item => item.setTitle("完成后继续").setIcon("list-plus").onClick(() => {
          const text = input.value.trim();
          if (!text) { new Notice("请先输入要在当前任务完成后继续处理的内容"); input.focus(); return; }
          const runId = this.assistantLiveRun.runId;
          void this.agentRuntime.followUp(runId, text).then(() => {
            const user = messages.createDiv({cls: "la-message la-message--user"});
            user.createDiv({cls: "la-message-copy", text: `完成后继续：${text}`});
            input.value = ""; this.assistantDraft = "";
            new Notice("已加入完成后队列");
          }).catch(error => new Notice(`加入后续任务失败：${error.message}`));
        }));
      }
      menu.addSeparator();
      menu.addItem(item => item.setTitle("模型与 API 设置").setIcon("settings-2").onClick(openProviderDrawer));
      menu.addItem(item => item.setTitle("刷新会话").setIcon("refresh-cw").onClick(() => void this.refresh()));
      menu.showAtMouseEvent(event);
    };
    const permissionPicker = composerTools.createDiv({cls: "la-composer-permission-picker"});
    const permissionButton = permissionPicker.createEl("button", {
      cls: "la-composer-permission",
      attr: {
        type: "button",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
      },
    });
    const permissionPopover = permissionPicker.createDiv({
      cls: "la-permission-popover",
      attr: {role: "menu", tabindex: "-1", "aria-label": "当前任务权限模式"},
    });
    permissionPopover.hidden = true;
    const paintPermissionButton = (): void => {
      permissionButton.empty();
      setIcon(permissionButton.createSpan(), this.assistantPermissionMode === "allow_all" ? "shield-check" : "shield-question");
      permissionButton.createSpan({text: this.assistantPermissionMode === "allow_all" ? "全部允许" : "请求权限"});
      setIcon(permissionButton.createSpan({cls: "la-composer-permission__chevron"}), "chevron-up");
      permissionButton.toggleClass("is-all", this.assistantPermissionMode === "allow_all");
      permissionButton.setAttribute("aria-label", this.assistantPermissionMode === "allow_all"
        ? "当前任务全部允许；点击修改"
        : "需要敏感能力时请求权限；点击修改");
    };
    const rebuildPermissionPopover = (): void => {
      permissionPopover.empty();
      const intro = permissionPopover.createDiv({cls: "la-permission-popover__intro"});
      intro.createEl("strong", {text: "当前任务权限"});
      intro.createEl("p", {text: this.assistantPermissionMode === "allow_all"
        ? "安全且可逆的能力可在当前 Run 内自动扩展；受保护知识、任意外部副作用和越界路径仍然禁止。"
        : "默认在受控沙箱中运行。需要写入、整理目录或使用开发工作区时，知序会暂停并在最新对话下方请求；批准后原地继续。"});
      const addChoice = (modeValue: "ask" | "allow_all", label: string, detail: string, icon: string): void => {
        const row = permissionPopover.createEl("button", {
          cls: `la-permission-option ${this.assistantPermissionMode === modeValue ? "is-selected" : ""}`,
          attr: {type: "button", role: "menuitemradio", "aria-checked": String(this.assistantPermissionMode === modeValue)},
        });
        setIcon(row.createSpan({cls: "la-permission-option__icon"}), icon);
        const copy = row.createSpan({cls: "la-permission-option__copy"});
        copy.createSpan({text: label});
        copy.createEl("small", {text: detail});
        if (this.assistantPermissionMode === modeValue) setIcon(row.createSpan({cls: "la-permission-option__check"}), "check");
        row.onclick = () => {
          this.assistantPermissionMode = modeValue;
          paintPermissionButton();
          permissionPopover.hidden = true;
          permissionButton.setAttribute("aria-expanded", "false");
        };
      };
      addChoice("ask", "请求权限", "遇到敏感操作时暂停，确认后同一任务继续", "shield-question");
      addChoice("allow_all", "当前任务全部允许", "仅开放当前 Run 的安全可逆能力", "shield-check");
    };
    paintPermissionButton();
    rebuildPermissionPopover();
    permissionButton.onclick = () => {
      const open = permissionPopover.hidden;
      if (open) rebuildPermissionPopover();
      permissionPopover.hidden = !open;
      permissionButton.setAttribute("aria-expanded", String(open));
      if (open) permissionPopover.focus();
    };
    permissionPicker.addEventListener("focusout", () => window.setTimeout(() => {
      if (!permissionPicker.contains(document.activeElement)) {
        permissionPopover.hidden = true;
        permissionButton.setAttribute("aria-expanded", "false");
      }
    }, 0));
    permissionPicker.onkeydown = event => {
      if (event.key === "Escape") {
        permissionPopover.hidden = true;
        permissionButton.setAttribute("aria-expanded", "false");
        permissionButton.focus();
      }
    };
    const safetyText = composerTools.createSpan({cls: "la-composer-local", text: this.assistantNetworkEnabled ? "联网来源不可信 · 写入可撤销" : "本地优先 · 低风险写入可撤销"});
    networkSafetyText = safetyText;
    const mode = composerTools.createEl("button", {
      cls: `la-composer-mode ${this.assistantNetworkEnabled ? "is-enabled" : ""}`,
      attr: {
        type: "button",
        "aria-pressed": String(this.assistantNetworkEnabled),
        "aria-label": this.assistantNetworkEnabled ? "关闭联网检索" : "开启联网检索",
      },
    });
    networkToggleButton = mode;
    mode.onclick = toggleNetwork;
    networkStatusIcon = mode.createSpan();
    networkStatusLabel = mode.createSpan();
    const enabledProfiles = profiles.filter(item => item.enabled);
    let selectedProfileId = String(routes.assistant_chat?.profileId ?? routes.assistant?.profileId ?? "");
    if (!selectedProfileId || !enabledProfiles.some(item => item.id === selectedProfileId)) {
      selectedProfileId = String(enabledProfiles[0]?.id ?? "");
    }
    const modelPicker = composerTools.createDiv({cls: "la-model-picker"});
    const modelTrigger = modelPicker.createEl("button", {
      cls: "la-composer-model",
      attr: {type: "button", "aria-label": "选择助手模型", "aria-haspopup": "listbox", "aria-expanded": "false"},
    });
    const modelTriggerIcon = modelTrigger.createSpan({cls: "la-model-icon"});
    const modelTriggerLabel = modelTrigger.createSpan({cls: "la-composer-model__label"});
    const modelTriggerChevron = modelTrigger.createSpan({cls: "la-composer-model__chevron"}); setIcon(modelTriggerChevron, "chevron-up");
    const modelPopover = modelPicker.createDiv({cls: "la-model-popover", attr: {role: "listbox", tabindex: "-1", "aria-label": "助手模型列表"}});
    modelPopover.hidden = true;
    const paintModelTrigger = (): void => {
      const selected = enabledProfiles.find(item => item.id === selectedProfileId);
      modelTriggerIcon.empty();
      if (this.assistantModelAuto) setIcon(modelTriggerIcon, "sparkles");
      else renderModelBrand(modelTriggerIcon, selected, this.app);
      const modelLabel = this.assistantModelAuto ? "Auto" : (selected?.displayName || selected?.defaultModel || "选择模型");
      modelTriggerLabel.setText(this.assistantReasoningMode === "deep" ? `${modelLabel} · 深度` : modelLabel);
      modelTrigger.toggleClass("is-deep", this.assistantReasoningMode === "deep");
      modelTrigger.title = this.assistantModelAuto
        ? `Auto · ${selected?.displayName || selected?.defaultModel || "按助手路由选择"}${this.assistantReasoningMode === "deep" ? " · 深度思考" : ""}`
        : `${selected?.displayName || "模型"} · ${selected?.defaultModel || "手动模型"}${this.assistantReasoningMode === "deep" ? " · 深度思考" : ""}`;
    };
    const closeModelPopover = (): void => {
      modelPopover.hidden = true;
      modelTrigger.setAttribute("aria-expanded", "false");
    };
    const rebuildModelPopover = (): void => {
      modelPopover.empty();
      const reasoningRow = modelPopover.createEl("button", {
        cls: "la-model-popover__reasoning",
        attr: {type: "button", role: "switch", "aria-checked": String(this.assistantReasoningMode === "deep")},
      });
      const reasoningIcon = reasoningRow.createSpan({cls: "la-model-icon"}); setIcon(reasoningIcon, "brain-circuit");
      const reasoningCopy = reasoningRow.createSpan({cls: "la-model-popover__reasoning-copy"});
      reasoningCopy.createSpan({text: "深度思考"});
      reasoningCopy.createEl("small", {text: "提高推理预算，并要求证据与结果校验"});
      const reasoningSwitch = reasoningRow.createSpan({cls: `la-model-switch ${this.assistantReasoningMode === "deep" ? "is-on" : ""}`, attr: {"aria-hidden": "true"}}); reasoningSwitch.createSpan();
      reasoningRow.onclick = () => {
        this.assistantReasoningMode = this.assistantReasoningMode === "deep" ? "auto" : "deep";
        paintModelTrigger(); rebuildModelPopover();
      };
      const autoRow = modelPopover.createEl("button", {cls: "la-model-popover__auto", attr: {type: "button", role: "option", "aria-selected": String(this.assistantModelAuto)}});
      const autoIcon = autoRow.createSpan({cls: "la-model-icon"}); setIcon(autoIcon, "sparkles");
      autoRow.createSpan({cls: "la-model-popover__label", text: "Auto 模式"});
      const autoSwitch = autoRow.createSpan({cls: `la-model-switch ${this.assistantModelAuto ? "is-on" : ""}`, attr: {"aria-hidden": "true"}}); autoSwitch.createSpan();
      autoRow.onclick = () => {
        this.assistantModelAuto = !this.assistantModelAuto;
        paintModelTrigger(); rebuildModelPopover();
      };
      const list = modelPopover.createDiv({cls: "la-model-popover__list"});
      if (!enabledProfiles.length) list.createDiv({cls: "la-model-popover__empty", text: "尚未配置可用模型"});
      for (const profile of enabledProfiles) {
        const row = list.createEl("button", {
          cls: `la-model-option ${profile.id === selectedProfileId && !this.assistantModelAuto ? "is-selected" : ""}`,
          attr: {type: "button", role: "option", "aria-selected": String(profile.id === selectedProfileId && !this.assistantModelAuto)},
        });
        const icon = row.createSpan({cls: "la-model-icon"}); renderModelBrand(icon, profile, this.app);
        const copy = row.createSpan({cls: "la-model-option__copy"});
        copy.createSpan({cls: "la-model-option__name", text: profile.displayName || profile.defaultModel || "自定义模型"});
        if (profile.defaultModel && profile.defaultModel !== profile.displayName) copy.createSpan({cls: "la-model-option__model", text: profile.defaultModel});
        if (profile.id === selectedProfileId && !this.assistantModelAuto) { const check = row.createSpan({cls: "la-model-option__check"}); setIcon(check, "check"); }
        row.onclick = () => {
          selectedProfileId = profile.id;
          this.assistantModelAuto = false;
          paintModelTrigger(); closeModelPopover();
          void this.settingsService.updateModelRoute("assistant_chat", selectedProfileId).catch(error => new Notice(`模型切换失败：${error.message}`));
        };
      }
      const configure = modelPopover.createEl("button", {cls: "la-model-popover__configure", attr: {type: "button"}});
      const configureIcon = configure.createSpan(); setIcon(configureIcon, "pencil"); configure.createSpan({text: "配置自定义模型"});
      configure.onclick = () => { closeModelPopover(); openProviderDrawer(); };
    };
    rebuildModelPopover(); paintModelTrigger();
    modelTrigger.onclick = () => {
      const open = modelPopover.hidden;
      if (open) { rebuildModelPopover(); modelPopover.hidden = false; modelPopover.focus(); }
      else closeModelPopover();
      modelTrigger.setAttribute("aria-expanded", String(open));
    };
    modelPicker.addEventListener("focusout", () => window.setTimeout(() => {
      if (!modelPicker.contains(document.activeElement)) closeModelPopover();
    }, 0));
    modelPicker.onkeydown = event => {
      if (event.key === "Escape") { closeModelPopover(); modelTrigger.focus(); }
    };
    const send = iconButton(composerTools, "arrow-up", "发送", () => void sendMessage());
    send.addClass("la-composer-submit");
    const paintSendButton = (running: boolean): void => {
      send.empty();
      send.toggleClass("is-running", running);
      send.setAttribute("aria-label", running ? "停止生成" : "发送");
      send.title = running ? "停止生成" : "发送";
      if (running) {
        send.createSpan({cls: "la-composer-submit__spinner", attr: {"aria-hidden": "true"}});
      } else {
        send.createEl("img", {
          cls: "la-composer-submit__arrow",
          attr: {
            src: SEND_BUTTON_IDLE_VECTOR_DATA_URL,
            alt: "",
            draggable: "false",
            "aria-hidden": "true",
          },
        });
      }
    };
    const isLiveRunActive = isAssistantLiveRunActive(this.assistantLiveRun.status);
    paintSendButton(isLiveRunActive);
    if (isLiveRunActive) {
      input.setAttribute("placeholder", "运行中：输入可调整当前任务；也可从 + 选择完成后继续");
    }
    paintNetworkToggle();

    const ensureConversation = async (): Promise<string> => {
      if (this.conversationId) return this.conversationId;
      const response = await this.client.post<any>("/conversations", {title: "新会话"});
      this.conversationId = response.conversation.id; return this.conversationId;
    };
    const uploadFiles = async (files: File[]): Promise<void> => {
      if (!files.length) return;
      const conversationId = await ensureConversation();
      attach.disabled = true;
      try {
        for (const file of files.slice(0, 10)) {
          const mimeType = file.type || (file.name.toLowerCase().endsWith(".pdf") ? "application/pdf" : file.name.toLowerCase().endsWith(".md") ? "text/markdown" : "text/plain");
          const response = await this.client.uploadAttachment<any>(await file.arrayBuffer(), {conversationId, displayName: file.name, kind: mimeType === "application/pdf" ? "pdf" : file.name.endsWith(".json") ? "conversation" : "text", mimeType});
          if (!this.pendingAttachments.some(item => item.id === response.attachment.id)) this.pendingAttachments.push(response.attachment);
        }
        void this.refresh();
      } finally { attach.disabled = false; }
    };
    fileInput.onchange = () => void uploadFiles(Array.from(fileInput.files ?? []));
    chat.ondragover = event => { event.preventDefault(); chat.addClass("is-dragging"); };
    chat.ondragleave = () => chat.removeClass("is-dragging");
    chat.ondrop = event => { event.preventDefault(); chat.removeClass("is-dragging"); void uploadFiles(Array.from(event.dataTransfer?.files ?? [])); };
    input.onpaste = event => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length) { event.preventDefault(); void uploadFiles(files); return; }
      const text = event.clipboardData?.getData("text/plain")?.trim() ?? "";
      if (/^https?:\/\/\S+$/i.test(text)) {
        event.preventDefault();
        void (async () => {
          this.assistantNetworkEnabled = true;
          paintNetworkToggle();
          safetyText.setText("联网来源不可信 · 写入可撤销");
          const conversationId = await ensureConversation();
          const response = await this.client.post<any>("/intake/attachments", {conversation_id: conversationId, url: text, allow_network: true});
          this.pendingAttachments.push(response.attachment); input.value = `${input.value}${input.value ? "\n" : ""}请研究这个链接并整理关键内容`; this.assistantDraft = input.value; void this.refresh();
        })().catch(error => new Notice(`链接读取失败：${error.message}`));
      }
    };

    const sendMessage = async (): Promise<void> => {
      let content = input.value.trim();
      const regenerateMessageId = this.assistantRegenerateMessageId;
      const regenerateRunId = this.assistantRegenerateRunId;
      if (this.assistantSubmitInFlight && !isAssistantLiveRunActive(this.assistantLiveRun.status)) return;
      if (isAssistantLiveRunActive(this.assistantLiveRun.status)) {
        const activeRunId = this.assistantLiveRun.runId;
        if (this.assistantLiveRun.status === "running" && content && activeRunId) {
          await this.agentRuntime.steer(activeRunId, content);
          const user = messages.createDiv({cls: "la-message la-message--user"});
          user.createDiv({cls: "la-message-copy", text: content});
          input.value = ""; this.assistantDraft = "";
          input.setAttribute("placeholder", "运行中：输入可调整当前任务；也可从 + 选择完成后继续");
          messages.scrollTop = messages.scrollHeight;
          return;
        }
        this.abort?.abort();
        if (activeRunId) {
          await this.agentRuntime.cancel(activeRunId).catch(() => undefined);
        }
        this.assistantLiveRun = cancelledAssistantRun(this.assistantLiveRun);
        const traces = messages.querySelectorAll<HTMLElement>(".la-live-trace");
        const activeTrace = traces.item(traces.length - 1);
        if (activeTrace) this.paintAssistantLiveTrace(activeTrace, this.assistantLiveRun);
        paintSendButton(false);
        return;
      }
      if (!content && !this.pendingAttachments.length) return;
      this.assistantSubmitInFlight = true;
      send.disabled = true;
      const submittedDraft = content;
      let progressiveMarkdown: ProgressiveAssistantMarkdown | null = null;
      let awaitingInlineConfirmation = false;
      let runConversationId = this.conversationId;
      let liveRun = initialAssistantLiveRun();
      try {
        const conversationId = await ensureConversation();
        // The first message in an empty Vault creates its conversation here.
        // Bind the Run only after that stable identity exists; capturing the
        // earlier empty string makes every later event look like another chat.
        runConversationId = conversationId;
        if (!this.pendingAttachments.length && /^\/(?:Users|Volumes)\/[^\n]+$/.test(content)) {
          const pathResult = await this.client.post<any>("/intake/attachments", {conversation_id: conversationId, path: content, explicit_user_selection: true});
          if (pathResult.attachment?.requiresConfirmation) {
            new Notice(`该目录包含 ${pathResult.attachment.fileCount} 个文件，请缩小范围或在资料导入中确认批量处理。`); return;
          }
          this.pendingAttachments.push(pathResult.attachment); content = "请整理这个本地路径中的材料";
        }
        const mentionedNotePath = referencedVaultNotePath(content);
        const contextualNotePath = currentFile && isAssistantReadableVaultPath(currentFile.path) ? currentFile.path : mentionedNotePath;
        const runtimeConversationId = regenerateMessageId
          ? (await this.agentRuntime.fork(regenerateRunId, undefined, "regenerate")).conversationId
          : conversationId;
        if (!regenerateMessageId) {
          const user = messages.createDiv({cls: "la-message la-message--user"});
          const userCopy = user.createDiv({cls: "la-message-copy"}); userCopy.createEl("p", {text: content || "处理这些附件"});
          for (const attachment of this.pendingAttachments) userCopy.createSpan({cls: "la-message-attachment", text: attachment.displayName});
        }
        const trace = messages.createEl("section", {cls: "la-live-trace is-running", attr: {"aria-label": "任务执行进度"}});
        this.assistantLiveTraceEls.set(this.conversationId, trace);
        const assistant = messages.createDiv({cls: "la-message la-message--assistant la-message--streaming"});
        this.assistantLiveAssistantEls.set(this.conversationId, assistant);
        renderAssistantAvatar(assistant, this.app);
        const assistantCopy = assistant.createDiv({cls: "la-message-copy"});
        const markdown = assistantCopy.createDiv({cls: "la-message-markdown"});
        markdown.createSpan({cls: "la-stream-caret", text: "正在连接已选模型…"});
        const confirmationHost = messages.createDiv({cls: "la-inline-confirmation-host"});
        this.assistantLiveConfirmationEls.set(this.conversationId, confirmationHost);
        confirmationHost.hidden = true;
        let disposeConfirmation: (() => void) | undefined;
        progressiveMarkdown = new ProgressiveAssistantMarkdown(this.markdown, markdown);
        messages.scrollTop = messages.scrollHeight;
        liveRun = { ...liveRun, status: "running" as const };
        this.assistantLiveRun = liveRun;
        this.paintAssistantLiveTrace(trace, this.assistantLiveRun);
        input.value = "";
        this.assistantDraft = "";
        this.abort?.abort(); this.abort = new AbortController();
        paintSendButton(true);
        send.disabled = false;
        input.setAttribute("placeholder", "运行中：输入可调整当前任务；也可从 + 选择完成后继续");
        const markdownView = this.app.workspace.getActiveViewOfType(MarkdownView);
        const selection = markdownView?.editor?.getSelection() ?? "";
        let frame = 0;
        const paintDelta = (): void => {
          frame = 0;
          progressiveMarkdown?.push(this.assistantLiveRun.content || "正在生成…");
          if (messages.scrollHeight - messages.scrollTop - messages.clientHeight < 180) messages.scrollTop = messages.scrollHeight;
        };
        const turn = this.agentRuntime.prepareTurn({
          message: content || "请处理这些附件并告诉我下一步",
          regenerateMessageId: regenerateMessageId || undefined,
          conversationId: runtimeConversationId,
          profileId: selectedProfileId,
          model: enabledProfiles.find(item => item.id === selectedProfileId)?.defaultModel || undefined,
          attachments: this.pendingAttachments.map(item => ({
            attachment_id: item.id,
            kind: item.kind,
            display_name: item.displayName,
          })),
          activeNote: {path: contextualNotePath, selection},
          options: {
            available_minutes: 25,
            allow_network: this.assistantNetworkEnabled,
            mode: this.assistantMode,
            reasoning_mode: this.assistantReasoningMode,
            permission_mode: this.assistantPermissionMode,
          },
        });
        for await (const chunk of this.agentRuntime.query(turn, this.abort.signal)) {
          const event = agentChunkToAssistantEvent(chunk);
          liveRun = reduceAssistantStream(liveRun, event);

          // User switched to a different conversation — keep processing
          // in the background but skip UI updates for this view.
          if (this.conversationId !== runConversationId) {
            this.assistantLiveRuns.set(runConversationId, liveRun);
            continue;
          }

          this.assistantLiveRun = liveRun;

          // Re-attach live elements when switching back to this conversation
          // after they were detached by a refresh() during background processing.
          // chat is a stale reference — use the view's live DOM instead.
          if (!trace.isConnected) {
            const currentMessages = this.contentEl.querySelector('.la-message-list.la-chat-stream') as HTMLElement;
            if (currentMessages) {
              currentMessages.appendChild(trace);
              currentMessages.appendChild(assistant);
              currentMessages.appendChild(confirmationHost);
              currentMessages.scrollTop = currentMessages.scrollHeight;
            }
          }

          this.paintAssistantLiveTrace(trace, this.assistantLiveRun);
          if (event.type === "context.resolved") {
            this.assistantContext = {...(this.assistantContext ?? {}), focus: event.focus, currentUnderstanding: event.understanding};
          }
          if (
            event.type === "tool.completed" &&
            ["search_public_web", "search_academic_sources", "fetch_public_url"].includes(String(event.tool ?? "")) &&
            this.assistantInspectorTab === "sources"
          ) {
            context.empty();
            this.renderAssistantContext(context, loadedConversation, primaryArtifact, input);
          }
          if (event.type === "message.delta" && !frame) frame = window.requestAnimationFrame(paintDelta);
          if (event.type === "inline.confirmation.required" && this.assistantLiveRun.confirmation) {
            awaitingInlineConfirmation = true;
            dock.addClass("is-waiting-confirmation");
            input.disabled = true;
            disposeConfirmation?.();
            confirmationHost.hidden = false;
            confirmationHost.empty();
            const confirmation = this.assistantLiveRun.confirmation;
            const handlers: Parameters<typeof renderInlineAgentConfirmation>[2] = {
              confirm: async (runId, scope) => resumeInlineConfirmation(runId, true, scope),
              reject: async runId => resumeInlineConfirmation(runId, false),
              answer: async (runId, answer) => resumeInlineConfirmation(runId, true, "", answer),
            };
            if (confirmation.kind !== "permission" && confirmation.proposal_id) {
              handlers.openDiff = async proposalId => {
                const detail = await this.client.get<any>(`/change-sets/${encodeURIComponent(proposalId)}/diff`);
                const files = Array.isArray(detail.diff?.files) ? detail.diff.files : [];
                const preview = files.map((item: any) => `${String(item.path ?? "")}  +${Number(item.added ?? 0)} -${Number(item.deleted ?? 0)}\n\n${String(item.diff ?? "")}`).join("\n\n");
                new TextPreviewModal(this.app, "修改预览", preview || "暂无候选写入").open();
              };
            }
            disposeConfirmation = renderInlineAgentConfirmation(confirmationHost, confirmation, handlers);
            messages.scrollTop = messages.scrollHeight;
          } else if (event.type === "inline.confirmation.resolved") {
            awaitingInlineConfirmation = false;
            disposeConfirmation?.();
            disposeConfirmation = undefined;
            confirmationHost.empty();
            confirmationHost.hidden = true;
            dock.removeClass("is-waiting-confirmation");
            input.disabled = false;
          }
        }

        // Store the completed liveRun for this conversation
        this.assistantLiveRuns.set(runConversationId, liveRun);

        const appliedCall = [...liveRun.toolCalls].reverse().find(
          item => item.tool === "apply_vault_change" && item.status === "completed",
        );
        const nestedResult = appliedCall?.result?.result;
        const vaultAction = nestedResult && typeof nestedResult === "object" && !Array.isArray(nestedResult)
          ? (nestedResult as Record<string, unknown>)
          : null;
        const messageMetadata = buildAssistantMessageMetadata(liveRun, vaultAction);
        if (liveRun.runId && (liveRun.content || liveRun.status === "completed")) {
          // The terminal Run event is persisted before it reaches this loop, so
          // the backend assistant message already exists. Metadata persistence
          // is best-effort and must never turn a completed model Run into a UI
          // failure if the local service is restarting.
          await this.client.updateMessageMetadata(
            runConversationId,
            `pi-assistant-${liveRun.runId}`,
            messageMetadata,
          ).catch(() => undefined);
        }

        // If user switched away, don't add messages to another conversation
        if (this.conversationId !== runConversationId) {
          this.assistantLiveTraceEls.delete(runConversationId);
          this.assistantLiveAssistantEls.delete(runConversationId);
          this.assistantLiveConfirmationEls.delete(runConversationId);
          return;
        }

        this.assistantLiveRun = liveRun;
        if (this.assistantLiveRun.status === "failed") throw new Error(this.assistantLiveRun.error?.code ?? "assistant_stream_failed");
        if (this.assistantLiveRun.content) await progressiveMarkdown.flush(this.assistantLiveRun.content);
        assistant.removeClass("la-message--streaming");
        const completedMessageBase = this.assistantLiveRun.completedMessage ?? {
          id: this.assistantLiveRun.messageId, role: "assistant", content: this.assistantLiveRun.content,
          createdAt: new Date().toISOString(),
        };
        const completedMessage: Record<string, any> = {
          ...completedMessageBase,
          metadata: messageMetadata,
          reasoningBlocks: this.assistantLiveRun.reasoningBlocks
            .filter(block => block.content.trim())
            .map(block => ({...block, status: "completed"})),
          _traceSteps: liveRun.steps.map(step => ({...step, status: step.status === "running" ? "completed" : step.status})),
          _traceToolCalls: liveRun.toolCalls.map(call => ({...call})),
          _traceContext: liveRun.context,
          _tracePlannerRound: liveRun.plannerRound,
          _traceVaultAction: vaultAction,
          _traceRunId: liveRun.runId,
          _traceModel: liveRun.model,
        };
        assistantCopy.createEl("small", {text: completedMessage.createdAt ? new Date(completedMessage.createdAt).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""});
        this.renderAssistantMessageActions(assistantCopy, completedMessage, {content});
        if (!regenerateMessageId) {
          this.assistantMessages.push({id: `local-user-${Date.now()}`, role: "user", content, createdAt: new Date().toISOString()});
          this.assistantMessages.push(completedMessage);
          this.conversationMessageCache.set(runConversationId, [...this.assistantMessages]);
        }
        this.pendingAttachments = []; input.value = ""; this.assistantDraft = ""; this.assistantRegenerateMessageId = ""; this.assistantRegenerateRunId = "";
        this.assistantLiveTraceEls.delete(runConversationId);
        this.assistantLiveAssistantEls.delete(runConversationId);
        this.assistantLiveConfirmationEls.delete(runConversationId);
      } catch (error: any) {
        if (error?.name === "AbortError") {
          liveRun = cancelledAssistantRun(liveRun);
          this.assistantLiveRuns.set(runConversationId, liveRun);
          this.assistantLiveTraceEls.delete(runConversationId);
        this.assistantLiveAssistantEls.delete(runConversationId);
        this.assistantLiveConfirmationEls.delete(runConversationId);
          if (this.conversationId === runConversationId) this.assistantLiveRun = liveRun;
          return;
        }
        // Store failure state for this conversation
        this.assistantLiveRuns.set(runConversationId, liveRun);
        this.assistantLiveTraceEls.delete(runConversationId);
        this.assistantLiveAssistantEls.delete(runConversationId);
        this.assistantLiveConfirmationEls.delete(runConversationId);
        if (this.conversationId !== runConversationId) return;
        this.assistantLiveRun = liveRun;
        const failure = humanizeAssistantError(String(error.code ?? error.message ?? ""), String(error.message ?? ""), true);
        if (!input.value.trim() && submittedDraft) {
          input.value = submittedDraft;
          this.assistantDraft = submittedDraft;
        }
        const failed = messages.createDiv({cls: "la-assistant-recovery"});
        setIcon(failed.createSpan({cls: "la-assistant-recovery__icon"}), "circle-alert");
        const copy = failed.createDiv(); copy.createEl("strong", {text: failure.title}); copy.createEl("p", {text: failure.message});
        const actions = copy.createDiv({cls: "la-assistant-recovery__actions"});
        button(actions, "重试", () => sendMessage(), "mod-cta");
        button(actions, "切换模型", () => { this.assistantDrawerOpen = true; drawer.addClass("is-open"); shell.addClass("has-drawer"); });
        const technical = copy.createEl("details", {cls: "la-technical"}); technical.createEl("summary", {text: "技术详情"}); technical.createEl("code", {text: String(failure.technicalCode ?? "assistant_error")});
      } finally {
        progressiveMarkdown?.dispose();
        this.abort = null;
        this.assistantSubmitInFlight = false;
        send.disabled = false;
        input.disabled = awaitingInlineConfirmation;
        paintSendButton(false);
        input.setAttribute("placeholder", "今天帮你做些什么？  @ 引用对话文件，/ 调用技能与指令");
        if (!awaitingInlineConfirmation) input.focus();
      }
    };
    const resumeInlineConfirmation = async (
      runId: string,
      confirmed: boolean,
      scope = "",
      answer = "",
    ): Promise<void> => {
      for await (const chunk of this.agentRuntime.confirm(runId, confirmed, undefined, answer, scope)) {
          const event = agentChunkToAssistantEvent(chunk);
          this.assistantLiveRun = reduceAssistantStream(this.assistantLiveRun, event);
      }
    };
    const resultActiveArtifact = (artifacts: any[]): string => String([...artifacts].reverse().find(item => !["change_set", "quiz"].includes(item.type))?.id ?? "");
    input.onkeydown = event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } };
    const safety = dock.createDiv({cls: "la-assistant-safety"}); setIcon(safety.createSpan(), "shield-check"); safety.createSpan({text: "低风险维护由 Harness 校验并可撤销；受保护知识会在当前对话中请求授权。"});
    this.renderAssistantContext(context, loadedConversation, primaryArtifact, input);
    this.renderProviderDrawer(drawer, profiles, routes);
  }

  private paintAssistantLiveTrace(parent: HTMLElement, run: AssistantLiveRun): void {
    const previousDetails = parent.querySelector<HTMLDetailsElement>(".la-live-trace__details");
    const previousProviderReasoning = parent.querySelector<HTMLDetailsElement>(".la-provider-reasoning");
    const beganRunning = parent.hasClass("is-idle") && run.status === "running";
    const detailsOpen = beganRunning || (previousDetails?.open ?? run.status === "running");
    const providerReasoningOpen = previousProviderReasoning?.open ?? run.status === "running";
    // Preserve the spinner element across repaints so the CSS animation
    // continues smoothly instead of restarting on every stream event.
    const cachedSpinner = parent.querySelector<HTMLElement>(".la-live-trace__spinner");
    // Preserve step row DOM elements to avoid flicker from full DOM rebuild
    const savedStepRows = new Map<string, HTMLElement>();
    parent.querySelectorAll('.la-live-trace__step').forEach(el => {
      const stepId = (el as HTMLElement).dataset.step;
      if (stepId) savedStepRows.set(stepId, el as HTMLElement);
    });
    parent.empty();
    parent.className = `la-live-trace is-${run.status}`;
    const head = parent.createDiv({cls: "la-live-trace__head"});
    const status = head.createSpan({cls: "la-live-trace__status"});
    if (run.status === "running") {
      if (cachedSpinner) {
        status.appendChild(cachedSpinner);
      } else {
        status.createSpan({cls: "la-live-trace__spinner"});
      }
    } else {
      setIcon(status, run.status === "completed" ? "circle-check" : run.status === "failed" ? "circle-alert" : "circle-stop");
    }
    const copy = head.createDiv();
    copy.createEl("strong", {text: run.status === "completed" ? "知序已完成处理" : run.status === "failed" ? "处理未完成" : run.status === "cancelled" ? "已停止生成" : "知序正在处理"});
    copy.createEl("small", {text: run.model ? `模型 · ${run.model}` : "正在建立本地上下文"});
    const details = parent.createEl("details", {cls: "la-live-trace__details"});
    details.open = detailsOpen;
    details.createEl("summary", {text: "推理与执行过程"});
    details.createDiv({
      cls: "la-live-trace__disclaimer",
      text: "模型推理仅在供应商真实返回独立 reasoning block 时出现；工具状态来自实际运行事件。",
    });
    this.renderProviderReasoning(details, run.reasoningBlocks, providerReasoningOpen);
    const thinking = details.createDiv({cls: "la-live-trace__thinking"});
    const sourceCount = run.context?.sources?.length ?? 0;
    const thinkingText = run.status === "running"
      ? run.content
        ? "已取得所需观察结果，正在组织回答"
        : run.toolCalls.some(item => item.status === "running")
          ? "正在根据工具观察推进当前任务"
          : run.context
            ? `已装配当前上下文${sourceCount ? `与 ${sourceCount} 条来源` : ""}，正在决定下一步`
            : "正在理解请求并装配当前笔记、附件与会话上下文"
      : run.status === "completed"
        ? "已完成上下文理解、工具执行与结果校验"
        : run.status === "cancelled"
          ? "任务已由你停止，已返回的内容仍然保留"
          : run.status === "failed"
            ? "执行未完成；可展开下方步骤定位失败环节"
            : "等待任务开始";
    setIcon(thinking.createSpan(), "brain-circuit");
    const thinkingCopy = thinking.createDiv();
    thinkingCopy.createEl("strong", {text: "执行摘要"});
    thinkingCopy.createEl("p", {text: thinkingText});
    if (run.plannerRound > 0) thinkingCopy.createEl("small", {text: `已完成 ${run.plannerRound} 轮观察与重新规划`});
    const steps = details.createDiv({cls: "la-live-trace__steps"});
    const fallbackStatus = run.status === "completed"
      ? "completed"
      : run.status === "failed"
        ? "failed"
        : run.status === "cancelled"
          ? "cancelled"
          : "running";
    const rendered = run.steps.length ? run.steps : [{id: "pending", label: "理解当前请求与上下文", status: fallbackStatus}];
    for (const step of rendered) {
      // Reuse existing DOM when status unchanged to avoid spinner flicker
      const saved = savedStepRows.get(step.id);
      if (saved && saved.classList.contains(`is-${step.status}`)) {
        steps.appendChild(saved);
        continue;
      }
      const row = steps.createDiv({cls: `la-live-trace__step is-${step.status}`, attr: {"data-step": step.id}});
      const stepIcon = row.createSpan({cls: "la-live-trace__step-icon"});
      if (step.status === "running") {
        stepIcon.createSpan({cls: "la-live-trace__step-spinner"});
      } else {
        setIcon(stepIcon, step.status === "completed" ? "check" : step.status === "failed" ? "x" : "circle");
      }
      row.createSpan({text: step.label});
      const call = step.id.startsWith("tool:") ? run.toolCalls.find(item => `tool:${item.id}` === step.id) : undefined;
      row.createEl("small", {text: call?.summary || (step.status === "completed" ? "完成" : step.status === "running" ? "进行中" : step.status === "failed" ? "失败" : "等待")});
    }
    const appliedCall = [...run.toolCalls].reverse().find(
      item => item.tool === "apply_vault_change" && item.status === "completed",
    );
    const nestedResult = appliedCall?.result?.result;
    const action = nestedResult && typeof nestedResult === "object"
      ? nestedResult as Record<string, any>
      : null;
    if (action?.actionId && action.state === "applied") {
      const result = details.createDiv({cls: "la-live-trace__action-result"});
      const resultHead = result.createDiv({cls: "la-live-trace__action-result-head"});
      const iconWrap = resultHead.createSpan({cls: "la-live-trace__action-result-icon"});
      setIcon(iconWrap, "file-check-2");
      const resultCopy = resultHead.createDiv();
      resultCopy.createEl("strong", {text: "已整理完成"});
      const files = Array.isArray(action.files) ? action.files : [];
      const added = files.reduce((sum: number, item: any) => sum + Number(item.added ?? 0), 0);
      const deleted = files.reduce((sum: number, item: any) => sum + Number(item.deleted ?? 0), 0);
      resultCopy.createEl("small", {text: `${files.length} 个文件 · +${added} -${deleted} · 已校验`});
      const fileList = result.createDiv({cls: "la-live-trace__action-files"});
      for (const item of files.slice(0, 10)) {
        const file = fileList.createEl("a", {cls: "la-live-trace__action-file", href: "#"});
        setIcon(file.createSpan({cls: "la-live-trace__action-file-icon"}), "file-text");
        file.createSpan({cls: "la-live-trace__action-file-path", text: String(item.path ?? "Markdown 文件")});
        file.onclick = event => { event.preventDefault(); void this.app.workspace.openLinkText(String(item.path ?? ""), "", false); };
      }
      const actions = result.createDiv({cls: "la-live-trace__action-actions"});
      button(actions, "查看变化", async () => {
        const diff = await this.client.agentActionDiff(String(action.actionId));
        const preview = (Array.isArray(diff.files) ? diff.files : []).map((item: any) =>
          `${String(item.path ?? "")}  +${Number(item.added ?? 0)} -${Number(item.deleted ?? 0)}\n\n${String(item.diff ?? "")}`,
        ).join("\n\n");
        new TextPreviewModal(this.app, "本次修改", preview || "没有文本变化").open();
      }, "la-button--ghost");
      if (action.undoAvailable) button(actions, "撤销", async () => {
        await this.client.undoAgentAction(String(action.actionId));
        new Notice("已安全撤销本次修改");
        action.state = "undone";
        action.undoAvailable = false;
        this.paintAssistantLiveTrace(parent, run);
      }, "la-button--ghost");
    }
    if (run.proposalRequired) {
      const notice = details.createDiv({cls: "la-live-trace__proposal"}); setIcon(notice.createSpan(), "file-diff");
      notice.createSpan({text: "该操作超出当前任务授权，需要在当前对话中处理。"});
    }
  }

  private renderAssistantTaskThread(parent: HTMLElement, task: AssistantTaskThread, setComposer: (value: string) => void): void {
    const root = parent.createEl("section", {cls: `la-assistant-task-thread is-${task.status}`});
    const head = root.createDiv({cls: "la-assistant-task-thread__head"});
    const copy = head.createDiv(); copy.createEl("strong", {text: `当前任务：${task.title}`});
    copy.createEl("small", {text: task.status === "completed" ? "已完成，可继续调整" : task.status === "failed" ? "部分步骤需要处理" : `正在处理 · ${task.progress}%`});
    const chevron = iconButton(head, "chevron-up", "折叠任务", () => root.toggleClass("is-collapsed", !root.hasClass("is-collapsed")));
    chevron.addClass("la-task-thread-toggle");
    const steps = root.createDiv({cls: "la-assistant-task-steps"});
    for (const step of task.steps) {
      const item = steps.createDiv({cls: `la-assistant-task-step is-${step.status}`});
      const stepIcon = item.createSpan({cls: "la-assistant-task-step__icon"});
      if (step.status === "running") {
        stepIcon.createSpan({cls: "la-live-trace__step-spinner"});
      } else {
        setIcon(stepIcon, step.status === "completed" ? "circle-check" : step.status === "failed" ? "circle-alert" : "circle");
      }
      const text = item.createDiv(); text.createEl("strong", {text: step.label});
      text.createEl("small", {text: step.status === "completed" ? "完成" : step.status === "running" ? "进行中" : step.status === "failed" ? "需要处理" : "待开始"});
    }
    if (task.status === "failed" || task.error) {
      const failure = task.error ?? humanizeAssistantError(task.technical?.errorCode ?? "", "", true);
      const recovery = root.createDiv({cls: "la-assistant-recovery la-assistant-recovery--inline"});
      setIcon(recovery.createSpan({cls: "la-assistant-recovery__icon"}), "circle-alert");
      const text = recovery.createDiv(); text.createEl("strong", {text: failure.title}); text.createEl("p", {text: failure.message});
      const actions = text.createDiv({cls: "la-assistant-recovery__actions"});
      for (const action of failure.actions) button(actions, action.label, async () => {
        if (action.id === "retry") {
          setComposer("请根据刚才保留的上下文重试这个任务，并从失败的工具步骤继续。");
          return;
        }
        if (action.id === "change-model") { this.assistantDrawerOpen = true; await this.refresh(); return; }
        setComposer(action.id === "trusted-research" ? "请搜索可信网页和论文后继续这个任务" : action.id === "limited-guide" ? "先生成来源范围明确的概念导读" : "请调整并继续这个任务");
      }, action.primary ? "mod-cta" : "");
    }
    const technical = root.createEl("details", {cls: "la-technical"}); technical.createEl("summary", {text: "执行详情"}); technical.createEl("code", {text: task.technical?.runId ?? task.id});
  }

  private renderAssistantArtifactGroup(parent: HTMLElement, group: ArtifactGroup, setComposer: (value: string) => void): void {
    const primary = group.artifacts.find(item => item.id === group.primaryArtifactId) ?? group.artifacts[0];
    if (!primary) return;
    if (primary.type !== "learning_pack") {
      const region = parent.createDiv({cls: "la-chat-artifacts la-chat-artifacts--single"});
      this.renderArtifactCard(region, primary);
      return;
    }
    const view = learningPackView(primary);
    const card = parent.createEl("section", {cls: "la-learning-pack"});
    const head = card.createDiv({cls: "la-learning-pack__head"});
    const icon = head.createSpan({cls: "la-learning-pack__icon"}); setIcon(icon, "graduation-cap");
    const title = head.createDiv(); title.createEl("h2", {text: view.title});
    const meta = title.createDiv({cls: "la-learning-pack__meta"});
    meta.createSpan({text: `${view.minutes} 分钟`}); badge(meta, "learn", view.domain); badge(meta, "neutral", view.format);
    badge(head, "source", `v${group.version}`);
    const grid = card.createDiv({cls: "la-learning-pack__grid"});
    const outcomes = grid.createDiv({cls: "la-learning-pack__block"}); outcomes.createEl("strong", {text: "你将学到"});
    const outcomeList = outcomes.createEl("ul"); for (const item of view.outcomes) outcomeList.createEl("li", {text: item});
    const prereqs = grid.createDiv({cls: "la-learning-pack__block"}); prereqs.createEl("strong", {text: "前置知识"});
    const prereqList = prereqs.createEl("ul"); for (const item of view.prerequisites) prereqList.createEl("li", {text: item});
    const structure = grid.createDiv({cls: "la-learning-pack__block"}); structure.createEl("strong", {text: "内容结构"});
    const sectionList = structure.createEl("ol"); for (const item of view.sections) sectionList.createEl("li", {text: `${item.title}（${item.minutes} 分钟）`});
    const quiz = grid.createDiv({cls: "la-learning-pack__block"}); quiz.createEl("strong", {text: "小测预览"});
    quiz.createEl("p", {text: `${view.quizCount} 道理解题`}); quiz.createEl("small", {text: view.quizLabel});
    const actions = card.createDiv({cls: "la-learning-pack__actions"});
    button(actions, "预览", () => new TextPreviewModal(this.app, view.title, String(primary.payload?.answer ?? primary.payload?.summary ?? "学习包已生成")).open());
    button(actions, this.assistantToday.has(primary.id) ? "已加入今日" : "加入今日", async () => {
      const response = await this.client.post<any>("/integrations/today/add", {artifact_id: primary.id});
      this.assistantToday.add(primary.id); new Notice(isTodayDuplicate(response) ? "今天已有这个学习任务" : "已加入今日"); await this.refresh();
    });
    button(actions, "开始学习", async () => {
      const response = await this.client.post<any>("/integrations/today/add", {artifact_id: primary.id});
      this.assistantToday.add(primary.id); await this.startStudy(response.recommendation);
    }, "mod-cta");
    const suggestions = card.createDiv({cls: "la-learning-pack__suggestions"});
    suggestions.createSpan({text: "你也可以："});
    button(suggestions, "拆成三天", () => setComposer("把这个学习包拆成三天，每天控制在 15 分钟内"));
    button(suggestions, "先学前置知识", () => setComposer(`先帮我学习前置知识：${view.prerequisites.slice(0, 2).join("、")}`));
    button(suggestions, "查看来源", () => new TextPreviewModal(this.app, "学习包来源", view.sources.map(item => `${item.title}${item.path ? ` · ${item.path}` : ""}`).join("\n") || "当前仅使用已审核本地知识与明确标注的模型解释").open());
  }

  private renderAssistantContext(parent: HTMLElement, conversation: any, artifact: AssistantArtifact | undefined, composer: HTMLTextAreaElement): void {
    const tabs = parent.createDiv({cls: "la-assistant-inspector-tabs", attr: {role: "tablist", "aria-label": "助手检查器"}});
    for (const [id, label] of [["context", "上下文"], ["sources", "来源"], ["changes", "变更"]] as const) {
      const tab = tabs.createEl("button", {text: label, cls: this.assistantInspectorTab === id ? "is-active" : "", attr: {role: "tab", "aria-selected": String(this.assistantInspectorTab === id)}});
      tab.onclick = () => { this.assistantInspectorTab = id; void this.refresh(); };
    }
    iconButton(tabs, "x", "关闭检查器", () => {
      this.assistantInspectorOpen = false;
      parent.parentElement?.addClass("inspector-closed"); parent.parentElement?.removeClass("has-inspector");
    });
    if (this.assistantInspectorTab === "sources") {
      const heading = parent.createDiv({cls: "la-assistant-context__head"}); heading.createEl("h2", {text: "来源材料"});
      const resolved = this.assistantLiveRun.context?.sources ?? [];
      const recent = this.assistantContext?.recentMaterials ?? [];
      const artifactSources = assistantInspectorSources(this.assistantArtifacts);
      const webSources = this.assistantLiveRun.toolCalls.flatMap(call => {
        const result: any = call.result ?? {};
        if (["search_public_web", "search_academic_sources"].includes(call.tool)) return Array.isArray(result.results) ? result.results : [];
        if (call.tool === "fetch_public_url" && result.source) return [result.source];
        return [];
      }).map((item: any) => ({...item, kind: item.sourceType ?? "public_web", status: "untrusted-web"}));
      const items = [...webSources, ...resolved, ...recent, ...artifactSources].filter((item, index, all) => all.findIndex(other => String(other.id || other.path || other.url || other.title) === String(item.id || item.path || item.url || item.title)) === index);
      if (!items.length) {
        const empty = parent.createDiv({cls: "la-assistant-context-empty la-source-empty"});
        empty.createEl("p", {text: this.assistantNetworkEnabled ? "当前回答还没有引用来源；知序会在需要时主动检索并显示在这里。" : "当前为仅本地模式。需要最新资料时，可为本会话开启联网。"});
        if (!this.assistantNetworkEnabled) button(empty, "开启联网研究", () => { this.assistantNetworkEnabled = true; void this.refresh(); }, "mod-cta");
      }
      for (const source of items) {
        const isWeb = Boolean(source.url);
        const card = parent.createDiv({cls: `la-assistant-source-card ${isWeb ? "is-web" : ""}`});
        const icon = card.createSpan(); setIcon(icon, source.kind === "pdf" ? "file-text" : source.kind === "vault_note" ? "notebook-text" : isWeb ? "globe-2" : "link-2");
        const copy = card.createDiv(); copy.createEl("strong", {text: humanTitle(source.title, isWeb ? source.domain || "公开来源" : "本地来源")});
        copy.createEl("small", {text: isWeb ? `${source.domain || "公开网页"} · ${source.provider || "web"}${source.qualityScore ? ` · 质量 ${Math.round(Number(source.qualityScore) * 100)}%` : ""}` : `${String(source.kind ?? "material").replace(/_/g, " ")} · ${statusLabel(String(source.status ?? "local"))}`});
        if (isWeb && source.snippet) copy.createEl("p", {text: String(source.snippet), cls: "la-source-snippet"});
        if (source.path) copy.createEl("small", {text: String(source.path)});
        card.toggleClass("is-clickable", Boolean(source.path || source.url));
        card.onclick = () => source.path ? void this.app.workspace.openLinkText(String(source.path), "", false) : source.url ? window.open(String(source.url), "_blank", "noopener,noreferrer") : undefined;
      }
      const related = parent.createEl("section", {cls: "la-assistant-context-section"}); related.createEl("h3", {text: "相关笔记"});
      for (const note of this.assistantContext?.relatedNotes ?? []) {
        const row = related.createEl("button"); setIcon(row.createSpan(), "file-text"); row.createSpan({text: note.title}); setIcon(row.createSpan(), "external-link");
        row.onclick = () => note.path ? void this.app.workspace.openLinkText(note.path, "", false) : undefined;
      }
      return;
    }
    if (this.assistantInspectorTab === "changes") {
      const heading = parent.createDiv({cls: "la-assistant-context__head"}); heading.createEl("h2", {text: "变更"});
      const plan = this.assistantContext?.organizationPlan ?? {};
      const planChanges = (plan.executionResults ?? []).filter((item: any) => item.status === "applied" || item.changeSet);
      const artifactChanges = assistantInspectorChanges(this.assistantArtifacts);
      const changes = [...planChanges, ...artifactChanges].filter((item: any, index, all) => {
        const key = String(item.changeSetId ?? item.changeSet?.id ?? item.actionId ?? item.id ?? index);
        return all.findIndex((other: any, otherIndex) => String(other.changeSetId ?? other.changeSet?.id ?? other.actionId ?? other.id ?? otherIndex) === key) === index;
      });
      if (!changes.length) parent.createEl("p", {cls: "la-assistant-context-empty", text: "本次会话尚未修改知识库。普通问答不会创建文件。"});
      for (const change of changes) {
        const row = parent.createDiv({cls: "la-context-change"});
        const applied = change.status === "applied" || change.status === "completed";
        const top = row.createDiv(); setIcon(top.createSpan(), applied ? "file-check-2" : "file-diff");
        const copy = top.createDiv(); copy.createEl("strong", {text: applied ? "已应用可撤销修改" : "Change Set 待确认"});
        copy.createEl("small", {text: change.path ?? change.proposalPath ?? "受保护目标"});
        if (change.summary) copy.createEl("small", {text: String(change.summary)});
        const actions = row.createDiv({cls: "la-context-change__actions"});
        if (change.actionId) button(actions, "查看 Diff", async () => {
          const detail = await this.client.get<any>(`/agent-actions/${encodeURIComponent(change.actionId)}`);
          new TextPreviewModal(this.app, "变更记录", (detail.action?.changes ?? []).map((item: any) => `${item.relative_path}\n${item.diff_summary}`).join("\n\n") || "暂无变更").open();
        });
        const changeSetId = String(change.changeSetId ?? change.changeSet?.id ?? "");
        if (changeSetId) button(actions, "查看 Diff", async () => {
          const detail = await this.client.get<any>(`/change-sets/${encodeURIComponent(changeSetId)}`);
          const changeSet = detail.change_set ?? {};
          const writes = (changeSet.writes ?? []).map((item: any) => `${String(item.action ?? "update").toUpperCase()}  ${item.path}`).join("\n");
          new TextPreviewModal(this.app, changeSet.title ?? "Change Set", `${changeSet.preview ?? "等待确认"}\n\n${writes || "暂无候选写入"}`).open();
        });
        if (change.undoAvailable && change.actionId) button(actions, "撤销", async () => {
          await this.client.post(`/agent-actions/${encodeURIComponent(change.actionId)}/undo`, {}); new Notice("已安全撤销"); await this.refresh();
        });
        if (changeSetId) button(actions, "在助手中处理", () => this.setTab("assistant"), "mod-cta");
      }
      return;
    }
    const heading = parent.createDiv({cls: "la-assistant-context__head"}); heading.createEl("h2", {text: this.assistantTaskThread ? "本次任务" : "当前理解"});
    const focus = this.assistantContext?.focus ?? conversation?.focus ?? {};
    const understanding = this.assistantContext?.currentUnderstanding ?? {};
    const plan = this.assistantContext?.organizationPlan ?? {};
    const activeEntity = focus.activeMethod ?? focus.activeConcept ?? focus.activeTopic ?? focus.activeMaterial;
    const contextCard = parent.createDiv({cls: "la-assistant-context-card"});
    const contextTitle = contextCard.createDiv({cls: "la-assistant-context-card__title"}); setIcon(contextTitle.createSpan(), "scan-search"); contextTitle.createEl("strong", {text: "当前理解"});
    contextCard.createEl("p", {text: activeEntity?.displayName ? `正在讨论：${activeEntity.displayName}` : this.assistantTaskThread?.title ?? conversation?.activeTopic ?? "从对话、当前笔记和已审核知识中组织回答。"});
    if (understanding.title) contextCard.createEl("p", {text: `材料：${understanding.title} · ${String(understanding.kind ?? "conversation").replace(/_/g, " ")}`});
    if (focus.activeVaultNotePath) contextCard.createEl("small", {text: `当前笔记 · ${focus.activeVaultNotePath}`});
    if (Number(focus.confidence ?? 0) > 0) contextCard.createEl("small", {text: `上下文置信度 ${Math.round(Number(focus.confidence) * 100)}%`});
    if (conversation?.title) contextCard.createEl("small", {text: conversation.title});
    if (conversation?.summary?.summary) contextCard.createEl("p", {text: conversation.summary.summary, cls: "la-assistant-context-summary"});
    const focusEvidence = [...(focus.evidence ?? [])];
    const lastEvidence = focusEvidence[focusEvidence.length - 1];
    if (lastEvidence?.kind === "unresolved-pronoun") {
      const clarify = parent.createEl("section", {cls: "la-assistant-context-section la-context-clarify"});
      clarify.createEl("h3", {text: "需要确认一个指代"});
      clarify.createEl("p", {cls: "la-assistant-context-empty", text: `我还不能可靠判断“${lastEvidence.marker}”指什么。只需确认一次，不会重问整项任务。`});
      const useNote = clarify.createEl("button"); setIcon(useNote.createSpan(), "file-check-2"); useNote.createSpan({text: "使用当前笔记"}); setIcon(useNote.createSpan(), "chevron-right");
      useNote.onclick = () => { composer.value = "以当前打开的笔记为目标继续"; composer.focus(); };
      const describe = clarify.createEl("button"); setIcon(describe.createSpan(), "text-cursor-input"); describe.createSpan({text: "补充目标名称"}); setIcon(describe.createSpan(), "chevron-right"); describe.onclick = () => composer.focus();
    }
    if (plan.planId) {
      const working = parent.createEl("section", {cls: "la-assistant-context-section la-context-plan"});
      working.createEl("h3", {text: "Agent 正在做什么"});
      const action = plan.actions?.[0];
      const row = working.createDiv({cls: "la-context-plan-row"});
      const icon = row.createSpan(); setIcon(icon, plan.status === "applied" ? "circle-check-big" : "git-pull-request-draft");
      const copy = row.createDiv(); copy.createEl("strong", {text: plan.status === "applied" ? "整理已完成" : "整理方案待确认"});
      copy.createEl("small", {text: action?.targetPath ?? plan.explanation ?? "正在解析目标位置"});
      if (action?.reason) copy.createEl("p", {text: action.reason});
    }
    const recentChanges = (plan.executionResults ?? []).filter((item: any) => item.status === "applied" || item.changeSet);
    if (recentChanges.length) {
      const changes = parent.createEl("section", {cls: "la-assistant-context-section la-context-changes"});
      changes.createEl("h3", {text: "最近修改"});
      for (const change of recentChanges.slice(0, 3)) {
        const row = changes.createDiv({cls: "la-context-change"});
        const top = row.createDiv(); setIcon(top.createSpan(), change.status === "applied" ? "file-check-2" : "shield-alert");
        const copy = top.createDiv(); copy.createEl("strong", {text: change.status === "applied" ? "已写入可撤销草稿" : "已生成更新建议"}); copy.createEl("small", {text: change.path ?? "受保护目标"});
        const actions = row.createDiv({cls: "la-context-change__actions"});
        if (change.status === "applied" && change.path) button(actions, "打开笔记", () => void this.app.workspace.openLinkText(change.path, "", false));
        if (change.actionId) button(actions, "查看变化", async () => {
          const detail = await this.client.get<any>(`/agent-actions/${encodeURIComponent(change.actionId)}`);
          const lines = (detail.action?.changes ?? []).map((item: any) => `${item.relative_path}\n${item.diff_summary}\n${item.before_hash ?? "new"} → ${item.after_hash ?? ""}`);
          new TextPreviewModal(this.app, "Agent 修改记录", lines.join("\n\n") || "没有可显示的变化").open();
        });
        if (change.undoAvailable && change.actionId) button(actions, "撤销", async () => {
          await this.client.post(`/agent-actions/${encodeURIComponent(change.actionId)}/undo`, {});
          new Notice("已安全撤销；后续人工修改不会被覆盖"); await this.refresh();
        });
        if (change.changeSet?.id) button(actions, "在助手中处理", () => this.setTab("assistant"));
      }
    }
    const signals = parent.createEl("section", {cls: "la-assistant-context-section la-assistant-signals"});
    signals.createEl("h3", {text: `最近对话信号 ${conversation?.knowledgeSignals?.length ?? 0}`});
    if (!(conversation?.knowledgeSignals?.length)) signals.createEl("p", {cls: "la-assistant-context-empty", text: "继续自然对话后，这里会显示主题、困惑和前置缺口；不会推断未经你表达的掌握度。"});
    for (const signal of (conversation?.knowledgeSignals ?? []).slice(0, 4)) {
      const row = signals.createDiv({cls: "la-assistant-signal"});
      const top = row.createDiv(); top.createEl("strong", {text: String(signal.topic || "当前主题")});
      badge(top, signal.signalType === "confusion" ? "warning" : "neutral", ({confusion: "反复困惑", missing_prerequisite: "前置缺口", claimed_knowledge: "用户明确已知", interest: "兴趣信号", topic: "当前主题"} as Record<string, string>)[signal.signalType] ?? "学习信号");
      row.createEl("p", {text: String(signal.detail || "来自当前会话")});
      row.createEl("small", {text: `证据消息 ${signal.messageIds?.length ?? 1} · 重复 ${signal.recurrence ?? 1} 次 · ${Number(signal.confidence ?? 0) >= .75 ? "高置信" : "待积累"}`});
    }
    const related = parent.createEl("section", {cls: "la-assistant-context-section"});
    related.createEl("h3", {text: `相关笔记 ${this.assistantContext?.relatedNotes?.length ?? 0}`});
    if (!(this.assistantContext?.relatedNotes?.length)) {
      related.createEl("p", {cls: "la-assistant-context-empty", text: "暂无匹配的 reviewed/core 笔记"});
    }
    for (const note of this.assistantContext?.relatedNotes ?? []) {
      const row = related.createEl("button"); setIcon(row.createSpan(), "file-text"); row.createSpan({text: note.title}); setIcon(row.createSpan(), "external-link");
      row.onclick = () => note.path ? void this.app.workspace.openLinkText(note.path, "", false) : new Notice("该知识来自当前上下文，尚无独立笔记路径");
    }
    const materials = parent.createEl("section", {cls: "la-assistant-context-section"});
    materials.createEl("h3", {text: `最近资料 ${this.assistantContext?.recentMaterials?.length ?? 0}`});
    if (!(this.assistantContext?.recentMaterials?.length)) {
      materials.createEl("p", {cls: "la-assistant-context-empty", text: "暂无可追溯的近期资料"});
    }
    for (const material of this.assistantContext?.recentMaterials ?? []) {
      const row = materials.createEl("button"); setIcon(row.createSpan(), "file-archive"); row.createSpan({text: material.title}); setIcon(row.createSpan(), "external-link"); row.onclick = () => this.setTab("sources");
    }
    const actions = parent.createEl("section", {cls: "la-assistant-context-section"}); actions.createEl("h3", {text: "推荐动作"});
    const alreadyToday = Boolean(artifact && this.assistantToday.has(artifact.id));
    if (artifact) {
      const add = actions.createEl("button"); setIcon(add.createSpan(), alreadyToday ? "calendar-check" : "calendar-plus"); add.createSpan({text: alreadyToday ? "已在今日" : "加入今日"}); setIcon(add.createSpan(), "chevron-right");
      add.onclick = () => void (async () => { const result = await this.client.post<any>("/integrations/today/add", {artifact_id: artifact.id}); this.assistantToday.add(artifact.id); new Notice(isTodayDuplicate(result) ? "今天已有这个学习任务" : "已加入今日"); await this.refresh(); })();
    }
    const path = actions.createEl("button"); setIcon(path.createSpan(), "route"); path.createSpan({text: "生成学习路径"}); setIcon(path.createSpan(), "chevron-right"); path.onclick = () => { composer.value = "基于当前成果生成一条循序渐进的学习路径"; composer.focus(); };
    const followup = actions.createEl("button"); setIcon(followup.createSpan(), "message-circle-question"); followup.createSpan({text: "继续追问"}); setIcon(followup.createSpan(), "chevron-right"); followup.onclick = () => composer.focus();
    if (conversation?.id) {
      const personalization = actions.createEl("button"); setIcon(personalization.createSpan(), conversation.personalizationEnabled === false ? "user-round-x" : "user-round-check");
      personalization.createSpan({text: conversation.personalizationEnabled === false ? "启用本会话个性化" : "本会话不用于个性化"}); setIcon(personalization.createSpan(), "chevron-right");
      personalization.onclick = async () => {
        await this.client.patch(`/conversations/${encodeURIComponent(conversation.id)}/preferences`, {personalization_enabled: conversation.personalizationEnabled === false});
        new Notice(conversation.personalizationEnabled === false ? "已启用本会话个性化" : "本会话后续不再提取个性化信号"); await this.refresh();
      };
    }
    if (conversation?.id) {
      const footer = parent.createDiv({cls: "la-assistant-context__footer"}); footer.createSpan({text: `会话 ${String(conversation.id).slice(-8)}`});
      iconButton(footer, "copy", "复制会话 ID", () => void navigator.clipboard.writeText(String(conversation.id)));
    }
  }

  private renderConversationMessage(parent: HTMLElement, message: any, previousUserMessage: any = null): void {
    const piTrace = persistedAssistantTrace(message);
    if (piTrace && !message._traceSteps) {
      message._traceSteps = piTrace.steps;
      message._traceToolCalls = piTrace.toolCalls;
      message._traceContext = piTrace.context;
      message._tracePlannerRound = piTrace.plannerRound;
      message._traceVaultAction = piTrace.vaultAction;
      message._traceRunId = piTrace.runId;
      message._traceModel = piTrace.model;
      message._traceStatus = piTrace.status;
      message.reasoningBlocks = piTrace.reasoningBlocks;
    }
    const root = parent.createDiv({cls: `la-message la-message--${message.role === "user" ? "user" : "assistant"}`});
    if (message.role !== "user") renderAssistantAvatar(root, this.app);
    const copy = root.createDiv({cls: "la-message-copy"});
    if (message.role === "user") copy.createEl("p", {text: String(message.content ?? "")});
    else {
      // 缓存消息现在保留完整的 traceSteps 和 reasoningBlocks，无需赘述回退逻辑
      const traceSteps = message._traceSteps ?? [];
      const hasTrace = Array.isArray(traceSteps) && traceSteps.length > 0;
      if (!hasTrace) {
        this.renderProviderReasoning(copy, Array.isArray(message.reasoningBlocks) ? message.reasoningBlocks : [], false);
      }
      void this.markdown.render(copy.createDiv({cls: "la-message-markdown"}), String(message.content ?? ""));
      if (hasTrace) {
        const traceContainer = copy.createDiv({cls: "la-live-trace is-completed"});
        const syntheticRun = {
          runId: String(message._traceRunId ?? message.id ?? ""),
          conversationId: this.conversationId,
          messageId: String(message.id ?? ""),
          model: String(message._traceModel ?? ""),
          status: (["completed", "failed", "cancelled"].includes(String(message._traceStatus))
            ? message._traceStatus
            : "completed") as "completed" | "failed" | "cancelled",
          content: "",
          reasoningBlocks: Array.isArray(message.reasoningBlocks) ? message.reasoningBlocks : [],
          completedMessage: message,
          lastSequence: 0,
          started: true,
          context: message._traceContext ?? undefined,
          steps: traceSteps,
          proposalRequired: false,
          allowedTools: [] as string[],
          plannerRound: Number(message._tracePlannerRound ?? 0),
          toolCalls: message._traceToolCalls ?? [],
        };
        this.paintAssistantLiveTrace(traceContainer, syntheticRun);
      }
    }
    // User metadata is deliberately outside the painted message body. Keeping it
    // inside `.la-message-copy` made the timestamp/actions look like bubble content
    // under several Obsidian themes, even when the outer message was transparent.
    const metaParent = message.role === "user" ? root : copy;
    const meta = metaParent.createDiv({cls: `la-message-meta la-message-meta--${message.role === "user" ? "user" : "assistant"}`});
    meta.createEl("small", {text: message.createdAt ? new Date(message.createdAt).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""});
    if (message.role === "user") {
      const actions = meta.createDiv({cls: "la-message-actions"});
      iconButton(actions, "copy", "复制消息", () => void navigator.clipboard.writeText(String(message.content ?? "")));
      iconButton(actions, "pencil", "编辑后重发", () => {
        const composer = this.containerEl.querySelector<HTMLTextAreaElement>('textarea[aria-label="知序统一输入"]');
        if (!composer) return;
        composer.value = String(message.content ?? "");
        this.assistantDraft = composer.value;
        this.assistantRegenerateMessageId = "";
        this.assistantRegenerateRunId = "";
        composer.focus();
      });
      return;
    }
    this.renderAssistantMessageActions(meta, message, previousUserMessage);
  }

  private renderProviderReasoning(
    parent: HTMLElement,
    blocks: Array<{id?: string; provider?: string; content?: string; status?: string}>,
    open: boolean,
  ): void {
    const visible = blocks.filter(block => String(block.content ?? "").trim());
    if (!visible.length) return;
    const details = parent.createEl("details", {cls: "la-provider-reasoning"});
    details.open = open;
    const summary = details.createEl("summary");
    setIcon(summary.createSpan({cls: "la-provider-reasoning__icon"}), "brain-circuit");
    summary.createSpan({text: "模型推理"});
    const providers = [...new Set(visible.map(block => String(block.provider ?? "provider")).filter(Boolean))];
    const streaming = visible.some(block => block.status === "streaming");
    summary.createEl("small", {
      text: `供应商原始返回${providers.length ? ` · ${providers.join(" / ")}` : ""}${streaming ? " · 接收中" : ""}`,
    });
    const body = details.createDiv({cls: "la-provider-reasoning__body"});
    for (const block of visible) body.createEl("pre", {text: String(block.content ?? "")});
  }

  private renderAssistantMessageActions(copy: HTMLElement, message: any, previousUserMessage: any = null): void {
    const actions = copy.createDiv({cls: "la-message-actions"});
    iconButton(actions, "copy", "复制回答", () => void navigator.clipboard.writeText(String(message.content ?? "")));
    const previousContent = String(previousUserMessage?.content ?? "");
    const regenerateRunId = persistedAssistantTrace(message)?.runId ?? String(message._traceRunId ?? "");
    const governedWrite = /(保存|写入|存入\s*Obsidian|更新(?:到|进|当前)|修改(?:当前)?笔记|应用修改|整理到)/i.test(previousContent);
    if (previousUserMessage?.id && previousContent && regenerateRunId && !governedWrite) {
      iconButton(actions, "refresh-cw", "重新生成", () => {
        const composer = this.containerEl.querySelector<HTMLTextAreaElement>('textarea[aria-label="知序统一输入"]');
        const send = this.containerEl.querySelector<HTMLButtonElement>('button[aria-label="发送"]');
        if (!composer || !send) return;
        composer.value = previousContent;
        this.assistantDraft = previousContent;
        this.assistantRegenerateMessageId = String(previousUserMessage.id);
        this.assistantRegenerateRunId = regenerateRunId;
        send.click();
      });
    }
  }

  private renderArtifactCard(parent: HTMLElement, artifact: any): void {
    const labels: Record<string, string> = {material: "资料", capture_proposal: "保存提案", research_bundle: "研究包", learning_plan: "学习计划", change_set: "Change Set", knowledge_gap: "AI 补全", quiz: "小测", organization_plan: "整理方案", write_result: "Obsidian 写入", update_suggestion: "更新建议"};
    const icons: Record<string, string> = {material: "file-text", capture_proposal: "notebook-pen", research_bundle: "search-check", learning_plan: "calendar-check", change_set: "file-diff", knowledge_gap: "sparkles", quiz: "badge-help", organization_plan: "list-tree", write_result: "file-check-2", update_suggestion: "shield-alert"};
    const card = parent.createDiv({cls: `la-artifact-card la-artifact-card--${artifact.type}`});
    const head = card.createDiv({cls: "la-artifact-card__head"});
    const icon = head.createSpan({cls: "la-artifact-card__icon"}); setIcon(icon, icons[artifact.type] ?? "package-check");
    const title = head.createDiv(); title.createEl("strong", {text: artifact.title}); title.createEl("small", {text: `${labels[artifact.type] ?? artifact.type} · v${artifact.version ?? 1}`});
    badge(head, statusKind(artifact.status), statusLabel(artifact.status));
    const payload = artifact.payload ?? artifact.summary ?? {};
    if (artifact.type === "material") {
      const progress = card.createDiv({cls: "la-artifact-metrics"}); progress.createSpan({text: `状态 ${statusLabel(artifact.status)}`}); progress.createSpan({text: `${payload.progress ?? 0}%`}); progress.createSpan({text: payload.sourceType ?? "材料"});
    } else if (artifact.type === "change_set") {
      const metrics = card.createDiv({cls: "la-artifact-metrics"});
      for (const [label, value] of [["创建", payload.createCount ?? 0], ["更新", payload.updateCount ?? 0], ["链接", payload.linkCount ?? 0], ["冲突", payload.conflictCount ?? 0]]) metrics.createSpan({text: `${label} ${value}`});
    } else if (artifact.type === "learning_plan") {
      card.createEl("p", {text: `${payload.proposal?.tasks?.length ?? 0} 项任务 · 预计 ${payload.estimatedMinutes ?? 0} 分钟 · proposed`});
    } else if (artifact.type === "research_bundle") {
      card.createEl("p", {text: `${payload.sources?.length ?? payload.bundle?.source_count ?? 0} 个来源 · 预计 ${payload.estimatedMinutes ?? 0} 分钟`});
    } else if (artifact.type === "organization_plan") {
      card.createEl("p", {text: `${payload.summary ?? "已完成目标笔记解析"}${payload.actions?.[0]?.targetPath ? ` · ${payload.actions[0].targetPath}` : ""}`});
    } else if (artifact.type === "write_result" || artifact.type === "update_suggestion") {
      card.createEl("p", {text: String(payload.summary ?? payload.path ?? "受控写入结果已生成")});
      if (payload.path || payload.targetPath) card.createEl("code", {text: String(payload.path ?? payload.targetPath), cls: "la-artifact-path"});
    } else {
      void this.markdown.render(card.createDiv({cls: "la-artifact-card__markdown"}), String(payload.summary ?? payload.answer ?? "结构化成果已生成，可继续对话修改。"));
    }
    const actions = card.createDiv({cls: "la-artifact-card__actions"});
    if (artifact.type === "write_result") {
      if (payload.path) button(actions, "打开笔记", () => void this.app.workspace.openLinkText(String(payload.path), "", false), "mod-cta");
      if (payload.actionId) button(actions, "查看变化", async () => {
        const detail = await this.client.get<any>(`/agent-actions/${encodeURIComponent(payload.actionId)}`);
        new TextPreviewModal(this.app, "Agent 修改记录", JSON.stringify(detail.action?.changes ?? [], null, 2)).open();
      });
      if (payload.undoAvailable && payload.actionId) button(actions, "撤销", async () => {
        await this.client.post(`/agent-actions/${encodeURIComponent(payload.actionId)}/undo`, {}); new Notice("已撤销本次写入"); await this.refresh();
      });
      return;
    }
    if (artifact.type === "update_suggestion") {
      button(actions, "在助手中处理", () => this.setTab("assistant"), "mod-cta");
      if (payload.targetPath) button(actions, "查看原笔记", () => void this.app.workspace.openLinkText(String(payload.targetPath), "", false));
      return;
    }
    const target: MainTab = artifact.type === "material" || artifact.type === "research_bundle" ? "sources" : artifact.type === "learning_plan" ? "plan" : artifact.type === "change_set" || artifact.type === "capture_proposal" ? "assistant" : "today";
    button(actions, target === "assistant" ? "在助手中处理" : target === "sources" ? "查看资料" : target === "plan" ? "查看计划" : "加入今日", () => this.setTab(target));
    if (!["change_set", "quiz"].includes(artifact.type)) button(actions, "继续调整", () => { new Notice("在下方继续描述修改要求，将生成同一成果的新版本。" ); });
  }

  private renderProviderDrawer(root: HTMLElement, profiles: ModelProfile[], routes: any): void {
    const header = root.createDiv({cls: "la-pane-header la-inspector-head"});
    const title = header.createDiv();
    title.createEl("h2", {text: "模型与 API 设置"});
    title.createSpan({text: "Key 只保存到 macOS Keychain"});
    iconButton(header, "x", "关闭设置", () => {
      this.assistantDrawerOpen = false;
      root.removeClass("is-open");
      root.parentElement?.removeClass("has-drawer");
    });
    const scroll = root.createDiv({cls: "la-pane-scroll la-provider-scroll"});
    const providerHead = scroll.createDiv({cls: "la-provider-section-head"});
    providerHead.createEl("h3", {text: "提供商"});
    const picker = providerHead.createEl("select", {attr: {"aria-label": "Provider 配置档案"}});
    picker.createEl("option", {value: "", text: "新建配置"});
    for (const profile of profiles) picker.createEl("option", {value: profile.id, text: `${profile.displayName}${profile.configured ? " · 已配置" : " · 缺少 Key"}`});

    const form = scroll.createDiv({cls: "la-provider-form"});
    const field = (label: string, type = "text"): HTMLInputElement => {
      const wrap = form.createDiv({cls: "la-form-field"});
      wrap.createEl("label", {text: label});
      return wrap.createEl("input", {type, attr: {"aria-label": label}});
    };
    const name = field("配置名称");
    const typeWrap = form.createDiv({cls: "la-form-field"});
    typeWrap.createEl("label", {text: "服务类型"});
    const providerType = typeWrap.createEl("select");
    for (const [value, label] of [["deepseek", "DeepSeek"], ["openai", "OpenAI"], ["openai-compatible", "OpenAI-compatible"], ["custom", "Custom"]]) providerType.createEl("option", {value, text: label});
    const base = field("Base URL");
    const keyReference = field("API Key Reference");
    const key = field("API Key", "password");
    const model = field("模型名称");
    const advanced = form.createEl("details");
    advanced.createEl("summary", {text: "高级设置"});
    const advancedField = (label: string, type = "text"): HTMLInputElement => {
      const wrap = advanced.createDiv({cls: "la-form-field"});
      wrap.createEl("label", {text: label});
      return wrap.createEl("input", {type, attr: {"aria-label": label}});
    };
    const organizationId = advancedField("组织 ID（可选）");
    const temperature = advanced.createEl("input", {type: "number", attr: {min: "0", max: "2", step: "0.1", "aria-label": "Temperature"}});
    const maxTokens = advanced.createEl("input", {type: "number", attr: {min: "1", max: "200000", "aria-label": "Max Tokens"}});
    const timeout = advanced.createEl("input", {type: "number", attr: {min: "1", max: "300", "aria-label": "超时秒数"}});
    const capabilities = advanced.createDiv({cls: "la-provider-capabilities"});
    const capability = (label: string): HTMLInputElement => {
      const wrap = capabilities.createEl("label", {cls: "la-provider-capability"});
      const input = wrap.createEl("input", {type: "checkbox"});
      wrap.createSpan({text: label});
      return input;
    };
    const streaming = capability("支持流式响应");
    const jsonSchema = capability("支持 JSON Schema");
    const toolCalling = capability("原生 Tool Calling（支持工具调用）");
    const streamedToolCalls = capability("流式 Tool Call 参数");
    const reasoningContent = capability("支持深度推理协议");
    const parallelToolCalls = capability("并行 Tool Calls");
    const reasoningWrap = advanced.createDiv({cls: "la-form-field"});
    reasoningWrap.createEl("label", {text: "Reasoning Effort"});
    const reasoningEffort = reasoningWrap.createEl("select", {attr: {"aria-label": "Reasoning Effort"}});
    for (const value of ["", "none", "minimal", "low", "medium", "high", "xhigh"]) {
      reasoningEffort.createEl("option", {value, text: value || "Provider 默认"});
    }
    const headers = advanced.createEl("textarea", {attr: {placeholder: "自定义 Headers JSON（禁止 Authorization / Host / Content-Length）", "aria-label": "自定义 Headers"}});

    const probeCard = form.createDiv({cls: "la-capability-probe"});
    const probeHead = probeCard.createDiv({cls: "la-capability-probe__head"});
    probeHead.createEl("strong", {text: "模型能力探测"});
    const probeButton = button(probeHead, "重新探测", async () => {
      if (!picker.value) throw new Error("请先保存并选择配置");
      probeButton.disabled = true;
      probeButton.setText("探测中…");
      try {
        const result = await this.client.probeModelCapabilities(picker.value);
        renderProbe(result);
      } finally {
        probeButton.disabled = false;
        probeButton.setText("重新探测");
      }
    });
    const probeBody = probeCard.createDiv({cls: "la-capability-probe__body"});
    const capabilityLabels: Record<string, string> = {
      basicStreaming: "流式响应", nativeToolCalling: "原生工具调用",
      streamedToolCalls: "流式工具参数", preservesToolCallId: "Tool Call ID",
      parallelToolCalls: "并行工具", reasoningContent: "深度推理协议",
      usageReporting: "Usage", contextWindow: "上下文窗口",
      maxOutputTokens: "最大输出",
    };
    const renderProbe = (payload: any): void => {
      probeBody.empty();
      const probe = payload?.probe ?? payload;
      const values = probe?.capabilities ?? {};
      if (!Object.keys(values).length) {
        probeBody.createEl("small", {text: "尚未探测。运行时会采用保守参数，不会把偏好开关当作真实能力。"});
        return;
      }
      for (const [keyName, label] of Object.entries(capabilityLabels)) {
        const state = values[keyName];
        if (!state) continue;
        const row = probeBody.createDiv({cls: "la-capability-probe__row"});
        row.createSpan({text: label});
        const status = String(state.status ?? "inconclusive");
        row.createEl("code", {text: state.value == null ? status : `${status} · ${state.value}`, cls: `is-${status}`});
      }
      probeBody.createEl("small", {text: probe.checkedAt ? `最近探测：${new Date(probe.checkedAt).toLocaleString()}` : "结果来自安全能力缓存"});
    };

    const load = (profile?: ModelProfile): void => {
      providerType.value = profile?.providerType ?? "openai-compatible";
      name.value = profile?.displayName ?? "";
      base.value = profile?.baseUrl ?? ({deepseek: "https://api.deepseek.com/v1", openai: "https://api.openai.com/v1"} as Record<string, string>)[providerType.value] ?? "";
      key.value = "";
      keyReference.value = profile?.apiKeyReference ?? "";
      key.placeholder = profile?.keyHint ?? "仅保存到 macOS Keychain";
      model.value = profile?.defaultModel ?? "";
      organizationId.value = profile?.settings?.organizationId ?? "";
      temperature.value = String(profile?.settings?.temperature ?? 0.3);
      maxTokens.value = String(profile?.settings?.maxTokens ?? 2000);
      timeout.value = String(profile?.settings?.timeout ?? 30);
      streaming.checked = profile?.settings?.streaming ?? true;
      jsonSchema.checked = profile?.settings?.jsonSchema ?? true;
      toolCalling.checked = profile?.settings?.nativeToolCalling ?? profile?.settings?.toolCalling ?? true;
      streamedToolCalls.checked = profile?.settings?.streamedToolCalls ?? toolCalling.checked;
      reasoningContent.checked = profile?.settings?.reasoningContent ?? providerType.value === "deepseek";
      parallelToolCalls.checked = profile?.settings?.parallelToolCalls ?? false;
      reasoningEffort.value = profile?.settings?.reasoningEffort ?? "";
      headers.value = JSON.stringify(profile?.settings?.customHeaders ?? {}, null, 2);
    };
    load();
    picker.onchange = () => {
      load(profiles.find(item => item.id === picker.value));
      probeBody.empty();
      if (!picker.value) return renderProbe(null);
      void this.client.modelCapabilities(picker.value)
        .then(renderProbe)
        .catch(() => renderProbe(null));
    };
    renderProbe(null);

    const actions = scroll.createDiv({cls: "la-provider-actions"});
    button(actions, "保存", async () => {
      let customHeaders = {};
      try {
        customHeaders = headers.value.trim() ? JSON.parse(headers.value) : {};
      } catch {
        throw new Error("自定义 Headers 必须是 JSON 对象");
      }
      const payload = {
        displayName: name.value,
        providerType: providerType.value,
        baseUrl: base.value,
        apiKeyReference: keyReference.value,
        apiKey: key.value,
        defaultModel: model.value,
        availableModels: model.value ? [model.value] : [],
        enabled: true,
        settings: {
          organizationId: organizationId.value,
          temperature: Number(temperature.value),
          maxTokens: Number(maxTokens.value),
          timeout: Number(timeout.value),
          streaming: streaming.checked,
          jsonSchema: jsonSchema.checked,
          toolCalling: toolCalling.checked,
          nativeToolCalling: toolCalling.checked,
          streamedToolCalls: streamedToolCalls.checked,
          reasoningContent: reasoningContent.checked,
          thinkingControl: "provider-default",
          parallelToolCalls: parallelToolCalls.checked,
          reasoningEffort: reasoningEffort.value,
          customHeaders,
        },
      };
      await this.settingsService.saveModelProfile(picker.value, payload);
      new Notice("模型配置已安全保存；Key 未进入插件设置");
      await this.refresh();
    }, "mod-cta");
    button(actions, "测试连接", async () => {
      if (!picker.value) throw new Error("请先保存配置");
      const result = await this.settingsService.testModelProfile(picker.value);
      new Notice(result.message);
    });
    button(actions, "获取模型", async () => {
      if (!picker.value) throw new Error("请先保存配置");
      const result = await this.settingsService.listProfileModels(picker.value);
      new Notice(`发现 ${result.models.length} 个模型`);
    });
    button(actions, "删除", () => {
      if (!picker.value) return;
      new ExplicitConfirmModal(this.app, "删除模型配置", "只删除模型 Profile，不删除现有 Keychain 密钥，也不影响知识笔记。", async () => {
        await this.settingsService.deleteModelProfile(picker.value);
        await this.refresh();
      }).open();
    });
    scroll.createEl("h3", {text: "任务模型路由"});
    for (const [task, label] of [["agent_runtime", "Pi Agent Runtime"], ["curriculum_planner", "课程候选"], ["daily_knowledge_generator", "每日新知识"], ["claim_extractor", "Claim 提取"], ["claim_verifier", "Claim 验证"], ["lesson_generator", "微型课程"], ["research_synthesis", "研究综合"], ["tutor", "学习辅导"], ["quiz", "短测"], ["evaluation", "复述评估"], ["pdf_prepare", "PDF / 教材 Prepare"], ["assistant_chat", "助手对话"]]) {
      const row = scroll.createDiv({cls: "la-routing-row"});
      row.createEl("span", {text: label});
      const select = row.createEl("select");
      select.createEl("option", {value: "", text: "未配置"});
      for (const profile of profiles.filter(item => item.enabled)) select.createEl("option", {value: profile.id, text: profile.displayName});
      select.value = routes[task]?.profileId ?? "";
      select.onchange = () => void this.settingsService.updateModelRoute(task, select.value);
    }
  }

  private onKey(event: KeyboardEvent): void {
    if ((event.metaKey || event.ctrlKey) && /^[1-5]$/.test(event.key)) {
      event.preventDefault();
      this.setTab(MODULES[Number(event.key) - 1].id);
      return;
    }
    if (event.key === "Escape") {
      if (this.studyAssistantOpen) {
        this.studyAssistantOpen = false;
        this.rerenderToday();
      } else if (this.studyAssistOpen) {
        this.studyAssistOpen = false;
        this.rerenderToday();
      } else if (this.assistantDrawerOpen) {
        this.assistantDrawerOpen = false;
        this.containerEl.querySelector(".la-provider-drawer")?.removeClass("is-open");
        this.containerEl.querySelector(".la-assistant-shell-v3")?.removeClass("has-drawer");
      } else if (this.mobileDetail) {
        this.mobileDetail = false;
        this.rerenderToday();
      }
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
      event.preventDefault();
      (this.containerEl.querySelector(".la-search input") as HTMLInputElement | null)?.focus();
      return;
    }
    if (this.tab !== "today" || !this.dashboard) return;
    const items = selectRecommendations(this.dashboard.recommendations, this.query, this.filter, this.sort);
    const index = Math.max(0, items.findIndex(item => item.id === this.selectedId));
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      this.selectedId = items[Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.id ?? "";
      this.rerenderToday();
    } else if (event.key === "Enter" && items[index]) {
      void this.startStudy(items[index]);
    } else if (event.key === " " && items[index]) {
      event.preventDefault();
      new Notice(`${items[index].title} · ${items[index].estimatedMinutes} 分钟`);
    }
  }
}
