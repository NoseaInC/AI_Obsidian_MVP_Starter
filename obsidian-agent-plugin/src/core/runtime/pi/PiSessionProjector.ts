import type {AgentMessage} from "@earendil-works/pi-agent-core";
import type {PiSessionProjection} from "./types";

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0},
};

function timestamp(value: unknown): number {
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textContent(value: unknown): Array<{type: "text"; text: string}> {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const block = asRecord(item);
    return block.type === "text" ? [{type: "text" as const, text: String(block.text ?? "")}] : [];
  });
}

/**
 * Convert the backend's one-branch, privacy-bounded projection into the exact
 * message shapes Pi Agent Core accepts. Provider reasoning is deliberately
 * ignored even if a malformed backend payload attempts to include it.
 */
export function projectPiSessionMessages(projection: PiSessionProjection): AgentMessage[] {
  const messages: AgentMessage[] = [];
  const pendingCalls = new Map<string, {name: string; timestamp: number}>();
  let hasCompaction = false;
  const persistedPending = asRecord(projection?.pending);
  const persistedPendingId = String(persistedPending.toolCallId ?? "");
  const persistedPendingName = String(persistedPending.toolName ?? "");

  for (const rawValue of Array.isArray(projection?.messages) ? projection.messages : []) {
    const raw = asRecord(rawValue);
    const role = String(raw.role ?? "");
    const at = timestamp(raw.timestamp);
    if (role === "user") {
      const content = typeof raw.content === "string"
        ? raw.content
        : textContent(raw.content);
      if ((typeof content === "string" && !content.trim()) || (Array.isArray(content) && !content.length)) continue;
      messages.push({role: "user", content, timestamp: at} as AgentMessage);
      continue;
    }

    if (role === "assistant") {
      const blocks: Array<
        {type: "text"; text: string}
        | {type: "toolCall"; id: string; name: string; arguments: Record<string, unknown>}
      > = [];
      for (const item of Array.isArray(raw.content) ? raw.content : []) {
        const block = asRecord(item);
        if (block.type === "text") {
          blocks.push({type: "text", text: String(block.text ?? "")});
          continue;
        }
        if (block.type === "toolCall") {
          const id = String(block.id ?? "");
          const name = String(block.name ?? "");
          if (!id || !name) continue;
          const args = asRecord(block.arguments);
          pendingCalls.set(id, {name, timestamp: at});
          blocks.push({type: "toolCall", id, name, arguments: args});
        }
        // Never restore thinking/reasoning/private provider blocks.
      }
      if (!blocks.length) continue;
      messages.push({
        role: "assistant",
        content: blocks,
        api: "zhixu-secure-proxy",
        provider: "zhixu",
        model: "restored-session",
        usage: {...EMPTY_USAGE, cost: {...EMPTY_USAGE.cost}},
        stopReason: blocks.some(block => block.type === "toolCall") ? "toolUse" : "stop",
        timestamp: at,
      } as AgentMessage);
      continue;
    }

    if (role === "toolResult") {
      const callId = String(raw.toolCallId ?? "");
      const toolName = String(raw.toolName ?? "");
      const call = pendingCalls.get(callId);
      if (!call || !callId || call.name !== toolName) continue;
      const content = textContent(raw.content);
      if (!content.length) content.push({type: "text", text: JSON.stringify(asRecord(raw.details))});
      messages.push({
        role: "toolResult",
        toolCallId: callId,
        toolName,
        content,
        details: asRecord(raw.details),
        isError: raw.isError === true,
        timestamp: at,
      } as AgentMessage);
      pendingCalls.delete(callId);
      continue;
    }

    if (role === "custom") {
      const customType = String(raw.customType ?? "");
      if (!new Set(["steering", "follow_up", "persistedActionResult"]).has(customType)) continue;
      const content = typeof raw.content === "string" ? raw.content : textContent(raw.content);
      messages.push({
        role: "custom",
        customType,
        content,
        display: raw.display === true,
        details: asRecord(raw.details),
        timestamp: at,
      } as AgentMessage);
      continue;
    }

    if (role === "branchSummary") {
      messages.push({
        role: "branchSummary",
        summary: String(raw.summary ?? ""),
        fromId: String(raw.fromId ?? ""),
        timestamp: at,
      } as AgentMessage);
      continue;
    }

    if (role === "compactionSummary") {
      hasCompaction = true;
      messages.push({
        role: "compactionSummary",
        summary: String(raw.summary ?? ""),
        tokensBefore: Number(raw.tokensBefore ?? 0),
        timestamp: at,
      } as AgentMessage);
    }
  }

  // Defensive completion for old/malformed projections. The backend normally
  // inserts this result at the exact lineage boundary.
  for (const [toolCallId, call] of pendingCalls) {
    if (toolCallId === persistedPendingId && call.name === persistedPendingName) continue;
    const observation = {
      ok: false,
      status: "interrupted",
      code: "tool_call_interrupted_by_restart",
      message: "工具调用在结果持久化前中断，恢复时不会自动重复执行。",
    };
    messages.push({
      role: "toolResult",
      toolCallId,
      toolName: call.name,
      content: [{type: "text", text: JSON.stringify(observation)}],
      details: observation,
      isError: true,
      timestamp: call.timestamp,
    } as AgentMessage);
  }

  if (!hasCompaction && projection.compaction) {
    const checkpoint = asRecord(projection.compaction);
    const state = asRecord(checkpoint.structuredState);
    messages.push({
      role: "compactionSummary",
      summary: `Persisted structured checkpoint: ${JSON.stringify(state)}`,
      tokensBefore: Number(checkpoint.tokensBefore ?? 0),
      timestamp: timestamp(checkpoint.createdAt),
    } as AgentMessage);
  }
  return messages;
}

type ForkMessage = {
  role: string;
  toolCall?: unknown;
};

function isAssistantToolCall(message: ForkMessage): boolean {
  return message.role === "assistant" && message.toolCall != null;
}

function isToolResult(message: ForkMessage): boolean {
  return message.role === "toolResult";
}

export type ForkMode = "fork" | "regenerate";

/** Legacy in-memory fork slicing retained until R05 moves boundaries to entries. */
export function projectForkMessages<T extends ForkMessage>(
  messages: ReadonlyArray<T>,
  sequence: number | null | undefined,
  mode: ForkMode = "fork",
): T[] {
  const total = messages.length;
  if (total === 0) return [];
  const forkIndex = sequence == null ? total : Math.max(0, Math.min(total, Math.floor(sequence)));
  const keep = mode === "regenerate" ? Math.max(0, forkIndex - 1) : forkIndex;
  let kept = messages.slice(0, keep);
  if (kept.length && isAssistantToolCall(kept[kept.length - 1] as ForkMessage)) {
    const next = messages[keep] as ForkMessage | undefined;
    if (next && isToolResult(next)) kept = kept.slice(0, kept.length - 1);
  }
  return kept;
}

export default projectPiSessionMessages;
