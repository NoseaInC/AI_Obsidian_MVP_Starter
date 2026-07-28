"""Materials domain: Prepared PDF ingestion, review, and application.

The actual pipeline lives in 00-System/Scripts/ (prepared_pdf.py, ingest_pdf.py).
This package provides the service-layer API that the HTTP server exposes.

Not dependent on the legacy Brain coordinator. Pi Agent Runtime calls these
through typed tool requests, not through BrainRequest/IntentResult wrappers."""

from agent.materials.service import PreparedPdfService

__all__ = ["PreparedPdfService"]
