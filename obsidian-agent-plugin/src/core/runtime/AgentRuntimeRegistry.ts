import type {AgentRuntime} from "./AgentRuntime";

export type AgentRuntimeFactory = () => AgentRuntime;

export class AgentRuntimeRegistry {
  private readonly factories = new Map<string, AgentRuntimeFactory>();

  register(id: string, factory: AgentRuntimeFactory): void {
    if (!id || this.factories.has(id)) throw new Error(`Agent runtime already registered: ${id}`);
    this.factories.set(id, factory);
  }

  create(id: string): AgentRuntime {
    const factory = this.factories.get(id);
    if (!factory) throw new Error(`Agent runtime is not registered: ${id}`);
    return factory();
  }

  ids(): string[] {
    return [...this.factories.keys()];
  }
}
