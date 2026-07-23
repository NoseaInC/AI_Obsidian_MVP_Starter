import type {AgentRuntime} from "./core/runtime/AgentRuntime";
import type {AgentChunk, AgentTurnRequest} from "./core/runtime/types";
import {
  type AssistantLiveRun,
  type AssistantStreamEvent,
  agentChunkToAssistantEvent,
  initialAssistantLiveRun,
  reduceAssistantStream,
} from "./assistant-stream";

export interface PiAssistantTurnInput {
  runtime: AgentRuntime;
  message: string;
  conversationId?: string;
  currentNote?: string;
  selection?: string;
  attachments?: AgentTurnRequest["attachments"];
  references?: Array<{kind: string; path?: string; title?: string}>;
  context?: Record<string, unknown>;
  options?: Record<string, unknown>;
  profileId?: string;
  model?: string;
  regenerateMessageId?: string;
  initialRun?: AssistantLiveRun;
}

export interface PiAssistantTurnUpdate {
  chunk: AgentChunk;
  event: AssistantStreamEvent;
  run: AssistantLiveRun;
}

/** Shared prepare/query/reducer pipeline for every ordinary Assistant surface. */
export async function* runPiAssistantTurn(
  input: PiAssistantTurnInput,
  signal?: AbortSignal,
): AsyncGenerator<PiAssistantTurnUpdate> {
  const references = (input.references ?? []).slice(0, 20).map(item => ({
    kind: String(item.kind || "vault_note"),
    path: item.path ? String(item.path) : undefined,
    title: item.title ? String(item.title) : undefined,
  }));
  const taskContext = input.context || references.length
    ? `\n\n<zhixu_task_context>\n${JSON.stringify({context: input.context ?? {}, references})}\n</zhixu_task_context>`
    : "";
  const turn = input.runtime.prepareTurn({
    message: `${input.message}${taskContext}`,
    conversationId: input.conversationId,
    profileId: input.profileId,
    model: input.model,
    activeNote: {path: input.currentNote, selection: input.selection},
    attachments: input.attachments,
    options: input.options,
    regenerateMessageId: input.regenerateMessageId,
  });
  let run = input.initialRun ?? initialAssistantLiveRun();
  for await (const chunk of input.runtime.query(turn, signal)) {
    const event = agentChunkToAssistantEvent(chunk);
    run = reduceAssistantStream(run, event);
    yield {chunk, event, run};
  }
}
