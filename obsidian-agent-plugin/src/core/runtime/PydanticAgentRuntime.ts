import type {AgentRuntime} from "./AgentRuntime";
import type {
  AgentChunk,
  AgentConversationState,
  AgentRuntimeCapabilities,
  AgentTurnRequest,
  PreparedAgentTurn,
} from "./types";

export interface PydanticRuntimeTransport {
  streamAssistant(body: unknown, onEvent: (event: any) => void, signal?: AbortSignal): Promise<void>;
  confirmAssistantRun(runId: string, response: {confirmed: boolean; answer?: string; scope?: string}, onEvent: (event: any) => void, signal?: AbortSignal): Promise<void>;
  assistantRunEvents(runId: string, after?: number): Promise<{items: any[]; schemaVersion: 3}>;
  cancelAssistantRun(runId: string): Promise<unknown>;
  compactAssistantRun(runId: string): Promise<any>;
  forkAssistantRun(runId: string, sequence?: number): Promise<any>;
}

const CAPABILITIES: Readonly<AgentRuntimeCapabilities> = Object.freeze({
  reconnect: true,
  resume: true,
  fork: true,
  cancel: true,
  compact: true,
  regenerate: true,
  inlineConfirmation: true,
});

export class PydanticAgentRuntime implements AgentRuntime {
  readonly id = "pydantic-agent";
  private state: AgentConversationState | null = null;

  constructor(private readonly transport: PydanticRuntimeTransport) {}

  getCapabilities(): Readonly<AgentRuntimeCapabilities> {
    return CAPABILITIES;
  }

  prepareTurn(request: AgentTurnRequest): PreparedAgentTurn {
    return {
      request,
      payload: {
        message: request.message,
        conversation_id: request.conversationId,
        profile_id: request.profileId,
        model: request.model,
        active_note: request.activeNote,
        attachments: request.attachments ?? [],
        options: request.options ?? {},
        regenerate_message_id: request.regenerateMessageId,
        parent_run_id: request.parentRunId,
        forked_from_sequence: request.forkedFromSequence,
      },
    };
  }

  async *query(turn: PreparedAgentTurn, signal?: AbortSignal): AsyncGenerator<AgentChunk> {
    yield* this.consume(callback => this.transport.streamAssistant(turn.payload, callback, signal));
  }

  async *confirm(runId: string, confirmed: boolean, signal?: AbortSignal, answer = "", scope = ""): AsyncGenerator<AgentChunk> {
    yield* this.consume(callback => this.transport.confirmAssistantRun(runId, {confirmed, answer, scope}, callback, signal));
  }

  async reconnect(runId: string, afterSequence: number): Promise<AgentChunk[]> {
    const payload = await this.transport.assistantRunEvents(runId, afterSequence);
    return payload.items.map(event => this.normalize(event)).filter((item): item is AgentChunk => item !== null).map(item => { this.track(item); return item; });
  }

  async cancel(runId: string): Promise<void> {
    await this.transport.cancelAssistantRun(runId);
  }

  async compact(runId: string): Promise<AgentChunk> {
    const payload = await this.transport.compactAssistantRun(runId);
    return this.normalize(payload.event) ?? {
      type: "context_compacted", runId, conversationId: "", sequence: 0, checkpointId: String(payload.checkpointId ?? ""),
    };
  }

  async fork(runId: string, sequence?: number): Promise<AgentConversationState> {
    const payload = await this.transport.forkAssistantRun(runId, sequence);
    return {
      conversationId: String(payload.conversationId ?? ""),
      runId: String(payload.runId ?? ""),
      lastEventSequence: Number(payload.event?.seq ?? 0),
      selectedModel: this.state?.selectedModel,
      status: "completed",
      parentRunId: runId,
      forkedFromSequence: sequence ?? this.state?.lastEventSequence,
    };
  }

  syncConversationState(state: AgentConversationState | null): void {
    this.state = state ? {...state} : null;
  }

  getConversationState(): AgentConversationState | null {
    return this.state ? {...this.state} : null;
  }

