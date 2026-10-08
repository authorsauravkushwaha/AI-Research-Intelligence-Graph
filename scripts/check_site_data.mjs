#!/usr/bin/env node
/**
 * NEXUS — static snapshot verifier.
 *
 * The published site (GitHub Pages) has no backend: `assets/site.js` answers /api/* from the
 * snapshot in ./data/ and, for the interactive views, by recomputing them in the browser over
 * data/graph.json. "Recomputed in the browser" is only honest if it reproduces what the engine
 * said — so this script loads the *shipped* site.js against the *shipped* snapshot and compares
 * every recomputed view with the payload the FastAPI engine recorded for the same scope at
 * build time.
 *
 *   node scripts/check_site_data.mjs --site site
 *   node scripts/check_site_data.mjs --site site --json checks.json --quiet
 *
 * Exit code 0 = every recorded answer is reproduced. 1 = at least one mismatch (printed with
 * the JSON path that differs). 2 = the build itself is unusable (missing shell, missing data).
 *
 * Comparison rules, and why they are not "loose":
 *   · every key present in the engine's payload must be present and equal in the browser's;
 *     extra keys the browser adds (status envelope, static_snapshot note) are allowed and listed.
 *   · node/edge arrays are compared as sets keyed by `id`, because the engine serialises them
 *     from Python sets — their order is not part of the contract.
 *   · floats are compared with a 1e-9 relative tolerance (same rounding, different runtimes).
 *   · `status`/`static_snapshot`/`note` are the only keys exempted, and the exemption is printed.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const IGNORED_KEYS = new Set(["status", "static_snapshot"]);
const TOLERANCE = 1e-9;
/* The engine rounds its own scores/metrics to 5 decimals before serialising them; the
 * browser rounds the 6-decimal value it was shipped. Re-rounding a rounded number can land
 * one unit in the last place away from re-rounding the original (a float boundary, not a
 * disagreement), so differences up to this are counted and reported, not failed. */
const BOUNDARY = 2e-5;

/* --------------------------------------------------------------------- args */

