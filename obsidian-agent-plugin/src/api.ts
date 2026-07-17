import {requestUrl} from "obsidian";
import {AssistantStreamEvent, parseNdjsonBuffer} from "./assistant-stream";

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
  constructor(private baseUrl = DEFAULT_AGENT_URL, private sessionToken = "") {
    if (!isLocalAgentUrl(baseUrl)) throw new Error("Agent URL must be localhost");
  }
  configure(baseUrl: string, sessionToken: string) {
    if (!isLocalAgentUrl(baseUrl)) throw new Error("Agent URL must be localhost");
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.sessionToken = sessionToken;
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
  async streamAssistant(
    body: unknown,
    onEvent: (event: AssistantStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}${API_PREFIX}/assistant/stream`, {
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
      } catch { /* keep the bounded HTTP error */ }
      throw new Error(message);
    }
    if (!response.body) throw new Error("Assistant stream has no response body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let remainder = "";
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        const parsed = parseNdjsonBuffer(remainder + decoder.decode(value, {stream: true}));
        remainder = parsed.remainder;
        for (const event of parsed.events) onEvent(event);
      }
      const tail = (remainder + decoder.decode()).trim();
      if (tail) {
        const parsed = parseNdjsonBuffer(tail + "\n");
        for (const event of parsed.events) onEvent(event);
      }
    } finally {
      reader.releaseLock();
    }
  }
  async confirmAssistantRun(
    runId: string,
    confirmed: boolean,
    onEvent: (event: AssistantStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}${API_PREFIX}/assistant/runs/${encodeURIComponent(runId)}/confirm`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.sessionToken ? {Authorization: `Bearer ${this.sessionToken}`} : {}),
      },
      body: JSON.stringify({confirmed}),
      signal,
    });
    if (!response.ok) throw new Error(`Assistant confirmation failed: HTTP ${response.status}`);
    if (!response.body) throw new Error("Assistant confirmation stream has no response body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let remainder = "";
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        const parsed = parseNdjsonBuffer(remainder + decoder.decode(value, {stream: true}));
        remainder = parsed.remainder;
        for (const event of parsed.events) onEvent(event);
      }
      const tail = (remainder + decoder.decode()).trim();
      if (tail) {
        const parsed = parseNdjsonBuffer(`${tail}\n`);
        for (const event of parsed.events) onEvent(event);
      }
    } finally {
      reader.releaseLock();
    }
  }
  async assistantRunEvents(
    runId: string,
    after = 0,
  ): Promise<{
    schemaVersion: 3;
    items: AssistantStreamEvent[];
  }> {
    const encoded = encodeURIComponent(runId);
    return this.get(`/assistant/runs/${encoded}/events?after=${Math.max(0, after)}`);
  }
  async cancelAssistantRun(runId: string): Promise<unknown> {
    return this.post(`/assistant/runs/${encodeURIComponent(runId)}/cancel`, {});
  }
  async compactAssistantRun(runId: string): Promise<any> {
    return this.post(`/assistant/runs/${encodeURIComponent(runId)}/compact`, {});
  }
  async forkAssistantRun(runId: string, sequence?: number): Promise<any> {
    return this.post(`/assistant/runs/${encodeURIComponent(runId)}/fork`, {sequence});
  }
  async health(): Promise<HealthResponse> {
    const health = await this.get<HealthResponse>("/health");
    if (health.ok !== true || health.status !== "ok" || health.service !== "obsidian-learning-agent" || health.protocol_version !== 1) {
      throw new Error("Agent health protocol mismatch");
    }
    return health;
  }
}