  cleanup(): void {
    this.state = null;
  }

  private async *consume(start: (onEvent: (event: Record<string, unknown>) => void) => Promise<void>): AsyncGenerator<AgentChunk> {
    const queue: AgentChunk[] = [];
    let wake: (() => void) | null = null;
    let completed = false;
    let failure: unknown;
    const running = start(event => {
      const chunk = this.normalize(event);
      if (chunk) { this.track(chunk); queue.push(chunk); }
      wake?.();
      wake = null;
    }).catch(error => { failure = error; }).finally(() => { completed = true; wake?.(); wake = null; });
    while (!completed || queue.length) {
      if (!queue.length) await new Promise<void>(resolve => { wake = resolve; });
      while (queue.length) yield queue.shift()!;
    }
    await running;
    if (failure) throw failure;
  }

  private normalize(event: Record<string, unknown>): AgentChunk | null {
    if (Number(event.schemaVersion) !== 3) return null;
    const base = {
      runId: String(event.runId ?? ""),
      conversationId: String(event.conversationId ?? ""),
      sequence: Number(event.seq ?? 0),
    };
    const type = String(event.type ?? "");
    if (type === "message.delta") return {...base, type: "text", content: String(event.delta ?? ""), messageId: String(event.messageId ?? "")};
    if (type === "tool.started") return {...base, type: "tool_use", id: String(event.callId ?? ""), name: String(event.tool ?? "tool"), input: (event.input ?? event.arguments ?? {}) as Record<string, unknown>, status: "running"};
    if (type === "tool.completed") {
      const status = String(event.status ?? "completed");
      const normalized = (["completed", "failed", "blocked", "cancelled"].includes(status) ? status : "failed") as "completed" | "failed" | "blocked" | "cancelled";
      return {...base, type: "tool_result", id: String(event.callId ?? ""), name: String(event.tool ?? "tool"), result: (event.result ?? {}) as Record<string, unknown>, summary: String(event.summary ?? ""), status: normalized};
    }
    if (type === "write.diff") return {...base, type: "write_diff", proposalId: String(event.proposalId ?? ""), title: String(event.title ?? ""), writes: Array.isArray(event.writes) ? event.writes as Array<Record<string, unknown>> : []};
    if (type === "inline.confirmation.required") return {...base, type: "confirmation_required", confirmation: event.confirmation as any};
    if (type === "context.compacted") return {...base, type: "context_compacted", checkpointId: String(event.checkpointId ?? "")};
    if (type === "run.failed") return {...base, type: "error", code: String(event.code ?? "assistant_failed"), content: String(event.message ?? "助手运行失败"), partial: event.partial === true};
    if (type === "run.cancelled") return {...base, type: "done", status: "cancelled"};
    if (type === "run.completed") return {...base, type: "done", status: "completed", message: event.message as Record<string, unknown> | undefined};
    if (type === "usage.updated") return {...base, type: "usage", inputTokens: Number(event.inputTokens ?? 0), outputTokens: Number(event.outputTokens ?? 0), model: String(event.model ?? "")};
    return {...base, type: "notice", code: type, content: "", data: event};
  }

  private track(chunk: AgentChunk): void {
    const status = chunk.type === "confirmation_required" ? "waiting_confirmation"
      : chunk.type === "error" ? "failed"
      : chunk.type === "done" ? chunk.status
      : this.state?.status === "waiting_confirmation" ? "waiting_confirmation" : "running";
    this.state = {
      conversationId: chunk.conversationId || this.state?.conversationId || "",
      runId: chunk.runId || this.state?.runId || "",
      checkpointId: chunk.type === "context_compacted" ? chunk.checkpointId : this.state?.checkpointId,
      lastEventSequence: Math.max(chunk.sequence, this.state?.lastEventSequence ?? 0),
      selectedModel: chunk.type === "usage" ? chunk.model : this.state?.selectedModel,
      status,
      parentRunId: this.state?.parentRunId,
      forkedFromSequence: this.state?.forkedFromSequence,
    };
  }
}
