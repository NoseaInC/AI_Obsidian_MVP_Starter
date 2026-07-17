import type {InlineAgentConfirmation} from "./assistant-inline-confirmation";
import type {AgentChunk} from "./core/runtime/types";
import {toolTraceFromAgentChunk} from "./features/chat/ToolTraceRenderer";

export const ASSISTANT_STREAM_SCHEMA_VERSION = 3;

export type AssistantStreamEventType =
  | "run.started"
  | "context.resolved"
  | "plan.created"
  | "plan.decision"
  | "tool.requested"
  | "tool.started"
  | "tool.completed"
  | "write.diff"
  | "observation.recorded"
  | "step.updated"
  | "message.started"
  | "message.delta"
  | "message.completed"
  | "proposal.created"
  | "proposal.rejected"
  | "change.applied"
  | "inline.confirmation.required"
  | "inline.confirmation.resolved"
  | "run.waiting_confirmation"
  | "run.completed"
  | "run.cancelled"
  | "run.failed";

export interface AssistantStreamEvent {
  schemaVersion: number;
  seq: number;
  type: AssistantStreamEventType;
  runId: string;
  conversationId: string;

  messageId?: string;
  delta?: string;
  model?: string;
  profileId?: string;

  focus?: Record<string, unknown>;
  understanding?: Record<string, unknown>;
  sources?: Array<Record<string, unknown>>;

  allowedTools?: string[];
  mode?: string;
  round?: number;
  action?: string;
  purpose?: string;

  tool?: string;
  callId?: string;
  status?: string;
  summary?: string;
  arguments?: Record<string, unknown>;
  input?: Record<string, unknown>;
  result?: Record<string, unknown>;
  confirmation?: InlineAgentConfirmation;

  proposalId?: string;
  approvalId?: string;
  title?: string;
  preview?: string;
  writes?: Array<Record<string, unknown>>;
  requiresConfirmation?: boolean;
  riskLevel?: string;
  allowedActions?: string[];

  brainRunId?: string;
  step?: {
    id: string;
    label: string;
    status: "pending" | "running" | "completed" | "failed";
  };
  message?: Record<string, any>;
  reason?: string;
  code?: string;
  partial?: boolean;
}

export interface AssistantProposal {
  id: string;
  title: string;
  preview: string;
  writes: Array<Record<string, unknown>>;
  requiresConfirmation: boolean;
  riskLevel: string;
}

export interface AssistantLiveRun {
  runId: string;
  conversationId: string;
  messageId: string;
  model: string;
  status:
    | "idle"
    | "running"
    | "waiting_confirmation"
    | "completed"
    | "failed"
    | "cancelled";
  content: string;
  completedMessage?: Record<string, any>;
  lastSequence: number;
  started: boolean;
  context?: {
    focus: Record<string, unknown>;
    understanding: Record<string, unknown>;
    sources: Array<Record<string, unknown>>;
  };
  steps: Array<{
    id: string;
    label: string;
    status: string;
  }>;
  proposalRequired: boolean;
  proposal?: AssistantProposal;
  confirmation?: InlineAgentConfirmation;
  runtimeMode?: string;
  allowedTools: string[];
  plannerRound: number;
  toolCalls: Array<{
    id: string;
    tool: string;
    status: string;
    summary?: string;
    purpose?: string;
    input?: Record<string, unknown>;
    result?: Record<string, unknown>;
    diffData?: {proposalId: string; writes: Array<Record<string, unknown>>};
  }>;
  error?: {
    code: string;
    partial: boolean;
  };
}

export function initialAssistantLiveRun(): AssistantLiveRun {
  return {
    runId: "",
    conversationId: "",
    messageId: "",
    model: "",
    status: "idle",
    content: "",
    lastSequence: 0,
    started: false,
    steps: [],
    proposalRequired: false,
    allowedTools: [],
    plannerRound: 0,
    toolCalls: [],
  };
}

