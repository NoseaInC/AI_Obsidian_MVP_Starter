from __future__ import annotations

import uuid
from typing import Any

from agent.compat.context_builder import ContextBuilder
from agent.errors import BrainError, as_brain_error
from agent.core.compat_schemas import BrainRequest, BrainStatus, IntentResult
from agent.compat.verifier import Verifier


EXPLICIT_WORKFLOWS: dict[str, tuple[str, str]] = {
    "qa": ("ask_question", "tutor_topic"),
    "question": ("ask_question", "tutor_topic"),
    "tutor": ("learn_topic", "tutor_topic"),
    "capture": ("capture_text", "capture_text"),
    "save": ("capture_text", "capture_text"),
    "organize": ("organize_text", "organize_text"),
    "material": ("organize_material", "import_material"),
    "research": ("research_topic", "research_topic"),
    "plan": ("create_study_plan", "curriculum_planner"),
}


class StructuredWorkflowRunner:
    """Run one explicitly selected business workflow.

    This adapter exists for non-chat product actions such as the study tutor
    and curriculum refresh.  It never classifies prose, chooses tools, loops,
    or replans.  Pi remains the only production Agent loop.
    """

    version = "structured-workflow/1"

    def __init__(self, vault: Any, store: Any, skills: Any) -> None:
        self.vault = vault.resolve()
        self.store = store
        self.skills = skills
        self.context = ContextBuilder(self.vault)
        self.verifier = Verifier()

    def submit(self, request: BrainRequest, *, retry_of: str | None = None) -> dict[str, Any]:
        if request.idempotency_key:
            existing = self.store.find_brain_run_by_idempotency(request.idempotency_key)
            if existing:
                return self.store.get_brain_run(existing, include_details=True)
        mode = str(request.mode or "").casefold()
        if mode not in EXPLICIT_WORKFLOWS:
            raise ValueError("explicit_workflow_mode_required")
        intent_name, skill = EXPLICIT_WORKFLOWS[mode]
        if not self.skills.has(skill):
            raise ValueError("structured_workflow_not_registered")
        run_id = f"run-{uuid.uuid4().hex}"
        step_id = f"workflow-step-{uuid.uuid4().hex[:12]}"
        intent = IntentResult(intent_name, basis="explicit-workflow")
        self.store.create_brain_run(run_id, request, retry_of=retry_of)
        try:
            self.store.set_brain_intent(run_id, intent)
            context = self.context.build(request, intent)
            definition = self.skills.definition(skill)
            self.store.start_brain_step(run_id, step_id, 1, skill, definition.description)
            self.store.update_brain_run(run_id, BrainStatus.RUNNING.value)
            result = self.skills.execute(skill, request.public(), context, run_id=run_id, step_id=step_id)
            result = self.verifier.verify(skill, request.text, result, self.vault)
            self.store.complete_brain_step(step_id)
            self.store.finish_brain_run(run_id, BrainStatus.COMPLETED.value, {
                "results": [result],
                "reflection": {"step_count": 1, "completed_steps": 1},
                "context_manifest": context.get("context_manifest", []),
            })
        except Exception as error:
            failure = as_brain_error(error, request.correlation_id)
            self.store.fail_brain_run(run_id, BrainStatus.FAILED.value, failure)
        return self.store.get_brain_run(run_id, include_details=True)

    def retry(self, run_id: str) -> dict[str, Any]:
        prior = self.store.get_brain_run(run_id, include_details=True)
        if prior["status"] not in {BrainStatus.FAILED.value, BrainStatus.CANCELLED.value}:
            raise ValueError("workflow_retry_not_allowed")
        request = BrainRequest.from_dict(self.store.load_brain_request(run_id))
        request = BrainRequest(**{
            **request.__dict__,
            "request_id": uuid.uuid4().hex,
            "correlation_id": prior["correlation_id"],
            "idempotency_key": "",
        })
        return self.submit(request, retry_of=run_id)
