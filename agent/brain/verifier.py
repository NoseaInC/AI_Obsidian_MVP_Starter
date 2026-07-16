from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import ipaddress

from .errors import BrainError


class Verifier:
    def verify(self, skill: str, request_text: str, result: dict[str, Any], vault: Path) -> dict[str, Any]:
        if skill in {"capture_text", "organize_text"}:
            original = str(result.get("original_text", ""))
            if original != request_text:
                raise BrainError("brain_verification_failed", "原始文字未被逐字保留", False, "取消该提案")
            proposed = list(result.get("proposed_notes", []))
            if len(proposed) > 5:
                raise BrainError("brain_verification_failed", "生成的笔记数量超过限制", False, "减少拆分数量")
            for item in proposed:
                path = (vault / str(item.get("path", ""))).resolve()
                if not path.is_relative_to(vault.resolve()) or path.is_symlink():
                    raise BrainError("invalid_path", "提案路径越界或包含符号链接", False, "修改保存路径")
                if not str(item.get("content", "")).strip():
                    raise BrainError("brain_verification_failed", "提案包含空白笔记", False, "重新整理内容")
        if skill == "research_topic":
            allowed_types = {"local_vault", "imported_source", "academic_api", "official_documentation", "user_provided_url", "ai_curriculum_suggestion"}
            seen: set[tuple[str, str]] = set()
            for source in result.get("sources", []):
                source_type = str(source.get("source_type", ""))
                if source_type not in allowed_types:
                    raise BrainError("brain_verification_failed", "研究来源类型未注册")
                url = str(source.get("canonical_url", ""))
                key = (str(source.get("title", "")).casefold(), url)
                if key in seen:
                    raise BrainError("brain_verification_failed", "研究包包含重复来源")
                seen.add(key)
                if source_type == "ai_curriculum_suggestion" and url:
                    raise BrainError("brain_verification_failed", "AI 路线建议不能伪装成外部链接")
                if url and urlparse(url).scheme not in {"http", "https"}:
                    raise BrainError("invalid_url", "研究来源 URL 非法")
                if url:
                    host = (urlparse(url).hostname or "").casefold()
                    if host == "localhost" or host.endswith(".local"):
                        raise BrainError("invalid_url", "研究来源不能指向本机或内网")
                    try:
                        if not ipaddress.ip_address(host).is_global:
                            raise BrainError("invalid_url", "研究来源不能指向本机或内网")
                    except ValueError:
                        pass
        return result
