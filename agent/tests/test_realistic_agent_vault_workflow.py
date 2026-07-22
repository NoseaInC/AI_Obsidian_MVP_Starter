from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.core.service import AgentService


class _ScriptedResearchAgent:
    """A deterministic stand-in for the model; every side effect uses real tools."""

    def __init__(self, service: AgentService, authorization_id: str, run_id: str) -> None:
        self.service = service
        self.authorization_id = authorization_id
        self.run_id = run_id
        self.turn_id = "turn-research"
        self.source_message_id = "message-research"
        self.call_sequence = 0
        self.permission_requests: list[dict[str, Any]] = []

    def call(self, name: str, arguments: dict[str, Any], *, call_id: str | None = None) -> dict[str, Any]:
        self.call_sequence += 1
        result = self.service.call_runtime_tool({
            "toolName": name,
            "arguments": arguments,
            "runId": self.run_id,
            "turnId": self.turn_id,
            "toolCallId": call_id or f"call-{self.call_sequence}",
            "taskAuthorizationId": self.authorization_id,
            "sourceMessageId": self.source_message_id,
            "networkAuthorized": False,
        })
        request = dict((result.get("error") or {}).get("permissionRequest") or {})
        if request:
            self.permission_requests.append(request)
        return result

    def grant(self, blocked: dict[str, Any], *, mode: str = "once") -> dict[str, Any]:
        request = dict((blocked.get("error") or {}).get("permissionRequest") or {})
        self.assert_permission_request(request)
        payload: dict[str, Any] = {"runId": self.run_id, "mode": mode}
        if request.get("writes"):
            payload["writes"] = list(request["writes"])
        elif request.get("organization"):
            payload["organization"] = dict(request["organization"])
        else:
            payload["capability"] = request
        return self.service.expand_task_authorization(self.authorization_id, payload)

    @staticmethod
    def assert_permission_request(request: dict[str, Any]) -> None:
        if not request or not request.get("type"):
            raise AssertionError(f"missing typed permissionRequest: {request!r}")


