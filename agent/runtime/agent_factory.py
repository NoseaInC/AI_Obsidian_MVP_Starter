from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import (
    Agent,
    ApprovalRequired,
    DeferredToolRequests,
    ModelRetry,
    RunContext,
)

from .contracts import (
    ChangeProposalResult,
    ProposedWrite,
    WriteCommitResult,
)
from .dependencies import ZhixuDependencies
from .policy import assess_proposal


INSTRUCTIONS = """
你是知序，一个运行在 Obsidian 本地 Runtime 内的知识工作 Agent。

工作方式：
1. 先理解用户最终目标，而不是只回答用户最后一句话。
2. 只要任务依赖当前笔记、Vault、PDF、附件、网页或最近对话，就主动调用工具。
3. 每次工具返回后重新判断下一步；结果不足时换查询、读取命中笔记或扩大准确页码范围。
4. 不要求用户重复提供已经存在于当前会话、当前笔记、附件或 Conversation Focus 中的信息。
5. 普通问题直接自然回答，不生成学习包、研究包或文件修改。
6. 用户要求保存、创建或更新笔记，或当前对话已经自然推进到写入阶段时：
   - 先读取目标笔记和相关上下文；
   - 调用 propose_vault_change 创建真实 Change Set；
   - 必须紧接着调用 commit_vault_change，不要在创建提案后用文字声称“等待确认”；
   - 真正的等待确认由 commit_vault_change 和 Runtime 触发并暂停同一个 Run。
7. 不要因为用户只说“好”“继续”“就这样”而报错。如果模型已经形成具体写入方案，先创建
   Change Set，再由 commit_vault_change 交给 Harness 决定自动执行、在对话中询问或阻止。
8. commit_vault_change 的安全决策由 Runtime Harness 完成。低风险新建 Draft 可以自动应用；
   更新已有笔记、缺少明确写入授权、超出允许目录或风险较高时，Runtime 会在当前对话中
   展示目标、文件和风险并要求用户确认一次。
9. 不得声称已经读取、搜索、写入或验证，除非相应工具真实成功返回。
10. 不显示私有思维链，只在自然回答中给出结论、证据、未完成事项和必要说明。
11. 使用 Obsidian Markdown；公式使用 $...$ 和 $$...$$；内部链接使用 [[...]]。
12. reviewed/core、受保护笔记、路径逃逸、过期 base hash 和策略拒绝永远不能绕过。
"""


