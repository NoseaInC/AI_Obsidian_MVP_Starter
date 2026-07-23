import type {AgentEvent, AgentTool} from "@earendil-works/pi-agent-core";
import type {PiCompactionEntry} from "./PiCompaction";

export interface PiToolContract {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  uses_network: boolean;
  mutates_state: boolean;
  timeout_seconds: number;
  permission_level: "read_only" | "proposal" | "approval_required" | "forbidden";
  idempotent: boolean;
  cancellable: boolean;
  max_result_bytes: number;
}

export interface PiTaskAuthorization {
  id: string;
  sessionId: string;
  runId: string;
  turnId: string;
  sourceMessageId: string;
  objective: string;
  resourceScope: {
    currentNote: boolean;
    explicitVaultPaths: string[];
    createRoots: string[];
    workspaceId: string;
    workspaceIds?: string[];
    projectPaths: string[];
    /** Safe capabilities may be granted automatically for this Run only. */
    allowAllRunCapabilities?: boolean;
    /**
     * First reversible Markdown write plan freezes the scope deterministically.
     * "unbound": any plan still requires a permission card. "bound": the first
     * safe plan already established the explicit write scope.
     */
    writeScopeState: "unbound" | "bound";
    /** toolCallId of the plan that froze the scope; null until bound. */
    initialWriteToolCallId?: string | null;
    /** epoch seconds when the scope was frozen; null until bound. */
    initialWriteBoundAt?: number | null;
  };
  operationScope: string[];
  reversibleOnly: true;
  networkPolicy: "allow" | "deny";
  externalSideEffects: false;
  expiresAtRunEnd: true;
  parentRunId?: string;
  forkedFromSequence?: number;
}

export interface PiRunIdentity {
  conversationId: string;
  sessionId: string;
  runId: string;
  turnId: string;
  sourceMessageId: string;
  profileId: string;
  model: string;
  taskAuthorization: PiTaskAuthorization;
  parentRunId?: string;
  forkedFromSequence?: number;
}

export type PiSessionProjectionEntry = {
  id: string;
  parentId: string | null;
  timestamp: string;
  entryType: string;
  sessionId: string;
  runId: string;
  turnId: string;
  sequence: number;
  payload: Record<string, unknown>;
};

export type PiSessionProjection = {
  sessionId: string;
  leafId: string | null;
  branchId: string;
  entries: PiSessionProjectionEntry[];
  messages: Array<Record<string, unknown>>;
  focus: Record<string, unknown>;
  attachments: Array<Record<string, unknown>>;
  activeActions: Array<Record<string, unknown>>;
  pending: Record<string, unknown> | null;
  compaction: Record<string, unknown> | null;
  schemaVersion: number;
};

export type PiRuntimeTool = AgentTool<any, Record<string, unknown>>;
export type PiCoreEvent = AgentEvent;

export interface PiRuntimeTransport {
  streamModelProxy(
    body: unknown,
    onEvent: (event: Record<string, unknown>) => void,
    signal?: AbortSignal,
  ): Promise<void>;
  modelCapabilities?(profileId?: string): Promise<Record<string, unknown>>;
  registerTaskAuthorization(body: unknown): Promise<Record<string, unknown>>;
  expandTaskAuthorization(authorizationId: string, body: unknown): Promise<Record<string, unknown>>;
  appendRuntimeEvents(body: unknown): Promise<Record<string, unknown>>;
  runtimeEvents(runId: string, afterSequence?: number): Promise<Record<string, unknown>>;
  runtimeSession(sessionId: string): Promise<Record<string, unknown>>;
  runtimeSessionProjection(
    sessionId: string,
    options?: {leafId?: string; runId?: string; uptoSequence?: number},
  ): Promise<PiSessionProjection>;
  controlRuntimeRun(runId: string, type: "steering" | "follow_up", text: string): Promise<Record<string, unknown>>;
  cancelRuntimeRun(runId: string): Promise<Record<string, unknown>>;
  toolContracts(): Promise<{schemaVersion: number; items: PiToolContract[]}>;
  callRuntimeTool(body: unknown): Promise<Record<string, unknown>>;
  /**
   * Persist a tool call that is blocked awaiting a permission decision so that a
   * plugin restart can rebuild the same confirmation card and continue the run.
   * Optional so lightweight transports (tests, legacy backends) keep working.
   */
  savePendingToolCall?(body: Record<string, unknown>): Promise<Record<string, unknown>>;
  /** List still-active pending tool calls scoped by run or session. */
  listPendingToolCalls?(scope: {
    runId?: string;
    sessionId?: string;
  }): Promise<{items: PiPendingToolCallRecord[]}>;
  /** Move a pending tool call to a terminal (or interrupted) state. */
  resolvePendingToolCall?(
    runId: string,
    toolCallId: string,
    state: PiPendingToolCallState,
  ): Promise<Record<string, unknown>>;
  /** Validate recovery against the original Run and fresh server-side guards. */
  validatePendingToolCallRecovery?(
    runId: string,
    toolCallId: string,
  ): Promise<PiPendingRecoveryValidation>;
  /** Persist a structured compaction checkpoint so a restart can reuse it. */
  saveCompactionCheckpoint?(body: Record<string, unknown>): Promise<Record<string, unknown>>;
  /** Read the latest compaction checkpoint for a run (used after restart). */
  getCompactionCheckpoint?(runId: string): Promise<{entry: PiCompactionEntry | null}>;
}

export type PiPendingToolCallState =
  | "pending"
  | "allowed"
  | "denied"
  | "cancelled"
  | "interrupted"
  | "completed";

export type PiPendingToolCallInput = {
  sessionId: string;
  turnId: string;
  toolCallId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  permissionRequest: Record<string, unknown>;
  taskAuthorizationId: string;
  state?: PiPendingToolCallState;
};

export type PiPendingToolCallRecord = PiPendingToolCallInput & {
  runId: string;
  state: PiPendingToolCallState;
  createdAt: string;
  updatedAt: string;
  resolvedAt: string | null;
  recoveryGuard?: Record<string, unknown>;
};

export type PiPendingRecoveryValidation = {
  recoverable: boolean;
  resolved: boolean;
  code?: string;
  record?: PiPendingToolCallRecord;
  taskAuthorization?: PiTaskAuthorization;
  lastEventSequence?: number;
  schemaVersion?: number;
};

export interface PiPermissionRequest {
  code: string;
  message: string;
  toolName: string;
  toolCallId: string;
  /** Raw tool arguments, retained so a restart can re-run the exact call. */
  arguments?: Record<string, unknown>;
  writes: Array<Record<string, unknown>>;
  organization?: {
    directories: string[];
    moves: Array<{source_path: string; target_path: string}>;
    remove_empty_source_dirs: boolean;
  };
  capability: {
    type: "vault_writes" | "vault_organization" | "developer_workspace" | "network";
    workspaceId?: string;
    projectPath?: string;
    toolName?: string;
  };
}

export type PiPermissionDecision = "allow_once" | "allow_all" | "deny";
