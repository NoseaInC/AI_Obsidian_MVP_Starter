import type {AgentClient} from "../../api";

export interface ModelProfileInput {
  displayName: string;
  providerType: string;
  baseUrl: string;
  apiKeyReference: string;
  apiKey?: string;
  defaultModel: string;
  availableModels: string[];
  enabled: boolean;
  settings: Record<string, unknown>;
}

/** The only frontend boundary allowed to persist provider and routing settings. */
export class SettingsService {
  constructor(private readonly client: AgentClient) {}

  saveModelProfile(profileId: string, input: ModelProfileInput): Promise<unknown> {
    return profileId
      ? this.client.patch(`/model-profiles/${encodeURIComponent(profileId)}`, input)
      : this.client.post("/model-profiles", input);
  }

  deleteModelProfile(profileId: string): Promise<unknown> {
    return this.client.delete(`/model-profiles/${encodeURIComponent(profileId)}`);
  }

  testModelProfile(profileId: string): Promise<any> {
    return this.client.post(`/model-profiles/${encodeURIComponent(profileId)}/test`, {});
  }

  listProfileModels(profileId: string): Promise<any> {
    return this.client.post(`/model-profiles/${encodeURIComponent(profileId)}/models`, {});
  }

  updateModelRoute(task: string, profileId: string, modelOverride = ""): Promise<unknown> {
    return this.client.patch("/model-routing", {
      routes: {[task]: {profileId, modelOverride}},
    });
  }
}
