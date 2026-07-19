import type {AgentTurnRequest} from "../types";
import type {PiRunIdentity, PiTaskAuthorization} from "./types";

const newId = (prefix: string): string => `${prefix}-${crypto.randomUUID()}`;

/**
 * Creates the turn envelope without classifying natural-language intent.
 * Operation and resource grants are extended only by the model-authored plan
 * and the local Harness; no keyword, regex, or fixed phrase router exists here.
 */
export function createTurnIdentity(request: AgentTurnRequest): PiRunIdentity {
  const conversationId = request.conversationId || newId("conversation");
  const runId = newId("pi-run");
  const turnId = newId("turn");
  const sourceMessageId = newId("message");
  const taskAuthorization: PiTaskAuthorization = {
    id: newId("authorization"),
    sessionId: conversationId,
    runId,
    turnId,
    sourceMessageId,
    objective: request.message,
    resourceScope: {
      currentNote: Boolean(request.activeNote?.path),
      explicitVaultPaths: request.activeNote?.path ? [request.activeNote.path] : [],
      createRoots: [],
      workspaceId: "",
      projectPaths: [],
    },
    operationScope: [],
    reversibleOnly: true,
    networkPolicy:
      request.options?.networkAuthorized === true || request.options?.allow_network === true
        ? "allow"
        : "deny",
    externalSideEffects: false,
    expiresAtRunEnd: true,
    parentRunId: request.parentRunId,
    forkedFromSequence: request.forkedFromSequence,
  };
  return {
    conversationId,
    sessionId: conversationId,
    runId,
    turnId,
    sourceMessageId,
    profileId: request.profileId || "",
    model: request.model || "",
    taskAuthorization,
    parentRunId: request.parentRunId,
    forkedFromSequence: request.forkedFromSequence,
  };
}
