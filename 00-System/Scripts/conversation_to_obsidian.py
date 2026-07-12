#!/usr/bin/env python3
"""Compatibility entry point: AI conversations now always create Prepared Bundles."""

from prepared_conversation import parser, prepare_conversation
import ingest_pdf
import json


def main() -> None:
    print(prepare_conversation(parser().parse_args()))


if __name__ == "__main__":
    try: main()
    except (RuntimeError, ValueError, json.JSONDecodeError, ingest_pdf.ValidationError) as exc:
        raise SystemExit(f"失败：{exc}") from None
