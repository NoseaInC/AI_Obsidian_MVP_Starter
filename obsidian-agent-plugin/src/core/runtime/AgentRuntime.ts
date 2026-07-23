import type {
  AgentChunk,
  AgentConversationState,
  AgentRuntimeCapabilities,
  AgentTurnRequest,
  PreparedAgentTurn,
} from "./types";

export interface AgentRuntime {
  readonly id: string;
  getCapabilities(): Readonly<AgentRuntimeCapabilities>;
  prepareTurn(request: AgentTurnRequest): PreparedAgentTurn;
  query(turn: PreparedAgentTurn, signal?: AbortSignal): AsyncGenerator<AgentChunk>;
  confirm(runId: string, confirmed: boolean, signal?: AbortSignal, answer?: string, scope?: string): AsyncGenerator<AgentChunk>;
  reconnect(runId: string, afterSequence: number): Promise<AgentChunk[]>;
  steer(runId: string, text: string): Promise<void>;
  followUp(runId: string, text: string): Promise<void>;
  cancel(runId: string): Promise<void>;
  compact(runId: string): Promise<AgentChunk>;
  fork(runId: string, sequence?: number, mode?: "fork" | "regenerate"): Promise<AgentConversationState>;
  syncConversationState(state: AgentConversationState | null): void;
  getConversationState(): AgentConversationState | null;
  cleanup(): void;
}