export function reduceAssistantStream(
  state: AssistantLiveRun,
  event: AssistantStreamEvent,
): AssistantLiveRun {
  if (event.schemaVersion !== ASSISTANT_STREAM_SCHEMA_VERSION) {
    return state;
  }
  if (!Number.isSafeInteger(event.seq) || event.seq <= state.lastSequence) {
    return state;
  }
  if (state.runId && event.runId !== state.runId) {
    return state;
  }

  const next: AssistantLiveRun = {
    ...state,
    runId: event.runId,
    conversationId: event.conversationId,
    lastSequence: event.seq,
    steps: [...state.steps],
    allowedTools: [...state.allowedTools],
    toolCalls: [...state.toolCalls],
    proposal: state.proposal
      ? {...state.proposal, writes: [...state.proposal.writes]}
      : undefined,
    confirmation: state.confirmation
      ? {...state.confirmation, writes: [...state.confirmation.writes]}
      : undefined,
  };

  if (event.type === "run.started") {
    next.status = "running";
    next.started = true;
    next.model = String(event.model ?? "");
  } else if (event.type === "context.resolved") {
    next.context = {
      focus: event.focus ?? {},
      understanding: event.understanding ?? {},
      sources: event.sources ?? [],
    };
  } else if (event.type === "plan.created") {
    next.runtimeMode = String(event.mode ?? "");
    next.allowedTools = Array.isArray(event.allowedTools)
      ? event.allowedTools.map(String)
      : [];
  } else if (event.type === "plan.decision") {
    next.plannerRound = Math.max(
      next.plannerRound,
      Number(event.round ?? 0),
    );
  } else if (
    event.type === "tool.requested" ||
    event.type === "tool.started" ||
    event.type === "tool.completed"
  ) {
    const id = String(event.callId ?? `tool-${next.toolCalls.length}`);
    const tool = String(event.tool ?? "tool");
    const status =
      event.type === "tool.requested"
        ? "pending"
        : event.type === "tool.started"
          ? "running"
          : String(event.status ?? "completed");

    const normalized = (["pending", "running", "completed", "failed", "blocked", "cancelled"].includes(status)
      ? status
      : status === "skipped" ? "completed" : "failed") as "pending" | "running" | "completed" | "failed" | "blocked" | "cancelled";

    const call = {
      id,
      tool,
      status: normalized,
      summary: event.summary,
      purpose: event.purpose,
      input: event.input ?? event.arguments,
      result: event.result,
    };
    const callIndex = next.toolCalls.findIndex(item => item.id === id);
    if (callIndex >= 0) {
      next.toolCalls[callIndex] = call;
    } else {
      next.toolCalls.push(call);
    }

    const step = {
      id: `tool:${id}`,
      label: toolLabel(tool),
      status: normalized,
    };
    const stepIndex = next.steps.findIndex(item => item.id === step.id);
    if (stepIndex >= 0) {
      next.steps[stepIndex] = step;
    } else {
      next.steps.push(step);
    }
  } else if (event.type === "step.updated" && event.step) {
    const index = next.steps.findIndex(item => item.id === event.step!.id);
    if (index >= 0) {
      next.steps[index] = {...event.step};
    } else {
      next.steps.push({...event.step});
    }
  } else if (event.type === "message.started") {
    next.messageId = String(event.messageId ?? "");
  } else if (event.type === "message.delta") {
    next.content += String(event.delta ?? "");
  } else if (event.type === "write.diff") {
    next.proposalRequired = true;
    next.proposal = {
      id: String(event.proposalId ?? ""),
      title: String(event.title ?? "Obsidian 修改提案"),
      preview: String(event.preview ?? ""),
      writes: Array.isArray(event.writes) ? event.writes : [],
      requiresConfirmation: true,
      riskLevel: String(event.riskLevel ?? "medium"),
    };
    const call = [...next.toolCalls].reverse().find(item => item.tool === "propose_vault_change");
    if (call) call.diffData = {proposalId: next.proposal.id, writes: [...next.proposal.writes]};
  } else if (event.type === "proposal.created") {
    next.proposalRequired = true;
    next.proposal = {
      id: String(event.proposalId ?? ""),
      title: String(event.title ?? "Obsidian 修改提案"),
      preview: String(event.preview ?? ""),
      writes: Array.isArray(event.writes) ? event.writes : [],
      requiresConfirmation: event.requiresConfirmation !== false,
      riskLevel: String(event.riskLevel ?? "low"),
    };
  } else if (event.type === "proposal.rejected") {
    next.proposalRequired = false;
    next.status = "cancelled";
  } else if (event.type === "change.applied") {
    next.proposalRequired = false;
  } else if (event.type === "inline.confirmation.required") {
    next.proposalRequired = true;
    next.confirmation = event.confirmation;
  } else if (event.type === "message.completed") {
    next.messageId = String(
      (event.message as {id?: unknown} | undefined)?.id ?? next.messageId,
    );
    next.completedMessage = event.message
      ? {...event.message}
      : next.completedMessage;
    if (!next.content && typeof event.message?.content === "string") {
      next.content = event.message.content;
    }
  } else if (event.type === "inline.confirmation.resolved") {
    next.confirmation = undefined;
    next.proposalRequired = false;
  } else if (event.type === "run.waiting_confirmation") {
    next.status = "waiting_confirmation";
    next.proposalRequired = true;
  } else if (event.type === "run.completed") {
    next.status = "completed";
  } else if (event.type === "run.cancelled") {
    next.status = "cancelled";
  } else if (event.type === "run.failed") {
    next.status = "failed";
    next.error = {
      code: String(event.code ?? "assistant_stream_failed"),
      partial: Boolean(event.partial),
    };
  }

  return next;
}

