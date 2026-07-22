import {createHash} from "node:crypto";

export type StallGuardDescriptor = {
  toolCallId: string;
  name: string;
  args: Record<string, unknown>;
  mutatesState: boolean;
  idempotent: boolean;
  permissionLevel: string;
};

export type SeenObservation = {
  result: Record<string, unknown>;
  observationHash: string;
  repeats: number;
};

const NO_PROGRESS_NO_INFO = 2;
const NO_PROGRESS_REPLAN = 4;
const NO_PROGRESS_TERMINATE = 6;

function hash(value: unknown): string {
  try {
    return createHash("sha256").update(JSON.stringify(value)).digest("hex");
  } catch {
    return String(value);
  }
}

export class PiStallGuard {
  private modelRequests = 0;
  private toolCalls = 0;
  private startedAt = Date.now();
  private seen = new Map<string, SeenObservation>();
  private consecutiveNoProgress = 0;
  private uniqueObservationCount = 0;
  private uniqueActionIds = new Set<string>();

  readonly maxModelRequests = 32;
  readonly maxToolCalls = 96;
  readonly maxWindowMs = 30 * 60 * 1000;

  /** New Turn resets every budget and progress counter. */
  reset(): void {
    this.modelRequests = 0;
    this.toolCalls = 0;
    this.startedAt = Date.now();
    this.seen.clear();
    this.consecutiveNoProgress = 0;
    this.uniqueObservationCount = 0;
    this.uniqueActionIds.clear();
  }

  beforeModelRequest(): void {
    this.modelRequests += 1;
    this.assertBudget();
  }

  /**
   * Decide how a tool call is handled before execution.
   * - Read-only tools may reuse a previously observed result (no side effects).
   * - Side-effecting but idempotent tools are never cached by the frontend; the
   *   backend re-executes them idempotently (including authorization recovery
   *   under the same toolCallId).
   * - Side-effecting, non-idempotent tools that repeat an equivalent call are
   *   intercepted and must not reuse the previous result.
   */
  beforeTool(descriptor: StallGuardDescriptor): {key: string; cached?: SeenObservation; duplicate?: boolean} {
    this.toolCalls += 1;
    this.assertBudget();
    const key = `${descriptor.name}:${hash(descriptor.args)}`;
    const seen = this.seen.get(key);
    if (!descriptor.mutatesState) {
      if (seen) return {key, cached: seen};
      return {key};
    }
    if (descriptor.idempotent) {
      return {key};
    }
    if (seen) return {key, duplicate: true};
    return {key};
  }

  /** Reuse a cached read-only observation, tracking consecutive no-progress. */
  repeated(existing: SeenObservation): Record<string, unknown> {
    const stallGuard = this.bumpNoProgress();
    return {
      ...existing.result,
      stallGuard,
      observationHash: existing.observationHash,
    };
  }

  /** Intercept an equivalent repeat of a non-idempotent mutating tool call. */
  duplicateObservation(name: string): Record<string, unknown> {
    const stallGuard = this.bumpNoProgress();
    return {
      ok: false,
      status: "blocked",
      code: "duplicate_non_idempotent_tool_call",
      message: "该非幂等工具已被相同参数调用过，重复调用已被拦截。请复用已有结果或改用不同参数。",
      tool: name,
      stallGuard,
    };
  }

  /** Record a freshly executed observation. A new observation resets no-progress. */
  remember(key: string, result: Record<string, unknown>): void {
    const observationHash = hash(result);
    const existing = this.seen.get(key);
    if (!existing || existing.observationHash !== observationHash) {
      this.noteProgress();
    } else {
      existing.repeats += 1;
    }
    this.seen.set(key, {
      result,
      observationHash,
      repeats: existing && existing.observationHash === observationHash ? existing.repeats + 1 : 0,
    });
    const actionId = result && typeof result === "object" ? (result as Record<string, unknown>).actionId : undefined;
    if (typeof actionId === "string") this.uniqueActionIds.add(actionId);
  }

  get progress(): {
    consecutiveNoProgress: number;
    uniqueObservationCount: number;
    uniqueActionIds: string[];
    modelRequests: number;
    toolCalls: number;
  } {
    return {
      consecutiveNoProgress: this.consecutiveNoProgress,
      uniqueObservationCount: this.uniqueObservationCount,
      uniqueActionIds: [...this.uniqueActionIds],
      modelRequests: this.modelRequests,
      toolCalls: this.toolCalls,
    };
  }

  private noteProgress(): void {
    this.consecutiveNoProgress = 0;
    this.uniqueObservationCount += 1;
  }

  private bumpNoProgress(): string {
    this.consecutiveNoProgress += 1;
    if (this.consecutiveNoProgress >= NO_PROGRESS_TERMINATE) {
      throw new Error("stall_guard_safe_termination");
    }
    if (this.consecutiveNoProgress >= NO_PROGRESS_REPLAN) return "stall_replan_required";
    if (this.consecutiveNoProgress >= NO_PROGRESS_NO_INFO) return "no_new_information";
    return "reused_existing_observation";
  }

  private assertBudget(): void {
    const now = Date.now();
    if (this.modelRequests > this.maxModelRequests) throw new Error("stall_guard_model_requests_exceeded");
    if (this.toolCalls > this.maxToolCalls) throw new Error("stall_guard_tool_calls_exceeded");
    if (now - this.startedAt > this.maxWindowMs) throw new Error("stall_guard_window_exceeded");
  }
}
