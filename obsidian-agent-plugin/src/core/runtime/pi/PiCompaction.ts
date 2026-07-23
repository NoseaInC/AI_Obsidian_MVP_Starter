import {
  estimateContextTokens,
  estimateTokens,
  type AgentMessage,
} from "@earendil-works/pi-agent-core";

/**
 * Structured, recoverable snapshot of a run's working context.
 *
 * Everything the next turn needs to continue coherently is captured here so a
 * compacted session can be restored (after a restart) without faking a user
 * message. The fields are intentionally explicit and serialisable.
 */
export interface PiCompactionState {
  goal?: string | null;
  explicitConstraints?: string[];
  conversationFocus?: Record<string, unknown> | null;
  activeNote?: {path: string; name?: string} | null;
  activeSelectionReference?: string | null;
  attachments?: Array<Record<string, unknown>>;
  sourcesRead?: Array<Record<string, unknown>>;
  completedActions?: Array<Record<string, unknown>>;
  pendingActions?: Array<Record<string, unknown>>;
  failedTools?: Array<Record<string, unknown>>;
  activeWorkspace?: {workspaceId: string; workspaceIds?: string[]; projectPaths?: string[]} | null;
  taskBranch?: {branchId: string; parentRunId?: string | null; forkedFromSequence?: number | null; forkedFromEntryId?: string | null} | null;
  taskAuthorization?: unknown;
  currentLeafId?: string | null;
  branchId?: string | null;
  actionIds?: string[];
  undoState?: unknown;
}

/** A persisted compaction checkpoint. */
export interface PiCompactionEntry {
  cutEntryId: string;
  keptFromEntryId: string;
  summaryVersion: number;
  tokensBefore: number;
  tokensAfter: number;
  structuredState: PiCompactionState;
  createdAt: string;
}

export interface PiCompactionResult {
  messages: AgentMessage[];
  compacted: boolean;
  tokensBefore: number;
  keptTokens: number;
  summary: string;
  entry?: PiCompactionEntry;
  reason?: string;
}

export interface PiCompactionOptions {
  contextWindow?: number;
  reserve?: number;
  keepRecent?: number;
  force?: boolean;
}

/** Everything the compaction needs to rebuild a structured state. */
export interface PiCompactionSources {
  messages: AgentMessage[];
  goal?: string | null;
  explicitConstraints?: string[];
  conversationFocus?: Record<string, unknown> | null;
  attachments?: Array<Record<string, unknown>>;
  sourcesRead?: Array<Record<string, unknown>>;
  completedActions?: Array<Record<string, unknown>>;
  pendingActions?: Array<Record<string, unknown>>;
  failedTools?: Array<Record<string, unknown>>;
  actionIds?: string[];
  activeNote?: {path: string; name?: string} | null;
  activeSelectionReference?: string | null;
  activeWorkspace?: {workspaceId: string; workspaceIds?: string[]; projectPaths?: string[]} | null;
  taskAuthorization?: unknown;
  taskBranch?: {branchId: string; parentRunId?: string | null; forkedFromSequence?: number | null; forkedFromEntryId?: string | null} | null;
  currentLeafId?: string | null;
  branchId?: string | null;
  undoState?: unknown;
}

const DEFAULT_CONTEXT_WINDOW = 128_000;
const DEFAULT_RESERVE = 24_000;
const DEFAULT_KEEP_RECENT = 28_000;

function asContent(message: any): any[] {
  if (!message || !Array.isArray(message.content)) return [];
  return message.content;
}

function textOf(message: any): string {
  if (!message) return "";
  if (typeof message.content === "string") return message.content;
  return asContent(message)
    .filter(item => item && item.type === "text")
    .map(item => String(item.text ?? ""))
    .join("\n");
}

