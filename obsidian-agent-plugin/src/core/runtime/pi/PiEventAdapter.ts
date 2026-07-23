import type {AgentEvent} from "@earendil-works/pi-agent-core";
import type {AgentChunk, AgentInlineConfirmation} from "../types";
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
      const result: Record<string, unknown> = details && typeof details === "object"
        ? details as Record<string, unknown>
        : {content: event.result?.content ?? []};
      const blocked = result.blocked === true || result.status === "blocked";
      return [{
        ...base(),
        type: "tool_result",
        id: event.toolCallId,
        name: event.toolName,
        result,
        summary: blocked
          ? "用户未授权该操作，Agent 将依据 Observation 调整步骤"
          : event.isError
            ? "工具未能完成，Agent 将依据 Observation 调整步骤"
            : "工具已完成",
        status: blocked ? "blocked" : event.isError ? "failed" : "completed",
      }];
    }
    // agent_end also fires after an errored provider stream.  The runtime,
    // which can inspect agent.state.errorMessage, owns the terminal decision.
    if (event.type === "agent_end") return [];
    return [];
  }

  completed(): AgentChunk {
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "done",
      status: "completed",
    };
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

  confirmationRequired(confirmation: AgentInlineConfirmation): AgentChunk {
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "confirmation_required",
      confirmation,
    };
  }

  confirmationResolved(mode: "once" | "all" | "cancelled"): AgentChunk {
    const sequence = ++this.sequence;
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence,
      type: "notice",
      code: "inline.confirmation.resolved",
      content: "",
      data: {
        schemaVersion: 3,
        seq: sequence,
        type: "inline.confirmation.resolved",
        runId: this.identity.runId,
        conversationId: this.identity.conversationId,
        reason: mode,
      },
    };
  }

  recoveredToolResult(
    toolCallId: string,
    toolName: string,
    details: Record<string, unknown>,
    isError: boolean,
  ): AgentChunk {
    const blocked = details.blocked === true || details.status === "blocked";
    return {
      runId: this.identity.runId,
      conversationId: this.identity.conversationId,
      sequence: ++this.sequence,
      type: "tool_result",
      id: toolCallId,
      name: toolName,
      result: details,
      summary: blocked
        ? "用户未授权该操作，Agent 将依据 Observation 调整步骤"
        : isError
          ? "工具未能完成，Agent 将依据 Observation 调整步骤"
          : "工具已完成",
      status: blocked ? "blocked" : isError ? "failed" : "completed",
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
