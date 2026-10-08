/* NEXUS static data layer — makes the front end work with no backend at all.
 * ============================================================================
 * The published site (GitHub Pages) has no server, so this file intercepts
 * `/api/*` and answers from three sources, in this order:
 *
 *   1. recorded payloads  — `data/*.json`, captured from the real engines by
 *      `scripts/build_site.py` (gap scoring, GraphRAG answers, reports, meta);
 *   2. computation in the browser over the exported graph — graph views, expand,
 *      neighbours, shortest paths, paper/node detail, search, timeline, explorer;
 *   3. the DSL planner, reimplemented here from the same rules as the Kotlin
 *      planner / `backend/services/planner.py` and checked against
 *      `tests/data/planner_parity.json` by `scripts/check_site_data.mjs`.
 *
 * Everything is honest about which of the three produced an answer: payloads carry
 * the engine name they had when recorded, plus `static_snapshot` metadata.
 *
 * Loaded as a classic script (before app.js, which is a module) in the static
 * build only, and as a plain require()-able module in Node for the checks.
 */
(function (root) {
  "use strict";

  const VERSION = "1.0.0";
  const DATA_DIR = "data/";
  const MAX_DEPTH = 3;
  const SEED_LIMIT = 60;

  const state = {
    dataDir: DATA_DIR,
    fetchImpl: typeof fetch === "function" ? fetch.bind(root) : null,
    datasets: {},      // file name -> parsed JSON
    graph: null,       // { nodes: Map, adj: Map, byLabel: Map, papers: [] }
    index: null,       // data/index.json (build manifest)
    missing: [],       // files that were not shipped
  };

  /* ------------------------------------------------------------- helpers -- */

  const round = (value, digits) => {
    const factor = 10 ** digits;
    return Math.round((Number(value) || 0) * factor) / factor;
  };

  function slug(text) {
    if (text === null || text === undefined || text === "") return "default";
    const cleaned = String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return (cleaned || "default").slice(0, 64);
  }

  function contentTokens(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/[\s-]+/)
      .filter((t) => t.length > 2 && !STOPWORDS.has(t));
  }

  const STOPWORDS = new Set(("the a an and or of in on for to from with without is are was were be been " +
    "does do did not no never this that these those it its as at by about into over under between " +
    "which what where who how why when most important research papers paper study studies show shows " +
    "corpus graph nexus me my i you your we our").split(" "));

  function jaccard(a, b) {
    const A = new Set(a), B = new Set(b);
    if (!A.size || !B.size) return 0;
    let shared = 0;
    A.forEach((token) => { if (B.has(token)) shared += 1; });
    return shared / (A.size + B.size - shared);
  }

  function httpError(status, detail) {
    const error = new Error(detail);
    error.status = status;
    error.detail = detail;
    return error;
  }

  /* -------------------------------------------------------- data loading -- */

  async function loadJSON(name) {
    if (Object.prototype.hasOwnProperty.call(state.datasets, name)) return state.datasets[name];
    if (!state.fetchImpl) throw httpError(500, `no fetch available to load ${name}`);
    const response = await state.fetchImpl(state.dataDir + name, { cache: "force-cache" });
    if (!response.ok) {
      state.missing.push(name);
      throw httpError(404, `static snapshot: ${name} is not part of this build`);
    }
    const payload = await response.json();
    state.datasets[name] = payload;
    return payload;
  }

  async function haveJSON(name) {
    try {
      return await loadJSON(name);
    } catch (err) {
      return null;
    }
  }

  function configure(options) {
    Object.assign(state, options || {});
    if (options && options.graph) buildGraph(options.graph);
    return state;
  }

  async function load() {
    if (state.loaded) return state;
    const [graph, index] = await Promise.all([loadJSON("graph.json"), haveJSON("index.json")]);
    buildGraph(graph);
    state.index = index;
    state.loaded = true;
    return state;
  }

  /* ------------------------------------------------- graph index + rules -- */

  function buildGraph(dataset) {
    const nodes = new Map();
    const adj = new Map();
    const byLabel = new Map();
    (dataset.nodes || []).forEach((node) => {
      const record = {
        id: node.id,
        label: node.label,
        type: node.type,
        color: node.color,
        props: node.props || node.properties || {},
        pagerank: Number(node.pagerank || 0),
        betweenness: Number(node.betweenness || 0),
        degree: Number(node.degree || 0),
        community: node.community === undefined || node.community === null ? -1 : Number(node.community),
      };
      nodes.set(record.id, record);
      adj.set(record.id, []);
      if (!byLabel.has(record.type)) byLabel.set(record.type, []);
      byLabel.get(record.type).push(record);
    });
    const edges = (dataset.edges || []).map((edge) => ({
      id: edge.id, source: edge.source, target: edge.target, type: edge.type,
      weight: edge.weight, provenance: edge.provenance,
    }));
    edges.forEach((edge) => {
      const a = adj.get(edge.source), b = adj.get(edge.target);
      if (a) a.push({ edge, other: edge.target, direction: "out" });
      if (b) b.push({ edge, other: edge.source, direction: "in" });
    });
    const communities = (dataset.communities || []).slice();
    state.graph = { nodes, edges, adj, byLabel, communities, legend: dataset.legend, status: dataset.status };
    return state.graph;
  }

  function graphOrThrow() {
    if (!state.graph) throw httpError(500, "static snapshot: graph.json has not been loaded yet");
    return state.graph;
  }

  const yearOf = (node) => {
    const year = node.props && node.props.year;
    return typeof year === "number" ? year : Number(year) || null;
  };

  function passesYearFilters(node, yearMin, yearMax) {
    const year = yearOf(node);
    if (yearMin !== null && yearMin !== undefined && (year || 0) < yearMin) return false;
    if (yearMax !== null && yearMax !== undefined && (year || 9999) > yearMax) return false;
    return true;
  }

  const nodeSummary = (node) => ({
    id: node.id, label: node.label, type: node.type, color: node.color,
    pagerank: round(node.pagerank, 6), betweenness: round(node.betweenness, 6),
    community: node.community, degree: node.degree,
  });

  /* ------------------------------------------------------------- /graph -- */

  function searchIndex(text, labels, limit) {
    const needle = String(text || "").trim().toLowerCase();
    const allowed = labels && labels.length ? new Set(labels) : null;
    const results = [];
    graphOrThrow().nodes.forEach((node) => {
      if (allowed && !allowed.has(node.type)) return;
      const props = node.props || {};
      const haystack = `${node.label} ${props.text || ""} ${props.abstract || ""}`.toLowerCase();
      if (needle && haystack.indexOf(needle) === -1) return;
      let score = 0;
      if (needle) {
        const name = node.label.toLowerCase();
        if (name === needle) score += 2;
        if (name.startsWith(needle)) score += 1;
        score += (haystack.split(needle).length - 1) * 0.2;   // occurrences, like the engine
      }
      score += node.pagerank * 5;
      results.push({
        id: node.id, label: node.label, type: node.type, score: round(score, 5),
        year: props.year ?? null, field: props.field ?? null, url: props.url ?? null,
        summary_source: props.summary_source ?? null,
      });
    });
    results.sort((a, b) => b.score - a.score);
    return limit ? results.slice(0, limit) : results;
  }

  function graphPayload(request) {
    const graph = graphOrThrow();
    const req = Object.assign({
      seeds: null, query: null, depth: 1, node_types: null, rel_types: null,
      year_min: null, year_max: null, include_predicted: true,
      max_nodes: 260, max_edges: 800, focus: null,
    }, request || {});

    let seeds = (req.seeds || []).filter((id) => graph.nodes.has(id));
    if (!seeds.length && req.query) {
      seeds = searchIndex(req.query, req.node_types, SEED_LIMIT).map((hit) => hit.id);
    }
    if (!seeds.length) {
      seeds = Array.from(graph.nodes.values())
        .sort((a, b) => b.pagerank - a.pagerank)
        .slice(0, SEED_LIMIT)
        .map((node) => node.id);
    }

    const allowedTypes = req.node_types && req.node_types.length ? new Set(req.node_types) : null;
    const allowedRels = req.rel_types && req.rel_types.length ? new Set(req.rel_types) : null;
    const depth = Math.max(0, Math.min(Number(req.depth) || 0, MAX_DEPTH));
    const maxNodes = Math.max(10, Math.min(Number(req.max_nodes) || 260, 400));
    const maxEdges = Math.max(10, Math.min(Number(req.max_edges) || 800, 1200));

    const visited = new Set();
    let frontier = Array.from(new Set(seeds));
    let truncated = false;
    for (let level = 0; level <= depth; level += 1) {
      const next = [];
      frontier.forEach((id) => {
        if (visited.has(id)) return;
        if (visited.size >= maxNodes) { truncated = true; return; }
        const node = graph.nodes.get(id);
        if (!node) return;
        if (allowedTypes && !allowedTypes.has(node.type)) return;
        if (level > 0 && !passesYearFilters(node, req.year_min, req.year_max)) return;
        visited.add(id);
        (graph.adj.get(id) || []).forEach(({ edge, other }) => {
          if (edge.type === "PREDICTED_LINK" && !req.include_predicted) return;
          if (allowedRels && !allowedRels.has(edge.type)) return;
          next.push(other);
        });
      });
      frontier = next;
      if (!frontier.length) break;
    }

    const nodes = Array.from(visited).map((id) => nodeSummary(graph.nodes.get(id)));
    const counts = {};
    nodes.forEach((node) => { counts[node.type] = (counts[node.type] || 0) + 1; });

    const edges = [];
    graph.edges.forEach((edge) => {
      if (edges.length >= maxEdges) { truncated = true; return; }
      if (!visited.has(edge.source) || !visited.has(edge.target)) return;
      if (edge.type === "PREDICTED_LINK" && !req.include_predicted) return;
      if (allowedRels && !allowedRels.has(edge.type)) return;
      edges.push(edge);
    });

    const notes = [];
    if (truncated) {
      notes.push(`View truncated to ${nodes.length} nodes / ${edges.length} edges ` +
        "(progressive expansion keeps the first paint fast — expand a node to see more).");
    }

    let focus = null;
    if (req.focus) {
      let focusId = visited.has(req.focus) ? req.focus : null;
      if (!focusId) {
        const needle = String(req.focus).toLowerCase();
        visited.forEach((id) => {
          const node = graph.nodes.get(id);
          if (!focusId && node && node.label.toLowerCase() === needle) focusId = id;
        });
      }
      if (focusId) focus = { id: focusId, neighbours: neighbours(focusId, 40).neighbours };
    }

    return {
      nodes, edges, truncated, notes, counts, focus,
      legend: legend(graph),
      status: staticStatus(),
    };
  }

  function legend(graph) {
    const legend = Object.assign({}, graph.legend || {});
    if (!legend.node_colors) {
      legend.node_colors = {};
      graph.nodes.forEach((node) => { legend.node_colors[node.type] = node.color; });
    }
    return legend;
  }

  function staticStatus() {
    return {
      backend: "static-snapshot",
      requested_backend: "static-snapshot",
      degraded: false,
      reason: "This page is a build-time snapshot: the graph is exported and queried in your browser. " +
        "Run the app locally (or with docker compose) for the live engine, Neo4j and the polyglot sidecars.",
    };
  }

  function expandPayload(nodeIds, relTypes, limit) {
    const graph = graphOrThrow();
    const ids = (nodeIds || []).filter((id) => graph.nodes.has(id));
    const allowed = relTypes && relTypes.length ? new Set(relTypes) : null;
    const visited = new Set(ids);
    const edges = [];
    const notes = [];
    let truncated = false;
    outer:
    for (const id of ids) {
      for (const { edge, other } of graph.adj.get(id) || []) {
        if (edges.length >= (limit || 60)) { truncated = true; break outer; }
        if (allowed && !allowed.has(edge.type)) continue;
        if (visited.has(other)) { edges.push(edge); continue; }
        visited.add(other);
        edges.push(edge);
      }
    }
    const nodes = Array.from(visited).map((id) => nodeSummary(graph.nodes.get(id)));
    if (truncated) notes.push(`Expanded ${ids.length} node(s) by one hop (limit ${limit || 60}).`);
    return { nodes, edges, truncated, notes, counts: countsByType(nodes), focus: null,
             legend: legend(graph), status: staticStatus() };
  }

  function countsByType(nodes) {
    const counts = {};
    nodes.forEach((node) => { counts[node.type] = (counts[node.type] || 0) + 1; });
    return counts;
  }

  function neighbours(nodeId, limit) {
    const graph = graphOrThrow();
    const center = graph.nodes.get(nodeId);
    if (!center) throw httpError(404, `Node '${nodeId}' not found in this snapshot.`);
    const grouped = {};
    const all = [];
    (graph.adj.get(nodeId) || []).forEach(({ edge, other, direction }) => {
      const node = graph.nodes.get(other);
      if (!node) return;
      const entry = {
        id: node.id, label: node.label, type: node.type, direction,
        weight: edge.weight ?? null, rel: edge.type,
        predicted: edge.type === "PREDICTED_LINK" || Boolean((edge.provenance || "").toLowerCase().includes("predict")),
        pagerank: round(node.pagerank, 6), year: yearOf(node),
      };
      (grouped[edge.type] = grouped[edge.type] || []).push(entry);
      all.push(entry);
    });
    Object.keys(grouped).forEach((key) => grouped[key].sort((a, b) => b.pagerank - a.pagerank));
    return {
      center: Object.assign(nodeSummary(center), {
        name: center.label,
        title: (center.props && center.props.title) || center.label,
        abstract: (center.props && center.props.abstract) || null,
      }),
      neighbours: limit ? all.slice(0, limit) : all,
      grouped,
      count: all.length,
    };
  }

  /* shortest path: BFS over the exported edges (the primitive the gap engine uses) */
  function shortestPath(source, target, maxHops) {
    const graph = graphOrThrow();
    if (!graph.nodes.has(source)) throw httpError(404, `Node '${source}' not found in this snapshot.`);
    if (!graph.nodes.has(target)) throw httpError(404, `Node '${target}' not found in this snapshot.`);
    const limit = Math.max(1, Math.min(Number(maxHops) || 4, 6));

    let frontier = [source];
    const previous = new Map([[source, null]]);
    for (let hop = 0; hop < limit && !previous.has(target); hop += 1) {
      const next = [];
      for (const id of frontier) {
        for (const { edge, other } of graph.adj.get(id) || []) {
          if (previous.has(other)) continue;
          previous.set(other, { from: id, edge });
          if (other === target) break;
          next.push(other);
        }
      }
      frontier = next;
      if (!frontier.length) break;
    }
    if (!previous.has(target)) {
      return { found: false, nodes: [], hops: [], hops_count: 0, node_labels: {},
               note: `No path within ${limit} hops between '${source}' and '${target}'.` };
    }
    const chain = [];
    for (let cursor = target; cursor !== null && cursor !== undefined;) {
      chain.push(cursor);
      const step = previous.get(cursor);
      cursor = step ? step.from : null;
    }
    chain.reverse();
    const hops = [];
    for (let i = 0; i < chain.length - 1; i += 1) {
      const step = previous.get(chain[i + 1]);
      const edge = step.edge;
      const from = graph.nodes.get(edge.source) || graph.nodes.get(edge.target);
      const to = chain[i + 1];
      hops.push({
        from: step.from, to,
        label: `${graph.nodes.get(step.from).label} —${edge.type}→ ${graph.nodes.get(to).label}`,
        type: `${edge.type}`,
        direction: edge.source === step.from ? "out" : "in",
        from_label: from ? from.label : from,
      });
    }
    const node_labels = {};
    chain.forEach((id) => { node_labels[id] = graph.nodes.get(id).label; });
    return { found: true, nodes: chain, hops, hops_count: hops.length, node_labels };
  }

  /* ------------------------------------------------------------ /papers -- */

  async function paperItems(source, query) {
    const q = Object.assign({ q: null, topic: null, field: null, year_min: null, year_max: null,
                              sort: "pagerank", order: "desc", limit: 40, offset: 0 }, query || {});
    const graph = graphOrThrow();
    let items = (graph.byLabel.get("Paper") || []).slice();
    if (q.year_min !== null && q.year_min !== undefined) items = items.filter((n) => (yearOf(n) || 0) >= q.year_min);
    if (q.year_max !== null && q.year_max !== undefined) items = items.filter((n) => (yearOf(n) || 9999) <= q.year_max);
    if (q.field) items = items.filter((n) => (n.props.field || null) === q.field);

    const key = {
      pagerank: (n) => n.pagerank,
      betweenness: (n) => n.betweenness,
      degree: (n) => n.degree,
      year: (n) => yearOf(n) || 0,
      name: (n) => n.label.toLowerCase(),
    }[q.sort] || ((n) => n.pagerank);
    // python's `sorted(..., reverse=True)` and JS `Array#sort` are both stable, so equal
    // metrics keep the store's node order in both engines — the pages line up exactly
    items.sort((a, b) => (q.order === "asc" ? key(a) - key(b) : key(b) - key(a)));

    const total = items.length;
    let page = items.slice(q.offset, q.offset + q.limit);
    const payload = { total, offset: q.offset, limit: q.limit, sort: q.sort };

    // the engine pages first and filters the page afterwards — the static layer follows
    // the same order so `total`/`items` are identical rather than merely similar
    if (q.topic) {
      const keep = new Set((await scopedPapers(q.topic)).papers);
      page = page.filter((node) => keep.has(node.id));
      payload.filtered_by_topic = q.topic;
      payload.total = page.length;
    }
    if (q.q) {
      const hits = new Set(searchIndex(q.q, ["Paper"], 500).map((hit) => hit.id));
      page = page.filter((node) => hits.has(node.id));
      payload.filtered_by_query = q.q;
      payload.total = page.length;
    }

    payload.items = page.map((node) => ({
      id: node.id, label: node.label, type: node.type, year: yearOf(node),
      field: node.props.field ?? null, url: node.props.url ?? null,
      pagerank: round(node.pagerank, 5), betweenness: round(node.betweenness, 5),
      degree: node.degree, community: node.community,
    }));
    return payload;
  }

  /* the plain-language metric explanations, same wording as the engine */
  function explainMetrics(node, community) {
    const out = [];
    const pr = node.pagerank, bc = node.betweenness, degree = node.degree;
    if (pr > 0) {
      const strength = pr > 0.01 ? "top-tier" : pr > 0.004 ? "notable" : "modest";
      out.push({
        metric: "PageRank", value: pr.toFixed(5),
        meaning: `PageRank measures influence by how much highly-connected research points at this node. ` +
          `At ${pr.toFixed(5)} this is ${strength} influence within the analyzed corpus.`,
      });
    }
    if (bc > 0) {
      out.push({
        metric: "Betweenness centrality", value: bc.toFixed(5),
        meaning: `Betweenness counts how often this node sits on the shortest path between others. ` +
          (bc > 0.01 ? "This node acts as a bridge between otherwise separated research areas."
                     : "Its bridging role is limited: most paths do not need to pass through it."),
      });
    }
    out.push({
      metric: "Degree", value: String(degree),
      meaning: `${degree} direct relationships were collected in the graph for this node.`,
    });
    if (node.community >= 0) {
      out.push({
        metric: "Research community", value: String(node.community),
        meaning: `Louvain community detection places this node in community ${node.community}` +
          (community ? ` — '${community.name}' (${community.paper_count} papers).` : "."),
      });
    }
    return out;
  }

  function communityProfile(index) {
    const graph = graphOrThrow();
    return (graph.communities || []).find((c) => c.community_index === index) || null;
  }

  function nodeDetail(nodeId) {
    const graph = graphOrThrow();
    const node = graph.nodes.get(nodeId);
    if (!node) throw httpError(404, `Node '${nodeId}' not found in this snapshot.`);
    const grouped = {};
    const counts = {};
    (graph.adj.get(nodeId) || []).forEach(({ edge, other, direction }) => {
      const other_node = graph.nodes.get(other);
      if (!other_node) return;
      (grouped[edge.type] = grouped[edge.type] || []).push({
        id: other_node.id, name: other_node.label, type: other_node.type, direction,
        weight: edge.weight ?? null, predicted: edge.type === "PREDICTED_LINK",
        similarity: edge.weight ?? null, properties: edge.provenance ? { provenance: edge.provenance } : {},
      });
      counts[edge.type] = (counts[edge.type] || 0) + 1;
    });
    Object.keys(grouped).forEach((key) => {
      // the engine keeps the store's edge order (no re-sorting) — `.slice(0, 40)`
      // mirrors `store.node_detail`, so the static layer answers the same list
      grouped[key] = grouped[key].slice(0, 40);
    });
    const metrics = {
      pagerank: round(node.pagerank, 6), betweenness: round(node.betweenness, 6),
      degree: node.degree, community: node.community,
    };
    const detail = {
      id: node.id, label: node.label, type: node.type, properties: node.props,
      metrics, metrics_explained: explainMetrics(node, communityProfile(node.community)),
      neighbours: grouped, neighbour_counts: counts,
    };
    if (node.type === "Paper") {
      const names = (rel) => (grouped[rel] || []).map((entry) => entry.name);
      const citations = (grouped.CITES || []);
      detail.authors = names("AUTHORED");
      detail.topics = names("STUDIES");
      detail.methods = names("USES_METHOD");
      detail.datasets = names("USES_DATASET");
      detail.citations_out = citations.filter((c) => c.direction === "out").map((c) => c.id);
      detail.cited_by = citations.filter((c) => c.direction === "in").map((c) => c.id);
      detail.in_graph_citation_degree = detail.citations_out.length + detail.cited_by.length;
      detail.similar_papers = (grouped.SIMILAR_TO || [])
        .map((entry) => ({ id: entry.id, name: entry.name, similarity: entry.similarity }));
      detail.claims = (grouped.MAKES_CLAIM || []).map((entry) => {
        const claim = graph.nodes.get(entry.id);
        return { id: entry.id, text: claim ? claim.props.text : null, stance: claim ? claim.props.stance : null };
      });
      detail.predicted_links = (grouped.PREDICTED_LINK || []).map((entry) => ({
        id: entry.id, name: entry.name, type: entry.type, score: entry.similarity,
      }));
    }
    return detail;
  }

  /* ------------------------------------------------------------ timeline -- */

  async function scopedPapers(topic) {
    /* The gap engine resolves a phrase like "AI Agents" into many topics with a scored
       algorithm, so the resolution is shipped (data/scope--<slug>.json) instead of being
       guessed here. Without a shipped scope the browser falls back to a token match and
       says so in `resolution.strategy`. */
    const graph = graphOrThrow();
    if (!topic) {
      return { papers: (graph.byLabel.get("Paper") || []).map((p) => p.id),
               topics: (graph.byLabel.get("Topic") || []).map((t) => t.id),
               methods: (graph.byLabel.get("Method") || []).map((m) => m.id),
               resolution: { query: null, matched_topics: [], strategy: "all-papers" }, exact: true };
    }
    const shipped = await haveJSON(`scope--${slug(topic)}.json`);
    if (shipped) {
      const names = new Map();
      (shipped.topics || []).forEach((id) => { const node = graph.nodes.get(id); if (node) names.set(id, node.label); });
      return {
        papers: shipped.papers, topics: shipped.topics, methods: shipped.methods,
        resolution: Object.assign({}, shipped.resolution, { matched_topics: (shipped.resolution || {}).matched_topics
          || Array.from(names.values()) }),
        exact: true,
      };
    }
    const needle = String(topic).toLowerCase();
    const tokens = contentTokens(topic);
    const score = (label) => {
      const lower = label.toLowerCase();
      if (lower === needle || lower.includes(needle)) return 3;
      const hit = tokens.filter((token) => lower.includes(token)).length;
      return hit ? hit / Math.max(tokens.length, 1) * 2 : 0;
    };
    const topics = (graph.byLabel.get("Topic") || [])
      .map((node) => ({ node, value: score(node.label) }))
      .filter((entry) => entry.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 8).map((entry) => entry.node);
    const papers = new Set();
    const methods = new Set();
    topics.forEach((topicNode) => {
      (graph.adj.get(topicNode.id) || []).forEach(({ edge, other }) => {
        if ((edge.type === "STUDIES" || edge.type === "BELONGS_TO") && graph.nodes.get(other)
            && graph.nodes.get(other).type === "Paper") papers.add(other);
      });
    });
    topics.forEach((topicNode) => {
      papers.forEach((paperId) => {
        (graph.adj.get(paperId) || []).forEach(({ edge, other }) => {
          if (edge.type === "USES_METHOD") methods.add(other);
        });
      });
    });
    return {
      papers: Array.from(papers), topics: topics.map((node) => node.id), methods: Array.from(methods),
      resolution: {
        query: topic, matched_topics: topics.map((node) => node.label),
        strategy: "browser-token-match (no shipped scope for this phrase)",
      },
      exact: false,
    };
  }

  async function timelinePayload(topic) {
    const graph = graphOrThrow();
    const scope = await scopedPapers(topic);
    const scoped = new Set(scope.papers);
    const years = {};
    const perTopic = new Map();
    graph.edges.forEach((edge) => {
      if (edge.type !== "STUDIES") return;
      const paper = graph.nodes.get(edge.source);
      if (!paper || paper.type !== "Paper") return;
      if (scoped.size && !scoped.has(paper.id)) return;
      const year = yearOf(paper);
      if (!year) return;
      years[year] = (years[year] || 0) + 1;
      const topicNode = graph.nodes.get(edge.target);
      if (!topicNode) return;
      const bucket = perTopic.get(topicNode.label) || new Map();
      bucket.set(year, (bucket.get(year) || 0) + 1);
      perTopic.set(topicNode.label, bucket);
    });
    const sortedYears = {};
    Object.keys(years).map(Number).sort((a, b) => a - b).forEach((year) => { sortedYears[year] = years[year]; });
    const topics_over_time = {};
    Array.from(perTopic.entries())
      .sort((a, b) => sum(b[1]) - sum(a[1]))
      .slice(0, 12)
      .forEach(([name, bucket]) => {
        const ordered = {};
        Array.from(bucket.keys()).sort((a, b) => a - b).forEach((year) => { ordered[year] = bucket.get(year); });
        topics_over_time[name] = ordered;
      });
    return { topic: topic || null, papers_per_year: sortedYears, topics_over_time };
  }

  const sum = (bucket) => Array.from(bucket.values()).reduce((total, value) => total + value, 0);

  /* ------------------------------------------------------------ explorer -- */

  async function explorerOverview(params) {
    const q = Object.assign({ topic: null, year_min: null, year_max: null, field: null }, params || {});
    const graph = graphOrThrow();
    const scope = await scopedPapers(q.topic);
    let papers = scope.papers.map((id) => graph.nodes.get(id)).filter(Boolean);
    papers = papers.filter((paper) => (paper.type === "Paper") &&
      passesYearFilters(paper, q.year_min, q.year_max) && (!q.field || paper.props.field === q.field));

    const authors = new Set();
    const datasets = new Set();
    const communities = new Set();
    const methodsInUse = new Set();
    papers.forEach((paper) => {
      (graph.adj.get(paper.id) || []).forEach(({ edge, other, direction }) => {
        if (edge.type === "AUTHORED" && direction === "in") authors.add(other);
        if (edge.type === "USES_DATASET" && direction === "out") datasets.add(other);
        if (edge.type === "USES_METHOD" && direction === "out") methodsInUse.add(other);
      });
      if (paper.community >= 0) communities.add(paper.community);
    });

    const years = {};
    papers.forEach((paper) => {
      const year = yearOf(paper);
      if (year) years[year] = (years[year] || 0) + 1;
    });
    const ordered = {};
    Object.keys(years).map(Number).sort((a, b) => a - b).forEach((year) => { ordered[year] = years[year]; });
    const yearKeys = Object.keys(ordered).map(Number);
    const recent = yearKeys.filter((year) => year >= 2023).reduce((total, year) => total + ordered[year], 0);
    const totalPapers = papers.length;

    // `store.top()` ranks the whole label (not the scope) — the explorer mirrors that,
    // and the UI labels these lists as corpus-wide leaderboards.
    const topper = (label, metric, limit) => (graph.byLabel.get(label) || [])
      .slice()
      .sort((a, b) => b[metric] - a[metric])
      .slice(0, limit)
      .map((node) => ({
        id: node.id, label: node.label, type: node.type,
        value: round(node[metric], 6), degree: node.degree, year: yearOf(node),
        community: node.community, url: node.props.url ?? null,
      }));

    return {
      scope: {
        topic: q.topic, year_min: q.year_min, year_max: q.year_max, field: q.field,
        resolution: scope.resolution,
      },
      counts: {
        papers: totalPapers, authors: authors.size, topics: scope.topics.length,
        methods: scope.methods.length, datasets: datasets.size, institutions: 0,
        communities: communities.size,
        claims: (graph.byLabel.get("Claim") || []).length,   // corpus-wide, like the engine
      },
      years: ordered,
      recency: totalPapers ? {
        papers_since_2023: recent,
        share_since_2023: round(recent / totalPapers, 3),
        newest_year: yearKeys.length ? yearKeys[yearKeys.length - 1] : null,
        oldest_year: yearKeys.length ? yearKeys[0] : null,
      } : {},
      top_papers: topper("Paper", "pagerank", 8),
      top_topics: topper("Topic", "pagerank", 10),
      top_methods: topper("Method", "degree", 10),
      top_bridge_papers: topper("Paper", "betweenness", 8),
      communities: (graph.communities || [])
        .filter((profile) => !communities.size || communities.has(profile.community_index))
        .slice(0, 10),
      methods_in_scope: methodsInUse.size,
      status: staticStatus(),
      scope_source: scope.exact ? "shipped gap-engine resolution" : "browser token match",
    };
  }

  /* -------------------------------------------------------- DSL planner -- */

  /* ------------------------------------------------------------- agent --- */

  async function agentAnswer(question, options) {
    const opts = Object.assign({ depth: 2, top_k: 12 }, options || {});
    const key = slug(question);
    const recorded = await haveJSON(`agent--${key}.json`);
    if (recorded) return decorate(recorded, question);

    const index = state.index;
    const known = (index && index.precomputed && index.precomputed.questions) || [];
    let best = null, bestScore = 0;
    const tokens = contentTokens(question);
    known.forEach((candidate) => {
      const score = jaccard(tokens, contentTokens(candidate));
      if (score > bestScore) { bestScore = score; best = candidate; }
    });
    if (best && bestScore >= 0.6) {
      const payload = await haveJSON(`agent--${slug(best)}.json`);
      if (payload) {
        const decorated = decorate(payload, question);
        decorated.answer = `${decorated.answer}\n\n(Static snapshot: this is the recorded answer for the closest ` +
          `precomputed question — “${best}”. Run the app locally for a live answer to this wording.)`;
        return decorated;
      }
    }
    return templateAnswer(question, opts, known);
  }

  function decorate(payload, question) {
    const decorated = Object.assign({}, payload, {
      question,
      static_snapshot: {
        mode: "recorded",
        note: "Recorded from the live engine at build time; the question matched this precomputed answer.",
        topics: (state.index && state.index.precomputed && state.index.precomputed.topics) || [],
      },
    });
    return decorated;
  }

  /* graph templates: the same shape the engine returns when no LLM key is configured */
  function templateAnswer(question, opts, known) {
    const graph = graphOrThrow();
    const tokens = contentTokens(question);
    const text = question.toLowerCase();
    const topicNode = (graph.byLabel.get("Topic") || []).find((topic) =>
      tokens.some((token) => topic.label.toLowerCase().includes(token))) || null;

    const papers = (graph.byLabel.get("Paper") || []).slice();
    const topByPagerank = papers.slice().sort((a, b) => b.pagerank - a.pagerank).slice(0, 5);
    const topByBetweenness = papers.slice().sort((a, b) => b.betweenness - a.betweenness).slice(0, 5);
    const conflicts = graph.edges.filter((edge) => edge.type === "CONTRADICTS" && edge.provenance !== "predicted");
    const predicted = graph.edges.filter((edge) => edge.type === "PREDICTED_LINK");
    const communities = (graph.communities || []).slice(0, 4);

    let answer;
    let intent = "general_qa";
    const parts = [];
    if (/contradict|conflict|tension/.test(text)) {
      intent = "contradictions";
      const rows = conflicts.slice(0, 3).map((edge) => {
        const a = graph.nodes.get(edge.source), b = graph.nodes.get(edge.target);
        const textA = a ? a.label : edge.source, textB = b ? b.label : edge.target;
        return `• “${String(textA).slice(0, 120)}” vs “${String(textB).slice(0, 120)}” ` +
          `(weight ${round(edge.weight || 0, 2)}).`;
      });
      parts.push(`${conflicts.length} claim-level tensions exist in this corpus snapshot ` +
        `(potential contradictions, not established facts):`);
      parts.push(...rows);
    } else if (/gap|opportunit|underexplored/.test(text)) {
      intent = "gaps";
      const scope = (topicNode && topicNode.label) || null;
      const payload = scope ? await_have(`gaps--${slug(scope)}.json`) : await_have("gaps--default.json");
      const top = payload && payload.opportunities ? payload.opportunities : [];
      parts.push(scope
        ? `Candidate opportunities are scoped to “${scope}” (${payload ? payload.scope.papers : 0} papers in scope).`
        : "Candidate opportunities cover the whole corpus snapshot.");
      top.slice(0, 3).forEach((opportunity) => {
        parts.push(`• ${opportunity.title} — prototype score ${round(opportunity.opportunity_score, 1)}/100, ` +
          `${opportunity.confidence} confidence, trajectory ${opportunity.trajectory ? opportunity.trajectory.status : "n/a"}.`);
      });
      parts.push("These are hypotheses generated from graph structure; they are not claims that nobody has researched this.");
    } else if (/communit|cluster/.test(text)) {
      intent = "communities";
      parts.push(`${communities.length ? (graph.communities || []).length : 0} Louvain communities were detected. The largest cover:`);
      communities.forEach((community) => {
        parts.push(`• ${community.name} — ${community.paper_count} papers, topics: ` +
          `${(community.top_topics || []).slice(0, 4).join(", ")}.`);
      });
    } else if (/bridge|connect|between/.test(text)) {
      intent = "bridges";
      parts.push("Bridges by betweenness centrality (attention, not quality):");
      topByBetweenness.slice(0, 4).forEach((paper) => {
        parts.push(`• ${paper.label} — betweenness ${round(paper.betweenness, 4)}, degree ${paper.degree}, year ${yearOf(paper) || "n/a"}.`);
      });
    } else {
      intent = "important_papers";
      parts.push("Most influential papers in this snapshot by PageRank (influence, not quality):");
      topByPagerank.forEach((paper) => {
        parts.push(`• ${paper.label} — PageRank ${round(paper.pagerank, 5)}, degree ${paper.degree}, year ${yearOf(paper) || "n/a"}.`);
      });
    }

    const seeds = (topicNode ? [topicNode] : []).concat(topByPagerank.slice(0, 4)).map((node) => ({
      id: node.id, label: node.label, type: node.type, why: "ranked in this static snapshot", score: round(node.pagerank, 4),
    }));

    return {
      question,
      intent: { name: intent, confidence: 0.4, matched: tokens.slice(0, 4) },
      answer: parts.join("\n") + "\n\nThis answer was assembled from the exported graph in your browser " +
        "(no LLM key, no server) — every statement maps to a node, edge or score in the response.",
      answer_engine: "static-snapshot-template",
      used_llm: false,
      confidence: "Low-Medium",
      confidence_basis: "Graph templates over the exported snapshot; run the live app for the full agent with fallback reasons and tool traces.",
      tool_calls: [
        { tool: "graph_metrics", ok: true, args: {}, summary: "computed in the browser" },
        { tool: "find_conflicting_claims", ok: true, args: {}, summary: `${conflicts.length} tensions in the snapshot` },
      ],
      context: { papers: papers.slice(0, opts.top_k), topics: (graph.byLabel.get("Topic") || []).slice(0, 8), communities },
      evidence: topByPagerank.slice(0, 6).map((paper) => ({
        id: paper.id, title: paper.label, year: yearOf(paper), url: paper.props.url || null,
      })),
      explainability: {
        claim: parts[0] || "Answer assembled from the exported graph snapshot.",
        reasoning: [
          "Loaded the exported graph (nodes and edges) recorded by the build script.",
          "Ranked candidate papers with the PageRank values computed by the C++ kernel at build time.",
          `Counted ${conflicts.length} CONTRADICTS edges and ${predicted.length} predicted links (always labelled).`,
          "Composed the answer from graph templates — no LLM was called.",
        ],
        graph_evidence: { seeds },
        supporting_papers: topByPagerank.slice(0, 6).map((paper) => ({
          id: paper.id, title: paper.label, year: yearOf(paper), why: "ranked in this snapshot",
        })),
        confidence_basis: "Low-Medium — snapshot templates; the live agent adds retrieval, tool traces and confidence reasons.",
        safety_notice: "AI-generated hypothesis over a curated demo corpus, not a literature review. " +
          "Candidates are research directions, never claims that a topic is unstudied. Independently validate before investing.",
      },
      paths: [], predicted_links: predicted.slice(0, 8).map((edge) => ({
        source: edge.source, target: edge.target, type: "PREDICTED_LINK", score: round(edge.weight || 0, 4),
        label: "hypothesis — not an established relationship",
      })),
      conflicts: conflicts.slice(0, 6).map((edge) => ({
        claim_a: edge.source, claim_b: edge.target, score: round(edge.weight || 0, 3), kind: "recorded tension",
        text_a: (graph.nodes.get(edge.source) || {}).label, text_b: (graph.nodes.get(edge.target) || {}).label,
        reasons: ["Recorded CONTRADICTS edge shipped in the graph export."],
      })),
      follow_ups: (known && known.length ? known.slice(0, 4) : []),
      status: staticStatus(),
      static_snapshot: {
        mode: "computed",
        note: "No recorded answer matched this question, so the graph-template fallback answered in your browser.",
        topics: (state.index && state.index.precomputed && state.index.precomputed.topics) || [],
      },
    };
  }

  // small sync helper for the template path (answers are built synchronously)
  function await_have(name) {
    return Object.prototype.hasOwnProperty.call(state.datasets, name) ? state.datasets[name] : null;
  }

  /* ------------------------------------------------------------- reports -- */

  async function reportPayload(body) {
    /* the opportunity report is a build artefact: the report engine renders markdown and
       JSON at build time, so the static layer replays exactly what it produced */
    const topic = body && body.topic ? body.topic : null;
    const payload = await haveJSON(`report--${slug(topic)}.json`);
    if (payload) return payload;
    const available = Object.keys(state.datasets || {}).filter((name) => name.startsWith("report--")).length;
    throw httpError(404, `static snapshot: reports were precomputed for the demo scopes only ` +
      `(${available} topics). Run the app locally (python run.py) for a report on “${topic || "the whole corpus"}”.`);
  }

  async function gapsPayload(body) {
    const topic = body && body.topic ? body.topic : null;
    const recorded = await haveJSON(`gaps--${slug(topic)}.json`);
    if (recorded) {
      return Object.assign({}, recorded, {
        static_snapshot: { mode: "recorded", note: "Computed by the gap engine at build time." },
      });
    }
    const index = state.index || {};
    const topics = (index.precomputed && index.precomputed.topics) || [];
    const { SAFETY_NOTICE } = staticNotices();
    return {
      topic, resolution: { query: topic, matched_topics: [], strategy: "static-snapshot" },
      scope: { topic, year_min: body ? body.year_min : null, year_max: body ? body.year_max : null,
               field: null, papers: 0, resolution: { query: topic, matched_topics: [], strategy: "static-snapshot" } },
      methodology: ["Static snapshot: gap scoring runs at build time over the shipped corpus."],
      score_formula: {},
      safety_notice: SAFETY_NOTICE,
      limitations: ["Scores are computed over this curated corpus only, and are a prototype heuristic."],
      clusters: [],
      opportunities: [],
      filtered_pairs: [],
      ranking: { note: "No live scoring in the static build." },
      score_distribution: { candidates_considered: 0, unique_candidates: 0, rank_1_score: null },
      reason: `Static snapshot: the gap engine ran at build time for ${topics.length} scopes ` +
        `(${topics.slice(0, 6).join(", ")}${topics.length > 6 ? ", …" : ""}). ` +
        `Run the app locally (python run.py, or docker compose up) to score “${topic || "any"}” live.`,
      score_label: "NEXUS Opportunity Score",
      static_snapshot: { mode: "unavailable", precomputed_topics: topics },
    };
  }

  function staticNotices() {
    return {
      SAFETY_NOTICE: "AI-generated hypothesis over a curated demo corpus, not a literature review. " +
        "Candidates are research directions, never claims that a topic is unstudied. " +
        "Independently validate every candidate before investing time or money.",
    };
  }

  /* -------------------------------------------------------------- router -- */

  function queryOf(path) {
    const index = path.indexOf("?");
    return {
      route: index === -1 ? path : path.slice(0, index),
      params: new URLSearchParams(index === -1 ? "" : path.slice(index + 1)),
    };
  }

  async function api(path, options) {
    const opts = options || {};
    const body = opts.body ? (typeof opts.body === "string" ? JSON.parse(opts.body) : opts.body) : {};
    const { route, params } = queryOf(path);
    const numberOrNull = (key) => (params.get(key) === null || params.get(key) === ""
      ? null : Number(params.get(key)));

    await load();

    if (route === "/api/health") {
      const health = await haveJSON("health.json");
      if (!health) throw httpError(404, "static snapshot: health.json is missing");
      return Object.assign({}, health, {
        store: Object.assign({}, health.store, { engine: "static-snapshot", requested_engine: health.store.engine }),
        notices: (health.notices || []).concat([
          "Static snapshot: engines ran at build time. Graph queries are computed in your browser.",
        ]),
        static_snapshot: state.index || null,
      });
    }
    if (route === "/api/services") {
      const services = await haveJSON("services.json");
      if (services) return services;
    }
    for (const [prefix, file] of [["/api/dashboard", "dashboard.json"], ["/api/algorithms", "algorithms.json"],
                                  ["/api/safety", "safety.json"], ["/api/tools", "tools.json"],
                                  ["/api/mcp", "mcp.json"], ["/api/opportunity-score", "opportunity-score.json"],
                                  ["/api/communities", "communities.json"], ["/api/conflicts", "conflicts.json"],
                                  ["/api/predictions", "predictions.json"], ["/api/centrality", "centrality.json"]]) {
      if (route === prefix) {
        const payload = await haveJSON(file);
        if (payload) return payload;
      }
    }
    if (route === "/api/timeline") {
      const topic = params.get("topic");
      const recorded = await haveJSON(`timeline--${slug(topic)}.json`);
      if (recorded) return recorded;                     // byte-exact for precomputed scopes
      return timelinePayload(topic);                     // otherwise computed here
    }
    if (route === "/api/explorer") {
      const query = {
        topic: params.get("topic"), field: params.get("field"),
        year_min: numberOrNull("year_min"), year_max: numberOrNull("year_max"),
      };
      const filtered = query.field || query.year_min !== null || query.year_max !== null;
      if (!filtered) {
        const recorded = await haveJSON(`explorer--${slug(query.topic)}.json`);
        if (recorded) return recorded;
      }
      const payload = await explorerOverview(query);
      const corpusConflicts = await haveJSON("conflicts.json");
      if (corpusConflicts) {
        payload.conflicts = (corpusConflicts.conflicts || []).slice(0, 6);
      }
      return payload;
    }
    if (route === "/api/graph") {
      if (opts.method === "POST") return graphPayload(body);
      return graphPayload({ query: params.get("topic") || params.get("query"),
                            depth: Number(params.get("depth") || 1),
                            year_min: numberOrNull("year_min"), year_max: numberOrNull("year_max"),
                            max_nodes: Number(params.get("max_nodes") || 260) });
    }
    if (route === "/api/graph/expand") return expandPayload(body.node_ids, body.rel_types, body.limit);
    if (route.startsWith("/api/graph/neighbours/")) {
      return neighbours(decodeURIComponent(route.slice("/api/graph/neighbours/".length)),
                        Number(params.get("limit") || 60));
    }
    if (route === "/api/graph/path") {
      return shortestPath(params.get("source"), params.get("target"), numberOrNull("max_hops"));
    }
    if (route === "/api/papers") {
      return paperItems(null, { q: params.get("q"), topic: params.get("topic"), field: params.get("field"),
                                year_min: numberOrNull("year_min"), year_max: numberOrNull("year_max"),
                                sort: params.get("sort") || "pagerank", order: params.get("order") || "desc",
                                limit: Number(params.get("limit") || 40), offset: Number(params.get("offset") || 0) });
    }
    if (route.startsWith("/api/papers/")) {
      const id = decodeURIComponent(route.slice("/api/papers/".length));
      const key = id.startsWith("paper:") ? id : `paper:${id}`;
      const detail = nodeDetail(key);
      if (detail.type !== "Paper") throw httpError(404, `'${id}' is not a paper`);
      return Object.assign({ id: detail.id, label: detail.label, type: detail.type }, detail);
    }
    if (route === "/api/nodes") {
      const label = params.get("label");
      const list = ((graphOrThrow().byLabel.get(label) || [])).slice()
        .sort((a, b) => (params.get("sort") === "degree" ? b.degree - a.degree : b.pagerank - a.pagerank));
      const offset = Number(params.get("offset") || 0);
      const limit = Number(params.get("limit") || 50);
      return { total: list.length, offset, limit, items: list.slice(offset, offset + limit).map((node) => ({
        id: node.id, label: node.label, type: node.type, year: yearOf(node),
        field: node.props.field ?? null, url: node.props.url ?? null,
        pagerank: round(node.pagerank, 5), betweenness: round(node.betweenness, 5),
        degree: node.degree, community: node.community,
      })) };
    }
    if (route.startsWith("/api/nodes/")) {
      return nodeDetail(decodeURIComponent(route.slice("/api/nodes/".length)));
    }
    if (route === "/api/search") {
      const results = searchIndex(params.get("q"), params.get("labels") ? params.get("labels").split(",") : null,
                                  Number(params.get("limit") || 25));
      return { query: params.get("q") || "", count: results.length, results };
    }
    if (route === "/api/gaps") return gapsPayload(body);
    if (route.startsWith("/api/gaps/")) {
      const match = route.match(/^\/api\/gaps\/(\d+)\/evidence$/);
      if (match) {
        const topic = body && body.topic ? body.topic : null;
        const evidence = await haveJSON(`gaps-evidence--${slug(topic)}--${match[1]}.json`);
        if (evidence) return evidence;
        throw httpError(404, "static snapshot: that evidence bundle was not precomputed");
      }
    }
    if (route === "/api/report") return reportPayload(body);
    if (route === "/api/report/markdown") {
      const text = await haveText(`report--${slug(body.topic)}.md`);
      if (text !== null) return text;
      throw httpError(404, "static snapshot: that report was not precomputed.");
    }
    if (route === "/api/export/cypher") {
      const text = await haveText("export-cypher.txt");
      if (text !== null) return text;
      throw httpError(404, "static snapshot: the Cypher export was not bundled.");
    }
    if (route === "/api/plan") {
      const dsl = params.get("q") !== null ? params.get("q") : (body.query || "");
      const payload = planQuery(dsl);
      if (!payload.ok) throw httpError(422, payload.error);   // the API's contract
      return payload;
    }
    if (route === "/api/agent") return agentAnswer(body.question || "", body);
    throw httpError(404, `static snapshot: ${route} is not available without the server. ` +
      "Run the app locally (python run.py) for the full API.");
  }

  async function haveText(name) {
    if (!state.fetchImpl) return null;
    const response = await state.fetchImpl(state.dataDir + name, { cache: "force-cache" });
    return response.ok ? response.text() : null;
  }

  async function streamText(question) {
    const recorded = await haveText(`agent-stream--${slug(question)}.txt`);
    if (recorded !== null) return recorded;
    const payload = await agentAnswer(question, {});
    const frame = (stage, detail) => `event: ${stage}\ndata: ${JSON.stringify({ stage, detail })}\n\n`;
    return [
      frame("intent", payload.intent),
      frame("plan", { steps: ["load snapshot", "rank by PageRank", "compose templated answer"] }),
      frame("tool", { tool: "graph_metrics", ok: true }),
      frame("retrieval", { engine: "static-snapshot", papers: (payload.context.papers || []).length }),
      frame("synthesis", { engine: payload.answer_engine, used_llm: false }),
      frame("done", { ms: 0 }),
      frame("answer", payload),
    ].join("");
  }

  /* ---------------------------------------------------- DSL planner (JS) -- */

  function planQuery(dsl) {
    const NODE_LABELS = ["Paper", "Author", "Topic", "Method", "Dataset", "Institution", "Claim", "Community"];
    const REL_TYPES = ["AUTHORED", "CITES", "STUDIES", "USES_METHOD", "USES_DATASET", "AFFILIATED_WITH",
      "MAKES_CLAIM", "SUPPORTS", "CONTRADICTS", "BELONGS_TO", "RELATED_TO"];
    const FULLTEXT = "nexus-fulltext";
    const pick = (raw, allowed) => {
      const hit = allowed.find((value) => value.toLowerCase() === raw.trim().toLowerCase());
      if (!hit) {
        throw reject(`Unknown ${allowed === NODE_LABELS ? "node type" : "relationship"} '${raw.trim()}'. ` +
          `Allowed: ${allowed.join(", ")}`);
      }
      return hit;
    };
    let text = String(dsl || "");
    let depth = 1;          // declared outside the parse guard: the catch returns, and the
    let limit = 50;         // planner needs both afterwards
    const filters = [];
    const labels = [];
    const relationships = [];
    let topic = null;
    const reject = (message) => ({ ok: false, engine: "nexus-js-planner", error: message });
    const consume = (regex, apply) => {
      const match = regex.exec(text);
      if (!match) return null;
      text = text.replace(match[0], " ");
      return apply(match);
    };
    try {
    consume(/topic\s*:\s*"([^"]+)"/i, (m) => { topic = m[1].trim(); return null; });
    consume(/author\s*:\s*"([^"]+)"/i, (m) => filters.push({ kind: "TextContains", field: "name", value: m[1].trim() }));
    consume(/year\s*>=\s*(\d{4})/i, (m) => filters.push({ kind: "IntAtLeast", field: "year", value: Number(m[1]) }));
    consume(/year\s*<=\s*(\d{4})/i, (m) => filters.push({ kind: "IntAtMost", field: "year", value: Number(m[1]) }));
    consume(/depth\s*<=?\s*(\d{1,2})/i, (m) => { depth = Math.min(Math.max(Number(m[1]), 1), 3); return null; });
    consume(/limit\s+(\d{1,4})/i, (m) => { limit = Math.min(Math.max(Number(m[1]), 1), 500); return null; });
    consume(/type\s+in\s*\(([^)]*)\)/i, (m) => {
      m[1].split(",").map((s) => s.trim()).filter(Boolean).forEach((raw) => labels.push(pick(raw, NODE_LABELS)));
      return null;
    });
    consume(/rel\s+in\s*\(([^)]*)\)/i, (m) => {
      m[1].split(",").map((s) => s.trim()).filter(Boolean).forEach((raw) => relationships.push(pick(raw, REL_TYPES)));
      return null;
    });
    } catch (rejected) {
      return rejected && rejected.ok === false ? rejected : reject(String(rejected && rejected.message || rejected));
    }
    const free = text.replace(/\s+/g, " ").trim().replace(/^"|"$/g, "");
    const seed = [topic, free || null].filter(Boolean).join(" ");

    if (!seed && !filters.length && !labels.length && !relationships.length) {
      return reject('empty query: give free text, a topic:"…" phrase, or a structured clause');
    }

    const params = {};
    const why = [];
    const predicates = [];
    filters.forEach((filter) => {
      const { kind, field, value } = filter;
      if (kind === "TextEquals") { predicates.push(`toLower(n.${field}) = toLower($${field}Exact)`); params[`${field}Exact`] = value; }
      if (kind === "TextContains") { predicates.push(`toLower(n.${field}) CONTAINS toLower($${field})`); params[field] = value; }
      if (kind === "IntAtLeast") { predicates.push(`n.${field} >= $${field}Min`); params[`${field}Min`] = value; }
      if (kind === "IntAtMost") { predicates.push(`n.${field} <= $${field}Max`); params[`${field}Max`] = value; }
      if (kind === "InList") { predicates.push(`n.${field} IN $${field}List`); params[`${field}List`] = value; }
    });
    if (labels.length) why.push(`Restricted scan to labels: ${labels.join(", ")}.`);
    if (filters.length) {
      why.push("Applied structured filters: " + filters.map((f) => `${f.kind}(field=${f.field}, value=${f.value})`).join(", "));
    }
    if (relationships.length) {
      predicates.push(`(n)-[${relationships.join("|")}]-()`);
      why.push(`Relationship filter: edge type must be one of ${relationships.join(", ")}.`);
    }
    const where = predicates.join(" AND ");

    let cypher;
    if (seed) {
      why.push(`Free text '${seed}' handled by the full-text index (tokenised, relevance-ranked).`);
      params.q = seed;
      params.limit = limit;
      const head = [
        `CALL db.index.fulltext.queryNodes('${FULLTEXT}', $q) YIELD node AS n, score`,
        "WHERE score > 0.0",
      ];
      if (labels.length) head[1] += " AND (" + labels.map((l) => `'${l}' IN labels(n)`).join(" OR ") + ")";
      const lines = head.slice();
      if (where) {
        lines.push(`WITH n, score WHERE ${where}`);
        why.push("Post-filter applied on indexed properties (bound parameters only).");
      }
      lines.push("RETURN n, score ORDER BY score DESC LIMIT $limit");
      cypher = lines.join("\n");
    } else {
      params.limit = limit;
      why.push(`Limit ${limit} keeps the first paint under a second (progressive expansion).`);
      const labelClause = !labels.length ? "n" : labels.length === 1 ? `n:${labels[0]}` : `n:${labels.join("|")}`;
      const lines = [`MATCH (${labelClause})`];
      if (where) lines.push(`WHERE ${where}`);
      lines.push("RETURN n", "ORDER BY coalesce(n.pagerank, 0.0) DESC", "LIMIT $limit");
      cypher = lines.join("\n");
    }

    const cost = estimateCost({ labels, relationships, depth, limit, text: seed || null });
    return {
      ok: true, engine: "nexus-js-planner", source: "browser",
      query: { text: seed || null, labels, relationships, depth, limit },
      cypher, params, explanation: why, cost,
      dsl: String(dsl || ""),
      note: "Planned in your browser from the same rules as the Kotlin reference planner " +
        "(checked against tests/data/planner_parity.json). Cypher here is generated, not executed.",
    };
  }

  function estimateCost(query, corpusEntities) {
    const budget = corpusEntities || 4000;
    const fanout = 8;
    const seeds = Math.min(query.limit, 200);
    const hops = Math.max(query.depth - 1, 0);
    const raw = seeds * (1 + hops * fanout);
    return {
      depth: query.depth, fanout, seeds,
      estimated_nodes_visited: Math.min(Math.round(raw), budget),
      budget, strategy: query.text ? "fulltext -> expand" : "label scan -> rank",
      safe: raw <= budget,
    };
  }

  /* --------------------------------------------------------- browser glue -- */

  function status() {
    return { loaded: state.loaded, version: VERSION, graph: state.graph
      ? { nodes: state.graph.nodes.size, edges: state.graph.edges.length } : null,
      index: state.index, missing: state.missing };
  }

  const NEXUSStatic = {
    VERSION, configure, load, api, status,
    // exported for the node checks in scripts/check_site_data.mjs
    graph: graphPayload, expand: expandPayload, neighbours, path: shortestPath,
    papers: paperItems, nodeDetail, paperDetail: (id) => nodeDetail(id.startsWith("paper:") ? id : `paper:${id}`),
    search: searchIndex, timeline: timelinePayload, explorer: explorerOverview,
    plan: planQuery, agent: agentAnswer, stream: streamText, gaps: gapsPayload, report: reportPayload,
    slug, contentTokens, jaccard,
  };
  root.NEXUSStatic = NEXUSStatic;
  if (typeof module !== "undefined" && module.exports) module.exports = NEXUSStatic;

  /* in the browser the static build has no server: answer /api/* from the snapshot */
  if (typeof window !== "undefined" && window.NEXUS_MODE === "static") {
    const nativeFetch = window.fetch ? window.fetch.bind(window) : null;
    state.fetchImpl = nativeFetch;
    window.fetch = async function (input, init) {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const options = init || (typeof input === "object" ? input : {});
      if (!/^\/api\//.test(url)) return nativeFetch ? nativeFetch(input, init) : Promise.reject(new Error("no fetch"));
      try {
        if (/^\/api\/agent\/stream/.test(url)) {
          const question = options.body ? JSON.parse(options.body).question : "";
          const body = await streamText(question);
          return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        const payload = await api(url, options);
        const isText = typeof payload === "string";
        return new Response(isText ? payload : JSON.stringify(payload), {
          status: 200,
          headers: { "content-type": isText ? "text/plain; charset=utf-8" : "application/json" },
        });
      } catch (err) {
        return new Response(JSON.stringify({ detail: err.detail || err.message || "static snapshot error" }),
          { status: err.status || 500, headers: { "content-type": "application/json" } });
      }
    };

    // service worker + the install button (this is what makes it an app)
    window.addEventListener("load", () => {
      if ("serviceWorker" in navigator && location.protocol !== "file:") {
        navigator.serviceWorker.register("sw.js").catch(() => {});
      }
      const button = document.getElementById("install-app");
      if (!button) return;
      let prompt = null;
      window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        prompt = event;
        button.hidden = false;
      });
      button.addEventListener("click", async () => {
        if (!prompt) return;
        prompt.prompt();
        await prompt.userChoice;
        prompt = null;
        button.hidden = true;
      });
      window.addEventListener("appinstalled", () => { button.hidden = true; });
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
