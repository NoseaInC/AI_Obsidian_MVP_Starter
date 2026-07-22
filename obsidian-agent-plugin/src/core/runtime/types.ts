export type AgentToolStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled";

export interface AgentRuntimeCapabilities {
  reconnect: boolean;
  resume: boolean;
  fork: boolean;
  cancel: boolean;
  compact: boolean;
  regenerate: boolean;
  inlineConfirmation: boolean;
}

export interface AgentTurnRequest {
  message: string;
  conversationId?: string;
  profileId?: string;
  model?: string;
  activeNote?: {path?: string; selection?: string};
  attachments?: Array<{attachment_id: string; kind?: string; display_name?: string}>;
  options?: Record<string, unknown>;
  regenerateMessageId?: string;
  parentRunId?: string;
  forkedFromSequence?: number;
}

export interface PreparedAgentTurn {
  request: AgentTurnRequest;
  payload: Record<string, unknown>;
}

export interface AgentConversationState {
  conversationId: string;
  runId: string;
  checkpointId?: string;
  lastEventSequence: number;
  selectedModel?: string;
  status: "idle" | "running" | "waiting_confirmation" | "completed" | "failed" | "cancelled";
  parentRunId?: string;
  forkedFromSequence?: number;
}

export interface AgentInlineConfirmation {
  run_id: string;
  kind: "write" | "question" | "permission";
  proposal_id: string;
  title: string;
  summary: string;
  risk_level: "low" | "medium" | "high";
  writes: Array<Record<string, unknown>>;
  tool_name?: string;
  question: string;
  options: string[];
  reason: string;
  scope_candidates: string[];
  actions: string[];
}

interface AgentChunkBase {
  runId: string;
  conversationId: string;
  sequence: number;
}

export type AgentChunk =
  | (AgentChunkBase & {type: "text"; content: string; messageId?: string})
  | (AgentChunkBase & {
      type: "reasoning";
      blockId: string;
      provider: string;
      content: string;
      phase: "started" | "delta" | "completed";
    })
  | (AgentChunkBase & {type: "tool_use"; id: string; name: string; input: Record<string, unknown>; status: AgentToolStatus})
  | (AgentChunkBase & {type: "tool_result"; id: string; name: string; result: Record<string, unknown>; summary: string; status: AgentToolStatus})
  | (AgentChunkBase & {type: "write_diff"; proposalId: string; title: string; writes: Array<Record<string, unknown>>})
  | (AgentChunkBase & {type: "confirmation_required"; confirmation: AgentInlineConfirmation})
  | (AgentChunkBase & {type: "usage"; inputTokens: number; outputTokens: number; model?: string})
  | (AgentChunkBase & {type: "context_compacted"; checkpointId?: string})
  | (AgentChunkBase & {type: "notice"; code: string; content: string; data?: Record<string, unknown>})
  | (AgentChunkBase & {type: "error"; code: string; content: string; partial: boolean})
  | (AgentChunkBase & {type: "done"; status: "completed" | "cancelled"; message?: Record<string, unknown>});
