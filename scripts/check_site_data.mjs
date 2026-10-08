#!/usr/bin/env node
/* Check the static site's in-browser engine against recorded data and the Kotlin fixture.
 *
 *   node scripts/check_site_data.mjs                     # needs ./site (run build_site.py first)
 *   node scripts/check_site_data.mjs --out /tmp/js.json  # also dump computed payloads
 *
 * What this proves without a browser:
 *   1. every `/api/...` path the front end calls is answered by the static layer (no
 *      silent "not available" for a view the UI actually uses);
 *   2. the DSL planner in `frontend/assets/site.js` reproduces the Kotlin reference
 *      plans in `tests/data/planner_parity.json` exactly (cypher, params, explanation,
 *      cost and parsed query);
 *   3. recorded payloads are served unchanged (gaps, reports, agent answers);
 *   4. payload *shapes* computed in the browser match the recorded ones (nodes with
 *      metrics, papers with community, paths with hops, …).
 *
 * The numeric comparisons against a live engine live in scripts/compare_site_data.py,
 * which runs the same requests through FastAPI and diffs the dump this file writes.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(new URL("..", import.meta.url).pathname);
const SITE = path.join(ROOT, "site");
const outArg = process.argv.indexOf("--out");
const OUT = outArg !== -1 ? process.argv[outArg + 1] : null;

const problems = [];
const ok = (label, extra = "") => console.log(`  ok   ${label}${extra ? "  " + extra : ""}`);
const fail = (label, detail) => { problems.push(`${label}: ${detail}`); console.log(`  FAIL ${label}  ${detail}`); };

function readJSON(file) {
  return JSON.parse(fs.readFileSync(path.join(SITE, file), "utf8"));
}
function exists(file) {
  return fs.existsSync(path.join(SITE, file));
}

if (!exists("data/graph.json")) {
  console.error("site/data/graph.json is missing — run `python scripts/build_site.py` first");
  process.exit(2);
}

const graph = readJSON("data/graph.json");
const index = readJSON("data/index.json");
const NEXUS = require(path.join(SITE, "assets", "site.js"));

// hand the already-parsed datasets to the layer (no browser, no fetch)
const datasets = { "graph.json": graph, "index.json": index };
for (const name of fs.readdirSync(path.join(SITE, "data"))) {
  if (name.endsWith(".json") && name !== "graph.json" && name !== "index.json") {
    datasets[name] = readJSON(path.join("data", name));
  }
}
// a file-backed fetch, so the layer can also pull the .md/.txt recordings in node
const fileFetch = async (url) => {
  const file = path.join(SITE, url);
  const present = fs.existsSync(file);
  return {
    ok: present,
    status: present ? 200 : 404,
    json: async () => JSON.parse(fs.readFileSync(file, "utf8")),
    text: async () => fs.readFileSync(file, "utf8"),
  };
};
NEXUS.configure({ datasets, graph, index, loaded: true, dataDir: "data/", fetchImpl: fileFetch });

const dump = { engine: NEXUS.VERSION, corpus: index.counts, results: {} };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* ---------------------------------------------------------------- 1 · plans */
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, "tests/data/planner_parity.json"), "utf8"));
let planDiffs = 0;
for (const row of fixture.queries) {
  const expected = row.plan;
  let got;
  try {
    got = NEXUS.plan(row.dsl);
  } catch (err) {
    fail(`plan ${row.dsl.slice(0, 40) || "<empty>"}`, err.detail || err.message);
    planDiffs += 1;
    continue;
  }
  const fields = ["ok", "error", "query", "cypher", "params", "explanation", "cost"];
  const diffs = fields.filter((field) => !same(got[field] ?? null, expected[field] ?? null));
  if (diffs.length) {
    planDiffs += 1;
    fail(`plan ${row.dsl.slice(0, 40) || "<empty>"}`, diffs.join(", "));
    for (const field of diffs) {
      console.log(`       kotlin: ${JSON.stringify(expected[field])?.slice(0, 160)}`);
      console.log(`       js    : ${JSON.stringify(got[field])?.slice(0, 160)}`);
    }
  }
}
if (!planDiffs) {
  ok(`js planner matches the kotlin reference on all ${fixture.queries.length} fixture queries`,
     `(fixture: ${fixture.verified_with.split(" (")[0]})`);
}
dump.results.plans = fixture.queries.length;

