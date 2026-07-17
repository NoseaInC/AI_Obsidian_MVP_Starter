export const ASSISTANT_STREAM_SCHEMA_VERSION = 2;

export type AssistantStreamEventType =
  | "run.started"
  | "context.resolved"
  | "plan.created"
  | "plan.decision"
  | "tool.requested"
  | "tool.started"
  | "tool.completed"
  | "observation.recorded"
  | "step.updated"
  | "message.started"
  | "message.delta"
  | "message.completed"
  | "proposal.created"
  | "proposal.rejected"
  | "change.applied"
  | "write.proposal-required"
  | "approval.required"
  | "run.awaiting_approval"
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
    | "awaiting_approval"
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
  runtimeMode?: string;
  allowedTools: string[];
  plannerRound: number;
  toolCalls: Array<{
    id: string;
    tool: string;
    status: string;
    summary?: string;
    purpose?: string;
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

    const normalized =
      status === "skipped"
        ? "completed"
        : status === "completed"
          ? "completed"
          : status === "running"
            ? "running"
            : status === "pending"
              ? "pending"
              : "failed";

    const call = {
      id,
      tool,
      status: normalized,
      summary: event.summary,
      purpose: event.purpose,
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
  } else if (
    event.type === "write.proposal-required" ||
    event.type === "approval.required"
  ) {
    next.proposalRequired = true;
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
  } else if (event.type === "run.awaiting_approval") {
    next.status = "awaiting_approval";
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
      read_pdf_pages: "读取 PDF 页面",
      search_pdf: "搜索 PDF",
      get_conversation_focus: "读取当前会话焦点",
      get_recent_conversation_messages: "读取最近对话",
      create_change_set: "生成 Obsidian 修改提案",
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

export function cancelledAssistantRun(
  state: AssistantLiveRun,
): AssistantLiveRun {
  return state.status === "running"
    ? {...state, status: "cancelled"}
    : state;
}
