/**
 * Frontend session-tree projector.
 *
 * It only PROJECTS events that actually happened. It never synthesizes
 * reasoning, tool calls, or tool results. Given the real run events it
 * rebuilds a complete, serializable session tree: every turn (including
 * partial / aborted / error assistant turns), each turn's tool calls
 * (blocked / running / failed), each tool's result (error / partial), the
 * reasoning status (never provider prose), accumulated usage, and stop reason.
 */

export type SessionTreeEvent = {
  type: string;
  runId?: string;
  turnId?: string;
  sequence?: number;
  content?: unknown;
  phase?: unknown;
  tokenCount?: unknown;
  id?: string;
  name?: string;
  args?: unknown;
  status?: string;
  result?: unknown;
  error?: unknown;
  usage?: {promptTokens?: number; completionTokens?: number; totalTokens?: number};
  finishReason?: string;
  code?: string;
  errorMessage?: string;
  summary?: string;
};

export type SessionTreeToolCall = {
  id: string;
  name: string;
  args: unknown;
  status: string;
};

export type SessionTreeToolResult = {
  id: string;
  status: string;
  error?: unknown;
  summary?: string;
};

export type SessionTreeTurn = {
  runId?: string;
  turnId?: string;
  status: "running" | "completed" | "failed" | "cancelled" | "partial";
  assistant: string;
  reasoningStatus: Array<{phase: "started" | "completed"; tokenCount: number}>;
  toolCalls: SessionTreeToolCall[];
  toolResults: SessionTreeToolResult[];
  usage: {promptTokens: number; completionTokens: number; totalTokens: number};
  stopReason?: string;
};

export type SessionTree = {
  schemaVersion: number;
  turns: SessionTreeTurn[];
  serializable: true;
};

function safeString(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function projectSessionTree(events: Array<SessionTreeEvent>): SessionTree {
  const turns = new Map<string, SessionTreeTurn>();
  const order: string[] = [];

  const getTurn = (runId?: string, turnId?: string): SessionTreeTurn => {
    const key = runId || turnId || "default";
    if (!turns.has(key)) {
      order.push(key);
      turns.set(key, {
        runId,
        turnId,
        status: "running",
        assistant: "",
        reasoningStatus: [],
        toolCalls: [],
        toolResults: [],
        usage: {promptTokens: 0, completionTokens: 0, totalTokens: 0},
      });
    }
    return turns.get(key) as SessionTreeTurn;
  };

  for (const event of events) {
    const turn = getTurn(event.runId, event.turnId);
    const type = String(event.type || "");
    if (type === "text") {
      turn.assistant += safeString(event.content);
    } else if (type === "reasoning_status") {
      turn.reasoningStatus.push({
        phase: event.phase === "completed" ? "completed" : "started",
        tokenCount: Math.max(0, Number(event.tokenCount) || 0),
      });
    } else if (type === "tool_call_start" || type === "tool_call") {
      turn.toolCalls.push({
        id: String(event.id || ""),
        name: String(event.name || ""),
        args: event.args ?? null,
        status: String(event.status || "running"),
      });
    } else if (type === "tool_result") {
      turn.toolResults.push({
        id: String(event.id || ""),
        status: String(event.status || (event.error ? "failed" : "ok")),
        error: event.error,
        summary: event.summary ? String(event.summary) : undefined,
      });
    } else if (type === "usage") {
      const usage = event.usage || {};
      turn.usage.promptTokens += Number(usage.promptTokens) || 0;
      turn.usage.completionTokens += Number(usage.completionTokens) || 0;
      turn.usage.totalTokens += Number(usage.totalTokens) || 0;
    } else if (type === "done") {
      turn.status = "completed";
      turn.stopReason = event.finishReason ? String(event.finishReason) : "stop";
    } else if (type === "error") {
      turn.status = event.status === "cancelled" ? "cancelled" : "failed";
      turn.stopReason = event.code
        ? String(event.code)
        : event.errorMessage
          ? String(event.errorMessage)
          : "error";
    }
  }

  for (const turn of turns.values()) {
    if (turn.status === "running") {
      turn.status =
        turn.assistant || turn.toolCalls.length || turn.reasoningStatus.length ? "partial" : "running";
    }
  }

  return {
    schemaVersion: 2,
    turns: order.map((key) => turns.get(key) as SessionTreeTurn),
    serializable: true,
  };
}

export default projectSessionTree;
