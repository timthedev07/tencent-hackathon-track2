# Google Drive retrieval handoff: missing `428` answer

## Objective

Make the query below reliably return the exact customer-impact count from the authorised Google Doc:

> How many checkout attempts failed during the Q3 payment gateway outage?

Expected answer: **428 failed checkout attempts**.

This document is a handoff for the agent taking ownership of the retrieval fix.

## Observed behaviour

The evidence list includes the correct Google Drive document, but the answer says that the exact number is unavailable. It mentions the outage date, duration, and payment errors, while failing to return `428`.

Re-ingesting the Google Doc alone is unlikely to fix the problem. The relevant paragraph can be discarded after ingestion during retrieval and context assembly.

## Root causes to investigate

### 1. Authorised chunks from one document were reduced incorrectly

The affected path is `_load_authorised_text()` in `services/api/src/app/retrieval/pipeline.py`.

The Google Doc is split into multiple chunks at blank-line boundaries. Earlier behaviour grouped chunks by source object and retained only one chunk. Because chunks from the same document can share the same `source_revision`, ordering by revision does not identify the most relevant paragraph; the retained chunk is effectively arbitrary. The chunk containing text similar to:

```text
Customer impact: 428 failed checkout attempts.
```

can therefore be discarded before generation.

### 2. Citations could include evidence not supplied to the model

The pipeline persisted citations from the reranked evidence set even when context compilation had excluded some blocks. This made the UI appear to cite the correct Google Doc although the model may never have received the paragraph containing `428`.

Citation persistence must be based on the evidence blocks that remain in `compiled_context`, not on every reranked candidate.

### 3. Context-budget truncation could remove the useful block

`compile_context()` in `services/api/src/app/retrieval/context.py` can reach the context budget before the relevant evidence block. If truncation stops processing at that point, later relevant evidence is omitted even when it could fit or should have been prioritised.

## Required implementation

1. Preserve all authorised matching chunks from the same Google Drive document, or select them by relevance (`fused_score`) rather than arbitrary database order.
2. Prefer adjacent chunks from the same source document when assembling evidence so a multi-paragraph postmortem remains coherent.
3. Make context assembly explicitly return the evidence blocks actually included in the model prompt.
4. Persist audit evidence and citations only for those included blocks.
5. Ensure context-budget handling skips an oversized block when possible and continues evaluating later relevant blocks.
6. Keep tenant, identity, ACL, and live-authorisation checks unchanged; this is a retrieval-quality fix, not an access-control relaxation.

## Regression test requirements

Add or extend a focused pipeline test with a multi-paragraph Google Doc split into at least three chunks:

1. outage summary and timing;
2. customer-impact paragraph containing `428 failed checkout attempts`;
3. remediation or follow-up details.

The test must assert that:

- the authorised text passed to the model contains `428`;
- the generated or extractive answer contains `428`;
- the correct Google Doc/chunk is cited;
- no citation is emitted for an evidence block excluded from the compiled context;
- the test remains tenant- and authorisation-scoped.

Recommended focused test locations:

- `tests/pipeline/test_isolated_query_pipeline.py`
- `tests/retrieval/test_context.py`
- any existing retrieval/pipeline regression test module

Do not run the full test suite unless required. Start with the focused retrieval and pipeline tests, then run Ruff and type checks for touched code.

## Acceptance criteria

The query returns an answer equivalent to:

> 428 checkout attempts failed during the Q3 payment gateway outage.

The answer is grounded in the Google Doc paragraph containing the count, the citation points to evidence included in the model context, and no unrelated or unauthorised content is exposed.
