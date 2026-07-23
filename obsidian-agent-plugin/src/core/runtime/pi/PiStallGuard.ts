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

export type StallGuardStage =
  | "reused_existing_observation"
  | "no_new_information"
  | "stall_replan_required"
  | "stall_safe_termination";

export type RunProgress = {
  lastNewObservationAt: number | null;
  uniqueObservationCount: number;
  uniqueActionIds: string[];
  uniqueResultPages: string[];
  consecutiveNoProgress: number;
  repeatedValidationFailures: number;
  repeatedMissingToolCalls: number;
  modelRequests: number;
  toolCalls: number;
};

const NO_PROGRESS_NO_INFO = 2;
const NO_PROGRESS_REPLAN = 4;
const NO_PROGRESS_TERMINATE = 6;

const ACTION_KEYS = new Set(["actionid", "actionids"]);
const RESULT_PAGE_KEYS = new Set([
  "cursor",
  "nextcursor",
  "offset",
  "nextoffset",
  "page",
  "pagenumber",
  "pagetoken",
]);
const FILE_PATH_KEYS = new Set([
  "path",
  "paths",
  "filepath",
  "filepaths",
  "sourcepath",
  "targetpath",
  "destinationpath",
]);
const TEST_RESULT_KEYS = new Set([
  "testresult",
  "testresults",
  "teststatus",
  "teststatuses",
  "exitcode",
  "passed",
  "failed",
]);

function hash(value: unknown): string {
  try {
    return createHash("sha256").update(JSON.stringify(value)).digest("hex");
  } catch {
    return String(value);
  }
}

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function primitiveValues(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(primitiveValues);
  if (["string", "number", "boolean"].includes(typeof value)) return [String(value)];
  return [];
}

type StructuredProgress = {
  actionIds: Set<string>;
  resultPages: Set<string>;
  filePaths: Set<string>;
  testResults: Set<string>;
};

/** Extract only typed result fields. Free-form language never counts as progress. */
function extractStructuredProgress(value: unknown): StructuredProgress {
  const found: StructuredProgress = {
    actionIds: new Set(),
    resultPages: new Set(),
    filePaths: new Set(),
    testResults: new Set(),
  };
  const visited = new WeakSet<object>();
  const visit = (item: unknown): void => {
    if (!item || typeof item !== "object") return;
    if (visited.has(item)) return;
    visited.add(item);
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    for (const [rawKey, child] of Object.entries(item as Record<string, unknown>)) {
      const key = normalizedKey(rawKey);
      const values = primitiveValues(child);
      if (ACTION_KEYS.has(key)) values.forEach(entry => found.actionIds.add(entry));
      if (RESULT_PAGE_KEYS.has(key)) values.forEach(entry => found.resultPages.add(`${key}:${entry}`));
      if (FILE_PATH_KEYS.has(key)) values.forEach(entry => found.filePaths.add(entry));
      if (TEST_RESULT_KEYS.has(key)) found.testResults.add(`${key}:${hash(child)}`);
      visit(child);
    }
  };
  visit(value);
  return found;
}

function addNew(target: Set<string>, values: Set<string>): boolean {
  let changed = false;
  for (const value of values) {
    if (target.has(value)) continue;
    target.add(value);
    changed = true;
  }
  return changed;
}

function isValidationFailure(code: string): boolean {
  const normalized = normalizedKey(code);
  return normalized.includes("validation")
    || normalized.includes("invalidtoolargument")
    || normalized.includes("schemaerror");
}

function isMissingToolFailure(code: string): boolean {
  const normalized = normalizedKey(code);
  return normalized.includes("missingtool")
    || normalized.includes("toolmissing")
    || normalized.includes("unknowntool")
    || normalized.includes("toolnotfound");
}

