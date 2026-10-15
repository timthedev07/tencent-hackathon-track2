# Relay: five-minute demo script

**Running time:** 5:00

**Recording plan:** Present slides 1 to 4, use slide 5 as the handover cue, then switch to the deployed Relay app. Return to slides 6 to 8 after the walkthrough. Keep the browser at 125% zoom, hide bookmarks and notifications, and have Bob’s and Carol’s demo sessions ready before recording. Timings include clicks and pauses. Rehearse once to match your delivery pace.

## Slide 1 — Relay (0:00–0:15)

“This is Relay, a permission-safe enterprise AI assistant. It answers questions across company knowledge while preserving source permissions. We will cover the architecture, demonstrate the product and explain how agents supported our work.”

## Slide 2 — Why Relay (0:15–0:40)

“An incident answer may need a Jira issue for the cause, Slack for the mitigation, a Confluence runbook and a Drive postmortem. Relay helps employees assemble that context in one place. Administrators and compliance teams also need to reconstruct what happened. Every answer must respect the asking user’s access.”

## Slide 3 — Ingestion and the knowledge index (0:40–1:10)

“Relay uses retrieval-augmented generation, or RAG, to answer from source evidence. When a user connects Atlassian, Slack or Google Drive, Relay records the exact provider account that granted access and stores that account’s OAuth credential encrypted. Celery workers fetch and normalise content, resolve access-control metadata and split text into chunks. Embeddings support semantic search, alongside PostgreSQL full-text indexes in the pgvector-backed database.

Reconciliation discovers updates. A revision becomes active only when indexing is ready, and immutable versions preserve provenance. Indexed permissions help narrow candidates. Live checks still decide query-time access.”

## Slide 4 — Permission-safe retrieval and RAG (1:10–1:55)

“At query time, vector similarity finds related meaning and full-text search finds matching terms. Reciprocal-rank fusion combines their candidate rankings, carrying IDs and scores before text loading.

Relay resolves the user and tenant, then checks each candidate through the source provider’s API using the OAuth credential connected for that provider. Uncertain access means deny. Only allowed text reaches reranking and a token-budgeted context labelled with evidence IDs. Retrieved text is untrusted data, and the model returns an answer with claim evidence references.

Verification checks those references against authorised evidence. On failure, Relay retries once, then uses an extractive fallback. This validates references, rather than proving factual truth. The audit must commit before release. SHA-256 chaining makes changes detectable, and opening a citation checks access again.”

## Slide 5 — One question, four sources: live walkthrough (1:55–3:40)

**Switch to the deployed demo. Sign in as Bob.**

**Relative cues:** 0:00 sources, 0:15 submit, 0:55 answer, 1:20 citation, 1:30 Carol’s audit view. Paste the prepared query to save typing time.

“Here is Relay as Bob, with the shared incident corpus connected across Confluence, Jira, Slack and Google Drive.

I will ask: ‘What caused the Q3 payment gateway outage, what follow-up was created, what mitigation did the team agree, what does the runbook prescribe, and how many checkout attempts failed? Cite the evidence.’

The progress states show the pipeline we just described: retrieval, live access checks, reranking, generation, verification and audit.

The answer combines the Jira cause and follow-up, Slack mitigation, Confluence runbook and Drive impact count. In this synthetic corpus, 428 checkout attempts failed. I will point out the source citations supporting those details.

Opening this citation triggers another access check before showing the evidence.

Now, in Carol’s compliance session, we open the same query’s audit trail. It records the identity, question, evidence revisions, access decisions and answer, with chain verification.”

**Fallback if the audit session is unavailable:** “The completed query has an audit trail link. In the prepared compliance session, Carol can inspect the same query record and its chain verification.”

## Slide 6 — WorkBuddy shaped the plan (3:40–4:10)

“We started with an initial idea and draft proposal for one assistant across enterprise knowledge. With WorkBuddy, we iteratively improved the use case, target users and security promise.

We asked it to challenge assumptions and expose failure modes. Each iteration made the proposal and implementation plan more concrete, including cross-source answers, permission denial, revocation, freshness and audit scenarios.”

## Slide 7 — CodeBuddy in the build loop (4:10–4:35)

“CodeBuddy supported the build loop: reading the specification, mapping responsibilities across the frontend, API and worker, and implementing focused slices.

Targeted tests and code review helped us debug query stages, citations and fail-closed behaviour. The team made the final decisions and checked the observed results.”

## Slide 8 — What we learned (4:35–5:00)

“Our main lesson is that enterprise RAG needs a clear trust model throughout retrieval. Citations help users verify answers, while live authorisation protects the evidence boundary.

Agents helped us turn a large brief into concrete work and repeatable scenarios. Next, we would expand the live corpus and test more provider edge cases. Thank you.”

## Recording checklist

- Confirm each source is connected before recording.
- Use the exact payment query shown on slide 5.
- Let the progress states remain visible briefly before the answer appears.
- Open at least one citation.
- Show the audit trail only in Carol’s compliance session.
- Do not show restricted document names, test canaries, credentials, browser profiles or provider tokens.

## Technical reference for rehearsal (not spoken)

- **Ingestion and revisions:** `tencent_hackathon_implementation_spec.md`, sections 15–19. Workers normalise, resolve ACLs, chunk and embed before activation. Indexing uses the connected ingestion identity. Live access checks protect the query-time reranker and generation boundary, rather than implying that ingestion never processes source text.
- **Retrieval and generation:** `services/api/src/app/retrieval/pipeline.py` and `rrf.py`. RRF merges rank positions from vector and lexical results. Authorised evidence then enters reranking, context compilation and generation.
- **Verification:** `services/api/src/app/retrieval/verify.py`. Valid evidence references do not independently establish that every generated statement is factually correct.
- **Trust invariants:** specification section 4. Unknown or failed access checks deny access, positive decisions are query-scoped, and there is no service-account fallback. Final answers require an audit commit, and citations reauthorise on open.
- The live segment demonstrates an authorised answer, citation access and audit inspection. It does not by itself demonstrate revocation, freshness bounds or every negative permission case.
