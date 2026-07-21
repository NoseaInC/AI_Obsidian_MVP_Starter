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
    projectPaths: string[];
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
  appendRuntimeEvents(body: unknown): Promise<Record<string, unknown>>;
  runtimeEvents(runId: string, afterSequence?: number): Promise<Record<string, unknown>>;
  runtimeSession(sessionId: string): Promise<Record<string, unknown>>;
  controlRuntimeRun(runId: string, type: "steering" | "follow_up", text: string): Promise<Record<string, unknown>>;
  cancelRuntimeRun(runId: string): Promise<Record<string, unknown>>;
  toolContracts(): Promise<{schemaVersion: number; items: PiToolContract[]}>;
  callRuntimeTool(body: unknown): Promise<Record<string, unknown>>;
}
