from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.core.service import AgentService
from agent.core.storage import StateStore


class PiSessionTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, store=self.store)
        self.authorization = {
            "id": "auth-tree-1",
            "sessionId": "session-tree-1",
            "runId": "run-tree-1",
            "turnId": "turn-tree-1",
            "sourceMessageId": "message-tree-1",
            "objective": "读取当前知识并回答",
            "resourceScope": {"currentNote": True, "explicitVaultPaths": ["20-Knowledge/Drafts/current.md"], "createRoots": ["01-Inbox"], "workspaceId": "", "projectPaths": []},
            "operationScope": ["read"],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        }
        self.service.register_task_authorization({
            "conversationId": "conversation-tree",
            "profileId": "profile-primary",
            "model": "fake",
            "providerAdapterVersion": "pi-model-proxy-v1",
            "taskAuthorization": self.authorization,
        })

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_append_only_tree_pairs_tools_and_replays_events(self) -> None:
        events = [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "tool_use", "id": "call-1", "name": "search_vault", "input": {"query": "因果"}, "status": "running"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "tool_result", "id": "call-1", "name": "search_vault", "result": {"items": [{"path": "20-Knowledge/x.md"}]}, "summary": "ok", "status": "completed"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 3, "type": "text", "content": "找到一条笔记"},
        ]
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        restored = self.service.pi_run_events("run-tree-1", 1)
        self.assertEqual([item["sequence"] for item in restored["items"]], [2, 3])
        session = self.service.pi_session("session-tree-1")["session"]
        types = [item["entry_type"] for item in session["entries"]]
        self.assertEqual(types, ["task_authorization", "tool_call", "tool_result", "message"])
        self.assertEqual(session["entries"][-1]["id"], session["state"]["currentLeafId"])
        tool_result = next(item for item in session["entries"] if item["entry_type"] == "tool_result")
        self.assertNotIn("items", tool_result["payload"])
        self.assertEqual(
            tool_result["payload"]["modelObservation"]["items"],
            [{"path": "20-Knowledge/x.md"}],
        )
        self.assertLessEqual(
            len(json.dumps(tool_result["payload"]["modelObservation"], ensure_ascii=False).encode()),
            12 * 1024,
        )
        self.assertEqual(tool_result["payload"]["callId"], "call-1")

    def test_steering_follow_up_and_cancel_are_durable(self) -> None:
        steering = self.service.control_pi_run("run-tree-1", {"type": "steering", "text": "只看统计推断"})
        follow_up = self.service.control_pi_run("run-tree-1", {"type": "follow_up", "text": "再给三道题"})
        self.assertEqual(steering["control"]["type"], "steering")
        self.assertEqual(follow_up["control"]["type"], "follow_up")
        result = self.service.cancel_pi_run("run-tree-1")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.store.get_pi_run("run-tree-1")["status"], "cancelled")
        entries = self.service.pi_session("session-tree-1")["session"]["entries"]
        self.assertEqual([item["entry_type"] for item in entries][-2:], ["steering", "follow_up"])

    def test_terminal_run_persists_idempotent_conversation_history_for_restart(self) -> None:
        events = [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "text", "content": "第一段。"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "text", "content": "第二段。"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 3, "type": "done", "status": "completed"},
        ]
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})

        payload = self.service.pi_session("session-tree-1")
        history = payload["history"]
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[0]["id"], "message-tree-1")
        self.assertEqual(history[0]["content"], "读取当前知识并回答")
        self.assertEqual(history[1]["id"], "pi-assistant-run-tree-1")
        self.assertEqual(history[1]["content"], "第一段。第二段。")

        metadata = {
            "piTrace": {
                "schemaVersion": 1,
                "runId": "run-tree-1",
                "status": "completed",
                "steps": [{"id": "tool:call-1", "status": "completed"}],
                "toolCalls": [{"id": "call-1", "tool": "search_vault", "status": "completed"}],
                "reasoningBlocks": [{"id": "provider-reasoning-0", "content": "本地推理"}],
            }
        }
        updated = self.service.update_message_metadata(
            "conversation-tree", "pi-assistant-run-tree-1", metadata,
        )
        self.assertNotIn("本地推理", json.dumps(updated["metadata"], ensure_ascii=False))
        self.assertEqual(updated["metadata"]["piTrace"]["reasoningBlocks"], [{
            "id": "provider-reasoning-0",
            "provider": "provider",
            "tokenCount": 1,
            "status": "completed",
        }])
        restored = self.service.intake.get_conversation("conversation-tree")["messages"][-1]
        self.assertEqual(restored["content"], "第一段。第二段。")
        self.assertEqual(restored["metadata"], updated["metadata"])

    def test_message_metadata_is_conversation_scoped_and_bounded(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "text", "content": "回答"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "done", "status": "completed"},
        ]})
        other = self.service.create_conversation({"title": "其他会话"})
        with self.assertRaisesRegex(ValueError, "conversation_message_not_found"):
            self.service.update_message_metadata(other["id"], "pi-assistant-run-tree-1", {"piTrace": {}})
        with self.assertRaisesRegex(ValueError, "message_metadata_too_large"):
            self.service.update_message_metadata(
                "conversation-tree", "pi-assistant-run-tree-1", {"payload": "x" * 1_000_001},
            )


    def test_partial_run_includes_assistant_content_in_tree(self):
        # Reuse the session/run created in setUp (no explicit session creation needed).
        self.service.append_pi_events(
            {"runId": "run-tree-1", "events": [
                {"type": "text", "sequence": 1, "content": "部分产出内容"},
            ]}
        )
        session = self.service.pi_session("session-tree-1")["session"]
        entries = session["entries"]
        message_entry = next(
            (item for item in entries if item["entry_type"] == "message" and "部分产出内容" in str(item["payload"].get("content", ""))),
            None,
        )
        self.assertIsNotNone(message_entry, "partial run assistant content must appear in the session tree")
        self.assertEqual(str(session["state"]["currentLeafId"]), str(message_entry["id"]))

    def test_projection_restores_complete_tool_pair_and_aggregates_text(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "先搜索。"},
            {"sequence": 2, "type": "tool_use", "id": "call-1", "name": "search_vault", "input": {"query": "因果"}, "status": "running"},
            {"sequence": 3, "type": "tool_result", "id": "call-1", "name": "search_vault", "result": {"result": {"next_cursor": "c2"}}, "summary": "找到一页", "status": "completed"},
            {"sequence": 4, "type": "text", "content": "第一段。"},
            {"sequence": 5, "type": "text", "content": "第二段。"},
            {"sequence": 6, "type": "done", "status": "completed"},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        self.assertEqual(projected["sessionId"], "session-tree-1")
        self.assertEqual(projected["leafId"], projected["entries"][-1]["id"])
        self.assertEqual([item["role"] for item in projected["messages"]], [
            "user", "assistant", "toolResult", "assistant",
        ])
        call = projected["messages"][1]["content"][-1]
        result = projected["messages"][2]
        self.assertEqual(call, {"type": "toolCall", "id": "call-1", "name": "search_vault", "arguments": {"query": "因果"}})
        self.assertEqual(result["toolCallId"], call["id"])
        self.assertEqual(result["toolName"], call["name"])
        self.assertEqual(result["details"]["modelObservation"], {"next_cursor": "c2"})
        self.assertEqual(projected["messages"][-1]["content"], [{"type": "text", "text": "第一段。第二段。"}])
        bounded = self.service.pi_session_projection("session-tree-1", upto_sequence=3)
        self.assertEqual(bounded["leafId"], "pi-entry-run-tree-1-000000000003")
        self.assertEqual([item["role"] for item in bounded["messages"]], ["user", "assistant", "toolResult"])

    def test_projection_restores_blocked_failed_and_completed_action_references(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "tool_use", "id": "blocked-1", "name": "write_note", "input": {"path": "01-Inbox/x.md"}},
            {"sequence": 2, "type": "tool_result", "id": "blocked-1", "name": "write_note", "result": {"result": {"code": "user_denied_permission"}}, "summary": "denied", "status": "blocked"},
            {"sequence": 3, "type": "tool_use", "id": "failed-1", "name": "search_vault", "input": {"query": "x"}},
            {"sequence": 4, "type": "tool_result", "id": "failed-1", "name": "search_vault", "result": {"result": {"code": "search_failed"}}, "summary": "failed", "status": "failed"},
            {"sequence": 5, "type": "tool_use", "id": "action-call", "name": "apply_vault_change", "input": {"change_set_id": "cs-1"}},
            {"sequence": 6, "type": "tool_result", "id": "action-call", "name": "apply_vault_change", "result": {"result": {"actionId": "action-done"}}, "summary": "applied", "status": "completed"},
            {"sequence": 7, "type": "action_result", "actionId": "action-done", "state": "completed"},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        results = [item for item in projected["messages"] if item["role"] == "toolResult"]
        self.assertEqual([item["details"]["status"] for item in results], ["blocked", "failed", "completed"])
        self.assertFalse(results[0]["isError"])
        self.assertTrue(results[1]["isError"])
        self.assertEqual(results[2]["details"]["actionId"], "action-done")
        self.assertEqual(projected["activeActions"], [{"id": "action-done", "status": "referenced"}])
        action_message = next(item for item in projected["messages"] if item.get("customType") == "persistedActionResult")
        self.assertEqual(action_message["details"]["state"], "completed")

    def test_projection_uses_parent_lineage_and_excludes_sibling_branch(self) -> None:
        auth_id = f"pi-entry-auth-{self.authorization['id']}"
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "当前分支"},
        ]})
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO pi_session_entries(
                     id, parent_id, timestamp, entry_type, session_id, run_id,
                     turn_id, sequence, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "pi-entry-sibling", auth_id, "2026-07-23T10:00:00+08:00", "message",
                    "session-tree-1", "run-tree-1", "turn-tree-1", 99,
                    json.dumps({"sequence": 99, "type": "text", "content": "兄弟分支"}, ensure_ascii=False),
                ),
            )
            self.store.connection.commit()
        current = self.service.pi_session_projection("session-tree-1")
        sibling = self.service.pi_session_projection("session-tree-1", leaf_id="pi-entry-sibling")
        self.assertNotIn("兄弟分支", json.dumps(current, ensure_ascii=False))
        self.assertNotIn("当前分支", json.dumps(sibling, ensure_ascii=False))
        self.assertIn("兄弟分支", json.dumps(sibling["messages"], ensure_ascii=False))

    def test_projection_includes_persisted_branch_metadata(self) -> None:
        forked = {
            **self.authorization,
            "id": "auth-tree-fork",
            "runId": "run-tree-fork",
            "turnId": "turn-tree-fork",
            "sourceMessageId": "message-tree-fork",
            "objective": "分支继续",
            "parentRunId": "run-tree-1",
            "forkedFromSequence": 1,
        }
        self.service.register_task_authorization({
            "conversationId": "conversation-tree",
            "model": "fake",
            "taskAuthorization": forked,
        })
        projected = self.service.pi_session_projection("session-tree-1", run_id="run-tree-fork")
        branch = projected["messages"][0]
        self.assertEqual(projected["branchId"], "run-tree-fork")
        self.assertEqual(branch["role"], "branchSummary")
        self.assertEqual(branch["fromId"], "run-tree-1")
        self.assertIn("event boundary 1", branch["summary"])

    def test_projection_restores_controls_and_interrupts_orphaned_call(self) -> None:
        self.service.control_pi_run("run-tree-1", {"type": "steering", "text": "只看主线"})
        self.service.control_pi_run("run-tree-1", {"type": "follow_up", "text": "完成后出题"})
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "tool_use", "id": "orphan-1", "name": "search_vault", "input": {"query": "x"}},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        controls = [item["customType"] for item in projected["messages"] if item["role"] == "custom"]
        self.assertEqual(controls[:2], ["steering", "follow_up"])
        interrupted = next(item for item in projected["messages"] if item["role"] == "toolResult")
        self.assertEqual(interrupted["toolCallId"], "orphan-1")
        self.assertEqual(interrupted["toolName"], "search_vault")
        self.assertEqual(interrupted["details"]["code"], "tool_call_interrupted_by_restart")

    def test_projection_excludes_reasoning_and_redacts_api_keys(self) -> None:
        secret = "sk-verysecret123456"
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "reasoning", "content": f"private reasoning {secret}"},
            {"sequence": 2, "type": "tool_use", "id": "call-secret", "name": "search_vault", "input": {"apiKey": secret, "accessToken": "opaque-value", "query": "safe"}},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        encoded = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("private reasoning", encoded)
        replay = json.dumps(self.service.pi_run_events("run-tree-1", 0), ensure_ascii=False)
        self.assertNotIn(secret, replay)
        self.assertNotIn("private reasoning", replay)
        self.assertIn("reasoning_status", replay)
        assistant = next(item for item in projected["messages"] if item["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["arguments"]["apiKey"], "[redacted]")
        self.assertEqual(assistant["content"][0]["arguments"]["accessToken"], "[redacted]")

    def test_fork_resolves_event_sequences_without_using_message_indexes(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 10, "type": "text", "content": "序号十，不是第十条消息。"},
            {"sequence": 20, "type": "tool_use", "id": "fork-call", "name": "search_vault", "input": {"query": "主线"}},
            {"sequence": 30, "type": "tool_result", "id": "fork-call", "name": "search_vault", "result": {"result": {"actionId": "action-fork"}}, "status": "completed"},
            {"sequence": 40, "type": "text", "content": "旧回答"},
            {"sequence": 50, "type": "done", "status": "completed"},
        ]})
        inside_pair = self.service.pi_fork_projection("run-tree-1", sequence=20)
        self.assertEqual(inside_pair["resolvedForkSequence"], 10)
        self.assertNotIn("fork-call", json.dumps(inside_pair["projection"]["messages"], ensure_ascii=False))

        after_result = self.service.pi_fork_projection("run-tree-1", sequence=30)
        roles = [message["role"] for message in after_result["projection"]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "toolResult"])
        self.assertEqual(after_result["completedActionIds"], ["action-fork"])
        self.assertEqual(after_result["sourceContext"], {
            "conversationId": "conversation-tree",
            "profileId": "profile-primary",
            "selectedModel": "fake",
            "providerAdapterVersion": "pi-model-proxy-v1",
            "activeNote": {"path": "20-Knowledge/Drafts/current.md"},
        })

    def test_tool_observation_is_secret_free_and_command_output_is_bounded(self) -> None:
        secret = "sk-supersecret123456"
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "tool_use", "id": "command-1", "name": "run_bash", "input": {"script": "pytest"}},
            {
                "sequence": 2,
                "type": "tool_result",
                "id": "command-1",
                "name": "run_bash",
                "result": {"result": {
                    "cwd": "task-worktree",
                    "exitCode": 0,
                    "stdout": f"tests passed {secret}\n" + ("line\n" * 10_000),
                    "apiKey": secret,
                }},
                "summary": "tests passed",
                "status": "completed",
            },
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        observation = next(
            item["details"]["modelObservation"]
            for item in projected["messages"]
            if item["role"] == "toolResult"
        )
        encoded = json.dumps(observation, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertEqual(observation["apiKey"], "[redacted]")
        self.assertIn("[truncated sha256=", observation["stdout"])
        self.assertLessEqual(len(encoded.encode()), 12 * 1024)
        replay = json.dumps(self.service.pi_run_events("run-tree-1", 0), ensure_ascii=False)
        self.assertNotIn(secret, replay)
        self.assertLess(len(replay.encode()), 16 * 1024)

    def test_cold_restart_fork_preserves_run_profile_identity(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "persisted source"},
        ]})
        database = self.store.path
        self.store.close()
        self.store = StateStore(database)
        self.service = AgentService(self.vault, store=self.store)
        forked = self.service.pi_fork_projection("run-tree-1")
        self.assertEqual(forked["sourceContext"]["profileId"], "profile-primary")
        self.assertEqual(forked["sourceContext"]["selectedModel"], "fake")
        self.assertEqual(
            forked["sourceContext"]["providerAdapterVersion"],
            "pi-model-proxy-v1",
        )

    def test_startup_migration_erases_historical_reasoning_payloads(self) -> None:
        secret_reasoning = "historical private chain"
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO pi_agent_events(
                     id, run_id, sequence, event_type, payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "legacy-reasoning-event",
                    "run-tree-1",
                    77,
                    "reasoning",
                    json.dumps({
                        "runId": "run-tree-1",
                        "conversationId": "conversation-tree",
                        "sequence": 77,
                        "type": "reasoning",
                        "phase": "delta",
                        "content": secret_reasoning,
                    }),
                    "2026-07-23T00:00:00+08:00",
                ),
            )
            self.store.connection.execute(
                """INSERT INTO pi_session_entries(
                     id, parent_id, timestamp, entry_type, session_id, run_id,
                     turn_id, sequence, payload_json
                   ) VALUES (?, NULL, ?, 'custom', ?, ?, ?, ?, ?)""",
                (
                    "legacy-reasoning-entry",
                    "2026-07-23T00:00:00+08:00",
                    "session-tree-1",
                    "run-tree-1",
                    "turn-tree-1",
                    77,
                    json.dumps({"type": "thinking_delta", "thinking": secret_reasoning}),
                ),
            )
            self.store.connection.execute(
                """INSERT INTO pi_agent_events(
                     id, run_id, sequence, event_type, payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "legacy-tool-event",
                    "run-tree-1",
                    78,
                    "tool_result",
                    json.dumps({
                        "runId": "run-tree-1",
                        "sequence": 78,
                        "type": "tool_result",
                        "id": "legacy-call",
                        "name": "search_vault",
                        "status": "completed",
                        "summary": "工具已完成",
                        "result": {"result": {
                            "items": [{"path": "20-Knowledge/legacy.md", "excerpt": "legacy observation"}],
                        }},
                    }),
                    "2026-07-23T00:00:01+08:00",
                ),
            )
            self.store.connection.execute(
                """INSERT INTO pi_session_entries(
                     id, parent_id, timestamp, entry_type, session_id, run_id,
                     turn_id, sequence, payload_json
                   ) VALUES (?, NULL, ?, 'tool_result', ?, ?, ?, ?, ?)""",
                (
                    "legacy-tool-entry",
                    "2026-07-23T00:00:01+08:00",
                    "session-tree-1",
                    "run-tree-1",
                    "turn-tree-1",
                    78,
                    json.dumps({
                        "sequence": 78,
                        "callId": "legacy-call",
                        "tool": "search_vault",
                        "status": "completed",
                        "summary": "工具已完成",
                    }),
                ),
            )
            self.store.connection.commit()
        database = self.store.path
        self.store.close()
        self.store = StateStore(database)
        self.service = AgentService(self.vault, store=self.store)
        with self.store.lock:
            raw = "\n".join(
                str(row[0])
                for row in self.store.connection.execute(
                    "SELECT payload_json FROM pi_agent_events "
                    "UNION ALL SELECT payload_json FROM pi_session_entries"
                ).fetchall()
            )
        self.assertNotIn(secret_reasoning, raw)
        self.assertIn("reasoning_status", raw)
        with self.store.lock:
            migrated = self.store.connection.execute(
                "SELECT payload_json FROM pi_session_entries WHERE id='legacy-tool-entry'"
            ).fetchone()
        payload = json.loads(migrated["payload_json"])
        self.assertEqual(
            payload["modelObservation"]["items"][0]["path"],
            "20-Knowledge/legacy.md",
        )

    def test_regenerate_drops_old_answer_and_future_pending_but_keeps_completed_fact(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 10, "type": "tool_use", "id": "fact-call", "name": "search_vault", "input": {"query": "fact"}},
            {"sequence": 20, "type": "tool_result", "id": "fact-call", "name": "search_vault", "result": {"result": {"actionId": "action-complete"}}, "status": "completed"},
            {"sequence": 30, "type": "text", "content": "需要重新生成的旧回答"},
            {"sequence": 40, "type": "tool_use", "id": "future-call", "name": "plan_vault_change", "input": {"title": "future", "writes": [{"path": "01-Inbox/future.md", "content": "future"}]}},
        ]})
        self.service.save_pending_tool_call("run-tree-1", {
            "toolCallId": "future-call",
            "toolName": "plan_vault_change",
            "turnId": "turn-tree-1",
            "sessionId": "session-tree-1",
            "taskAuthorizationId": "auth-tree-1",
            "arguments": {"title": "future", "writes": [{"path": "01-Inbox/future.md", "content": "future"}]},
            "permissionRequest": {"type": "vault_writes", "writes": [{"path": "01-Inbox/future.md"}]},
        })
        regenerated = self.service.pi_fork_projection("run-tree-1", sequence=30, mode="regenerate")
        encoded = json.dumps(regenerated["projection"], ensure_ascii=False)
        self.assertNotIn("需要重新生成的旧回答", encoded)
        self.assertNotIn("future-call", encoded)
        self.assertIsNone(regenerated["projection"]["pending"])
        self.assertEqual(regenerated["completedActionIds"], ["action-complete"])
        self.assertEqual(regenerated["resolvedForkSequence"], 20)

    def test_forked_authorization_first_entry_uses_resolved_parent_entry(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 10, "type": "text", "content": "分支点"},
            {"sequence": 20, "type": "text", "content": "源分支未来"},
        ]})
        forked = self.service.pi_fork_projection("run-tree-1", sequence=10)
        authorization = {
            **self.authorization,
            "id": "auth-real-entry-fork",
            "runId": "run-real-entry-fork",
            "turnId": "turn-real-entry-fork",
            "sourceMessageId": "message-real-entry-fork",
            "objective": "从真实 Entry 继续",
            "parentRunId": "run-tree-1",
            "forkedFromSequence": forked["resolvedForkSequence"],
            "forkedFromEntryId": forked["resolvedForkEntryId"],
            "resourceScope": {
                "currentNote": False,
                "explicitVaultPaths": [],
                "createRoots": [],
                "workspaceId": "",
                "projectPaths": [],
            },
            "operationScope": [],
            "networkPolicy": "deny",
        }
        self.service.register_task_authorization({
            "conversationId": "conversation-entry-fork",
            "model": "fake",
            "taskAuthorization": authorization,
        })
        session = self.store.get_pi_session("session-tree-1")
        first = next(entry for entry in session["entries"] if entry["run_id"] == "run-real-entry-fork")
        self.assertEqual(first["parent_id"], forked["resolvedForkEntryId"])
        source = self.service.pi_session_projection("session-tree-1", run_id="run-tree-1")
        self.assertIn("源分支未来", json.dumps(source["messages"], ensure_ascii=False))

    def test_forking_turn_two_excludes_later_turns(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "Turn 1"},
        ]})

        def add_turn(number: int, parent: str) -> str:
            run_id = f"run-tree-{number}"
            authorization = {
                **self.authorization,
                "id": f"auth-tree-{number}",
                "runId": run_id,
                "turnId": f"turn-tree-{number}",
                "sourceMessageId": f"message-tree-{number}",
                "objective": f"User Turn {number}",
                "parentRunId": parent,
            }
            self.service.register_task_authorization({
                "conversationId": "conversation-tree", "model": "fake", "taskAuthorization": authorization,
            })
            self.service.append_pi_events({"runId": run_id, "events": [
                {"sequence": 1, "type": "text", "content": f"Assistant Turn {number}"},
            ]})
            return run_id

        run_two = add_turn(2, "run-tree-1")
        run_three = add_turn(3, run_two)
        add_turn(4, run_three)
        forked = self.service.pi_fork_projection(run_two)
        encoded = json.dumps(forked["projection"]["messages"], ensure_ascii=False)
        self.assertIn("Assistant Turn 2", encoded)
        self.assertNotIn("Assistant Turn 3", encoded)
        self.assertNotIn("Assistant Turn 4", encoded)


if __name__ == "__main__":
    unittest.main()
