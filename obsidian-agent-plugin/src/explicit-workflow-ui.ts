/**
 * Presentation helpers for Prepared PDF/manual import/background jobs.
 * These states belong to explicitly selected legacy workflows and must never
 * be used to route an ordinary Assistant turn.
 */
const REVIEW_GATE_STATE = "awaiting_confirmation";

export function explicitWorkflowStatusLabel(state: string): string | null {
  return state === REVIEW_GATE_STATE ? "待确认" : null;
}

export function isExplicitWorkflowPending(state: string): boolean {
  return state === "prepared" || state === REVIEW_GATE_STATE;
}

export function explicitWorkflowStageIndex(stage: string): number {
  if (stage === "applied" || stage === "completed") return 5;
  if (stage === "prepared" || stage === REVIEW_GATE_STATE) return 3;
  if (stage === "running") return 2;
  if (stage === "queued") return 0;
  return 1;
}

export function isActiveExplicitWorkflowJob(state: string): boolean {
  return ["queued", "running", "prepared", REVIEW_GATE_STATE, "applying"].includes(state);
}