function safeLine(value: unknown, max = 400): string {
  const text = String(value ?? "");
  const trimmed = text.replace(/\s+/g, " ").trim();
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

function isUser(message: AgentMessage): boolean {
  return (message as any)?.role === "user";
}

function isAssistant(message: AgentMessage): boolean {
  return (message as any)?.role === "assistant";
}

function toolCallIds(message: AgentMessage): string[] {
  if ((message as any)?.role !== "assistant") return [];
  return asContent(message)
    .filter(item => item && item.type === "toolCall" && item.id)
    .map(item => String(item.id));
}

function toolResultId(message: AgentMessage): string | null {
  if ((message as any)?.role !== "toolResult") return null;
  const direct = (message as any).toolCallId;
  if (direct) return String(direct);
  const first = asContent(message)[0];
  if (first && first.toolCallId) return String(first.toolCallId);
  return null;
}

/**
 * True when a tool call has been issued but its result has not arrived yet.
 * Splitting the transcript while this is true would orphan a tool pair.
 */
export function hasUnresolvedToolCall(messages: AgentMessage[]): boolean {
  const calls = new Set<string>();
  const resolved = new Set<string>();
  for (const message of messages) {
    for (const id of toolCallIds(message)) calls.add(id);
    const resultId = toolResultId(message);
    if (resultId) resolved.add(resultId);
  }
  for (const id of calls) {
    if (!resolved.has(id)) return true;
  }
  return false;
}

/** Typed pending questions are decision boundaries just like permissions. */
export function hasPendingQuestion(messages: AgentMessage[]): boolean {
  return messages.some(message => {
    const raw = message as unknown as Record<string, unknown>;
    if (raw.role !== "custom") return false;
    if (!["question", "question_required", "pendingQuestion"].includes(String(raw.customType ?? ""))) return false;
    const details = raw.details && typeof raw.details === "object"
      ? raw.details as Record<string, unknown>
      : {};
    return !["answered", "resolved", "cancelled"].includes(String(details.status ?? "pending"));
  });
}

/**
 * Find the oldest cut index so that dropping messages[0:cut] leaves a complete
 * prefix (never mid tool-pair) while keeping at least `keepRecentTokens` of the
 * most recent context. Returns -1 when no safe cut exists within budget.
 */
export function findSafeCut(messages: AgentMessage[], keepRecentTokens: number): number {
  let keptTokens = 0;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    keptTokens += estimateTokens(messages[i]);
    if (keptTokens < keepRecentTokens) continue;
    const boundary = messages[i];
    if (!isUser(boundary) && !isAssistant(boundary)) continue;
    if (hasUnresolvedToolCall(messages.slice(0, i + 1))) continue;
    return i;
  }
  return -1;
}

export function buildCompactionState(sources: PiCompactionSources): PiCompactionState {
  return {
    goal: sources.goal ?? null,
    explicitConstraints: sources.explicitConstraints ?? [],
    conversationFocus: sources.conversationFocus ?? null,
    activeNote: sources.activeNote ?? null,
    activeSelectionReference: sources.activeSelectionReference ?? null,
    attachments: sources.attachments ?? [],
    sourcesRead: sources.sourcesRead ?? [],
    completedActions: sources.completedActions ?? [],
    pendingActions: sources.pendingActions ?? [],
    failedTools: sources.failedTools ?? [],
    activeWorkspace: sources.activeWorkspace ?? null,
    taskBranch: sources.taskBranch ?? null,
    taskAuthorization: sources.taskAuthorization ?? null,
    currentLeafId: sources.currentLeafId ?? null,
    branchId: sources.branchId ?? null,
    actionIds: sources.actionIds ?? [],
    undoState: sources.undoState ?? null,
  };
}

function summarize(old: AgentMessage[], state: PiCompactionState): string {
  const lines: string[] = [];
  if (state.goal) lines.push(`目标：${safeLine(state.goal, 240)}`);
  if (state.conversationFocus) lines.push(`当前焦点：${safeLine(JSON.stringify(state.conversationFocus), 400)}`);
  if (state.explicitConstraints && state.explicitConstraints.length) {
    lines.push(`硬性约束：${state.explicitConstraints.map(c => safeLine(c, 120)).join("；")}`);
  }
  if (state.activeNote) lines.push(`当前笔记：${state.activeNote.path}`);
  if (state.activeSelectionReference) lines.push(`选区引用：${safeLine(state.activeSelectionReference, 200)}`);
  if (state.attachments && state.attachments.length) {
    lines.push(`附件引用：${state.attachments.map(a => safeLine(JSON.stringify(a), 160)).join("，")}`);
  }
  if (state.sourcesRead && state.sourcesRead.length) {
    lines.push(`已读来源：${state.sourcesRead.map(s => safeLine(JSON.stringify(s), 160)).join("，")}`);
  }
  const completed = state.completedActions ?? [];
  if (completed.length) lines.push(`已完成动作：${completed.map(a => safeLine(JSON.stringify(a), 160)).join("，")}`);
  const pending = state.pendingActions ?? [];
  if (pending.length) lines.push(`待办动作：${pending.map(a => safeLine(JSON.stringify(a), 160)).join("，")}`);
  if (state.failedTools && state.failedTools.length) {
    lines.push(`失败工具：${state.failedTools.map(t => safeLine(JSON.stringify(t), 160)).join("，")}`);
  }
  if (state.activeWorkspace) {
    const paths = (state.activeWorkspace.projectPaths ?? []).join(", ");
    lines.push(`活动工作区：${state.activeWorkspace.workspaceId || "(本 vault)"}${paths ? ` @ ${paths}` : ""}`);
  }
  if (state.taskBranch) {
    lines.push(`任务分支：${state.taskBranch.branchId}（fork@${state.taskBranch.forkedFromSequence ?? "?"})`);
  }
  const recalled = old
    .filter(isUser)
    .map(textOf)
    .map(line => safeLine(line, 280))
    .filter(Boolean);
  if (recalled.length) {
    lines.push("已压缩的用户输入：");
    for (const line of recalled) lines.push(`- ${line}`);
  }
  return lines.join("\n");
}