/* ------------------------------------------- 2 · every route the UI calls */
const appjs = fs.readFileSync(path.join(SITE, "assets", "app.js"), "utf8");
const literals = [...appjs.matchAll(/api\(\s*[`"']([^`"'$]*)/g)].map((m) => m[1]);
const routes = new Set(literals.filter((p) => p.startsWith("/api/")).map((p) => p.split("?")[0]));
// the template-literal calls the regex cannot capture, listed explicitly
[["/api/papers", { limit: 25, offset: 0, sort: "pagerank" }],
 ["/api/papers/paper:2210.03629", null],
 ["/api/nodes/paper:2210.03629", null],
 ["/api/explorer", null],
 ["/api/timeline", null]].forEach(([route, body]) => routes.add(route));

const calls = {
  "/api/health": {}, "/api/dashboard": {}, "/api/services": {}, "/api/timeline": {},
  "/api/explorer": {}, "/api/graph": {}, "/api/communities": {}, "/api/conflicts": {},
  "/api/opportunity-score": {}, "/api/algorithms": {}, "/api/safety": {}, "/api/tools": {},
  "/api/mcp": {}, "/api/centrality": {}, "/api/predictions": {}, "/api/export/cypher": {},
  "/api/plan": { body: { query: 'topic:"AI Agents" limit 20' } },
  "/api/gaps": { body: { topic: "AI Agents", top_k: 5 } },
  "/api/agent": { body: { question: index.precomputed.questions[0] } },
  "/api/report": { body: { topic: "AI Agents" } },
  "/api/report/markdown": { body: { topic: "AI Agents" } },
  "/api/graph/expand": { body: { node_ids: ["paper:2210.03629"], limit: 40 } },
  "/api/papers": { params: "?limit=25&offset=0&sort=pagerank" },
  "/api/papers/paper:2210.03629": {},
  "/api/nodes": { params: "?label=Paper&limit=5" },
  "/api/nodes/paper:2210.03629": {},
  "/api/search": { params: "?q=agent%20memory" },
  "/api/graph/path": { params: "?source=paper:2210.03629&target=topic:agent-memory" },
  "/api/graph/neighbours/paper:2210.03629": {},
};

// `api(`/api/papers/${id}`)` matches as "/api/papers/" — those prefixes are covered
// by the concrete dynamic routes below, so drop bare prefixes.
[...routes].forEach((route) => {
  if (route.endsWith("/") && Object.keys(calls).some((known) => known.startsWith(route))) routes.delete(route);
});
const unknown = [...routes].filter((route) => !Object.keys(calls).includes(route));
if (unknown.length) fail("routes used by the UI but not covered by this check", unknown.join(", "));

let routeProblems = 0;
for (const route of routes) {
  const call = calls[route];
  if (!call) continue;
  const url = route + (call.params || "");
  try {
    const payload = await NEXUS.api(url, call.body ? { method: "POST", body: call.body } : {});
    if (payload === undefined || payload === null) throw new Error("empty payload");
    if (typeof payload === "string" && !payload.trim()) throw new Error("empty text payload");
  } catch (err) {
    routeProblems += 1;
    fail(`route ${url}`, err.detail || err.message);
  }
}
if (!routeProblems) ok(`all ${routes.size} routes the UI calls are answered by the static layer`);

/* ------------------------------------------------------- 3 · recorded data */
const served = [
  ["gaps--ai-agents.json", () => NEXUS.gaps({ topic: "AI Agents", top_k: 5 })],
  ["gaps--agent-memory.json", () => NEXUS.gaps({ topic: "Agent Memory", top_k: 5 })],
  ["report--ai-agents.json", () => NEXUS.report({ topic: "AI Agents" })],
];
console.log(`  · ${Object.keys(datasets).length} recorded payloads loaded`);
let passthroughProblems = 0;
for (const [file, run] of served) {
  const recorded = datasets[file];
  if (!recorded) { fail(`recorded ${file}`, "missing from the build"); passthroughProblems += 1; continue; }
  const payload = await run();
  const strip = (value) => { const copy = JSON.parse(JSON.stringify(value)); delete copy.static_snapshot; return copy; };
  if (strip(payload) === undefined || JSON.stringify(strip(payload)) !== JSON.stringify(strip(recorded))) {
    fail(`recorded ${file}`, "the static layer does not serve the recording unchanged");
    passthroughProblems += 1;
  }
}
if (!passthroughProblems) ok("recorded gap and report payloads are served unchanged");

const question = index.precomputed.questions[0];
const answer = await NEXUS.agent(question, {});
const recordedAnswer = datasets[`agent--${NEXUS.slug(question)}.json`];
if (!recordedAnswer) fail("recorded agent answer", `agent--${NEXUS.slug(question)}.json missing`);
else if (!same(answer.explainability, recordedAnswer.explainability)) {
  fail("recorded agent answer", "explainability block differs from the recording");
} else ok(`agent serves the recorded answer for “${question.slice(0, 48)}…”`);

const stream = await NEXUS.stream(question);
if (!/^event: intent/m.test(stream) || !/event: answer/.test(stream)) {
  fail("agent stage stream", "recorded SSE frames missing intent/answer events");
} else ok("agent SSE stage trace replays (intent → … → answer)");

const unknownGaps = await NEXUS.gaps({ topic: "Quantum Basket Weaving", top_k: 5 });
if (unknownGaps.opportunities.length !== 0 || !unknownGaps.reason || !unknownGaps.safety_notice) {
  fail("honest empty state for an un-precomputed scope", JSON.stringify(unknownGaps).slice(0, 120));
} else ok("an un-precomputed scope returns the documented empty state with a reason");

/* --------------------------------------------- 4 · computed payload shapes */
const checks = [
  ["graph default", async () => NEXUS.graph({}), (p) => p.nodes.length && p.edges.length && p.legend && p.status.backend === "static-snapshot"],
  ["graph seeded", async () => NEXUS.graph({ seeds: ["paper:2210.03629"], depth: 1 }), (p) => p.nodes.some((n) => n.id === "paper:2210.03629")],
  ["graph focus", async () => NEXUS.graph({ focus: "community:0" }), (p) => p.focus === null || p.focus.id],
  ["neighbours", async () => NEXUS.neighbours("paper:2210.03629", 40), (p) => p.center.id === "paper:2210.03629" && p.neighbours.length > 0],
  ["path", async () => NEXUS.path("paper:2210.03629", "topic:agent-memory", 4), (p) => p.found === true && p.hops.length === p.hops_count],
  ["papers page", async () => NEXUS.papers(null, { limit: 25 }), (p) => p.total === index.counts.labels.Paper && p.items.length === 25],
  ["paper detail", async () => NEXUS.paperDetail("paper:2210.03629"), (p) => p.metrics.pagerank > 0 && Array.isArray(p.claims)],
  ["node detail", async () => NEXUS.nodeDetail("topic:agent-memory"), (p) => p.metrics_explained.length >= 2],
  ["search", async () => NEXUS.search("agent memory", null, 12), (p) => p.length > 0 && p[0].id.startsWith("topic:")],
  ["timeline", async () => NEXUS.timeline("AI Agents"), (p) => Object.keys(p.papers_per_year).length > 0],
  ["explorer", async () => NEXUS.explorer({ topic: "AI Agents" }), (p) => p.counts.papers > 0 && p.top_papers.length > 0],
  ["timeline (whole corpus)", async () => NEXUS.timeline(null),
   (p) => JSON.stringify(p.papers_per_year) === JSON.stringify(datasets["timeline--default.json"].papers_per_year)],
];
dump.results.records = {};
for (const [label, run, assert] of checks) {
  try {
    const payload = await run();
    if (!assert(payload)) throw new Error("shape assertion failed");
    ok(label, `(${JSON.stringify(payload).length} bytes)`);
  } catch (err) {
    fail(label, err.detail || err.message);
  }
}
const weight = (payload) => JSON.stringify(payload).length;
const byteCounts = [];
for (const [label, run] of checks) {
  try { byteCounts.push([label, weight(await run())]); } catch { byteCounts.push([label, 0]); }
}
dump.results.bytes = Object.fromEntries(byteCounts);

/* the computed payloads, for scripts/compare_site_data.py to diff against the engine */
dump.results.computed = {
  graph: [await NEXUS.graph({}), await NEXUS.graph({ seeds: ["paper:2210.03629"], depth: 1 }), await NEXUS.graph({ focus: "community:c14" })],
  paths: index.references.paths.map((pair) => Object.assign({}, pair, { payload: NEXUS.path(pair.source, pair.target, 4) })),
  papers: index.references.papers.map((id) => NEXUS.paperDetail(id)),
  paper_lists: await Promise.all(index.references.paper_lists.map(async (query) => ({ query, payload: await NEXUS.papers(null, query) }))),
  nodes: index.references.nodes.map((id) => NEXUS.nodeDetail(id)),
  neighbours: index.references.neighbours.map((id) => ({ id, payload: NEXUS.neighbours(id, 40) })),
  node_lists: await Promise.all(index.references.node_lists.map(async (query) => ({ query, payload: await NEXUS.api(`/api/nodes?label=${query.label}&limit=${query.limit}&sort=${query.sort}`) }))),
  search: index.references.search.map((query) => ({ query, payload: NEXUS.search(query.q, ["Topic"], 5) })),
  explorer: await Promise.all(index.references.explorer.map(async (query) => ({ query, payload: await NEXUS.explorer(query) }))),
  timeline: await Promise.all(index.references.timeline.map(async (query) => ({ query, payload: await NEXUS.timeline(query.topic) }))),
};

if (OUT) {
  fs.writeFileSync(OUT, JSON.stringify(dump, null, 1));
  console.log(`  · computed payloads written to ${OUT} (${(fs.statSync(OUT).size / 1024).toFixed(0)} KB)`);
}

console.log(problems.length
  ? `\nstatic layer: ${problems.length} problem(s)`
  : `\nstatic layer verified: planner parity, ${routes.size} routes, recorded data and computed payloads`);
process.exit(problems.length ? 1 : 0);
