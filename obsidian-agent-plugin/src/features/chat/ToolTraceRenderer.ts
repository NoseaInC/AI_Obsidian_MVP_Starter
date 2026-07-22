import type {AgentChunk, AgentToolStatus} from "../../core/runtime/types";

export interface ToolTraceViewModel {
  id: string;
  name: string;
  status: AgentToolStatus;
  input?: Record<string, unknown>;
  result?: Record<string, unknown>;
  summary?: string;
}
/** Derive display state exclusively from normalized runtime chunks. */
export function toolTraceFromAgentChunk(chunk: AgentChunk): ToolTraceViewModel | null {
  if (chunk.type === "tool_use") {
    return {id: chunk.id, name: chunk.name, status: chunk.status, input: chunk.input};
  }
  if (chunk.type === "tool_result") {
    return {
      id: chunk.id,
      name: chunk.name,
      status: chunk.status,
      result: chunk.result,
      summary: chunk.summary,
    };
  }
  return null;
}
