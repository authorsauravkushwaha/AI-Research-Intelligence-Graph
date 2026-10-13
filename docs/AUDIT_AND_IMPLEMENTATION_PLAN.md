# Repository audit and investigation implementation

## Baseline audit (before implementation)

The repository already contains a working FastAPI application, a vanilla JavaScript/Three.js frontend, a curated demo corpus, an in-process graph store, an official Neo4j Python-driver store, graph analytics, GraphRAG, a scoped opportunity engine, an optional LLM-backed agent, a native C++ kernel, and a static-site recorder. This work extends those components; it does not replace the stack or add a competing graph schema.

Verified before tracked-file changes:

- Corpus validator: 204 papers dated 2013–2025, 75 topics, 61 methods, 68 datasets, 60 curated citations, and 51 claims.
- Existing Python suite: 76 tests passed. Native kernel self-tests passed.
- `GraphRAG.retrieve()` uses the `local:lexical-baseline` in this environment and can return broad neighborhoods. Its raw order is not safe to present as question relevance; the Investigator therefore reranks scoped paper records independently and labels the retrieval engine.
- `GapEngine` builds candidates from the locally hydrated graph projection. Small scopes can have no supported opportunities; the Investigator reports that instead of substituting unrelated corpus-wide candidates.
- The static site records some expensive API responses at build time and returns 501 for unrecorded endpoints. The Investigator must use exact, explicitly recorded question/scope pairs in a static build.
- The graph-view loader accepted `seeds` in its JavaScript call signature but omitted them from the `/api/graph` request body.

The Neo4j path existed but had parity risks discovered during audit: imported field taxonomy nodes were not connected to papers; imported Claim IDs did not use the stable `claim:` prefix used by the in-process schema; JSON-encoded claim provenance could be treated as a dictionary; schema-creation errors were silently discarded; a failed load could leave a driver open; and investigation paths were not directly checked against Neo4j.

## Implementation

The Investigator adds one deterministic, evidence-first workflow on the existing schema:

1. Ask a bounded research question and optionally choose a corpus scope/year/field.
2. Retrieve GraphRAG context, then independently rank paper records using disclosed lexical overlap across title, summary, topics, and methods. Embedding similarity is only a retrieval signal; it is not presented as calibrated relevance.
3. Run the existing gap engine within the selected scope and filter its candidates for question/scope term support. Preserve the published prototype score and expose a separate relevance heuristic.
4. Attach source-linked paper evidence, explainable graph paths made only of non-predicted corpus relationships, detected claim tensions labelled as AI hypotheses, coverage counts, limitations, and a falsifiable experiment proposal.
5. When Neo4j is active, verify candidate paths with bounded, parameterized Cypher against that database. In-memory mode uses a bounded BFS over its graph and is labelled accordingly.

## Verification boundary

The baseline and implementation tests run offline. There is no configured Neo4j URI/password or live Neo4j service in this workspace, so unit tests can validate connection, parameterization, and error handling with fakes, but no live-service result will be claimed. Static build outputs are generated for checking and are not committed.