/**
 * Compact an agent transcript into a recoverable checkpoint.
 *
 * Guarantees (matching the hardening plan T08):
 *  - never splits a tool call / tool result pair;
 *  - never compacts while a tool call is unresolved;
 *  - the summary is injected as a runtime checkpoint (assistant role with a
 *    <zhixu_runtime_checkpoint> marker), never as a fake user message;
 *  - the structured state is returned in `entry` so it can be persisted and
 *    restored after a restart.
 */
export function compactAgentMessages(
  messages: AgentMessage[],
  options: PiCompactionOptions = {},
  sources?: PiCompactionSources,
): PiCompactionResult {
  const contextWindow = options.contextWindow ?? DEFAULT_CONTEXT_WINDOW;
  const reserve = options.reserve ?? DEFAULT_RESERVE;
  const keepRecent = options.keepRecent ?? DEFAULT_KEEP_RECENT;
  const force = options.force ?? false;

  const tokensBefore = estimateContextTokens(messages).tokens;

  if (!force && tokensBefore <= contextWindow - reserve) {
    return {messages, compacted: false, tokensBefore, keptTokens: tokensBefore, summary: ""};
  }
  if (hasUnresolvedToolCall(messages)) {
    return {
      messages,
      compacted: false,
      tokensBefore,
      keptTokens: tokensBefore,
      summary: "",
      reason: "unresolved_tool_call",
    };
  }
  if (hasPendingQuestion(messages)) {
    return {
      messages,
      compacted: false,
      tokensBefore,
      keptTokens: tokensBefore,
      summary: "",
      reason: "pending_question",
    };
  }
  const cut = findSafeCut(messages, keepRecent);
  if (cut <= 0) {
    return {
      messages,
      compacted: false,
      tokensBefore,
      keptTokens: tokensBefore,
      summary: "",
      reason: "no_safe_cut",
    };
  }

  const old = messages.slice(0, cut);
  const state = buildCompactionState(sources ?? {messages});
  const summary = summarize(old, state);
  // A runtime checkpoint is emitted as an assistant message carrying an
  // explicit marker. It is never disguised as a user turn.
  const summaryMessage = {
    role: "assistant",
    content: [
      {
        type: "text",
        text: `<zhixu_runtime_checkpoint version="1">\n${summary}\n</zhixu_runtime_checkpoint>`,
      },
    ],
    timestamp: Date.now(),
  } as AgentMessage;

  const compactedMessages = [summaryMessage, ...messages.slice(cut)];
  const tokensAfter = estimateContextTokens(compactedMessages).tokens;
  const entryMetadata = (message: AgentMessage | undefined): Record<string, unknown> => {
    const raw = message as unknown as Record<string, unknown> | undefined;
    return raw?.metadata && typeof raw.metadata === "object"
      ? raw.metadata as Record<string, unknown>
      : {};
  };
  const cutMetadata = entryMetadata(old[old.length - 1]);
  const keptMetadata = entryMetadata(messages[cut]);
  const fallbackLeaf = String(state.currentLeafId ?? "");
  const entry: PiCompactionEntry = {
    cutEntryId: String(cutMetadata.entryEndId ?? cutMetadata.entryId ?? fallbackLeaf),
    keptFromEntryId: String(keptMetadata.entryStartId ?? keptMetadata.entryId ?? fallbackLeaf),
    summaryVersion: 1,
    tokensBefore,
    tokensAfter,
    structuredState: state,
    createdAt: new Date().toISOString(),
  };
  return {
    messages: compactedMessages,
    compacted: true,
    tokensBefore,
    keptTokens: tokensAfter,
    summary,
    entry,
  };
}
