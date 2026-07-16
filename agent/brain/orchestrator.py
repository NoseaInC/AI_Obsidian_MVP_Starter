from __future__ import annotations

import uuid
from typing import Any

from .context_builder import ContextBuilder
from .errors import BrainError, as_brain_error
from .executor import Executor
from .intent_router import IntentRouter
from .memory_manager import MemoryManager
from .planner import Planner
from .policy_engine import PolicyEngine
from .reflection import Reflection
from .schemas import BrainRequest, BrainStatus
from .verifier import Verifier


class BrainOrchestrator:
    version = "1.0"

    def __init__(self, vault: Any, store: Any, skill_registry: Any, *, intent_classifier: Any = None) -> None:
        self.vault, self.store, self.skills = vault.resolve(), store, skill_registry
        self.router = IntentRouter(intent_classifier)
        self.context_builder = ContextBuilder(self.vault)
        self.planner = Planner(skill_registry)
        self.policy = PolicyEngine()
        self.verifier = Verifier()
        self.executor = Executor(skill_registry, self.policy, self.verifier, store)
        self.memory = MemoryManager(store)
        self.reflection = Reflection()

    def submit(self, request: BrainRequest, *, retry_of: str | None = None) -> dict[str, Any]:
        if request.idempotency_key:
            existing = self.store.find_brain_run_by_idempotency(request.idempotency_key)
            if existing:
                return self.store.get_brain_run(existing, include_details=True)
        run_id = f"run-{uuid.uuid4().hex}"
        self.store.create_brain_run(run_id, request, retry_of=retry_of)
        try:
            self.store.update_brain_run(run_id, BrainStatus.UNDERSTANDING.value)
            intent = self.router.route(request)
            self.store.set_brain_intent(run_id, intent)
            context = self.context_builder.build(request, intent)
            self.store.update_brain_run(run_id, BrainStatus.PLANNING.value)
            plan = self.planner.plan(request, intent)
            self.store.set_brain_plan(run_id, plan)
            self.store.update_brain_run(run_id, BrainStatus.AWAITING_AUTHORIZATION.value)
            self.store.update_brain_run(run_id, BrainStatus.RUNNING.value)
            results, needs_confirmation = self.executor.execute(run_id, plan, request.public(), context, self.vault)
            self.store.update_brain_run(run_id, BrainStatus.VERIFYING.value)
            reflection = self.reflection.summarize(plan.to_dict(), results)
            status = BrainStatus.AWAITING_CONFIRMATION if needs_confirmation else BrainStatus.COMPLETED
            self.store.finish_brain_run(run_id, status.value, {"results": results, "reflection": reflection, "context_manifest": context.get("context_manifest", [])})
        except Exception as error:
            failure = as_brain_error(error, request.correlation_id)
            status = BrainStatus.CANCELLED if failure.code == "brain_cancelled" else BrainStatus.FAILED
            self.store.fail_brain_run(run_id, status.value, failure)
        return self.store.get_brain_run(run_id, include_details=True)

    def cancel(self, run_id: str) -> dict[str, Any]:
        self.store.request_brain_cancel(run_id)
        return self.store.get_brain_run(run_id, include_details=True)

    def retry(self, run_id: str) -> dict[str, Any]:
        prior = self.store.get_brain_run(run_id, include_details=True)
        if prior["status"] not in {BrainStatus.FAILED.value, BrainStatus.CANCELLED.value}:
            raise BrainError("brain_retry_not_allowed", "只有失败或已取消任务可以重试")
        request = BrainRequest.from_dict(self.store.load_brain_request(run_id))
        request = BrainRequest(**{**request.__dict__, "request_id": uuid.uuid4().hex, "correlation_id": prior["correlation_id"], "idempotency_key": ""})
        return self.submit(request, retry_of=run_id)
