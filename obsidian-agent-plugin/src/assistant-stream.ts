export const ASSISTANT_STREAM_SCHEMA_VERSION = 1;

export type AssistantStreamEventType =
  | "run.started"
  | "context.resolved"
  | "plan.created"
  | "tool.requested"
  | "tool.started"
  | "tool.completed"
  | "step.updated"
  | "message.started"
  | "message.delta"
  | "message.completed"
  | "write.proposal-required"
  | "approval.required"
  | "run.completed"
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
  focus?: Record<string, any>;
  understanding?: Record<string, any>;
  sources?: Array<Record<string, any>>;
  allowedTools?: string[];
  mode?: string;
  tool?: string;
  callId?: string;
  status?: string;
  summary?: string;
  arguments?: Record<string, any>;
  brainRunId?: string;
  step?: {id: string; label: string; status: "pending" | "running" | "completed" | "failed"};
  message?: Record<string, any>;
  reason?: string;
  code?: string;
  partial?: boolean;
}

export interface AssistantLiveRun {
  runId: string;
  conversationId: string;
  messageId: string;
  model: string;
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  content: string;
  completedMessage?: Record<string, any>;
  lastSequence: number;
  started: boolean;
  context?: {focus: Record<string, any>; understanding: Record<string, any>; sources: Array<Record<string, any>>};
  steps: Array<{id: string; label: string; status: string}>;
  proposalRequired: boolean;
  runtimeMode?: string;
  allowedTools: string[];
  toolCalls: Array<{id: string; tool: string; status: string; summary?: string}>;
  error?: {code: string; partial: boolean};
}

export function initialAssistantLiveRun(): AssistantLiveRun {
  return {
    runId: "", conversationId: "", messageId: "", model: "", status: "idle",
    content: "", lastSequence: 0, started: false, steps: [], proposalRequired: false,
    allowedTools: [], toolCalls: [],
  };
}

export function reduceAssistantStream(state: AssistantLiveRun, event: AssistantStreamEvent): AssistantLiveRun {
  if (event.schemaVersion !== ASSISTANT_STREAM_SCHEMA_VERSION) return state;
  if (!Number.isSafeInteger(event.seq) || event.seq <= state.lastSequence) return state;
  if (state.runId && event.runId !== state.runId) return state;
  const next: AssistantLiveRun = {
    ...state,
    runId: event.runId,
    conversationId: event.conversationId,
    lastSequence: event.seq,
    steps: [...state.steps],
    allowedTools: [...state.allowedTools],
    toolCalls: [...state.toolCalls],
  };
  if (event.type === "run.started") {
    next.status = "running"; next.started = true; next.model = String(event.model ?? "");
  } else if (event.type === "context.resolved") {
    next.context = {focus: event.focus ?? {}, understanding: event.understanding ?? {}, sources: event.sources ?? []};
  } else if (event.type === "plan.created") {
    next.runtimeMode = String(event.mode ?? "");
    next.allowedTools = Array.isArray(event.allowedTools) ? event.allowedTools.map(String) : [];
  } else if (event.type === "tool.requested" || event.type === "tool.started" || event.type === "tool.completed") {
    const id = String(event.callId ?? `tool-${next.toolCalls.length}`);
    const tool = String(event.tool ?? "tool");
    const status = event.type === "tool.requested" ? "pending" : event.type === "tool.started" ? "running" : String(event.status ?? "completed");
    const normalized = status === "skipped" ? "completed" : status === "completed" ? "completed" : status === "running" ? "running" : status === "pending" ? "pending" : "failed";
    const callIndex = next.toolCalls.findIndex(item => item.id === id);
    const call = {id, tool, status, summary: event.summary};
    if (callIndex >= 0) next.toolCalls[callIndex] = call;
    else next.toolCalls.push(call);
    const step = {id: `tool:${id}`, label: toolLabel(tool), status: normalized};
    const stepIndex = next.steps.findIndex(item => item.id === step.id);
    if (stepIndex >= 0) next.steps[stepIndex] = step;
    else next.steps.push(step);
  } else if (event.type === "step.updated" && event.step) {
    const index = next.steps.findIndex(item => item.id === event.step!.id);
    if (index >= 0) next.steps[index] = {...event.step};
    else next.steps.push({...event.step});
  } else if (event.type === "message.started") {
    next.messageId = String(event.messageId ?? "");
  } else if (event.type === "message.delta") {
    next.content += String(event.delta ?? "");
  } else if (event.type === "write.proposal-required" || event.type === "approval.required") {
    next.proposalRequired = true;
  } else if (event.type === "message.completed") {
    next.messageId = String(event.message?.id ?? next.messageId);
    next.completedMessage = event.message ? {...event.message} : next.completedMessage;
  } else if (event.type === "run.completed") {
    next.status = "completed";
  } else if (event.type === "run.failed") {
    next.status = "failed";
    next.error = {code: String(event.code ?? "assistant_stream_failed"), partial: Boolean(event.partial)};
  }
  return next;
}

export function toolLabel(name: string): string {
  return ({
    get_vault_overview: "读取知识库概览",
    search_vault: "搜索知识库",
    read_note_metadata: "读取当前笔记属性",
    read_note_excerpt: "读取当前笔记片段",
    get_related_notes: "查找相关笔记与反向链接",
    get_backlinks: "读取反向链接",
    get_learning_state: "读取学习状态",
    get_due_reviews: "读取到期复习",
    get_recent_materials: "查找最近资料",
    search_academic_sources: "检索可信本地来源",
  } as Record<string, string>)[name] ?? "执行受控工具";
}

export function parseNdjsonBuffer(buffer: string): {events: AssistantStreamEvent[]; remainder: string} {
  const lines = buffer.split("\n");
  const remainder = lines.pop() ?? "";
  const events: AssistantStreamEvent[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.length > 1_000_000) throw new Error("Assistant stream event is too large");
    const value = JSON.parse(line);
    if (!value || typeof value !== "object" || typeof value.type !== "string") {
      throw new Error("Assistant stream event is invalid");
    }
    events.push(value as AssistantStreamEvent);
  }
  return {events, remainder};
}

export function cancelledAssistantRun(state: AssistantLiveRun): AssistantLiveRun {
  return state.status === "running" ? {...state, status: "cancelled"} : state;
}
