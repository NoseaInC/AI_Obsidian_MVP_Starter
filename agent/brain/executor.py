from __future__ import annotations

from typing import Any

from .errors import BrainError
from .schemas import BrainPlan


class Executor:
    def __init__(self, registry: Any, policy: Any, verifier: Any, store: Any) -> None:
        self.registry, self.policy, self.verifier, self.store = registry, policy, verifier, store

    def execute(self, run_id: str, plan: BrainPlan, payload: dict[str, Any], context: dict[str, Any], vault: Any) -> tuple[list[dict[str, Any]], bool]:
        results: list[dict[str, Any]] = []
        needs_confirmation = False
        for ordinal, step in enumerate(plan.steps, 1):
            if self.store.brain_cancel_requested(run_id):
                raise BrainError("brain_cancelled", "任务已取消")
            definition = self.registry.definition(step.skill)
            decision = self.policy.decide(step.skill, payload, definition)
            if not decision.allowed:
                raise BrainError("brain_policy_denied", decision.reason)
            self.store.start_brain_step(run_id, step.step_id, ordinal, step.skill, step.purpose)
            try:
                result = self.registry.execute(step.skill, payload, context, run_id=run_id, step_id=step.step_id)
                result = self.verifier.verify(step.skill, str(payload.get("text", "")), result, vault)
                result["policy"] = decision.to_dict()
                self.store.complete_brain_step(step.step_id)
                results.append(result)
                needs_confirmation = needs_confirmation or decision.requires_confirmation or bool(result.get("change_set"))
            except Exception as error:
                self.store.fail_brain_step(step.step_id, getattr(error, "code", type(error).__name__))
                raise
        return results, needs_confirmation
