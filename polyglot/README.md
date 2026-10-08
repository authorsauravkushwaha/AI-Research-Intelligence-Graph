# Polyglot sidecars

NEXUS gives each language a job it is actually good at. Nothing in the demo depends on
these services being reachable — when a sidecar is not running, the Python implementation
behind the same interface answers instead, and the response says which engine ran.

| Language | File | Role | Endpoint (env var) | Executed here? |
| --- | --- | --- | --- | --- |
| **Ruby** | `ruby/claim_resolver.rb` | Claim parsing, negation/polarity, direction axes, conflict scoring | `NEXUS_CLAIM_URL` (HTTP, `--serve 8093`) | **Yes** — CRuby 3.2 (wasm) on the real corpus, self-test + parity, see below |
| **Kotlin (JVM)** | `kotlin/src/main/kotlin/NexusPlanner.kt` | Query planner: NEXUS DSL → parameterised Cypher, plus a fallback traversal engine | `NEXUS_PLANNER_URL` (`/api/plan`) | **Yes** — kotlinc 2.4.21 + OpenJDK 25, self-test and `/api/services` live, see below |
| **Go** | `go/main.go` | Corpus ingestion + embedding service (static binary, concurrency) | `NEXUS_INGEST_URL` | Not in the sandbox (no toolchain) — built and self-tested in CI |
| **C# / .NET** | `dotnet/Program.cs`, `dotnet/NexusExport.csproj` | GraphML/CSV/JSON/Cypher export service | `NEXUS_EXPORT_URL` | Not in the sandbox (no SDK) — built and self-tested in CI |
| **C++17** | `../native/` | Graph kernel: PageRank, Louvain, betweenness, kNN, link prediction | in-process subprocess | **Yes** — built and benchmarked |
| **JavaScript** | `../frontend/` | 3D graph UI (vendored three.js r160, no build step) | served by the API | **Yes** — syntax-checked, served over HTTP |
| **Python** | `../backend/` | API, GraphRAG, agent, gap engine, reference implementations of every sidecar's job | — | **Yes** |

## The Kotlin planner was executed too, and it agrees with its Python port

The API needs to answer `POST /api/plan` with or without a JVM, so the same DSL has two
implementations: the Kotlin planner (reference) and `backend/services/planner.py` (port).
Neither "it compiles" nor "CI will catch it" was good enough here:

1. **Toolchain without a system JVM**: kotlinc 2.4.21 came from the npm package
   `kotlin-compiler` and a Temurin JDK 25 from the wheel `jdk4py` —
   `npm pack kotlin-compiler && pip download jdk4py`, nothing to install.
2. `kotlinc NexusPlanner.kt -include-runtime -d planner.jar` compiled clean, and
   `java -jar planner.jar --test` **exited 0**. Running it first time found four real
   defects, all fixed in this repository:
   * `MATCH (nn WHERE n:Paper OR n:Method)` — an unbound variable and a syntax error, so
     every label-restricted query was invalid Cypher;
   * `topic:"X"` was planned as an exact-name equality instead of the full-text seed;
   * filters were appended *after* `RETURN` in the full-text path;
   * the cost model was unbounded (`estimated_nodes_visited` grew as `limit × 8^depth`).
3. The sidecar then ran for real: `java -jar planner.jar --serve 8092`, `GET /api/plan`
   returning Cypher + typed bind parameters + explanation + cost, and `GET /api/services`
   reporting it as live. `python scripts/compare_planners.py --url http://127.0.0.1:8092`
   and `--jar planner.jar` both answer: **"kotlin and python planners agree on all 11
   queries"** (plus whatever extra DSL you pass).
4. The fixture `tests/data/planner_parity.json` is the captured JVM output, and
   `tests/test_planner.py` fails the build when the Python port drifts from it.

Bind parameters keep their JSON type (`limit: 30`, not `"30"`) because a string would make
`LIMIT $limit` fail in Neo4j; the parity check compares that too.

## The Ruby claim resolver was executed, and it agrees with Python

The correctness claim in `backend/ingestion/claims.py` — "both engines use the SAME
additive, auditable rule set" — was verified rather than asserted:

1. `polyglot/ruby/claim_resolver.rb` was run on **CRuby 3.2 compiled to WebAssembly**
   (`ruby+stdlib.wasm` from npm `@ruby/3.2-wasm-wasi`, driven by Node 22 through
   `@ruby/wasm-wasi`), so no system Ruby was needed.
2. Its own `--test` suite executed end-to-end in the VM and exited 0.
3. Both engines then processed the **51 curated claims** in `data/demo/corpus.json`.
   Result: **identical** output — 18 conflicts, the same claim pairs, the same scores and
   the same kinds, e.g.

   | Pair | Score | Kind |
   | --- | --- | --- |
   | `2502.12110:1` × `2505.16067:1` | 0.875 | polarity |
   | `2508.19828:1` × `2505.16067:2` | 0.855 | polarity |
   | `2505.16067:2` × `2303.11366:1` | 0.845 | polarity |

   The full set is the fixture `tests/data/claim_parity.json`; the Python side of the
   contract is asserted by
   `tests/test_agent_rag.py::test_conflict_detection_matches_the_ruby_reference_engine`,
   so a change to either implementation fails the suite until both are updated.

