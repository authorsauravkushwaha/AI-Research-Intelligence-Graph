# Polyglot sidecars

NEXUS gives each language a job it is actually good at. Nothing in the demo depends on
these services being reachable — when a sidecar is not running, the Python implementation
behind the same interface answers instead, and the response says which engine ran.

| Language | File | Role | Endpoint (env var) | Executed here? |
| --- | --- | --- | --- | --- |
| **Ruby** | `ruby/claim_resolver.rb` | Claim parsing, negation/polarity, direction axes, conflict scoring | `NEXUS_CLAIM_URL` (HTTP) | **Yes** — CRuby 3.2 (wasm) on the real corpus, see below |
| **Kotlin (JVM)** | `kotlin/src/main/kotlin/NexusPlanner.kt` | Query planner: natural-language question → Cypher plan | `NEXUS_PLANNER_URL` | Not in the sandbox (no JVM) — compiled and self-tested in CI |
| **Go** | `go/main.go` | Corpus ingestion + embedding service (static binary, concurrency) | `NEXUS_INGEST_URL` | Not in the sandbox (no toolchain) — built and self-tested in CI |
| **C# / .NET** | `dotnet/Program.cs`, `dotnet/NexusExport.csproj` | GraphML/CSV/JSON/Cypher export service | `NEXUS_EXPORT_URL` | Not in the sandbox (no SDK) — built and self-tested in CI |
| **C++17** | `../native/` | Graph kernel: PageRank, Louvain, betweenness, kNN, link prediction | in-process subprocess | **Yes** — built and benchmarked |
| **JavaScript** | `../frontend/` | 3D graph UI (vendored three.js r160, no build step) | served by the API | **Yes** — syntax-checked, served over HTTP |
| **Python** | `../backend/` | API, GraphRAG, agent, gap engine, reference implementations of every sidecar's job | — | **Yes** |

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

## Wiring a sidecar in

Each sidecar is an HTTP service; point NEXUS at it and the same interface is served from
the sidecar instead of Python:

```bash
export NEXUS_CLAIM_URL=http://127.0.0.1:8088/resolve   # Ruby
export NEXUS_PLANNER_URL=http://127.0.0.1:8092/plan   # Kotlin
export NEXUS_INGEST_URL=http://127.0.0.1:8090/ingest  # Go
export NEXUS_EXPORT_URL=http://127.0.0.1:8091/export  # C#/.NET
```

Every call site is fail-soft: a timeout, a connection refusal or a malformed response
falls back to the Python implementation and is recorded in the response metadata rather
than surfacing as an error to the user.

## Sidecars that could not be executed in this environment

The Kotlin, Go and .NET sidecars are complete implementations, but the development sandbox
had no JVM, Go toolchain or .NET SDK, and GitHub release assets were unreachable (HTTP 000
/ empty 302) so toolchains could not be fetched. They are therefore **unverified by
execution** — treat their code as reviewed-by-hand, not proven. CI (`.github/workflows/ci.yml`,
job `polyglot`) builds and self-tests all four sidecars on every push: `go vet` + `go build`
+ `nexus-ingest test`, `dotnet build` + `nexus-export test`, the Kotlin planner compiled with
the embeddable compiler (`--test`, and a real `--plan` query), and the Ruby resolver's own
`--test` plus the Python-parity comparison via `scripts/compare_claim_engines.py`. Whatever
the runners cannot provide is reported as an explicit warning step, never as a silent
success.
