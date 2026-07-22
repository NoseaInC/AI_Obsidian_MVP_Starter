import type {AgentEvent, AgentTool} from "@earendil-works/pi-agent-core";

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
  controlRuntimeRun(runId: string, type: "steering" | "follow_up", text: string): Promise<Record<string, unknown>>;
  cancelRuntimeRun(runId: string): Promise<Record<string, unknown>>;
  toolContracts(): Promise<{schemaVersion: number; items: PiToolContract[]}>;
  callRuntimeTool(body: unknown): Promise<Record<string, unknown>>;
}

export interface PiPermissionRequest {
  code: string;
  message: string;
  toolName: string;
  toolCallId: string;
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
