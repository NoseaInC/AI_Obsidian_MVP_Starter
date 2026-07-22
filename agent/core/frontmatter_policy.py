from __future__ import annotations

import json
import re


POLICY_FIELDS = {"status", "agent_access", "agent_protected", "type"}


def _without_inline_comment(value: str) -> str:
    """Remove a YAML inline comment without changing quoted `#` characters."""
    quote = ""
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {"'", '"'}:
            if not quote:
                quote = character
            elif quote == character:
                # YAML single-quoted strings escape a quote by doubling it.
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    continue
                quote = ""
            continue
        if character == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _policy_scalar(value: str) -> tuple[str, bool]:
    raw = _without_inline_comment(value).strip()
    if not raw:
        return "", False
    if raw.startswith('"'):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "", False
        return (str(decoded).strip().casefold(), True) if isinstance(decoded, str) else ("", False)
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            return "", False
        return raw[1:-1].replace("''", "'").strip().casefold(), True
    if raw[0] in "[{|>&*!" or any(character in raw for character in "'\""):
        return "", False
    return raw.casefold(), True


def parse_policy_frontmatter(text: str) -> tuple[dict[str, str], bool]:
    """Parse only protection-related scalar frontmatter fields.

    The knowledge boundary must fail closed. Unsupported or malformed values in
    a protection field therefore make the result invalid instead of silently
    treating a note as ordinary. Unknown YAML fields are intentionally ignored.
    """
    normalized = text.removeprefix("\ufeff")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, True
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), -1)
    if closing < 0:
        return {}, False

    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1).casefold()
        if key not in POLICY_FIELDS:
            continue
        if key in fields:
            return {}, False
        scalar, valid = _policy_scalar(match.group(2))
        if not valid:
            return {}, False
        fields[key] = scalar
    return fields, True


def policy_metadata_from_bytes(body: bytes) -> tuple[dict[str, str], bool]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}, False
    return parse_policy_frontmatter(text)