export function toolLabel(name: string): string {
  return (
    {
      get_vault_overview: "读取知识库概览",
      search_vault: "搜索知识库",
      read_note_metadata: "读取笔记属性",
      read_note_excerpt: "读取笔记正文",
      get_related_notes: "查找相关笔记与反向链接",
      get_backlinks: "读取反向链接",
      get_learning_state: "读取学习状态",
      get_due_reviews: "读取到期复习",
      get_recent_materials: "查找最近资料",
      search_academic_sources: "检索可信来源",
      fetch_user_provided_url: "读取公开网页",
      get_attachment_metadata: "读取附件信息",
      get_current_note: "读取当前笔记",
      get_current_selection: "读取当前选区",
      read_vault_note: "读取笔记正文",
      find_related_notes: "查找相关笔记与反向链接",
      read_pdf_pages: "读取 PDF 页面",
      search_pdf: "搜索 PDF",
      get_conversation_focus: "读取当前会话焦点",
      get_recent_conversation_messages: "读取最近对话",
      create_change_set: "生成 Obsidian 修改提案",
      propose_vault_change: "生成 Obsidian 修改提案",
      commit_vault_change: "校验并提交 Obsidian 修改",
    } as Record<string, string>
  )[name] ?? "执行受控工具";
}

export function parseNdjsonBuffer(buffer: string): {
  events: AssistantStreamEvent[];
  remainder: string;
} {
  const lines = buffer.split("\n");
  const remainder = lines.pop() ?? "";
  const events: AssistantStreamEvent[] = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.length > 1_000_000) {
      throw new Error("Assistant stream event is too large");
    }
    const value = JSON.parse(line);
    if (
      !value ||
      typeof value !== "object" ||
      typeof value.type !== "string"
    ) {
      throw new Error("Assistant stream event is invalid");
    }
    events.push(value as AssistantStreamEvent);
  }

  return {events, remainder};
}

export function agentChunkToAssistantEvent(chunk: AgentChunk): AssistantStreamEvent {
  const base = {
    schemaVersion: ASSISTANT_STREAM_SCHEMA_VERSION,
    seq: chunk.sequence,
    runId: chunk.runId,
    conversationId: chunk.conversationId,
  };
  if (chunk.type === "text") return {...base, type: "message.delta", delta: chunk.content, messageId: chunk.messageId};
  const toolTrace = toolTraceFromAgentChunk(chunk);
  if (chunk.type === "tool_use" && toolTrace) return {...base, type: "tool.started", callId: toolTrace.id, tool: toolTrace.name, input: toolTrace.input, status: toolTrace.status};
  if (chunk.type === "tool_result" && toolTrace) return {...base, type: "tool.completed", callId: toolTrace.id, tool: toolTrace.name, result: toolTrace.result, summary: toolTrace.summary, status: toolTrace.status};
  if (chunk.type === "write_diff") return {...base, type: "write.diff", proposalId: chunk.proposalId, title: chunk.title, writes: chunk.writes};
  if (chunk.type === "confirmation_required") return {...base, type: "inline.confirmation.required", confirmation: chunk.confirmation};
  if (chunk.type === "error") return {...base, type: "run.failed", code: chunk.code, partial: chunk.partial, reason: chunk.content};
  if (chunk.type === "done") return {...base, type: chunk.status === "cancelled" ? "run.cancelled" : "run.completed", message: chunk.message};
  if (chunk.type === "notice" && chunk.data) return chunk.data as unknown as AssistantStreamEvent;
  return {...base, type: "context.resolved"};
}

export function cancelledAssistantRun(
  state: AssistantLiveRun,
): AssistantLiveRun {
  return state.status === "running"
    ? {...state, status: "cancelled"}
    : state;
}