class RealisticAgentVaultWorkflowTests(unittest.TestCase):
    """A temp-Vault acceptance test for the complete research-to-write loop."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name) / "Vault"
        for relative in (
            "01-Inbox/Research",
            "10-Sources/Research",
            "20-Knowledge/Topics",
            "20-Knowledge/Concepts",
            "90-Local-Only/Agent",
        ):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        (self.vault / "01-Inbox/Research/MPNN-概览.md").write_text(
            "# MPNN 概览\n\n消息传递由 message、aggregate、update 三步组成。\n\n来源：https://arxiv.org/abs/1704.01212\n",
            encoding="utf-8",
        )
        (self.vault / "01-Inbox/Research/GNN-读书摘录.md").write_text(
            "# GNN 读书摘录\n\n节点表示通过邻居信息的迭代聚合进行更新。\n\n来源：Gilmer et al. (2017)\n",
            encoding="utf-8",
        )
        (self.vault / "10-Sources/Papers/Unsorted").mkdir(parents=True, exist_ok=True)
        (self.vault / "10-Sources/Papers/Unsorted/MPNN-来源索引.md").write_text(
            "---\ntype: source-index\nstatus: ai-draft\n---\n\n"
            "# Neural Message Passing for Quantum Chemistry\n\n"
            "来源：https://arxiv.org/abs/1704.01212\n",
            encoding="utf-8",
        )
        self.service = AgentService(self.vault)
        self.run_id = "run-realistic-research"
        self.authorization_id = "authorization-realistic-research"
        self.service.register_task_authorization({
            "conversationId": "conversation-realistic-research",
            "taskAuthorization": {
                "id": self.authorization_id,
                "sessionId": "session-realistic-research",
                "runId": self.run_id,
                "turnId": "turn-research",
                "sourceMessageId": "message-research",
                "objective": "研究消息传递神经网络，整理已有资料并写入 Obsidian",
                "resourceScope": {
                    "currentNote": False,
                    "explicitVaultPaths": [],
                    "createRoots": [],
                    "workspaceIds": [],
                    "projectPaths": [],
                },
                "operationScope": [],
                "reversibleOnly": True,
                "networkPolicy": "deny",
                "externalSideEffects": False,
                "expiresAtRunEnd": True,
            },
        })
        self.agent = _ScriptedResearchAgent(
            self.service, self.authorization_id, self.run_id,
        )

    def tearDown(self) -> None:
        self.service.store.close()
        self.temporary.cleanup()

    @staticmethod
    def _content(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("content")
        if not isinstance(value, dict):
            raise AssertionError(f"tool did not return object content: {result!r}")
        return value

    def _research_evidence(self) -> list[str]:
        listed = self.agent.call(
            "list_vault_folder",
            {"path": "01-Inbox/Research", "recursive": True, "limit": 20},
        )
        self.assertTrue(listed["ok"], listed)
        items = list(self._content(listed).get("items") or [])
        paths = [str(item["path"]) for item in items if str(item.get("path") or "").startswith("01-Inbox/Research/")]
        self.assertEqual(set(paths), {
            "01-Inbox/Research/MPNN-概览.md",
            "01-Inbox/Research/GNN-读书摘录.md",
        })
        for path in paths:
            read = self.agent.call("read_vault_note", {"path": path, "max_chars": 50_000})
            self.assertTrue(read["ok"], read)
            self.assertFalse(self._content(read).get("truncated"), read)
        return sorted(paths)

    def test_research_permission_resume_nested_write_move_and_undo(self) -> None:
        source_paths = self._research_evidence()
        topic_path = "20-Knowledge/Topics/图神经网络/消息传递神经网络.md"
        topic_body = (
            "---\nstatus: ai-draft\nsource_paths:\n"
            "  - 01-Inbox/Research/MPNN-概览.md\n"
            "  - 01-Inbox/Research/GNN-读书摘录.md\n---\n\n"
            "# 消息传递神经网络\n\n"
            "MPNN 将邻居消息的计算、聚合与节点状态更新组织为可复用框架。\n\n"
            "## 证据\n\n- [[MPNN-概览]]\n- [[GNN-读书摘录]]\n"
        )

        plan_args = {
            "title": "整理消息传递神经网络",
            "writes": [{"path": topic_path, "content": topic_body, "category": "topic"}],
        }
        blocked_plan = self.agent.call("plan_vault_change", plan_args, call_id="call-plan-topic")
        self.assertFalse(blocked_plan["ok"])
        self.assertEqual(blocked_plan["error"]["permissionRequest"]["type"], "vault_writes")
        self.assertFalse((self.vault / topic_path).exists())
        self.agent.grant(blocked_plan, mode="once")

        # The original tool call is retried in the same run after the inline grant.
        planned = self.agent.call("plan_vault_change", plan_args, call_id="call-plan-topic")
        self.assertTrue(planned["ok"], planned)
        change_set_id = str(self._content(planned)["id"])
        applied = self.agent.call(
            "apply_vault_change", {"change_set_id": change_set_id}, call_id="call-apply-topic",
        )
        self.assertTrue(applied["ok"], applied)
        topic_action_id = str(self._content(applied)["actionId"])
        self.assertTrue((self.vault / topic_path).is_file())
        combined_sources = "".join(
            (self.vault / source).read_text(encoding="utf-8") for source in source_paths
        )
        self.assertIn("消息传递由 message", combined_sources)

        moves = [
            {
                "source_path": source,
                "target_path": f"20-Knowledge/Drafts/图神经网络/资料/{Path(source).name}",
            }
            for source in source_paths
        ]
        organize_args = {
            "title": "归档消息传递神经网络资料",
            "moves": moves,
            "remove_empty_source_dirs": True,
        }
        blocked_move = self.agent.call("organize_vault_notes", organize_args, call_id="call-organize")
        self.assertFalse(blocked_move["ok"])
        request = blocked_move["error"]["permissionRequest"]
        self.assertEqual(request["type"], "vault_organization")
        self.assertEqual(request["toolName"], "organize_vault_notes")
        self.assertEqual(len(request["organization"]["moves"]), 2)
        self.assertIn(moves[0]["source_path"], request["summary"])
        self.assertIn(moves[0]["target_path"], request["summary"])
        self.agent.grant(blocked_move, mode="once")

        organized = self.agent.call("organize_vault_notes", organize_args, call_id="call-organize")
        self.assertTrue(organized["ok"], organized)
        move_action_id = str(self._content(organized)["actionId"])
        for move in moves:
            self.assertFalse((self.vault / move["source_path"]).exists())
            self.assertTrue((self.vault / move["target_path"]).is_file())
        self.assertFalse((self.vault / "01-Inbox/Research").exists())

        # No manual mkdir/mv step is part of the workflow, and every action is reversible.
        undo_move = self.service.undo_agent_action(move_action_id)["action"]
        self.assertEqual(undo_move["state"], "undone")
        for move in moves:
            self.assertTrue((self.vault / move["source_path"]).is_file())
            self.assertFalse((self.vault / move["target_path"]).exists())
        undo_topic = self.service.undo_agent_action(topic_action_id)["action"]
        self.assertEqual(undo_topic["state"], "undone")
        self.assertFalse((self.vault / topic_path).exists())

    def test_non_reviewed_source_index_can_move_to_a_new_sources_subdirectory(self) -> None:
        source = "10-Sources/Papers/Unsorted/MPNN-来源索引.md"
        target = "10-Sources/Papers/Graph-Neural-Networks/MPNN-来源索引.md"
        arguments = {
            "title": "整理图神经网络论文来源索引",
            "moves": [{"source_path": source, "target_path": target}],
            "remove_empty_source_dirs": True,
        }
        blocked = self.agent.call("organize_vault_notes", arguments, call_id="call-source-index")
        self.assertFalse(blocked["ok"], blocked)
        request = blocked["error"]["permissionRequest"]
        self.assertEqual(request["toolName"], "organize_vault_notes")
        self.assertIn(f"{source} → {target}", request["summary"])
        self.agent.grant(blocked)
        moved = self.agent.call("organize_vault_notes", arguments, call_id="call-source-index")
        self.assertTrue(moved["ok"], moved)
        action_id = str(self._content(moved)["actionId"])
        self.assertFalse((self.vault / source).exists())
        self.assertTrue((self.vault / target).is_file())
        self.assertFalse((self.vault / "10-Sources/Papers/Unsorted").exists())
        self.service.undo_agent_action(action_id)
        self.assertTrue((self.vault / source).is_file())
        self.assertFalse((self.vault / target).exists())

    def test_denied_scope_leaves_the_vault_byte_for_byte_unchanged(self) -> None:
        before = {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in self.vault.rglob("*.md")
        }
        blocked = self.agent.call("organize_vault_notes", {
            "title": "不批准的归档",
            "moves": [{
                "source_path": "01-Inbox/Research/MPNN-概览.md",
                "target_path": "20-Knowledge/Concepts/拒绝/MPNN-概览.md",
            }],
            "remove_empty_source_dirs": True,
        })
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["permissionRequest"]["type"], "vault_organization")
        after = {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in self.vault.rglob("*.md")
        }
        self.assertEqual(after, before)
        self.assertFalse((self.vault / "20-Knowledge/Concepts/拒绝").exists())

    def test_reviewed_or_core_notes_can_never_be_moved_even_with_allow_all(self) -> None:
        protected = self.vault / "20-Knowledge/Concepts/正式知识.md"
        protected.write_text("---\nstatus: reviewed\n---\n\n# 正式知识\n", encoding="utf-8")
        attempted_target = "20-Knowledge/Drafts/重组/正式知识.md"
        blocked = self.agent.call("organize_vault_notes", {
            "title": "不得移动正式知识",
            "moves": [{
                "source_path": "20-Knowledge/Concepts/正式知识.md",
                "target_path": attempted_target,
            }],
        })
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "reviewed_core_read_only")
        self.assertNotIn("permissionRequest", blocked["error"])
        self.assertTrue(protected.is_file())
        self.assertFalse((self.vault / attempted_target).exists())

    def test_allow_all_is_scoped_to_one_run_and_protected_notes_remain_read_only(self) -> None:
        first_args = {
            "title": "建立本轮授权请求",
            "writes": [{
                "path": "20-Knowledge/Topics/图神经网络/本轮.md",
                "content": "# 本轮",
            }],
        }
        blocked = self.agent.call("plan_vault_change", first_args)
        self.assertFalse(blocked["ok"])
        self.agent.grant(blocked, mode="all")
        planned = self.agent.call("plan_vault_change", first_args)
        self.assertTrue(planned["ok"], planned)

        protected = self.vault / "20-Knowledge/Concepts/已审核.md"
        protected.write_text("---\nstatus: reviewed\n---\n\n# 已审核\n", encoding="utf-8")
        denied = self.agent.call("plan_vault_change", {
            "title": "不得覆盖",
            "writes": [{"path": "20-Knowledge/Concepts/已审核.md", "content": "覆盖"}],
        })
        self.assertFalse(denied["ok"])
        self.assertEqual(protected.read_text(encoding="utf-8"), "---\nstatus: reviewed\n---\n\n# 已审核\n")

        self.service.store.complete_pi_run(self.run_id, "completed")
        late = self.agent.call("plan_vault_change", {
            "title": "运行结束后不可沿用",
            "writes": [{"path": "20-Knowledge/Topics/图神经网络/越权.md", "content": "# 越权"}],
        })
        self.assertFalse(late["ok"])
        self.assertEqual(late["error"]["code"], "task_authorization_expired")
        self.assertFalse((self.vault / "20-Knowledge/Topics/图神经网络/越权.md").exists())


if __name__ == "__main__":
    unittest.main()