The run also surfaced three real defects, now fixed: `add()` exploded a single-Hash
payload into key/value pairs; negation was evaluated on stopword-filtered tokens (so
"does not improve" stopped being a negation); and the Ruby weights/direction axes had
drifted from the published model. The Python side had a matching defect — `"degrades"`
normalised to `"degrad"` and silently dropped a real opposing-direction signal — which is
exactly what cross-implementation verification is for.

## Re-running the Ruby parity check

```bash
npm pack @ruby/3.2-wasm-wasi @ruby/wasm-wasi     # ~31 MB, engine only
# unpack, then drive claim_resolver.rb with @ruby/wasm-wasi's DefaultRubyVM
# (see the driver used for the verification: embed the file with Base64 and eval it,
#  ARGV = ["--test"] for the self-test, or feed {"claims": [...]} for parity runs)
```

With a system Ruby installed it is simply:

```bash
ruby polyglot/ruby/claim_resolver.rb --test
echo '{"claims":[{"id":"c1","text":"Scaling improves reasoning"},
                 {"id":"c2","text":"Scaling does not improve reasoning"}]}' \
  | ruby polyglot/ruby/claim_resolver.rb
```

The same resolver also runs as the service the API expects (`POST /resolve`, `GET
/health`, stdlib only — no gems), which is what `NEXUS_CLAIM_URL` points at:

```bash
ruby polyglot/ruby/claim_resolver.rb --serve 8093 &
curl -s -X POST localhost:8093/resolve -H 'content-type: application/json' \
  -d @/tmp/claims.json > /tmp/ruby_http.json
export NEXUS_CLAIM_URL=http://127.0.0.1:8093
python scripts/compare_claim_engines.py /tmp/ruby_http.json
```

(The socket mode needs a native CRuby; WASI has no sockets, so the wasm run above covers
the resolver logic and this covers the transport. CI does both.)

## Running a sidecar

Each sidecar is an HTTP service on a loopback port; give NEXUS its **base URL** and the
same interface is served from the sidecar instead of Python. Nothing else changes — the
route you already call stays the same, and the response names the engine that answered.

```bash
# Kotlin planner (:8092) — GET {url}/api/plan?q=<dsl>
npm pack kotlin-compiler && tar xzf kotlin-compiler-*.tgz          # kotlinc, no install
pip download jdk4py && unzip -q jdk4py*.whl -d jdk                 # a Temurin JDK, no install
package/bin/kotlinc polyglot/kotlin/src/main/kotlin/NexusPlanner.kt -include-runtime -d planner.jar
jdk/jdk4py/java-runtime/bin/java -jar planner.jar --serve 8092
export NEXUS_PLANNER_URL=http://127.0.0.1:8092

# Ruby claim resolver (:8093) — POST {url}/resolve, GET {url}/health
ruby polyglot/ruby/claim_resolver.rb --serve 8093
export NEXUS_CLAIM_URL=http://127.0.0.1:8093

# Go ingest (:8090) — /health, /v1/embeddings, /extract, /fetch/arxiv
cd polyglot/go && go run . serve
export NEXUS_INGEST_URL=http://127.0.0.1:8090

# C# export (:8091) — /health, /graphml, /csv, /report
cd polyglot/dotnet && dotnet run --project NexusExport.csproj -- serve 8091
export NEXUS_EXPORT_URL=http://127.0.0.1:8091
```

Inspect what is actually live at any time — the UI shows the same payload on Home:

```bash
curl -s localhost:8000/api/services | jq '.summary, .sidecars[] | {language, reachable, engine}'
```

Every call site is fail-soft: a timeout, a connection refusal or a malformed response
falls back to the Python implementation, and the response still says which engine ran.

## What has and has not been executed

| Sidecar | Compiled here | Executed here | Checked in CI |
| --- | --- | --- | --- |
| Kotlin planner | yes — kotlinc 2.4.21 (npm `kotlin-compiler`) | yes — OpenJDK 25 (`jdk4py`): `--test` exit 0, `--plan`, `--serve` + `GET /api/plan`, `/api/services` | compile + `--test` + `--plan` + `scripts/compare_planners.py` |
| Ruby claim resolver | — (interpreted) | yes — CRuby 3.2 via `ruby.wasm`: `--test` exit 0, 51-claim parity run | `--test` + `--serve` + HTTP `/resolve` + `scripts/compare_claim_engines.py` |
| C++ kernel | yes — g++ | yes — built under `pytest`, benchmarks in `native/README.md` | build + algorithm tests |
| Go ingest | no toolchain here | **no** | `go vet`, `go build`, `nexus-ingest test` |
| C#/.NET export | no SDK here | **no** | `dotnet build`, `nexus-export test` |

The Go and .NET sidecars are complete implementations that have never been run in this
environment: no toolchain was available and GitHub release assets were unreachable
(HTTP 000 / empty 302), so they could not be fetched. Treat them as reviewed-by-hand
rather than proven — that is exactly what the table above is for. CI builds and
self-tests them on every push, and reports anything a runner cannot provide as an
explicit `::warning::` instead of a silent pass.
