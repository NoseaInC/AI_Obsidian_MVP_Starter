# Assistant Failure Recovery Specification

Failures are translated into a short human explanation, impact and recovery actions. Internal codes remain under technical details.

Examples:

- No trusted research result: continue with reviewed local knowledge, add a source, or retry trusted research.
- Model unavailable: retry, change the routed model, or continue with deterministic local functions.
- Missing context: attach material, select a note, or narrow the request.

Research with no external hit returns a recoverable partial result instead of failing the whole Task Thread. Recovery never weakens source labels or silently marks model knowledge as reviewed.