def build_zhixu_agent(model):
    agent: Agent[ZhixuDependencies, str | DeferredToolRequests] = Agent(
        model,
        deps_type=ZhixuDependencies,
        output_type=[str, DeferredToolRequests],
        instructions=INSTRUCTIONS,
        retries={"tools": 2, "output": 1},
    )

    @agent.tool
    async def get_current_note(
        ctx: RunContext[ZhixuDependencies],
        max_chars: int = 8_000,
    ) -> dict[str, Any]:
        """读取用户当前打开的 Obsidian 笔记。

        在解释当前笔记、比较当前笔记或准备更新当前笔记之前调用。
        """
        path = ctx.deps.active_note_path
        if not path:
            return {"available": False, "reason": "no_active_note"}
        return await ctx.deps.call_registered_tool(
            "read_note_excerpt",
            {
                "path": path,
                "max_chars": min(max(max_chars, 100), 8_000),
            },
        )

    @agent.tool
    async def get_current_selection(
        ctx: RunContext[ZhixuDependencies],
    ) -> dict[str, Any]:
        """读取用户在当前笔记中明确选中的文本。"""
        selection = ctx.deps.active_selection.strip()
        return {
            "available": bool(selection),
            "path": ctx.deps.active_note_path,
            "selection": selection[:20_000],
        }

    @agent.tool
    async def search_vault(
        ctx: RunContext[ZhixuDependencies],
        query: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """搜索 Obsidian Vault 的标题、别名和正文。

        query 应是完整概念、主题或检索问题，而不是用户原句碎片。
        """
        return await ctx.deps.call_registered_tool(
            "search_vault",
            {"query": query, "limit": min(max(limit, 1), 30)},
        )

    @agent.tool
    async def read_vault_note(
        ctx: RunContext[ZhixuDependencies],
        path: str,
        max_chars: int = 8_000,
    ) -> dict[str, Any]:
        """读取一个已经通过 Vault 路径校验的 Markdown 笔记。"""
        return await ctx.deps.call_registered_tool(
            "read_note_excerpt",
            {
                "path": path,
                "max_chars": min(max(max_chars, 100), 8_000),
            },
        )

    @agent.tool
    async def find_related_notes(
        ctx: RunContext[ZhixuDependencies],
        path: str,
    ) -> dict[str, Any]:
        """查找指定笔记的 Wiki 出链、反向链接和相关笔记。"""
        return await ctx.deps.call_registered_tool(
            "get_related_notes",
            {"path": path},
        )

    @agent.tool
    async def get_conversation_focus(
        ctx: RunContext[ZhixuDependencies],
    ) -> dict[str, Any]:
        """读取当前会话已解析的主题、方法、材料和写入目标。"""
        focus = await asyncio.to_thread(
            ctx.deps.store.get_conversation_focus,
            ctx.deps.conversation_id,
        )
        return focus or {}

    @agent.tool
    async def get_recent_conversation_messages(
        ctx: RunContext[ZhixuDependencies],
        limit: int = 12,
    ) -> dict[str, Any]:
        """读取最近对话，用于解析“这个方法”“刚才那个”等指代。"""
        items = await asyncio.to_thread(
            ctx.deps.intake.recent_messages,
            ctx.deps.conversation_id,
            min(max(limit, 1), 30),
        )
        return {"items": items}

    @agent.tool
    async def get_attachment_metadata(
        ctx: RunContext[ZhixuDependencies],
        attachment_id: str,
    ) -> dict[str, Any]:
        """读取当前会话附件的真实元数据。"""
        if attachment_id not in ctx.deps.attachment_ids:
            raise PermissionError("attachment_not_in_current_context")
        item = await asyncio.to_thread(
            ctx.deps.intake.get_attachment,
            attachment_id,
        )
        if item.get("conversationId") != ctx.deps.conversation_id:
            raise PermissionError("attachment_conversation_mismatch")
        return {
            "id": item.get("id"),
            "displayName": item.get("displayName"),
            "kind": item.get("kind"),
            "mimeType": item.get("mimeType"),
            "sizeBytes": item.get("sizeBytes"),
            "status": item.get("status"),
        }

    @agent.tool
    async def read_pdf_pages(
        ctx: RunContext[ZhixuDependencies],
        attachment_id: str,
        page_start: int,
        page_end: int,
    ) -> dict[str, Any]:
        """读取 PDF 的准确页码范围并保留页码证据。"""
        return await ctx.deps.read_pdf_pages(
            attachment_id,
            page_start,
            page_end,
        )

    @agent.tool
    async def search_pdf(
        ctx: RunContext[ZhixuDependencies],
        attachment_id: str,
        query: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """在 PDF 页面内搜索术语并返回页码与真实摘录。"""
        return await ctx.deps.search_pdf(
            attachment_id,
            query,
            min(max(limit, 1), 30),
        )

    @agent.tool
    async def search_public_web(
        ctx: RunContext[ZhixuDependencies],
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """在用户允许联网时搜索公开网页。"""
        if not ctx.deps.allow_network:
            return {
                "available": False,
                "reason": "network_not_authorized",
            }
        return await asyncio.to_thread(
            ctx.deps.search_public_web,
            query,
            min(max(limit, 1), 10),
        )

    @agent.tool
    async def fetch_public_url(
        ctx: RunContext[ZhixuDependencies],
        url: str,
    ) -> dict[str, Any]:
        """安全读取用户明确提供的公开 HTTP/HTTPS URL。"""
        if not ctx.deps.allow_network:
            return {
                "available": False,
                "reason": "network_not_authorized",
            }
        return await asyncio.to_thread(
            ctx.deps.fetch_public_web,
            url,
        )

    @agent.tool
    async def propose_vault_change(
        ctx: RunContext[ZhixuDependencies],
        title: str,
        writes: list[ProposedWrite],
    ) -> ChangeProposalResult:
        """创建真实 Change Set 和 Diff，但不直接修改 Vault。

        当当前任务已经形成具体、可验证的写入方案时调用。创建 Change Set 本身
        不会修改 Vault；是否执行由 Harness 在 commit_vault_change 中决定。创建
        成功后必须继续调用 commit_vault_change，不要自行用文字模拟等待确认。
        """
        result = await asyncio.to_thread(
            ctx.deps.change_sets.create,
            {
                "run_id": ctx.deps.run_id,
                "title": title,
                "writes": [item.model_dump() for item in writes],
            },
        )
        ctx.deps.proposed_change_set_ids.append(str(result["id"]))
        return ChangeProposalResult(
            proposal_id=str(result["id"]),
            title=str(result.get("title") or title),
            preview=str(result.get("preview") or ""),
            writes=list(result.get("writes") or []),
        )

    @agent.tool
    async def commit_vault_change(
        ctx: RunContext[ZhixuDependencies],
        proposal_id: str,
    ) -> WriteCommitResult:
        """提交已经创建的 Change Set。

        低风险新建 Draft 可自动执行；更新已有笔记或中高风险操作会在
        当前对话中要求用户确认一次。确认后仍会重新验证 base hash、
        protected/core 和路径安全。
        """
        ctx.deps.commit_attempted = True
        validation = await asyncio.to_thread(
            ctx.deps.change_sets.validate,
            {"change_set_id": proposal_id},
        )
        if validation.get("idempotent"):
            return WriteCommitResult(
                proposal_id=proposal_id,
                state="applied",
                message="该修改已经应用，无需重复执行。",
            )

        record = await asyncio.to_thread(
            ctx.deps.change_sets.get_record,
            proposal_id,
        )
        configured_roots = ctx.deps.store.get_setting(
            "assistant_auto_write_roots",
            None,
        )
        decision = assess_proposal(
            record,
            write_requested=ctx.deps.write_requested,
            autonomy_mode=ctx.deps.autonomy_mode,
            configured_roots=configured_roots,
        )

        if not decision.auto_apply and not ctx.tool_call_approved:
            raise ApprovalRequired(
                metadata={
                    "proposalId": proposal_id,
                    "title": record.get("title") or "Obsidian 修改",
                    "summary": " · ".join(filter(None, [str(record.get("preview") or ""), decision.reason])),
                    "riskLevel": decision.risk_level,
                    "writes": [
                        {key: value for key, value in item.items() if key != "payload_path"}
                        for item in (record.get("writes") or [])
                    ],
                    "reason": decision.reason,
                }
            )

        applied = await asyncio.to_thread(
            ctx.deps.change_sets.apply,
            {
                "change_set_id": proposal_id,
                "confirmed": True,
            },
        )
        verification = await asyncio.to_thread(
            ctx.deps.change_sets.verify_applied,
            proposal_id,
        )
        if not verification.get("verified"):
            raise RuntimeError("harness_post_apply_verification_failed")
        return WriteCommitResult(
            proposal_id=proposal_id,
            state="applied",
            transaction_id=applied.get("transaction_id"),
            journal=applied.get("journal"),
            verification=verification,
            message=(
                "修改已应用并通过 Change Set 校验。"
                if decision.auto_apply
                else "用户已在当前对话中确认，修改已应用。"
            ),
        )

    @agent.output_validator
    async def require_governed_commit(
        ctx: RunContext[ZhixuDependencies],
        output: str | DeferredToolRequests,
    ) -> str | DeferredToolRequests:
        """Prevent a model from simulating approval with prose after proposing a write."""
        if (
            isinstance(output, str)
            and ctx.deps.commit_required
            and ctx.deps.proposed_change_set_ids
            and not ctx.deps.commit_attempted
        ):
            raise ModelRetry(
                "用户要求执行保存。你已经创建 Change Set；现在必须调用 "
                "commit_vault_change。不要用文字模拟等待确认。"
            )
        return output

    return agent
