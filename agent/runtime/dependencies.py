from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent.tools.change_set import ChangeSetTools


def _extract_pdf_pages(path: Path) -> list[str]:
    """Load the legacy PDF extractor only when a PDF tool is invoked.

    The script lives outside the importable ``agent`` package in this vault.
    Keeping this import lazy also prevents ordinary assistant requests and
    policy tests from depending on the PDF ingestion command's sys.path.
    """
    try:
        from ingest_pdf import extract_pages
    except ModuleNotFoundError:
        import importlib.util

        script = Path(__file__).resolve().parents[2] / "00-System" / "Scripts" / "ingest_pdf.py"
        spec = importlib.util.spec_from_file_location("zhixu_ingest_pdf", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("pdf_extractor_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extract_pages = module.extract_pages
    return extract_pages(path)


@dataclass
class ZhixuDependencies:
    vault: Path
    store: Any
    intake: Any
    tool_registry: Any
    change_sets: "ChangeSetTools"
    run_id: str
    conversation_id: str
    active_note_path: str
    active_selection: str
    attachment_ids: list[str]
    allow_network: bool
    fetch_public_web: Callable[[str], dict[str, Any]]
    search_public_web: Callable[[str, int], dict[str, Any]]
    session_allow_create_roots: list[str] = field(default_factory=list)
    proposed_change_set_ids: list[str] = field(default_factory=list)

    async def call_registered_tool(
        self,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.tool_registry.call,
            name,
            payload,
            run_id=self.run_id,
            step_id="assistant-agent",
            record_event=False,
        )

    async def read_pdf_pages(
        self,
        attachment_id: str,
        page_start: int,
        page_end: int,
    ) -> dict[str, Any]:
        if attachment_id not in self.attachment_ids:
            raise PermissionError("attachment_not_in_current_context")
        attachment = self.intake.get_attachment(attachment_id)
        if attachment.get("conversationId") != self.conversation_id:
            raise PermissionError("attachment_conversation_mismatch")
        if attachment.get("kind") != "pdf":
            raise ValueError("attachment_is_not_pdf")
        path = self.intake.resolve_attachment_path(attachment_id)
        pages = await asyncio.to_thread(_extract_pdf_pages, path)
        if page_start < 1 or page_end < page_start or page_start > len(pages):
            raise ValueError("pdf_page_range_invalid")
        end = min(page_end, len(pages))
        return {
            "attachmentId": attachment_id,
            "displayName": attachment.get("displayName"),
            "pageCount": len(pages),
            "pageStart": page_start,
            "pageEnd": end,
            "pages": [
                {"page": index + 1, "text": pages[index][:12_000]}
                for index in range(page_start - 1, end)
            ],
        }

    async def search_pdf(
        self,
        attachment_id: str,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        if attachment_id not in self.attachment_ids:
            raise PermissionError("attachment_not_in_current_context")
        attachment = self.intake.get_attachment(attachment_id)
        if attachment.get("conversationId") != self.conversation_id:
            raise PermissionError("attachment_conversation_mismatch")
        if attachment.get("kind") != "pdf":
            raise ValueError("attachment_is_not_pdf")
        path = self.intake.resolve_attachment_path(attachment_id)
        pages = await asyncio.to_thread(_extract_pdf_pages, path)
        folded = query.casefold()
        matches: list[dict[str, Any]] = []
        for index, text in enumerate(pages):
            position = text.casefold().find(folded)
            if position < 0:
                continue
            left = max(0, position - 350)
            right = min(len(text), position + len(query) + 850)
            matches.append(
                {"page": index + 1, "excerpt": text[left:right]}
            )
            if len(matches) >= limit:
                break
        return {
            "attachmentId": attachment_id,
            "displayName": attachment.get("displayName"),
            "query": query,
            "items": matches,
        }

    async def apply_validated_change_set(
        self,
        proposal_id: str,
    ) -> dict[str, Any]:
        """Harness-owned commit boundary with mandatory post-confirm revalidation."""
        validation = await asyncio.to_thread(
            self.change_sets.validate,
            {"change_set_id": proposal_id},
        )
        if validation.get("idempotent"):
            return {
                "idempotent": True,
                "verification": await asyncio.to_thread(
                    self.change_sets.verify_applied,
                    proposal_id,
                ),
            }
        applied = await asyncio.to_thread(
            self.change_sets.apply,
            {"change_set_id": proposal_id, "confirmed": True},
        )
        verification = await asyncio.to_thread(
            self.change_sets.verify_applied,
            proposal_id,
        )
        if not verification.get("verified"):
            raise RuntimeError("harness_post_apply_verification_failed")
        return {**applied, "verification": verification}
