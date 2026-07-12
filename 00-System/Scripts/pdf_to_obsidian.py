#!/usr/bin/env python3
"""Compatibility wrapper for the structured PDF ingestion pipeline.

Existing users may keep calling ``pdf_to_obsidian.py``. All arguments are
handled by ``ingest_pdf.py``; new commands should use that entry point.
"""

from ingest_pdf import main


if __name__ == "__main__":
    main()