function parseArgs(argv) {
  const args = { site: "site", json: null, quiet: false };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--site") args.site = argv[++i];
    else if (token === "--json") args.json = argv[++i];
    else if (token === "--quiet") args.quiet = true;
    else if (token === "--help" || token === "-h") {
      console.log("usage: node scripts/check_site_data.mjs [--site site] [--json out.json] [--quiet]");
      process.exit(0);
    } else throw new Error(`unknown argument: ${token}`);
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
const siteDir = path.resolve(args.site);

/* ------------------------------------------------------------- site loading */

async function exists(target) {
  try { await fs.stat(target); return true; } catch { return false; }
}

async function readJson(target) {
  return JSON.parse(await fs.readFile(target, "utf8"));
}

/** A fetch() shim over the built directory, so the shipped site.js runs unmodified. */
function fileFetch(root) {
  return async function fetchShim(url) {
    const clean = String(url).replace(/^\.?\//, "");
    const target = path.join(root, clean);
    let body;
    try {
      body = await fs.readFile(target);
    } catch {
      return new Response(null, { status: 404 });
    }
    return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
  };
}

/* ------------------------------------------------------------- comparisons */

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sameNumber(a, b) {
  return Math.abs(a - b) <= TOLERANCE * Math.max(1, Math.abs(a), Math.abs(b));
}

function stableKey(value) {
  if (Array.isArray(value)) return `[${value.map(stableKey).join(",")}]`;
  if (isObject(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableKey(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function truncate(text, limit = 110) {
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function keyedById(list) {
  return Array.isArray(list) && list.length > 0 && list.every((item) => isObject(item) && typeof item.id === "string");
}

function compare(expected, actual, where, issues, extras, boundary, allow) {
  if (allow && allow.has(where)) {
    boundary.allowed.add(`${where} = ${truncate(JSON.stringify(actual))}`);
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) {
      issues.push(`${where}: expected array, got ${actual === null ? "null" : typeof actual}`);
      return;
    }
    if (keyedById(expected)) {
      /* Entries are matched by id — but an id can legitimately appear twice (a paper can be
         both source and target of a SIMILAR_TO edge), so each id's entries are matched as a
         multiset by their full content before the fields are compared. */
      if (expected.length !== actual.length) {
        issues.push(`${where}: expected ${expected.length} entries, got ${actual.length}`);
      }
      const group = (list) => {
        const byId = new Map();
        list.filter(isObject).forEach((item) => {
          if (!byId.has(item.id)) byId.set(item.id, []);
          byId.get(item.id).push(item);
        });
        return byId;
      };
      const expectedById = group(expected);
      const actualById = group(actual);
      for (const [id, entries] of expectedById) {
        const matches = actualById.get(id);
        if (!matches) {
          issues.push(`${where}[${id}]: missing in the browser answer`);
          continue;
        }
        if (entries.length === matches.length) {
          /* Same count: the engine walks the store's edge list, and so does the browser,
             so the entries line up positionally — and field messages stay precise. */
          entries.forEach((item, index) => {
            compare(item, matches[index], `${where}[${id}][${index}]`, issues, extras, boundary, allow);
          });
          continue;
        }
        issues.push(`${where}[${id}]: expected ${entries.length} entries, got ${matches.length}`);
        const remaining = matches.slice();
        entries.forEach((item) => {
          const key = stableKey(item);
          const at = remaining.findIndex((candidate) => stableKey(candidate) === key);
          if (at === -1) {
            issues.push(`${where}[${id}]: the browser's entry differs — expected ${truncate(key)}`);
            if (remaining.length) compare(item, remaining.shift(), `${where}[${id}]`, issues, extras, boundary, allow);
            return;
          }
          const match = remaining.splice(at, 1)[0];
          compare(item, match, `${where}[${id}]`, issues, extras, boundary);
        });
        remaining.forEach((item) => issues.push(`${where}[${id}]: unexpected extra entry ${truncate(stableKey(item))}`));
      }
      for (const id of actualById.keys()) {
        if (!expectedById.has(id)) issues.push(`${where}[${id}]: not in the engine's answer`);
      }
      return;
    }
    if (expected.length !== actual.length) {
      issues.push(`${where}: expected ${expected.length} entries, got ${actual.length}`);
      return;
    }
    expected.forEach((item, index) => compare(item, actual[index], `${where}[${index}]`, issues, extras, boundary, allow));
    return;
  }

  if (isObject(expected)) {
    if (!isObject(actual)) {
      issues.push(`${where}: expected object, got ${actual === null ? "null" : typeof actual}`);
      return;
    }
    for (const [key, value] of Object.entries(expected)) {
      if (IGNORED_KEYS.has(key)) continue;
      if (!(key in actual)) issues.push(`${where}.${key}: missing in the browser answer`);
      else compare(value, actual[key], `${where}.${key}`, issues, extras, boundary, allow);
    }
    for (const key of Object.keys(actual)) {
      if (IGNORED_KEYS.has(key)) extras.add(`${where}.${key}`);
      else if (!(key in expected)) extras.add(`${where}.${key}`);
    }
    return;
  }

  if (typeof expected === "number" && typeof actual === "number") {
    if (sameNumber(expected, actual)) return;
    if (Math.abs(expected - actual) <= BOUNDARY) {
      boundary.count += 1;
      boundary.examples.add(`${where} (${expected} vs ${actual})`);
      return;
    }
    issues.push(`${where}: expected ${expected}, got ${actual}`);
    return;
  }
  if (expected !== actual) {
    const shown = (value) => (typeof value === "string" && value.length > 90 ? `${value.slice(0, 90)}…` : JSON.stringify(value));
    issues.push(`${where}: expected ${shown(expected)}, got ${shown(actual)}`);
  }
}

/* ----------------------------------------------------------------- harness */

const results = [];
const notes = [];

function record(name, group, expectedLength, issues, extras, boundary) {
  results.push({
    name, group,
    ok: issues.length === 0,
    compared: expectedLength,
    issues: issues.slice(0, 12),
    issue_count: issues.length,
    extra_keys: [...extras].slice(0, 6),
    rounding_boundary: boundary ? boundary.count : 0,
    allowed_differences: boundary && boundary.allowed ? [...boundary.allowed] : [],
  });
  const tag = issues.length === 0 ? "PASS" : "FAIL";
  if (!args.quiet || issues.length) console.log(`${tag}  ${name}`);
  issues.slice(0, 6).forEach((issue) => console.log(`        ${issue}`));
  if (issues.length > 6) console.log(`        … ${issues.length - 6} more`);
}

function countLeaves(value) {
  if (Array.isArray(value)) return value.reduce((sum, item) => sum + countLeaves(item), 0);
  if (isObject(value)) return Object.values(value).reduce((sum, item) => sum + countLeaves(item), 0);
  return 1;
}

const boundaryTotal = { count: 0, examples: new Set() };

function check(name, group, expected, actual, allow) {
  const issues = [];
  const extras = new Set();
  const boundary = { count: 0, examples: new Set(), allowed: new Set() };
  compare(expected, actual, "$", issues, extras, boundary, allow ? new Set(allow) : null);
  boundary.allowed.forEach((item) => notes.push(`allowed difference: ${item}`));
  boundaryTotal.count += boundary.count;
  boundary.examples.forEach((item) => boundaryTotal.examples.add(item));
  record(name, group, countLeaves(expected), issues, extras, boundary);
}

function checkTrue(name, group, ok, detail) {
  record(name, group, 1, ok ? [] : [detail], new Set());
}

/* -------------------------------------------------------------------- main */

async function main() {
  const shell = {
    "index.html": "the page itself",
    "assets/app.js": "the front end",
    "assets/style.css": "the stylesheet",
    "assets/site.js": "this snapshot data layer",
    "manifest.webmanifest": "the installable-app manifest",
    "sw.js": "the offline service worker",
    ".nojekyll": "GitHub Pages marker (no Jekyll pass)",
  };

  console.log(`NEXUS static snapshot check — ${siteDir}`);
  const missingShell = [];
  for (const name of Object.keys(shell)) {
    if (!(await exists(path.join(siteDir, name)))) missingShell.push(name);
  }
  if (missingShell.length) {
    console.error(`\nunusable build: missing ${missingShell.join(", ")}`);
    return 2;
  }

  const index = await readJson(path.join(siteDir, "data", "index.json"));
  const graph = await readJson(path.join(siteDir, "data", "graph.json"));
  const files = index.files || {};
  const topics = (index.precomputed && index.precomputed.topics) || [];
  const planQueries = (index.precomputed && index.precomputed.plan_queries) || [];

  /* ---------------------------------------------------- 1. shell + manifest */
  const html = await fs.readFile(path.join(siteDir, "index.html"), "utf8");
  checkTrue("index.html carries the snapshot + loader", "shell",
    html.includes('id="nexus-snapshot"') && html.includes("assets/site.js") && html.includes("assets/app.js"),
    "index.html is missing #nexus-snapshot or the asset includes");
  checkTrue("index.html references only relative assets", "shell",
    !/(?:src|href)="\/(?:assets|vendor|icons|data)\//.test(html),
    "absolute /assets|/vendor|/icons|/data URLs break on a project Pages site");
  checkTrue("index.html has no localhost dependency", "shell",
    !/localhost|127\.0\.0\.1/.test(html),
    "the published page must not call localhost");
  checkTrue("index.html offers the install affordance", "shell",
    html.includes('id="install-app"'),
    "#install-app button is missing (PWA install path)");

  const manifest = await readJson(path.join(siteDir, "manifest.webmanifest"));
  const iconNames = (manifest.icons || []).map((icon) => icon.src);
  const missingIcons = [];
  for (const icon of iconNames) {
    const relative = icon.replace(/^(\.\/|\/)/, "");
    if (!(await exists(path.join(siteDir, relative)))) missingIcons.push(icon);
  }
  checkTrue("manifest icons all exist", "shell", missingIcons.length === 0,
    `missing icons: ${missingIcons.join(", ")}`);
  checkTrue("manifest is installable (192 + 512 + maskable)", "shell",
    iconNames.some((s) => s.includes("192")) && iconNames.some((s) => s.includes("512")) &&
      (manifest.icons || []).some((icon) => (icon.purpose || "").includes("maskable")) &&
      manifest.display === "standalone" && Boolean(manifest.start_url),
    "manifest lacks a 192px, a 512px or a maskable icon, or is not standalone");

  const sw = await fs.readFile(path.join(siteDir, "sw.js"), "utf8");
  checkTrue("service worker is versioned and caches the shell", "shell",
    /VERSION|version/.test(sw) && sw.includes("index.html"),
    "sw.js does not mention a version or the shell");

  /* ------------------------------------------------- 2. snapshot integrity */
  const listed = Object.keys(files);
  const missingFiles = [];
  const sizeMismatch = [];
  for (const name of listed) {
    const target = path.join(siteDir, "data", name);
    try {
      const stat = await fs.stat(target);
      if (files[name].bytes !== undefined && stat.size !== files[name].bytes) {
        sizeMismatch.push(`${name} (recorded ${files[name].bytes}, on disk ${stat.size})`);
      }
    } catch {
      missingFiles.push(name);
    }
  }
  checkTrue("every payload in index.json is on disk at the recorded size", "snapshot",
    missingFiles.length === 0 && sizeMismatch.length === 0,
    `missing: ${missingFiles.join(", ") || "none"}; size mismatch: ${sizeMismatch.join(", ") || "none"}`);

  const onDisk = (await fs.readdir(path.join(siteDir, "data"))).filter((name) => !name.startsWith("."));
  const unlisted = onDisk.filter((name) => !(name in files) && name !== "index.json");
  checkTrue("no unlisted payloads in data/", "snapshot", unlisted.length === 0,
    `unlisted: ${unlisted.join(", ")}`);

  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const danglingEdges = graph.edges.filter((edge) => !nodeIds.has(edge.source) || !nodeIds.has(edge.target));
  checkTrue("every edge endpoint is a node in the snapshot", "snapshot", danglingEdges.length === 0,
    `${danglingEdges.length} dangling edges, e.g. ${danglingEdges[0] && danglingEdges[0].id}`);
  checkTrue("graph counts agree with index.json", "snapshot",
    index.counts.nodes === graph.nodes.length && index.counts.edges === graph.edges.length,
    `index says ${index.counts.nodes}/${index.counts.edges}, graph has ${graph.nodes.length}/${graph.edges.length}`);

  const missingProps = graph.nodes.filter((node) => !node.props || Object.keys(node.props).length === 0).length;
  checkTrue("papers carry the properties search needs", "snapshot",
    graph.nodes.filter((node) => node.type === "Paper").every((node) => node.props.title && node.props.url),
    "a Paper node is missing title/url — search and paper pages would degrade");
  notes.push(`${missingProps} of ${graph.nodes.length} nodes have no properties of their own ` +
    "(Author/Topic/Community carry none, Method/Dataset are identified by name) — expected, not an error");
  checkTrue("the graph legend ships with the snapshot", "snapshot",
    Boolean(graph.legend && graph.legend.node_colors && graph.legend.rel_types),
    "graph.json has no legend, the UI would have no colours");

  /* ---------------------------------------------------- 3. boot site.js */
  const dynamic = require(path.join(siteDir, "assets", "site.js"));
  dynamic.configure({ fetcher: fileFetch(siteDir), dataDir: "data/" });
  await dynamic.ready();
  const status = dynamic.status();
  checkTrue("site.js boots into static mode over the snapshot", "boot",
    status.mode === "static" && status.graph && status.graph.nodes === graph.nodes.length,
    `booted with ${JSON.stringify(status.graph)}`);
  checkTrue("nothing the browser needs was missing at boot", "boot", status.missing.length === 0,
    `missing datasets: ${status.missing.join(", ")}`);

  const call = (route, options) => dynamic.api(route, options);

  /* --------------------------------------------- 4. recorded-answer parity */
  const recordedRequests = new Map();
  const slug = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "default";

  for (const name of listed) {
    if (name === "graph.json" || name === "papers.json" || name === "index.json") continue;
    if (name.endsWith(".md") || name.endsWith(".txt")) continue;
    if (name.startsWith("graph--default")) recordedRequests.set(name, () => call("/api/graph", { method: "POST", body: {} }));
    else if (name.startsWith("timeline--")) {
      const topic = topics.find((candidate) => slug(candidate) === name.slice("timeline--".length, -".json".length));
      if (topic) recordedRequests.set(name, () => call(`/api/timeline?topic=${encodeURIComponent(topic)}`));
    } else if (name === "timeline.json") recordedRequests.set(name, () => call("/api/timeline"));
    else if (name.startsWith("explorer--")) {
      const key = name.slice("explorer--".length, -".json".length);
      if (key === "default") recordedRequests.set(name, () => call("/api/explorer"));
      else {
        const topic = topics.find((candidate) => slug(candidate) === key);
        if (topic) recordedRequests.set(name, () => call(`/api/explorer?topic=${encodeURIComponent(topic)}`));
      }
    } else if (name.startsWith("plan--")) {
      const query = planQueries.find((candidate) => slug(candidate) === name.slice("plan--".length, -".json".length));
      if (query) recordedRequests.set(name, () => call("/api/plan", { method: "POST", body: { query } }));
    } else if (name.startsWith("paper--")) {
      const payload = await readJson(path.join(siteDir, "data", name));
      recordedRequests.set(name, () => call(`/api/papers/${encodeURIComponent(payload.id)}`));
    } else if (name.startsWith("node--")) {
      const payload = await readJson(path.join(siteDir, "data", name));
      recordedRequests.set(name, () => call(`/api/nodes/${encodeURIComponent(payload.id)}`));
    } else if (name.startsWith("search--")) {
      const payload = await readJson(path.join(siteDir, "data", name));
      recordedRequests.set(name, () => call(`/api/search?q=${encodeURIComponent(payload.query)}`));
    }
  }

  for (const [name, request] of recordedRequests) {
    const expected = await readJson(path.join(siteDir, "data", name));
    let actual;
    try {
      actual = await request();
    } catch (error) {
      record(`${name} — engine vs browser`, "parity", countLeaves(expected),
        [`the browser answered with an error: ${error.status || ""} ${error.message || error}`], new Set(), null);
      continue;
    }
    const allow = name.startsWith("plan--") ? ["$.engine", "$.source"] : null;
    check(`${name} — engine vs browser`, "parity", expected, actual, allow);
  }

  /* ---------------------------------- 5. the corpus list the UI paginates */
  const corpus = await readJson(path.join(siteDir, "data", "papers.json"));
  for (let offset = 0; offset < corpus.count; offset += 200) {
    const slice = { count: Math.min(200, corpus.count - offset), items: corpus.items.slice(offset, offset + 200) };
    const page = await call(`/api/papers?limit=200&offset=${offset}&sort=pagerank`);
    check(`GET /api/papers (offset ${offset}, ${slice.items.length} rows)`, "corpus",
      slice, { count: page.items.length, items: page.items });
  }

  /* --------------------------------------- 6. interactive views, no target */
  const expand = await call("/api/graph/expand", { method: "POST", body: { node_ids: [graph.nodes[0].id], limit: 40 } });
  checkTrue("POST /api/graph/expand answers with nodes + edges", "interactive",
    Array.isArray(expand.nodes) && expand.nodes.length >= 1 && Array.isArray(expand.edges),
    `expand returned ${expand.nodes && expand.nodes.length} nodes`);
  const routePath = await call(`/api/graph/path?source=${encodeURIComponent(graph.nodes[0].id)}&target=${encodeURIComponent(graph.nodes[0].id)}`);
  checkTrue("GET /api/graph/path finds a trivial path", "interactive",
    routePath.found === true && routePath.hops_count === 0,
    `path returned found=${routePath.found} hops=${routePath.hops_count}`);
  const neighbours = await call(`/api/graph/neighbours/${encodeURIComponent(graph.nodes[0].id)}`);
  checkTrue("GET /api/graph/neighbours answers with the centre node", "interactive",
    Boolean(neighbours.center && neighbours.center.id === graph.nodes[0].id) &&
      neighbours.neighbours.length <= neighbours.count && neighbours.count >= 1,
    "neighbours payload is malformed");
  const plan = await call("/api/plan", { method: "POST", body: { query: 'topic:"AI Agents" limit 40' } });
  checkTrue("POST /api/plan produces Cypher with bind parameters", "interactive",
    plan.ok === true && /CALL db\.index\.fulltext/.test(plan.cypher) && plan.params.limit === 40,
    `planner returned ${JSON.stringify(plan.error || plan.query)}`);
  let planError = null;
  try { await call("/api/plan", { method: "POST", body: { query: "type in (Nonsense)" } }); }
  catch (error) { planError = error; }
  checkTrue("POST /api/plan rejects values outside the whitelist (422)", "interactive",
    planError && planError.status === 422,
    "an unknown node type was not rejected");
  const gapFallback = await call("/api/gaps", { method: "POST", body: { topic: "quantum basket weaving", top_k: 5 } });
  checkTrue("POST /api/gaps for an unrecorded topic degrades honestly", "interactive",
    gapFallback.opportunities.length === 0 && Boolean(gapFallback.reason) && Boolean(gapFallback.safety_notice),
    "an unrecorded scope must say it is not precomputed, not invent candidates");
  const agent = await call("/api/agent", { method: "POST", body: { question: "What should I read first?" } });
  checkTrue("POST /api/agent replays a recorded answer with its reasoning", "interactive",
    Boolean(agent.answer) && Boolean(agent.explainability) && Array.isArray(agent.tool_calls),
    "the agent answer is missing its explainability trail");
  const stream = await call("/api/agent/stream", { method: "POST", body: { question: "What should I read first?" } });
  checkTrue("POST /api/agent/stream replays the event trace", "interactive",
    typeof stream === "string" && stream.includes("event: answer") && stream.includes("event: intent"),
    "the SSE trace is missing its stages");

  /* ------------------------------------------------------------- summary */
  const failed = results.filter((result) => !result.ok);
  const groups = {};
  results.forEach((result) => {
    groups[result.group] = groups[result.group] || { pass: 0, fail: 0 };
    groups[result.group][result.ok ? "pass" : "fail"] += 1;
  });
  const compared = results.reduce((sum, result) => sum + result.compared, 0);

  console.log("");
  for (const [group, tally] of Object.entries(groups)) {
    console.log(`  ${group.padEnd(12)} ${tally.pass} pass${tally.fail ? `, ${tally.fail} FAIL` : ""}`);
  }
  console.log(`\n${results.length - failed.length}/${results.length} checks passed · ` +
    `${compared} values compared against the engine's own recorded answers`);
  if (boundaryTotal.count) {
    const examples = [...boundaryTotal.examples].slice(0, 3).join("; ");
    notes.push(`${boundaryTotal.count} numeric values sit one unit in the last place from the ` +
      `engine's value because both sides re-round an already-rounded float (e.g. ${examples})`);
  }
  if (notes.length) notes.forEach((note) => console.log(`  note: ${note}`));

  if (args.json) {
    await fs.writeFile(path.resolve(args.json), JSON.stringify({
      site: siteDir, built: index.built, version: index.version,
      checks: results, notes,
      rounding_boundary_values: boundaryTotal.count,
      summary: { total: results.length, failed: failed.length, compared },
    }, null, 2));
    console.log(`  wrote ${path.resolve(args.json)}`);
  }
  return failed.length === 0 ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(`\ncheck_site_data failed: ${error.stack || error}`);
  process.exit(2);
});
