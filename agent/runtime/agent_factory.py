from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pydantic_ai import (
    Agent,
    ApprovalRequired,
    CallDeferred,
    DeferredToolRequests,
    RunContext,
)

from .contracts import (
    ChangeProposalResult,
    ProposedWrite,
    WriteCommitResult,
)
from .dependencies import ZhixuDependencies
from .policy import ToolPermissionGate


INSTRUCTIONS = """
你是知序，一个运行在 Obsidian 本地 Runtime 内的知识工作 Agent。

工作方式：
1. 先理解用户最终目标，而不是只回答用户最后一句话。
2. 只要任务依赖当前笔记、Vault、PDF、附件、网页或最近对话，就主动调用工具。
3. 每次工具返回后重新判断下一步；结果不足时换查询、读取命中笔记或扩大准确页码范围。
4. 不要求用户重复提供已经存在于当前会话、当前笔记、附件或 Conversation Focus 中的信息。
5. 工具无法消除关键歧义，或选择只能由用户本人决定时，调用 ask_user；能通过读取、搜索或
   检查附件解决的问题不得反问用户，也不要在普通文本中模拟等待回答。
6. 普通问题直接自然回答，不生成学习包、研究包或文件修改。
7. 需要保存、创建或更新笔记时，先读取目标与相关上下文，再调用 propose_vault_change。
   用户只要求查看方案时可停在提案；用户要求实际写入时必须在提案成功后立即调用
   commit_vault_change。不得用普通文本询问“是否确认”，也不得把用户已经表达的写入意图
   再问一次；commit_vault_change 会让 Runtime 在当前对话中渲染真实确认卡。
8. commit_vault_change 的安全决策只由 Runtime Harness 完成。提交默认在当前对话询问；仅当
   本会话已有范围化的新建目录授权时可自动执行。越界、受保护或过期修改会直接拒绝。
9. 不得声称已经读取、搜索、写入或验证，除非相应工具真实成功返回。
10. 不显示私有思维链，只给出结论、证据、未完成事项和必要的简短行动说明。
11. 使用 Obsidian Markdown；公式使用 $...$ 和 $$...$$；内部链接使用 [[...]]。
12. reviewed/core、受保护笔记、路径逃逸、过期 base hash 和策略拒绝永远不能绕过。
13. 若一次提案同时包含新建草稿和 reviewed/core 更新，Harness 会把受保护更新自动改写为
    90-Local-Only 下的 update-suggestion；这不是失败，不要重试覆盖正式笔记。
"""


DEEP_MODE_INSTRUCTIONS = """
当前回合由用户启用了“深度思考”。这不是要求输出冗长文字，而是提高证据和校验门槛：
1. 先区分已知事实、当前上下文中的主张、仍需验证的假设，再决定是否调用工具。
2. 请求涉及当前笔记、Vault、附件、PDF、网页、近期资料或先前写入结果时，必须至少执行一次
   对应的真实读取或检索；不得仅凭对话摘要声称已经核对。
3. 研究、比较和知识整理任务应尽量交叉检查两个独立证据；只有一个来源时明确说明限制。
4. 在给出最终答案前检查：是否回答了真实目标、关键条件是否遗漏、结论能否由观察结果支持、
   是否存在容易混淆的反例或适用边界。
5. 简单算术和纯表达任务不需要为了展示过程而滥用工具。最终回答保持清楚、紧凑；供应商返回的
   reasoning block 由 Runtime 独立展示，不要把私有思维链复制进正文。
"""


