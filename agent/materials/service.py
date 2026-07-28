"""Prepared PDF service — thin API wrapper around the scripts pipeline.

The heavy lifting lives in 00-System/Scripts/prepared_pdf.py and ingest_pdf.py.
This module provides typed service methods for the HTTP API layer.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System" / "Scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepared_pdf
import ingest_pdf


class PreparedPdfService:
    """Typed API for the Prepared PDF workflow: list, inspect, apply, reject."""

    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()

    def list_bundles(self) -> dict[str, Any]:
        return {"bundles": prepared_pdf.list_prepared(self.vault)}

    def inspect(self, prepared_id: str) -> dict[str, Any]:
        return {"preview": prepared_pdf.inspect_bundle(self.vault, prepared_id)}

    def reject(self, prepared_id: str, reason: str = "") -> dict[str, Any]:
        result = prepared_pdf.reject_prepared(self.vault, prepared_id, reason)
        return {"rejected": True, "prepared_id": prepared_id, "reason": result}

    def apply(self, prepared_id: str) -> dict[str, Any]:
        result = prepared_pdf.apply_prepared(self.vault, prepared_id)
        return {"result": result, "prepared_id": prepared_id}
