from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (ROOT / "agent" / "api", ROOT / "agent" / "core", ROOT / "agent" / "tools")


def production_python_files() -> list[Path]:
    return [path for root in PRODUCTION_ROOTS for path in root.rglob("*.py") if "__pycache__" not in path.parts]


class RuntimeArchitectureBoundaryTests(unittest.TestCase):
    def test_production_has_no_pydanticai_or_retired_coordinator(self) -> None:
        banned = ("pydantic_ai", "AssistantRunCoordinator", "PydanticAgentRuntime", "BrainOrchestrator", "IntentRouter", "classify_intent", "INTENT_SCHEMA")
        for path in production_python_files():
            source = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, source, f"{path.relative_to(ROOT)} contains retired token {token}")

    def test_production_has_no_keyword_intent_router(self) -> None:
        banned = ("decide_assistant_outcome", "classify_assistant_intent", "SAVE_MARKERS", "NO_SAVE_MARKERS", "_WRITE_TOKENS")
        for path in production_python_files():
            source = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, source, f"{path.relative_to(ROOT)} contains keyword intent routing")

    def test_retired_loop_and_intent_router_modules_are_absent(self) -> None:
        for relative in (
            "agent/brain/orchestrator.py", "agent/brain/intent_router.py",
            "agent/brain/planner.py", "agent/brain/executor.py",
        ):
            self.assertFalse((ROOT / relative).exists(), f"retired production path remains: {relative}")
        self.assertEqual(list((ROOT / "agent" / "runtime").glob("*.py")), [])
        server = (ROOT / "agent" / "api" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("/brain/", server)
        self.assertNotIn("/brain-change-sets", server)

    def test_pi_model_proxy_cannot_apply_change_sets(self) -> None:
        source = (ROOT / "agent" / "core" / "pi_model_proxy.py").read_text(encoding="utf-8")
        self.assertNotIn("apply_confirmed_change_set", source)
        self.assertNotIn("execute_plan(", source)
        self.assertNotIn("write_text(", source)

    def test_provider_reasoning_is_not_persisted_as_conversation_content(self) -> None:
        source = (ROOT / "agent" / "core" / "intake.py").read_text(encoding="utf-8")
        self.assertNotIn("reasoning_blocks", source)
        self.assertNotIn("reasoningBlocks", source)

    def test_legacy_change_set_tool_has_no_apply_entrypoint(self) -> None:
        source = (ROOT / "agent" / "tools" / "change_set.py").read_text(encoding="utf-8")
        self.assertNotIn("def apply(", source)
        self.assertNotIn("explicit_confirmation_required", source)

    def test_core_does_not_import_plugin_or_ui(self) -> None:
        for path in production_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertFalse(any("obsidian-agent-plugin" in item or ".views" in item for item in imports))

    def test_service_layer_isolated_from_legacy_brain(self) -> None:
        service = (ROOT / "agent" / "core" / "service.py").read_text(encoding="utf-8")
        banned = (
            "BrainModelGateway", "IntentResult", "StructuredWorkflowRunner",
            "AssistantOutcome", "resolve_assistant_outcome", "BrainRequest",
            "ContextMaterialCoordinator", "submit_intake", "precise_intent",
            "assistantIntent", "assistant_intent", "primary_intent",
        )
        for token in banned:
            self.assertNotIn(token, service, f"agent/core/service.py still references legacy Brain symbol {token}")

    def test_legacy_brain_logic_relocated_to_explicit_workflow_service(self) -> None:
        explicit = (ROOT / "agent" / "core" / "explicit_workflow_service.py").read_text(encoding="utf-8")
        for token in ("BrainModelGateway", "IntentResult", "BrainRequest", "submit_intake", "ContextMaterialCoordinator"):
            self.assertIn(token, explicit, f"explicit_workflow_service.py must own legacy Brain symbol {token}")

    def test_service_has_no_submit_intake_method(self) -> None:
        tree = ast.parse((ROOT / "agent" / "core" / "service.py").read_text(encoding="utf-8"), filename="service.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "submit_intake":
                self.fail("agent/core/service.py must not define submit_intake")

    def test_legacy_intake_helpers_live_only_in_explicit_workflow_service(self) -> None:
        service = (ROOT / "agent" / "core" / "service.py").read_text(encoding="utf-8")
        explicit = (ROOT / "agent" / "core" / "explicit_workflow_service.py").read_text(encoding="utf-8")
        for name in (
            "_artifacts_for_run", "_assistant_response", "_record_conversation_intelligence",
            "_requested_today_budget", "_requested_today_constraints", "_latest_write_status",
            "_intake_summary", "_conversation_title", "_assistant_task_title",
        ):
            self.assertNotIn(f"def {name}", service)
            self.assertIn(f"def {name}", explicit)

    def test_explicit_workflow_service_owns_legacy_paths(self) -> None:
        from agent.core.explicit_workflow_service import ExplicitWorkflowService
        for name in (
            "submit_intake", "submit_workflow", "apply_autonomous_vault_change",
            "save_research_bundle", "_complete_local_intake_answer",
        ):
            self.assertTrue(hasattr(ExplicitWorkflowService, name), f"ExplicitWorkflowService must own {name}")


if __name__ == "__main__":
    unittest.main()
