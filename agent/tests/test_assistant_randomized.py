from __future__ import annotations

import os
import random
import tempfile
import unittest
from pathlib import Path

from agent.core.context_material import ConversationFocusService
from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.core.vault_autonomy import VaultAutonomyService
from agent.tools.source_fetch import validate_public_url


SEED = int(os.environ.get("LA_TEST_SEED", "20260715"))


class AssistantRandomizedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        for relative in ("01-Inbox", "20-Knowledge/Concepts", "90-Local-Only/Agent"):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        self.service = AgentService(self.vault, key_store=FakeKeyStore())
        self.random = random.Random(SEED)

    def tearDown(self) -> None:
        self.service.store.close()
        self.temp.cleanup()

    def test_3000_multiturn_focus_cases_are_scoped_and_non_hallucinatory(self) -> None:
        conversations = [self.service.create_conversation({"title": f"随机上下文 {index}"})["id"] for index in range(3)]
        resolver = ConversationFocusService(self.service.store)
        expected = {conversations[0]: "Delta Method", conversations[1]: "Bootstrap Method", conversations[2]: "Score Method"}
        for conversation_id, name in expected.items():
            focus = resolver.resolve({
                "conversationId": conversation_id, "currentMessage": {"id": f"init-{conversation_id}", "content": f"介绍一下 {name}"},
                "attachments": [], "currentNote": {},
            })
            self.assertEqual(focus["activeMethod"]["canonicalName"], name, f"seed={SEED}")
        pronouns = ["继续讲这个方法", "它有什么限制", "刚才那个再举例", "前面说的是什么", "继续推导", "第二个条件为什么需要"]
        for index in range(3_000):
            conversation_id = conversations[index % len(conversations)]
            phrase = self.random.choice(pronouns)
            focus = resolver.resolve({
                "conversationId": conversation_id, "currentMessage": {"id": f"turn-{index}", "content": phrase},
                "attachments": [], "currentNote": {},
            })
            resolved = (focus.get("resolution") or {}).get("resolvedReference")
            # “它” is intentionally not treated as a resolvable marker by the
            # bounded resolver; it must keep focus without inventing an entity.
            self.assertEqual(focus["activeMethod"]["canonicalName"], expected[conversation_id], f"seed={SEED}; turn={index}; phrase={phrase}")
            if resolved:
                self.assertEqual(resolved["canonicalName"], expected[conversation_id], f"seed={SEED}; turn={index}")
        unknown = self.service.create_conversation({"title": "无上下文"})["id"]
        unresolved = resolver.resolve({
            "conversationId": unknown, "currentMessage": {"id": "unknown", "content": "把这个方法保存"},
            "attachments": [], "currentNote": {},
        })
        self.assertTrue(unresolved["resolution"]["requiresConfirmation"], f"seed={SEED}")
        self.assertIsNone(unresolved["resolution"]["resolvedReference"], f"seed={SEED}")

    def test_2000_attachment_inputs_deduplicate_and_never_escape_temp_store(self) -> None:
        conversation = self.service.create_conversation({"title": "附件随机测试"})
        unique_ids: set[str] = set()
        for index in range(2_000):
            variant = index % 50
            body = f"fixture-{variant}-seed-{SEED}".encode()
            attachment = self.service.create_attachment(
                {"conversation_id": conversation["id"], "display_name": f"材料 {variant}.txt", "kind": "text"},
                body, "text/plain",
            )
            unique_ids.add(attachment["id"])
            self.assertTrue((attachment["sha256"]), f"seed={SEED}; index={index}")
        self.assertEqual(len(unique_ids), 50, f"seed={SEED}")
        self.assertEqual(len(list((self.vault / "90-Local-Only/Agent/Attachments").rglob("*.txt"))), 50, f"seed={SEED}")
        with self.assertRaises(ValueError):
            self.service.create_attachment(
                {"conversation_id": conversation["id"], "display_name": "伪 PDF.pdf", "kind": "pdf"},
                b"not-a-pdf", "application/pdf",
            )

    def test_2000_url_cases_reject_ssrf_and_accept_only_public_http(self) -> None:
        public = lambda host: ["93.184.216.34"]
        private = lambda host: ["127.0.0.1"]
        for index in range(2_000):
            kind = index % 8
            if kind == 0:
                value, resolver, allowed = f"https://example.com/p/{index}", public, True
            elif kind == 1:
                value, resolver, allowed = "http://localhost/admin", public, False
            elif kind == 2:
                value, resolver, allowed = "http://169.254.169.254/latest/meta-data", private, False
            elif kind == 3:
                value, resolver, allowed = "file:///etc/passwd", public, False
            elif kind == 4:
                value, resolver, allowed = "ftp://example.com/file", public, False
            elif kind == 5:
                value, resolver, allowed = "https://metadata.google.internal/compute", public, False
            elif kind == 6:
                value, resolver, allowed = f"https://docs.example.org/{index}?q=x", public, True
            else:
                value, resolver, allowed = "https://user:pass@example.com/private", public, False
            if allowed:
                self.assertEqual(validate_public_url(value, resolver), value, f"seed={SEED}; index={index}")
            else:
                with self.assertRaises(ValueError, msg=f"seed={SEED}; index={index}; url={value}"):
                    validate_public_url(value, resolver)

    def test_2000_vault_write_inputs_preserve_boundaries_hashes_and_undo(self) -> None:
        autonomy = VaultAutonomyService(self.vault, self.service.store)
        applied = 0
        for index in range(2_000):
            kind = index % 25
            if kind == 0:
                relative = f"01-Inbox/random-{index}.md"
                content = f"---\nstatus: ai-draft\nagent_managed: true\n---\n\n# 随机 {index}\n\nseed {SEED}\n"
                result = autonomy.apply_low_risk({"path": relative, "operation": "create_draft_note", "content": content, "reason": "随机边界测试"})
                self.assertTrue(result["applied"], f"seed={SEED}; index={index}")
                self.assertNotEqual(result["beforeHash"], result["afterHash"], f"seed={SEED}; index={index}")
                undo = autonomy.undo(result["actionId"])
                self.assertEqual(undo["state"], "undone", f"seed={SEED}; index={index}")
                self.assertFalse((self.vault / relative).exists(), f"seed={SEED}; index={index}")
                applied += 1
            else:
                invalid = self.random.choice([f"../escape-{index}.md", f"/tmp/escape-{index}.md", f"Private/secret-{index}.md"])
                with self.assertRaises((ValueError, PermissionError), msg=f"seed={SEED}; index={index}; path={invalid}"):
                    autonomy.classify(invalid, allow_missing=True)
        self.assertEqual(applied, 80, f"seed={SEED}")


if __name__ == "__main__":
    unittest.main()
