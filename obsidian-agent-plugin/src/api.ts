export const DEFAULT_AGENT_URL = "http://127.0.0.1:8765";

export interface HealthResponse {
  ok: true;
  status: "ok";
  service: "obsidian-learning-agent";
  protocol_version: 1;
  vault: string;
  jobs: number;
}

export function isLocalAgentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && ["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname);
  } catch { return false; }
}

export class AgentClient {
  constructor(private readonly baseUrl = DEFAULT_AGENT_URL) {
    if (!isLocalAgentUrl(baseUrl)) throw new Error("Agent URL must be localhost");
  }
  async get<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`);
    if (!response.ok) throw new Error((await response.json()).error ?? `HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }
  async post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error((await response.json()).error ?? `HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }
  async health(): Promise<HealthResponse> {
    const health = await this.get<HealthResponse>("/health");
    if (health.ok !== true || health.status !== "ok" || health.service !== "obsidian-learning-agent" || health.protocol_version !== 1) {
      throw new Error("Agent health protocol mismatch");
    }
    return health;
  }
}