export class PiStallGuard {
  private modelRequests = 0;
  private toolCalls = 0;
  private startedAt = Date.now();
  private seen = new Map<string, SeenObservation>();
  private consecutiveNoProgress = 0;
  private lastNewObservationAt: number | null = null;
  private observationHashes = new Set<string>();
  private uniqueActionIds = new Set<string>();
  private uniqueResultPages = new Set<string>();
  private uniqueFilePaths = new Set<string>();
  private uniqueTestResults = new Set<string>();
  private finalTextHashes = new Set<string>();
  private failureSignatures = new Set<string>();
  private repeatedValidationFailures = 0;
  private repeatedMissingToolCalls = 0;

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
    this.lastNewObservationAt = null;
    this.observationHashes.clear();
    this.uniqueActionIds.clear();
    this.uniqueResultPages.clear();
    this.uniqueFilePaths.clear();
    this.uniqueTestResults.clear();
    this.finalTextHashes.clear();
    this.failureSignatures.clear();
    this.repeatedValidationFailures = 0;
    this.repeatedMissingToolCalls = 0;
  }

  beforeModelRequest(): void {
    this.modelRequests += 1;
    this.assertBudget();
  }

  /**
   * Only a read-only, explicitly idempotent and non-mutating tool may reuse a
   * frontend observation. Other idempotent tools are re-executed; repeated
   * non-idempotent calls are intercepted to avoid duplicating side effects.
   */
  beforeTool(descriptor: StallGuardDescriptor): {key: string; cached?: SeenObservation; duplicate?: boolean} {
    this.toolCalls += 1;
    this.assertBudget();
    const key = `${descriptor.name}:${hash(descriptor.args)}`;
    const seen = this.seen.get(key);
    const cacheable = descriptor.permissionLevel === "read_only"
      && descriptor.idempotent === true
      && descriptor.mutatesState === false;
    if (cacheable && seen) return {key, cached: seen};
    if (!descriptor.idempotent && seen) return {key, duplicate: true};
    return {key};
  }

  /** Reuse a cached observation, tracking consecutive no-progress exactly once. */
  repeated(existing: SeenObservation): Record<string, unknown> {
    existing.repeats += 1;
    const stallGuard = this.bumpNoProgress();
    if (stallGuard === "stall_safe_termination") {
      return this.safeTerminationObservation(undefined, existing.observationHash);
    }
    return {
      ...existing.result,
      stallGuard,
      observationHash: existing.observationHash,
      repeats: existing.repeats,
    };
  }

  /** Intercept an equivalent repeat of a non-idempotent tool call. */
  duplicateObservation(name: string): Record<string, unknown> {
    const stallGuard = this.bumpNoProgress();
    if (stallGuard === "stall_safe_termination") return this.safeTerminationObservation(name);
    return {
      ok: false,
      status: "blocked",
      code: "duplicate_non_idempotent_tool_call",
      message: "该非幂等工具已被相同参数调用过，重复调用已被拦截。请复用已有结果或改用不同参数。",
      tool: name,
      stallGuard,
    };
  }

  /**
   * Record a freshly executed observation. The same Tool+Args+result advances
   * the repeat counter by exactly one; only new structured evidence resets it.
   */
  remember(key: string, result: Record<string, unknown>): SeenObservation {
    const observationHash = hash(result);
    const existing = this.seen.get(key);
    const sameObservation = existing?.observationHash === observationHash;
    const structured = extractStructuredProgress(result);
    let madeProgress = false;
    if (!this.observationHashes.has(observationHash)) {
      this.observationHashes.add(observationHash);
      madeProgress = true;
    }
    madeProgress = addNew(this.uniqueActionIds, structured.actionIds) || madeProgress;
    madeProgress = addNew(this.uniqueResultPages, structured.resultPages) || madeProgress;
    madeProgress = addNew(this.uniqueFilePaths, structured.filePaths) || madeProgress;
    madeProgress = addNew(this.uniqueTestResults, structured.testResults) || madeProgress;
    if (madeProgress) this.noteProgress();
    else this.bumpNoProgress();

    const observation: SeenObservation = {
      result,
      observationHash,
      repeats: sameObservation ? existing.repeats + 1 : 0,
    };
    this.seen.set(key, observation);
    return observation;
  }

  /** A new non-empty final answer is progress even when no tool was called. */
  noteFinalText(text: string): void {
    const value = text.trim();
    if (!value) return;
    const textHash = hash(value);
    if (this.finalTextHashes.has(textHash)) return;
    this.finalTextHashes.add(textHash);
    this.noteProgress();
  }

  /** Track repeated typed runtime failures without inspecting natural language. */
  noteFailure(code: string, context: Record<string, unknown> = {}): StallGuardStage | undefined {
    const signature = hash({code, context});
    if (!this.failureSignatures.has(signature)) {
      this.failureSignatures.add(signature);
      this.noteProgress();
      return undefined;
    }
    if (isValidationFailure(code)) this.repeatedValidationFailures += 1;
    if (isMissingToolFailure(code)) this.repeatedMissingToolCalls += 1;
    return this.bumpNoProgress();
  }

  safeTerminationObservation(tool?: string, lastObservationHash?: string): Record<string, unknown> {
    return {
      ok: false,
      status: "blocked",
      code: "stall_guard_safe_termination",
      message: "连续多次操作没有产生新信息，当前运行已安全停止。请基于现有观察给出结论，或在下一轮更换策略。",
      ...(tool ? {tool} : {}),
      ...(lastObservationHash ? {lastObservationHash} : {}),
      stallGuard: "stall_safe_termination",
    };
  }

  get progress(): RunProgress {
    return {
      lastNewObservationAt: this.lastNewObservationAt,
      uniqueObservationCount: this.observationHashes.size,
      uniqueActionIds: [...this.uniqueActionIds],
      uniqueResultPages: [...this.uniqueResultPages],
      consecutiveNoProgress: this.consecutiveNoProgress,
      repeatedValidationFailures: this.repeatedValidationFailures,
      repeatedMissingToolCalls: this.repeatedMissingToolCalls,
      modelRequests: this.modelRequests,
      toolCalls: this.toolCalls,
    };
  }

  private noteProgress(): void {
    this.consecutiveNoProgress = 0;
    this.lastNewObservationAt = Date.now();
  }

  private bumpNoProgress(): StallGuardStage {
    this.consecutiveNoProgress += 1;
    if (this.consecutiveNoProgress >= NO_PROGRESS_TERMINATE) return "stall_safe_termination";
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
