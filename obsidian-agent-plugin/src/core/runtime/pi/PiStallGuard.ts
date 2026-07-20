function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function hash(value: unknown): string {
  const text = canonical(value);
  let result = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    result ^= text.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

interface SeenObservation {
  result: Record<string, unknown>;
  observationHash: string;
  repeats: number;
}

/** Execution-fact loop guard. Natural-language intent is never inspected. */
export class PiStallGuard {
  private startedAt = Date.now();
  private readonly seen = new Map<string, SeenObservation>();
  private toolCalls = 0;
  private modelRequests = 0;

  /** Budgets and repeated-observation detection belong to one run, not the
   * long-lived conversation. A resumed conversation may remain open for days. */
  reset(): void {
    this.startedAt = Date.now();
    this.seen.clear();
    this.toolCalls = 0;
    this.modelRequests = 0;
  }

  beforeModelRequest(): void {
    this.modelRequests += 1;
    this.assertBudget();
  }

  beforeTool(name: string, args: Record<string, unknown>): {key: string; cached?: SeenObservation} {
    this.toolCalls += 1;
    this.assertBudget();
    const key = `${name}:${hash(args)}`;
    return {key, cached: this.seen.get(key)};
  }

  remember(key: string, result: Record<string, unknown>): void {
    const observationHash = hash(result);
    const existing = this.seen.get(key);
    this.seen.set(key, {result, observationHash, repeats: existing?.observationHash === observationHash ? existing.repeats + 1 : 0});
  }

  repeated(existing: SeenObservation): Record<string, unknown> {
    if (existing.repeats >= 2) throw new Error("stall_detected: repeated tool call produced no new information");
    existing.repeats += 1;
    return {
      ...existing.result,
      stallGuard: existing.repeats === 1 ? "reused_existing_observation" : "no_new_information",
      observationHash: existing.observationHash,
    };
  }

  private assertBudget(): void {
    if (this.modelRequests > 32) throw new Error("stall_guard_model_request_limit");
    if (this.toolCalls > 96) throw new Error("stall_guard_tool_call_limit");
    if (Date.now() - this.startedAt > 30 * 60_000) throw new Error("stall_guard_elapsed_time_limit");
  }
}