def _recoverable_tool_failure(
    error: Exception,
    *,
    hint: str,
) -> dict[str, Any]:
    """Turn a denied or unavailable observation into model-visible evidence.

    Permission checks still run at the same boundary.  The only difference is
    that a model choosing the wrong *read* tool can re-plan in the same Run
    instead of aborting every parallel tool call.
    """
    raw_code = str(error).strip().splitlines()[0][:160]
    return {
        "ok": False,
        "error": {
            "code": raw_code or type(error).__name__,
            "type": type(error).__name__,
            "recoverable": True,
            "retryable": not isinstance(error, PermissionError),
            "hint": hint[:500],
        },
    }


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
    async def get_vault_overview(
        ctx: RunContext[ZhixuDependencies],
    ) -> dict[str, Any]:
        """读取模型可见知识目录的分类计数与最近笔记概览。"""
        return await ctx.deps.call_registered_tool("get_vault_overview", {})

    @agent.tool
    async def list_vault_folder(
        ctx: RunContext[ZhixuDependencies],
        path: str,
        recursive: bool = False,
        limit: int = 50,
        cursor: int = 0,
    ) -> dict[str, Any]:
        """列出指定 Obsidian 文件夹中的子目录和 Markdown 文件。

        当用户明确要求读取某个文件夹，或任务必须知道目录中的完整文件清单时使用。
        path 传入 "/" 或 "." 时列出模型可见的 Vault 顶层目录；其他路径必须是
        相对 Vault 的受控路径，例如 "20-Knowledge/Concepts"。
        此工具只返回受控知识目录中的路径和元数据；正文应随后用 read_vault_note 读取。
        """
        return await ctx.deps.call_registered_tool(
            "list_vault_folder",
            {
                "path": path,
                "recursive": recursive,
                "limit": min(max(limit, 1), 100),
                "cursor": max(cursor, 0),
            },
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
    async def get_learning_state(
        ctx: RunContext[ZhixuDependencies],
    ) -> dict[str, Any]:
        """读取 reviewed/core 知识的掌握度与下次复习状态。"""
        return await ctx.deps.call_registered_tool("get_learning_state", {})

    @agent.tool
    async def get_due_reviews(
        ctx: RunContext[ZhixuDependencies],
        limit: int = 5,
    ) -> dict[str, Any]:
        """读取当前已经到期、适合安排到今日的 reviewed/core 复习项。"""
        return await ctx.deps.call_registered_tool(
            "get_due_reviews",
            {"limit": min(max(limit, 1), 20)},
        )

    @agent.tool
    async def get_recent_materials(
        ctx: RunContext[ZhixuDependencies],
        query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        """按主题查找 Vault 中最近导入的资料索引。"""
        return await ctx.deps.call_registered_tool(
            "get_recent_materials",
            {"query": query, "limit": min(max(limit, 1), 20)},
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
        """读取当前会话附件的真实元数据。

        attachment_id 只能取自当前 runtime-context 的 attachments；论文号、
        DOI、URL 和搜索结果 ID 都不是附件 ID。
        """
        try:
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
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint=(
                    "Only use an attachment ID listed in the current runtime-context. "
                    "For a public paper or URL, use fetch_public_url on its landing page."
                ),
            )

    @agent.tool
    async def read_pdf_pages(
        ctx: RunContext[ZhixuDependencies],
        attachment_id: str,
        page_start: int,
        page_end: int,
    ) -> dict[str, Any]:
        """读取当前对话已上传 PDF 的准确页码范围并保留页码证据。

        只接受 runtime-context attachments 中的真实 attachment_id。不得传入
        arXiv ID、DOI、URL 或搜索结果 ID；公开论文应先用 fetch_public_url
        读取其落地页证据。
        """
        try:
            return await ctx.deps.read_pdf_pages(
                attachment_id,
                page_start,
                page_end,
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint=(
                    "This tool only reads a PDF attached to the current conversation. "
                    "Use fetch_public_url for an arXiv/DOI/public-paper landing page."
                ),
            )

    @agent.tool
    async def search_pdf(
        ctx: RunContext[ZhixuDependencies],
        attachment_id: str,
        query: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """在当前对话已上传 PDF 内搜索术语并返回页码与真实摘录。

        attachment_id 不能使用论文号、DOI 或 URL。
        """
        try:
            return await ctx.deps.search_pdf(
                attachment_id,
                query,
                min(max(limit, 1), 30),
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint=(
                    "This tool only searches a PDF attached to the current conversation. "
                    "Use search_academic_sources or fetch_public_url for public papers."
                ),
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
        try:
            return await asyncio.to_thread(
                ctx.deps.search_public_web,
                query,
                min(max(limit, 1), 10),
            )
        except (OSError, PermissionError, TimeoutError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint="Adjust the query or continue with other available sources.",
            )

    @agent.tool
    async def search_academic_sources(
        ctx: RunContext[ZhixuDependencies],
        query: str,
        limit: int = 6,
    ) -> dict[str, Any]:
        """在用户允许联网时联合检索 arXiv 与 Crossref 学术来源。

        论文、教材、方法比较和需要正式引用的研究问题应优先使用此工具；
        返回的是候选元数据，关键结论仍需进一步读取原文并核对。
        """
        if not ctx.deps.allow_network:
            return {
                "available": False,
                "reason": "network_not_authorized",
            }
        try:
            return await asyncio.to_thread(
                ctx.deps.search_academic_web,
                query,
                min(max(limit, 1), 10),
            )
        except (OSError, PermissionError, TimeoutError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint="Adjust the query or continue with other available sources.",
            )

    @agent.tool
    async def fetch_public_url(
        ctx: RunContext[ZhixuDependencies],
        url: str,
    ) -> dict[str, Any]:
        """安全读取用户提供或联网搜索命中的公开 HTTP/HTTPS URL。

        适合网页、官方文档以及 arXiv/DOI 论文落地页；不能用它读取本地文件。
        """
        if not ctx.deps.allow_network:
            return {
                "available": False,
                "reason": "network_not_authorized",
            }
        try:
            return await asyncio.to_thread(
                ctx.deps.fetch_public_web,
                url,
            )
        except (OSError, PermissionError, TimeoutError, ValueError) as error:
            return _recoverable_tool_failure(
                error,
                hint=(
                    "The URL was unavailable or rejected by the public-network policy. "
                    "Try another public result or use the search-result metadata."
                ),
            )

    @agent.tool
    async def get_current_datetime(
        ctx: RunContext[ZhixuDependencies],
    ) -> dict[str, Any]:
        """读取本机当前日期、时间与时区，用于处理“今天”“下周”等时间请求。"""
        now = datetime.now().astimezone()
        return {
            "iso": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "timezone": str(now.tzinfo or "local"),
            "weekday": now.strftime("%A"),
        }

    @agent.tool
    async def ask_user(
        ctx: RunContext[ZhixuDependencies],
        question: str,
        options: list[str] | None = None,
        reason: str = "",
    ) -> str:
        """暂停当前 Run，向用户询问一个无法通过现有工具解决的必要问题。

        只用于用户专属选择、缺失且不可检索的必要信息或不可逆的外部决定。
        回答会作为真实工具结果交回同一个 Run，模型随后继续规划。
        """
        clean_question = question.strip()
        clean_options = [item.strip() for item in (options or []) if item.strip()]
        if not clean_question:
            raise ValueError("ask_user_question_required")
        if len(clean_options) > 8 or any(len(item) > 160 for item in clean_options):
            raise ValueError("ask_user_options_invalid")
        raise CallDeferred(metadata={
            "kind": "question",
            "question": clean_question,
            "options": clean_options,
            "reason": reason.strip()[:1000],
            "title": "知序需要你的选择",
        })

    @agent.tool
    async def propose_vault_change(
        ctx: RunContext[ZhixuDependencies],
        title: str,
        writes: list[ProposedWrite],
    ) -> ChangeProposalResult:
        """创建真实 Change Set 和 Diff，但不直接修改 Vault。

        用户只要求查看方案时可以在提案后回答；用户要求实际写入时，必须紧接着调用
        commit_vault_change，不能在普通文本中模拟确认。创建 Change Set 本身不会修改 Vault。
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

        默认在当前对话中要求确认；只有本会话已有范围化的新建目录授权时才
        可能自动执行。确认后仍会重新验证 base hash、protected/core 和路径安全。
        """
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
            "assistant_writable_roots",
            None,
        )
        decision = ToolPermissionGate(configured_roots).assess_commit(
            record,
            session_allow_create_roots=ctx.deps.session_allow_create_roots,
        )

        if decision.decision == "deny":
            return WriteCommitResult(
                proposal_id=proposal_id,
                state="denied",
                message=decision.reason,
            )

        if decision.decision == "ask" and not ctx.tool_call_approved:
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
                    "scopeCandidates": list(decision.scope_candidates),
                }
            )

        applied = await ctx.deps.apply_validated_change_set(proposal_id)
        verification = dict(applied.get("verification") or {})
        return WriteCommitResult(
            proposal_id=proposal_id,
            state="applied",
            transaction_id=applied.get("transaction_id"),
            journal=applied.get("journal"),
            verification=verification,
            message=(
                "修改已应用并通过 Change Set 校验。"
                if decision.decision == "allow"
                else "用户已在当前对话中确认，修改已应用。"
            ),
        )

    return agent
