from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "agent/runtime"


class RuntimeArchitectureBoundaryTests(unittest.TestCase):
    def test_runtime_does_not_import_or_consume_legacy_intent_router(self) -> None:
        banned = (
            "classify_assistant_intent",
            "intent_router",
            'prepared["intent"]',
            'prepared.get("intent")',
            "write_requested",
            "commit_required",
            "SAVE_MARKERS",
            "NO_SAVE_MARKERS",
        )
        for path in RUNTIME.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, source, f"{path.name} contains banned runtime token {token}")

    def test_runtime_has_no_dependency_on_legacy_context_material_module(self) -> None:
        for path in RUNTIME.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertFalse(
                any("context_material" in item for item in imports),
                f"{path.name} imports legacy context_material",
            )

    def test_model_layer_cannot_apply_change_sets_directly(self) -> None:
        source = (RUNTIME / "agent_factory.py").read_text(encoding="utf-8")
        self.assertNotIn("execute_plan(", source)
        self.assertNotIn("write_text(", source)
        self.assertNotIn("change_sets.apply", source)
        self.assertIn("ctx.deps.apply_validated_change_set", source)


if __name__ == "__main__":
    unittest.main()
