import type {AgentTurnRequest} from "../types";
import type {PiRunIdentity, PiTaskAuthorization} from "./types";

const newId = (prefix: string): string => `${prefix}-${crypto.randomUUID()}`;
export const PI_PROVIDER_ADAPTER_VERSION = "pi-model-proxy-v1";

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
  const allowAllRunCapabilities = request.options?.permission_mode === "allow_all";
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
      allowAllRunCapabilities,
      // Every Plan starts unbound; the first safe Markdown write plan freezes
      // the scope without a confirmation card. No keyword/regex/Intent Router.
      writeScopeState: "unbound",
      initialWriteToolCallId: null,
      initialWriteBoundAt: null,
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
    forkedFromEntryId: request.resolvedForkEntryId,
  };
  return {
    conversationId,
    sessionId: conversationId,
    runId,
    turnId,
    sourceMessageId,
    profileId: request.profileId || "",
    model: request.model || "",
    providerAdapterVersion: PI_PROVIDER_ADAPTER_VERSION,
    taskAuthorization,
    parentRunId: request.parentRunId,
    forkedFromSequence: request.forkedFromSequence,
    forkedFromEntryId: request.resolvedForkEntryId,
  };
}
