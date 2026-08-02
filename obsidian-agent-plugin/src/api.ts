import {requestUrl} from "obsidian";
import {parseNdjsonBuffer} from "./assistant-stream";

export const DEFAULT_AGENT_URL = "http://127.0.0.1:8765";
const API_PREFIX = "/api/v1";

export interface HealthResponse {
  ok: true;
  status: "ok";
  service: "obsidian-learning-agent";
  protocol_version: 1;
  vault: string;
  jobs: number;
  brain_version?: string;
  schema_version?: number;
}

export function isLocalAgentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && ["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname);
  } catch { return false; }
}

export class AgentClient {
  private runtimeUpgradeHandler: ((request: Record<string, unknown>) => Promise<Record<string, unknown>>) | null = null;
  constructor(private baseUrl = DEFAULT_AGENT_URL, private sessionToken = "") {
    if (!isLocalAgentUrl(baseUrl)) throw new Error("Agent URL must be localhost");
  }
  configure(baseUrl: string, sessionToken: string) {
    if (!isLocalAgentUrl(baseUrl)) throw new Error("Agent URL must be localhost");
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.sessionToken = sessionToken;
  }
  configureRuntimeUpgradeHandler(handler: ((request: Record<string, unknown>) => Promise<Record<string, unknown>>) | null): void {
    this.runtimeUpgradeHandler = handler;
  }
  private async request<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown, idempotencyKey = ""): Promise<T> {
    const isHealth = path === "/health";
    const response = await requestUrl({
      url: `${this.baseUrl}${isHealth ? path : API_PREFIX + path}`,
      method,
      headers: {
        ...(body === undefined ? {} : {"Content-Type": "application/json"}),
        ...(this.sessionToken ? {Authorization: `Bearer ${this.sessionToken}`} : {}),
        ...(idempotencyKey ? {"Idempotency-Key": idempotencyKey} : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      throw: false,
    });
    const payload = response.json;
    if (response.status < 200 || response.status >= 300) {
      const error = payload?.error;
      throw new Error(typeof error === "string" ? error : error?.message ?? `HTTP ${response.status}`);
    }
    return payload as T;
  }
  async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }
  async post<T>(path: string, body: unknown, idempotencyKey = ""): Promise<T> {
    return this.request<T>("POST", path, body, idempotencyKey);
  }
  async uploadAttachment<T>(
    body: ArrayBuffer,
    metadata: {conversationId: string; conversationTitle?: string; displayName: string; kind: string; mimeType: string},
  ): Promise<T> {
    const response = await requestUrl({
      url: `${this.baseUrl}${API_PREFIX}/intake/attachments`,
      method: "POST",
      headers: {
        "Content-Type": metadata.mimeType || "application/octet-stream",
        Authorization: `Bearer ${this.sessionToken}`,
        "X-Conversation-Id": encodeURIComponent(metadata.conversationId),
        "X-Conversation-Title": encodeURIComponent(metadata.conversationTitle || "新会话"),
        "X-Attachment-Name": encodeURIComponent(metadata.displayName),
        "X-Attachment-Kind": metadata.kind,
      },
      body,
      throw: false,
    });
    const payload = response.json;
    if (response.status < 200 || response.status >= 300) {
      throw new Error(payload?.error?.message ?? `HTTP ${response.status}`);
    }
    return payload as T;
  }
  async patch<T>(path: string, body: unknown): Promise<T> { return this.request<T>("PATCH", path, body); }
  async delete<T>(path: string): Promise<T> { return this.request<T>("DELETE", path); }
  async streamModelProxy(
    body: unknown,
    onEvent: (event: Record<string, unknown>) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}${API_PREFIX}/model/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.sessionToken ? {Authorization: `Bearer ${this.sessionToken}`} : {}),
      },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload?.error?.message ?? message;
      } catch { /* bounded transport error */ }
      throw new Error(message);
    }
    if (!response.body) throw new Error("Model proxy stream has no response body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let remainder = "";
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        const parsed = parseNdjsonBuffer(remainder + decoder.decode(value, {stream: true}));
        remainder = parsed.remainder;
        for (const event of parsed.events) onEvent(event as unknown as Record<string, unknown>);
      }
      const tail = (remainder + decoder.decode()).trim();
      if (tail) {
        const parsed = parseNdjsonBuffer(`${tail}\n`);
        for (const event of parsed.events) onEvent(event as unknown as Record<string, unknown>);
      }
    } finally {
      reader.releaseLock();
    }
  }
  async modelCapabilities(profileId = ""): Promise<any> {
    const suffix = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "";
    return this.get(`/model/capabilities${suffix}`);
  }
  async probeModelCapabilities(profileId: string): Promise<any> {
    return this.post("/model/capabilities/probe", {profileId});
  }
  async toolContracts(): Promise<any> {
    return this.get("/tools/contracts");
  }
  async registerTaskAuthorization(body: unknown): Promise<any> {
    return this.post("/task-authorizations", body);
  }
  async expandTaskAuthorization(authorizationId: string, body: unknown): Promise<any> {
    return this.post(`/task-authorizations/${encodeURIComponent(authorizationId)}/expand`, body);
  }
  async appendRuntimeEvents(body: unknown): Promise<any> {
    return this.post("/agent/events", body);
  }
  async updateMessageMetadata(conversationId: string, messageId: string, metadata: Record<string, unknown>): Promise<any> {
    return this.post(`/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/metadata`, {metadata});
  }
  async runtimeEvents(runId: string, afterSequence = 0): Promise<any> {
    return this.get(`/agent/runs/${encodeURIComponent(runId)}/events?after=${Math.max(0, afterSequence)}`);
  }
  async runtimeSession(sessionId: string): Promise<any> {
    return this.get(`/agent/sessions/${encodeURIComponent(sessionId)}`);
  }
  async runtimeSessionProjection(
    sessionId: string,
    options: {leafId?: string; runId?: string; uptoSequence?: number} = {},
  ): Promise<any> {
    const params = new URLSearchParams();
    if (options.leafId) params.set("leafId", options.leafId);
    if (options.runId) params.set("runId", options.runId);
    if (options.uptoSequence != null) params.set("uptoSequence", String(Math.max(0, options.uptoSequence)));
    const query = params.toString();
    return this.get(`/agent/sessions/${encodeURIComponent(sessionId)}/projection${query ? `?${query}` : ""}`);
  }
  async runtimeForkProjection(
    runId: string,
    options: {sequence?: number; mode?: "fork" | "regenerate"} = {},
  ): Promise<any> {
    return this.post(`/agent/runs/${encodeURIComponent(runId)}/fork-projection`, {
      mode: options.mode ?? "fork",
      ...(options.sequence != null ? {sequence: Math.max(0, options.sequence)} : {}),
    });
  }
  async controlRuntimeRun(runId: string, type: "steering" | "follow_up", text: string): Promise<any> {
    return this.post(`/agent/runs/${encodeURIComponent(runId)}/control`, {type, text});
  }
  async cancelRuntimeRun(runId: string): Promise<any> {
    return this.post(`/agent/runs/${encodeURIComponent(runId)}/cancel`, {});
  }
  async savePendingToolCall(body: Record<string, unknown> & {runId?: string}): Promise<any> {
    const {runId, ...rest} = body;
    const id = String(runId ?? "");
    return this.post(`/agent/runs/${encodeURIComponent(id)}/pending-tool-calls`, rest);
  }
  async listPendingToolCalls(scope: {runId?: string; sessionId?: string}): Promise<any> {
    if (scope.runId) {
      return this.get(`/agent/runs/${encodeURIComponent(scope.runId)}/pending-tool-calls`);
    }
    if (scope.sessionId) {
      return this.get(`/agent/sessions/${encodeURIComponent(scope.sessionId)}/pending-tool-calls`);
    }
    return {items: []};
  }
  async resolvePendingToolCall(runId: string, toolCallId: string, state: string): Promise<any> {
    return this.post(`/agent/runs/${encodeURIComponent(runId)}/pending-tool-calls/resolve`, {
      toolCallId,
      state,
    });
  }
  async validatePendingToolCallRecovery(runId: string, toolCallId: string): Promise<any> {
    return this.post(`/agent/runs/${encodeURIComponent(runId)}/pending-tool-calls/validate`, {
      toolCallId,
    });
  }
  async saveCompactionCheckpoint(body: Record<string, unknown> & {runId?: string}): Promise<any> {
    const {runId, ...rest} = body;
    const id = String(runId ?? "");
    return this.post(`/agent/runs/${encodeURIComponent(id)}/compaction-checkpoint`, rest);
  }
  async getCompactionCheckpoint(runId: string): Promise<any> {
    return this.get(`/agent/runs/${encodeURIComponent(runId)}/compaction-checkpoint`);
  }
  async callRuntimeTool(body: unknown): Promise<any> {
    const requestBody = body && typeof body === "object" ? body as Record<string, any> : {};
    const response = await this.post<any>("/tools/call", requestBody);
    if (
      requestBody.toolName !== "activate_runtime_upgrade" ||
      response?.ok !== true ||
      !this.runtimeUpgradeHandler
    ) return response;
    const activation = response.content && typeof response.content === "object"
      ? response.content as Record<string, unknown>
      : {};
    try {
      const deployment = await this.runtimeUpgradeHandler(activation);
      return {...response, content: {...activation, deployment}};
    } catch {
      const workspaceId = String(activation.id ?? requestBody.arguments?.workspace_id ?? "");
      let rollback: any = null;
      if (workspaceId) {
        rollback = await this.post<any>("/tools/call", {
          ...requestBody,
          toolCallId: `${String(requestBody.toolCallId ?? "activation")}-rollback`,
          toolName: "rollback_task_branch",
          arguments: {workspace_id: workspaceId},
        }).catch(() => null);
      }
      if (rollback?.ok === true) {
        await this.runtimeUpgradeHandler({...activation, rollback: true}).catch(() => undefined);
      }
      throw new Error(rollback?.ok === true ? "runtime_upgrade_failed_and_rolled_back" : "runtime_upgrade_failed_rollback_required");
    }
  }
  async renameConversation(conversationId: string, title: string): Promise<any> {
    return this.patch(`/conversations/${encodeURIComponent(conversationId)}/rename`, {title});
  }
  async pinConversation(conversationId: string, pinned: boolean): Promise<any> {
    return this.patch(`/conversations/${encodeURIComponent(conversationId)}/pin`, {pinned});
  }
  async searchConversations(query: string, limit = 30): Promise<any> {
    return this.get(`/conversations/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  }
  async memoryContext(body: {query: string; memoryTypes?: string[]; maxItems?: number; maxTokens?: number}): Promise<any> {
    return this.post("/memory/context", body);
  }
  async agentAction(actionId: string): Promise<any> {
    return this.get(`/actions/${encodeURIComponent(actionId)}`);
  }
  async agentActionDiff(actionId: string): Promise<any> {
    return this.get(`/actions/${encodeURIComponent(actionId)}/diff`);
  }
  async undoAgentAction(actionId: string): Promise<any> {
    return this.post(`/actions/${encodeURIComponent(actionId)}/undo`, {});
  }
  async health(): Promise<HealthResponse> {
    const health = await this.get<HealthResponse>("/health");
    if (health.ok !== true || health.status !== "ok" || health.service !== "obsidian-learning-agent" || health.protocol_version !== 1) {
      throw new Error("Agent health protocol mismatch");
    }
    return health;
  }
}
