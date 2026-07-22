/**
 * Session fork / regenerate projector.
 *
 * A fork must rebuild the branch context from a chosen point in the existing
 * conversation WITHOUT copying the whole transcript and WITHOUT leaking any
 * message that came after the fork point. The projection also refuses to split
 * a tool pair: if the fork lands on an assistant tool call whose result has not
 * yet been produced, the dangling call is dropped so the branch never contains
 * an orphaned tool call.
 *
 * In `regenerate` mode the message at the fork point (and everything after it)
 * is dropped, so the model regenerates that answer instead of reusing it.
 *
 * Note: the project's persisted session tree (pi_session) exposes user/assistant
 * text only; the authoritative transcript lives in the runtime's in-memory
 * agent state. This projector therefore works on `AgentMessage[]` and treats
 * `sequence` as a 1-based message index into that transcript.
 */

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

export function projectForkMessages<T extends ForkMessage>(
  messages: ReadonlyArray<T>,
  sequence: number | null | undefined,
  mode: ForkMode = "fork",
): T[] {
  const total = messages.length;
  if (total === 0) return [];
  const forkIndex = sequence == null ? total : Math.max(0, Math.min(total, Math.floor(sequence)));
  // Number of messages to retain before tool-pair / regenerate adjustments.
  let keep = mode === "regenerate" ? Math.max(0, forkIndex - 1) : forkIndex;
  let kept = messages.slice(0, keep);
  // Never end a branch on a dangling assistant tool call: if the next original
  // message is the matching tool result, the pair would be split, so drop the call.
  if (kept.length && isAssistantToolCall(kept[kept.length - 1] as ForkMessage)) {
    const next = messages[keep] as ForkMessage | undefined;
    if (next && isToolResult(next)) kept = kept.slice(0, kept.length - 1);
  }
  return kept;
}

export default projectForkMessages;
