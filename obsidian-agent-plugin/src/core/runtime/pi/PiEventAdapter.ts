import type {AgentEvent} from "@earendil-works/pi-agent-core";
import type {AgentChunk} from "../types";
import type {PiRunIdentity} from "./types";

/**
 * Projects Pi lifecycle events into the UI protocol in their real order.
 * Provider thinking is exposed only when the provider emitted a dedicated
 * thinking event. The adapter never synthesizes reasoning from text, tools, or
 * internal runtime state.
 */
export class PiEventAdapter {
  private sequence = 0;

  constructor(private readonly identity: PiRunIdentity, initialSequence = 0) {
    this.sequence = initialSequence;
  }

  next(event: AgentEvent): AgentChunk[] {
    const base = () => ({
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
    });
    if (event.type === "message_update") {
      const update = event.assistantMessageEvent;
      if (update.type === "text_delta") {
        return [{...base(), type: "text", content: update.delta}];
      }
      if (
        update.type === "thinking_start" ||
        update.type === "thinking_delta" ||
        update.type === "thinking_end"
      ) {
        const phase = update.type === "thinking_start"
          ? "started"
          : update.type === "thinking_end"
            ? "completed"
            : "delta";
        return [{
          ...base(),
          type: "reasoning",
          blockId: `provider-reasoning-${Number(update.contentIndex ?? 0)}`,
          provider: this.identity.model || "provider",
          content: update.type === "thinking_delta" ? update.delta : "",
          phase,
        }];
      }
      return [];
    }
    if (event.type === "tool_execution_start") {
      return [{
        ...base(),
        type: "tool_use",
        id: event.toolCallId,
        name: event.toolName,
        input: event.args && typeof event.args === "object" ? event.args : {},
        status: "running",
      }];
    }
    if (event.type === "tool_execution_update") {
      return [{
        ...base(),
        type: "notice",
        code: "tool.execution.update",
        content: "",
        data: {
          callId: event.toolCallId,
          tool: event.toolName,
          partialResult: event.partialResult,
        },
      }];
    }
    if (event.type === "tool_execution_end") {
      const details = event.result?.details;
      const result = details && typeof details === "object"
        ? details as Record<string, unknown>
        : {content: event.result?.content ?? []};
      return [{
        ...base(),
        type: "tool_result",
        id: event.toolCallId,
        name: event.toolName,
        result,
        summary: event.isError ? "工具未能完成，Agent 将依据 Observation 调整步骤" : "工具已完成",
        status: event.isError ? "failed" : "completed",
      }];
    }
    if (event.type === "agent_end") {
      return [{...base(), type: "done", status: "completed"}];
    }
    return [];
  }

  failed(error: unknown, partial: boolean): AgentChunk {
    const message = error instanceof Error ? error.message : String(error);
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "error",
      code: "pi_runtime_failed",
      content: message,
      partial,
    };
  }

  cancelled(): AgentChunk {
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "done",
      status: "cancelled",
    };
  }

  compacted(checkpointId: string): AgentChunk {
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "context_compacted",
      checkpointId,
    };
  }

  currentSequence(): number {
    return this.sequence;
  }
}
