#!/usr/bin/env python3
"""
Archive and distill a manually selected high-value AI conversation.

Input should be a local Markdown or plain-text export. The original export is
copied into 90-Local-Only/Raw-Conversation-Archive. Only the text is sent to
the configured DeepSeek API.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from openai import OpenAI


def sanitize_filename(value: str, max_len: int = 120) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "未命名对话")[:max_len]


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


PROMPT = """请从以下高价值 AI 对话中提取可长期保存的知识。

约束：
- AI 回答不是天然可信来源，标记需要验证的事实。
- 保留问题背景。
- 不直接覆盖已有核心知识，只给出修改建议。
- 代码要说明适用条件、依赖和风险。
- 只输出 Markdown 正文，不输出 YAML。

结构：
# AI 对话提炼：{title}
## 对话目标
## 核心结论
## 有价值的解释
## 可复用代码或方法
## 需要验证的事实
## 尚未解决的问题
## 建议创建的概念草稿
## 对已有知识的修改建议
## 原始对话回链
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Markdown 或 TXT 对话导出")
    parser.add_argument("--vault", required=True, help="Obsidian Vault 根目录")
    parser.add_argument("--platform", default="ChatGPT")
    parser.add_argument("--title")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    args = parser.parse_args()

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("缺少环境变量 DEEPSEEK_API_KEY。")

    input_path = Path(args.input).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    if not input_path.is_file():
        raise SystemExit(f"输入文件不存在：{input_path}")
    if not vault.is_dir():
        raise SystemExit(f"Vault 不存在：{vault}")

    text = input_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise SystemExit("对话导出为空。")

    title = args.title or input_path.stem
    safe_title = sanitize_filename(title)
    digest = sha256(input_path)
    short_hash = digest[:12]
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")

    archive_folder = vault / "90-Local-Only" / "Raw-Conversation-Archive"
    source_folder = vault / "10-Sources" / "AI-Conversations"
    draft_folder = vault / "90-Local-Only" / "AI-Drafts"
    for folder in (archive_folder, source_folder, draft_folder):
        folder.mkdir(parents=True, exist_ok=True)

    archive_name = f"{safe_title}-{short_hash}{input_path.suffix or '.md'}"
    archive_path = archive_folder / archive_name
    if input_path != archive_path:
        shutil.copy2(input_path, archive_path)

    source_name = f"{safe_title}-{short_hash}"
    draft_name = f"{safe_title}-AI草稿-{short_hash}"

    client = OpenAI(api_key=api_key, base_url=args.base_url)
    response = client.chat.completions.create(
        model=args.model,
        messages=[
            {
                "role": "system",
                "content": "你是严谨的知识整理助手。不得把 AI 原回答当成已验证事实。",
            },
            {
                "role": "user",
                "content": PROMPT.format(title=title) + "\n\n对话全文：\n" + text,
            },
        ],
        stream=False,
    )
    body = response.choices[0].message.content
    if not body:
        raise SystemExit("模型返回了空内容。")

    source_path = source_folder / f"{source_name}.md"
    source_path.write_text(
        f"""---
type: source
source_type: ai-conversation
status: processed
source_id: {yaml_quote("conversation-" + short_hash)}
created: {today}
platform: {yaml_quote(args.platform)}
conversation_date:
original_location: {yaml_quote(str(input_path))}
archive_file: {yaml_quote(str(archive_path))}
processed_by: {yaml_quote(args.model)}
processed_at: {yaml_quote(now)}
ai_draft: {yaml_quote("[[" + draft_name + "]]")}
related_concepts: []
tags:
  - source/ai-conversation
---

# {title}

## 对话目标

## 本地归档

- `{archive_path}`
- SHA-256：`{digest}`
- AI 草稿：[[{draft_name}]]

## 人工批注
""",
        encoding="utf-8",
    )

    draft_path = draft_folder / f"{draft_name}.md"
    draft_path.write_text(
        f"""---
type: ai-draft
draft_kind: ai-conversation-distillation
status: ai-draft
created: {today}
processed_by: {yaml_quote(args.model)}
source_notes:
  - {yaml_quote("[[" + source_name + "]]")}
ai_generated: true
reviewed: false
tags:
  - ai/draft
---

{body.strip()}

## 审核结果

- [ ] 接受并拆分为知识笔记
- [ ] 修改后接受
- [ ] 保留为来源摘要
- [ ] 拒绝

## 我的审核备注
""",
        encoding="utf-8",
    )

    print(f"本地归档：{archive_path}")
    print(f"来源笔记：{source_path}")
    print(f"AI 草稿：{draft_path}")


if __name__ == "__main__":
    main()
