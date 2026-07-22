from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.brain.schemas import BrainRequest
from agent.core.storage import StateStore
from agent.core.structured_workflow import StructuredWorkflowRunner


class _Skills:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def has(self, name: str) -> bool:
        return name in {"tutor_topic", "capture_text"}

    def definition(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(description=f"explicit {name}")

    def execute(self, name: str, request: dict, context: dict, **_: object) -> dict:
        self.calls.append((name, request["text"]))
        if name == "capture_text":
            return {
                "kind": "capture",
                "original_text": request["text"],
                "proposed_notes": [{
                    "path": "01-Inbox/Ideas/capture.md",
                    "content": request["text"],
                }],
            }
        return {"kind": "tutor", "answer": "explicit tutor answer"}


class StructuredWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "01-Inbox/Ideas").mkdir(parents=True)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/state.sqlite3")
        self.skills = _Skills()
        self.runner = StructuredWorkflowRunner(self.vault, self.store, self.skills)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_free_form_auto_mode_is_rejected_instead_of_classified(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit_workflow_mode_required"):
            self.runner.submit(BrainRequest(text="帮我研究并写入这段内容", mode="auto"))
        self.assertEqual(self.skills.calls, [])

    def test_explicit_mode_executes_exactly_one_registered_workflow(self) -> None:
        run = self.runner.submit(BrainRequest(text="解释倾向得分", mode="tutor"))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.skills.calls, [("tutor_topic", "解释倾向得分")])
        self.assertEqual(len(run["steps"]), 1)
        self.assertEqual(run["primary_intent"], "learn_topic")

    def test_explicit_capture_preserves_original_without_applying_a_write(self) -> None:
        text = "原始内容必须逐字保留"
        run = self.runner.submit(BrainRequest(text=text, mode="capture"))
        result = run["result"]["results"][0]
        self.assertEqual(result["original_text"], text)
        self.assertFalse((self.vault / "01-Inbox/Ideas/capture.md").exists())

    def test_idempotency_returns_the_same_single_step_run(self) -> None:
        request = BrainRequest(text="解释倾向得分", mode="tutor", idempotency_key="same")
        first = self.runner.submit(request)
        second = self.runner.submit(request)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.skills.calls, [("tutor_topic", "解释倾向得分")])


if __name__ == "__main__":
    unittest.main()
