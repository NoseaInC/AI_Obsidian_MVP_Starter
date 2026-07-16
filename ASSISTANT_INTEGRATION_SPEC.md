# Assistant Five-module Integration Specification

## Today

`POST /api/v1/integrations/today/add` persists one recommendation for the primary Artifact. Repeating it is idempotent. `POST /api/v1/integrations/today/undo` removes that recommendation. Today counts and category queues update from the same persisted state.

## Materials

Material, PDF and research requests continue through the existing Job and Prepared workflows. Assistant shows the conversational result while Materials remains the processing/status authority.

## Review

Capture and knowledge-writing requests produce reviewable Artifacts or Change Sets. Assistant may revise them, but approval and transaction commit stay in Review.

## Plan

Learning-path and schedule requests create proposed tasks. Confirmation and task mutation stay in Plan.

## Context

`GET /api/v1/assistant/context` supplies only display-safe current context, reviewed/core note matches and material summaries. Empty matches are shown honestly.

