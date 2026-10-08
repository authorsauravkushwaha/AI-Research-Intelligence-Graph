/* NEXUS — static snapshot data layer.
 *
 * The published site (GitHub Pages) has no FastAPI behind it, so this file answers the
 * `/api/*` calls the front end makes — from the recorded snapshot in ./data/ and, for the
 * cheap views, by recomputing them in the browser over the shipped graph.
 *
 *     recorded at build time  (the expensive engines)
 *         /api/gaps, /api/report, /api/report/markdown, /api/agent, /api/agent/stream,
 *         /api/explorer (demo scopes), /api/timeline (demo scopes), /api/dashboard,
 *         /api/health, /api/services, /api/communities, /api/conflicts, /api/predictions,
 *         /api/centrality, /api/export/cypher, /api/algorithms, /api/safety, /api/tools
 *
 *     recomputed here          (everything the UI can reach interactively)
 *         /api/graph, /api/graph/expand, /api/graph/neighbours, /api/graph/path,
 *         /api/nodes, /api/papers, /api/search, /api/timeline, /api/explorer, /api/plan
 *
 * Each recomputed handler is a port of the server code it replaces (the same filters, the
 * same rounding, the same truncation rules), and `scripts/check_site_data.mjs` compares the
 * two on the recorded scopes — so "the static site answers like the app" is a test result,
 * not a claim. Anything the snapshot does not contain answers 501 with the command that
 * would answer it live; it never invents a number.
 */
(function (root) {
  "use strict";

  var NODE_LABELS = ["Paper", "Author", "Topic", "Method", "Dataset", "Institution", "Claim", "Community"];
  /* the planner's closed whitelist (backend/services/planner.py) */
  var REL_TYPES = ["AUTHORED", "CITES", "STUDIES", "USES_METHOD", "USES_DATASET", "AFFILIATED_WITH",
                   "MAKES_CLAIM", "SUPPORTS", "CONTRADICTS", "BELONGS_TO", "RELATED_TO"];
  /* every relationship type the graph can hold (backend/models/graph.py) */
  var GRAPH_REL_TYPES = REL_TYPES.concat(["MEASURED_BY", "SIMILAR_TO", "PREDICTED_LINK"]);
  var DEFAULT_DEPTH = 1;
  var DEFAULT_LIMIT = 50;
  var FANOUT = 8.0;
  var COST_BUDGET = 4000;
  var SEED_LIMIT = 60;

  var state = {
    dataDir: "data/",
    snapshot: {},
    datasets: Object.create(null),
    pending: Object.create(null),
    missing: [],
    index: null,
    graph: null,
    fetcher: null,
    delays: false,
  };

  /* ------------------------------------------------------------------ utils */

  function slug(text) {
    var cleaned = String(text === null || text === undefined ? "" : text)
      .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return cleaned.slice(0, 60) || "default";
  }

  function round(value, digits) {
    if (value === null || value === undefined || value === "") return value;
    var factor = Math.pow(10, digits === undefined ? 6 : digits);
    return Math.round(Number(value) * factor) / factor;
  }

  function fail(status, detail) {
    var error = new Error(detail);
    error.status = status;
    error.detail = detail;
    return error;
  }

  function notPrecomputed(what, hint) {
    return fail(501, "static snapshot: " + what + " is not part of the published build. " +
      (hint || "Run NEXUS locally (python run.py, or docker compose up) for a live answer."));
  }

  function fetcher() {
    if (state.fetcher) return state.fetcher;
    if (root && typeof root.fetch === "function") return root.fetch.bind(root);
    if (typeof fetch === "function") return fetch;
    throw fail(500, "no fetch implementation available");
  }

  function dataPath(name) {
    return state.dataDir + name;
  }

  function load(name) {
    if (Object.prototype.hasOwnProperty.call(state.datasets, name)) {
      return Promise.resolve(state.datasets[name]);
    }
    if (state.pending[name]) return state.pending[name];
    state.pending[name] = fetcher()(dataPath(name), { cache: "force-cache" })
      .then(function (response) {
        if (!response.ok) return null;
        return response.json().then(function (payload) {
          state.datasets[name] = payload;
          return payload;
        });
      })
      .catch(function () { return null; })
      .then(function (payload) {
        if (payload === null && state.missing.indexOf(name) === -1) state.missing.push(name);
        delete state.pending[name];
        return payload;
      });
    return state.pending[name];
  }

  function loadText(name) {
    return fetcher()(dataPath(name), { cache: "force-cache" })
      .then(function (response) { return response.ok ? response.text() : null; })
      .catch(function () { return null; });
  }

  function require$_(name, payload) {
    if (payload) return payload;
    throw fail(500, "static snapshot is incomplete: data/" + name + " is missing");
  }

  /* ------------------------------------------------------------- graph index */

  /* node shape in data/graph.json: { id, label, type, color, props, pagerank,
     betweenness, degree, community } — `label` is the human name, `type` the node label. */
  /* The API flattens a node's / an edge's properties into the same object as its
     structural fields (GNode.to_json / GEdge.to_json), so the snapshot keeps whatever the
     engine said and this splits the two apart again. */
  function structuralProps(raw, structural) {
    var props = {};
    Object.keys(raw).forEach(function (key) {
      if (structural.indexOf(key) === -1) props[key] = raw[key];
    });
    return props;
  }

  function buildGraph(snapshot) {
    var nodes = new Map();
    var byLabel = new Map();
    var adjacency = new Map();
    var edges = (snapshot.edges || []).map(function (edge) {
      return {
        id: edge.id || (edge.source + "|" + edge.type + "|" + edge.target),
        src: edge.source, dst: edge.target, type: edge.type,
        props: edge.properties || edge.props || structuralProps(edge, ["id", "source", "target", "type"]),
      };
    });
    (snapshot.nodes || []).forEach(function (node) {
      var record = {
        id: node.id,
        name: node.label,
        type: node.type,
        color: node.color,
        props: node.props || structuralProps(node, ["id", "label", "name", "type", "color"]),
        pagerank: Number(node.pagerank || 0),
        betweenness: Number(node.betweenness || 0),
        degree: Number(node.degree || 0),
        community: node.community === undefined || node.community === null ? -1 : node.community,
      };
      nodes.set(record.id, record);
      if (!byLabel.has(record.type)) byLabel.set(record.type, []);
      byLabel.get(record.type).push(record);
    });
    edges.forEach(function (edge) {
      if (!adjacency.has(edge.src)) adjacency.set(edge.src, []);
      if (!adjacency.has(edge.dst)) adjacency.set(edge.dst, []);
      adjacency.get(edge.src).push({ edge: edge, other: edge.dst, direction: "out" });
      adjacency.get(edge.dst).push({ edge: edge, other: edge.src, direction: "in" });
    });
    state.graph = { nodes: nodes, edges: edges, adjacency: adjacency, byLabel: byLabel,
                    legend: snapshot.legend || {}, communities: snapshot.communities || [] };
    return state.graph;
  }

  function graph() {
    if (!state.graph) throw fail(500, "static snapshot: the graph has not been loaded yet");
    return state.graph;
  }

  function nodeLabelList(type) {
    return (graph().byLabel.get(type) || []).slice();
  }

  function metricsFor(node) {
    return {
      pagerank: round(node.pagerank, 6),
      betweenness: round(node.betweenness, 6),
      community: node.community,
      degree: node.degree,
    };
  }

  /* node.to_json(): identity, colour, then the (flattened) properties + metrics. */
  function nodeJson(node) {
    var out = { id: node.id, label: node.name, type: node.type, color: node.color || colourFor(node.type) };
    var props = Object.assign({}, node.props);
    props.pagerank = round(node.pagerank, 6);
    props.betweenness = round(node.betweenness, 6);
    props.community = node.community;
    props.degree = node.degree;
    Object.keys(props).forEach(function (key) {
      if (props[key] === null || props[key] === undefined) delete props[key];
    });
    return Object.assign(out, props);
  }

  /* GNode.to_json(): identity, colour and properties — no metrics. The neighbours
     endpoint returns the centre node this way. */
  function nodeJsonPlain(node) {
    var out = { id: node.id, label: node.name, type: node.type, color: node.color || colourFor(node.type) };
    Object.keys(node.props).forEach(function (key) {
      if (node.props[key] !== null && node.props[key] !== undefined) out[key] = node.props[key];
    });
    return out;
  }

  function colourFor(type) {
    var colours = {
      Paper: "#4cc9f0", Author: "#b892ff", Topic: "#f4a261", Method: "#2ec4b6",
      Dataset: "#8ecae6", Institution: "#94a3b8", Claim: "#ff6b6b", Community: "#f9c74f",
      Metric: "#a3e635",
    };
    return colours[type] || "#8899aa";
  }

  function neighboursOf(nodeId) {
    var grouped = {};
    (graph().adjacency.get(nodeId) || []).forEach(function (entry) {
      var other = graph().nodes.get(entry.other);
      if (!other) return;
      if (!grouped[entry.edge.type]) grouped[entry.edge.type] = [];
      grouped[entry.edge.type].push({
        id: other.id, name: other.name, type: other.type, direction: entry.direction,
        weight: entry.edge.props.weight === undefined ? null : entry.edge.props.weight,
        predicted: Boolean(entry.edge.props.predicted),
        properties: Object.assign({}, entry.edge.props),
      });
    });
    return grouped;
  }

  /* ------------------------------------------------------------ graph views */

  function passesFilters(node, request) {
    var year = node.props ? node.props.year : null;
    if (request.year_min !== null && request.year_min !== undefined && (year || 0) < request.year_min) return false;
    if (request.year_max !== null && request.year_max !== undefined && (year || 9999) > request.year_max) return false;
    if (request.min_degree && node.degree < request.min_degree) return false;
    return true;
  }

  function subgraph(request) {
    var model = graph();
    var req = Object.assign({
      seeds: null, query: null, depth: DEFAULT_DEPTH, node_types: null, rel_types: null,
      year_min: null, year_max: null, include_predicted: true,
      max_nodes: 260, max_edges: 800, focus: null,
    }, request || {});

    var seeds = (req.seeds || []).filter(function (id) { return model.nodes.has(id); });
    if (!seeds.length && req.query) {
      seeds = search(req.query, req.node_types, SEED_LIMIT).map(function (hit) { return hit.id; });
    }
    if (!seeds.length) {
      // default view: the most influential nodes, never the whole graph
      seeds = Array.from(model.nodes.values())
        .sort(function (a, b) { return b.pagerank - a.pagerank; })
        .slice(0, SEED_LIMIT).map(function (node) { return node.id; });
    }

    var allowedTypes = req.node_types ? new Set(req.node_types) : null;
    var allowedRels = req.rel_types ? new Set(req.rel_types) : null;
    var adjacency = new Map();
    model.edges.forEach(function (edge) {
      if (edge.type === "PREDICTED_LINK" && !req.include_predicted) return;
      if (allowedRels && !allowedRels.has(edge.type)) return;
      if (!adjacency.has(edge.src)) adjacency.set(edge.src, []);
      if (!adjacency.has(edge.dst)) adjacency.set(edge.dst, []);
      adjacency.get(edge.src).push(edge);
      adjacency.get(edge.dst).push(edge);
    });

    var visited = new Set();
    var truncated = false;
    var frontier = Array.from(new Set(seeds));
    var maxDepth = Math.max(0, Math.min(req.depth, 3));
    for (var level = 0; level <= maxDepth; level += 1) {
      var next = [];
      frontier.forEach(function (nodeId) {
        if (visited.has(nodeId)) return;
        if (visited.size >= req.max_nodes) { truncated = true; return; }
        // the same rule the store applies: the cap is what truncates, an empty frontier is not
        var node = model.nodes.get(nodeId);
        if (!node) return;
        if (allowedTypes && !allowedTypes.has(node.type)) return;
        if (level > 0 && !passesFilters(node, req)) return;
        visited.add(nodeId);
        (adjacency.get(nodeId) || []).forEach(function (edge) {
          next.push(edge.src === nodeId ? edge.dst : edge.src);
        });
      });
      frontier = next;
      if (!frontier.length) break;
    }

    var nodes = [];
    visited.forEach(function (nodeId) {
      var node = model.nodes.get(nodeId);
      if (node) nodes.push(nodeJson(node));
    });
    var edges = [];
    for (var e = 0; e < model.edges.length; e += 1) {
      var edge = model.edges[e];
      if (edges.length >= req.max_edges) { truncated = true; break; }
      if (!visited.has(edge.src) || !visited.has(edge.dst)) continue;
      if (edge.type === "PREDICTED_LINK" && !req.include_predicted) continue;
      if (allowedRels && !allowedRels.has(edge.type)) continue;
      edges.push(edgeJson(edge));
    }

    var notes = [];
    if (truncated) {
      notes.push("View truncated to " + nodes.length + " nodes / " + edges.length + " edges " +
        "(progressive expansion keeps the first paint fast — expand a node to see more).");
    }
    var counts = {};
    nodes.forEach(function (node) { counts[node.type] = (counts[node.type] || 0) + 1; });

    var payload = { nodes: nodes, edges: edges, truncated: truncated, notes: notes, counts: counts, focus: null };
    if (req.focus) {
      var focusId = visited.has(req.focus) ? req.focus : null;
      if (!focusId) {
        Array.from(visited).forEach(function (id) {
          var node = model.nodes.get(id);
          if (!focusId && node && node.name.toLowerCase() === String(req.focus).toLowerCase()) focusId = id;
        });
      }
      if (focusId) payload.focus = { id: focusId, neighbours: neighboursRaw(focusId) };
    }
    return payload;
  }

  function edgeJson(edge) {
    var out = { id: edge.id, source: edge.src, target: edge.dst, type: edge.type };
    Object.keys(edge.props).forEach(function (key) {
      if (edge.props[key] !== null && edge.props[key] !== undefined) out[key] = edge.props[key];
    });
    return out;
  }

  function neighboursRaw(nodeId) {
    var out = [];
    (graph().adjacency.get(nodeId) || []).forEach(function (entry) {
      var other = graph().nodes.get(entry.other);
      if (!other) return;
      out.push({ id: other.id, label: other.name, type: other.type, rel: entry.edge.type,
                 properties: entry.edge.props });
    });
    return out;
  }

  /* store.expand(): one hop out of the given nodes. An edge is only attached when it
     brings a new node with it — that is what makes "expand" a view of the frontier rather
     than a dump of the whole neighbourhood. */
  function expand(nodeIds, relTypes, limit) {
    var model = graph();
    var allowed = relTypes && relTypes.length ? new Set(relTypes) : null;
    var cap = limit || 60;
    var visited = new Set();
    var nodes = [];
    (nodeIds || []).forEach(function (id) {
      var node = model.nodes.get(id);
      if (node) { visited.add(id); nodes.push(nodeJson(node)); }
    });
    var edges = [];
    var added = 0;
    var truncated = false;
    for (var i = 0; i < model.edges.length; i += 1) {
      if (added >= cap) { truncated = true; break; }
      var edge = model.edges[i];
      if (allowed && !allowed.has(edge.type)) continue;
      if (visited.has(edge.src) && !visited.has(edge.dst)) {
        var target = model.nodes.get(edge.dst);
        if (target && !visited.has(edge.dst)) { visited.add(edge.dst); nodes.push(nodeJson(target)); added += 1; }
        edges.push(edgeJson(edge));
      } else if (visited.has(edge.dst) && !visited.has(edge.src)) {
        var source = model.nodes.get(edge.src);
        if (source && !visited.has(edge.src)) { visited.add(edge.src); nodes.push(nodeJson(source)); added += 1; }
        edges.push(edgeJson(edge));
      }
    }
    var counts = {};
    nodes.forEach(function (node) { counts[node.type] = (counts[node.type] || 0) + 1; });
    return { nodes: nodes, edges: edges, truncated: truncated, notes: [], counts: counts };
  }

  function shortestPath(source, target, maxHops) {
    var model = graph();
    if (!model.nodes.has(source)) throw fail(404, "node " + source + " not found");
    if (!model.nodes.has(target)) throw fail(404, "node " + target + " not found");
    var limit = Math.max(1, Math.min(Number(maxHops) || 4, 6));

    var previous = new Map([[source, null]]);
    var frontier = [source];
    for (var level = 0; level < limit && !previous.has(target); level += 1) {
      var next = [];
      frontier.forEach(function (id) {
        (model.adjacency.get(id) || []).forEach(function (entry) {
          if (previous.has(entry.other)) return;
          previous.set(entry.other, { from: id, edge: entry.edge });
          next.push(entry.other);
        });
      });
      frontier = next;
      if (!frontier.length) break;
    }
    if (!previous.has(target)) {
      return { found: false, reason: "No path of " + limit + " hops or fewer between " + source + " and " + target, hops: [] };
    }
    var chain = [];
    for (var cursor = target; cursor !== null && cursor !== undefined; cursor = previous.get(cursor) ? previous.get(cursor).from : null) {
      chain.push(cursor);
    }
    chain.reverse();
    var hops = [];
    for (var i = 0; i < chain.length - 1; i += 1) {
      var step = previous.get(chain[i + 1]);
      var to = chain[i + 1];
      hops.push({
        from: chain[i], to: to, direction: step.edge.src === chain[i] ? "out" : "in",
        label: model.nodes.get(chain[i]).name + " —" + step.edge.type + "→ " + model.nodes.get(to).name,
        type: step.edge.type,
      });
    }
    var labels = {};
    chain.forEach(function (id) { labels[id] = model.nodes.get(id).name; });
    return { found: true, nodes: chain, hops: hops, hops_count: hops.length, node_labels: labels };
  }

  /* --------------------------------------------------------------- listings */

  function sortKey(sort) {
    if (sort === "name") return function (n) { return n.name.toLowerCase(); };
    if (sort === "betweenness") return function (n) { return n.betweenness; };
    if (sort === "degree") return function (n) { return n.degree; };
    if (sort === "year") return function (n) { return n.props.year || 0; };
    return function (n) { return n.pagerank; };
  }

  function listNodes(query) {
    var q = Object.assign({ label: null, q: null, sort: "pagerank", order: "desc",
                            year_min: null, year_max: null, field: null, limit: 50, offset: 0 }, query || {});
    var items = q.label ? nodeLabelList(q.label) : Array.from(graph().nodes.values());
    if (q.year_min !== null && q.year_min !== undefined) items = items.filter(function (n) { return (n.props.year || 0) >= q.year_min; });
    if (q.year_max !== null && q.year_max !== undefined) items = items.filter(function (n) { return (n.props.year || 9999) <= q.year_max; });
    if (q.field) items = items.filter(function (n) { return n.props.field === q.field; });

    var key = sortKey(q.sort);
    var reverse = q.order !== "asc";
    items = items.slice().sort(function (a, b) {
      var ka = key(a), kb = key(b);
      if (ka === kb) return 0;
      return (ka < kb ? -1 : 1) * (reverse ? -1 : 1);
    });
    /* list_nodes() has no text filter: year/field filters run first, the ranking decides the
       page, and the caller filters the page afterwards (which is why `total` in a filtered
       response describes the rows returned, not every match in the graph). */
    var total = items.length;
    var page = items.slice(Number(q.offset) || 0, (Number(q.offset) || 0) + (Number(q.limit) || 50));
    return {
      total: total, offset: Number(q.offset) || 0, limit: Number(q.limit) || 50,
      items: page.map(function (node) {
        return {
          id: node.id, label: node.name, type: node.type,
          year: node.props.year === undefined ? null : node.props.year,
          field: node.props.field === undefined ? null : node.props.field,
          url: node.props.url === undefined ? null : node.props.url,
          pagerank: round(node.pagerank, 5), betweenness: round(node.betweenness, 5),
          degree: node.degree, community: node.community,
        };
      }),
    };
  }

  function countOccurrences(haystack, needle) {
    if (!needle) return 0;
    var count = 0;
    var from = 0;
    for (;;) {
      var at = haystack.indexOf(needle, from);
      if (at === -1) return count;
      count += 1;
      from = at + needle.length;
    }
  }

  function search(text, labels, limit) {
    var needle = String(text || "").trim().toLowerCase();
    var allowed = labels && labels.length ? new Set(labels) : null;
    var results = [];
    graph().nodes.forEach(function (node) {
      if (allowed && !allowed.has(node.type)) return;
      var haystack = (node.name + " " + (node.props.text || "") + " " + (node.props.abstract || "")).toLowerCase();
      if (needle && haystack.indexOf(needle) === -1) return;
      var score = 0;
      if (needle) {
        if (node.name.toLowerCase() === needle) score += 2;
        if (node.name.toLowerCase().indexOf(needle) === 0) score += 1;
        score += countOccurrences(haystack, needle) * 0.2;
      }
      score += node.pagerank * 5;
      results.push({
        id: node.id, label: node.name, type: node.type, score: round(score, 5),
        year: node.props.year === undefined ? null : node.props.year,
        field: node.props.field === undefined ? null : node.props.field,
        url: node.props.url === undefined ? null : node.props.url,
        summary_source: node.props.summary_source === undefined ? null : node.props.summary_source,
      });
    });
    results.sort(function (a, b) { return b.score - a.score; });
    return limit ? results.slice(0, limit) : results;
  }

  /* ------------------------------------------------------------ node detail */

  function communityProfile(index) {
    return (graph().communities || []).find(function (entry) { return entry.community_index === index; }) || null;
  }

  function explainMetrics(node, metrics) {
    var out = [];
    var pr = metrics.pagerank || 0;
    var bc = metrics.betweenness || 0;
    if (pr > 0) {
      var strength = pr > 0.01 ? "top-tier" : (pr > 0.004 ? "notable" : "modest");
      out.push({
        metric: "PageRank", value: pr.toFixed(5),
        meaning: "PageRank measures influence by how much highly-connected research points at this node. " +
                 "At " + pr.toFixed(5) + " this is " + strength + " influence within the analyzed corpus.",
      });
    }
    if (bc > 0) {
      out.push({
        metric: "Betweenness centrality", value: bc.toFixed(5),
        meaning: "Betweenness counts how often this node sits on the shortest path between others. " +
          (bc > 0.01
            ? "This node acts as a bridge between otherwise separated research areas."
            : "Its bridging role is limited: most paths do not need to pass through it."),
      });
    }
    out.push({
      metric: "Degree", value: String(metrics.degree),
      meaning: metrics.degree + " direct relationships were collected in the graph for this node.",
    });
    if (metrics.community !== undefined && metrics.community >= 0) {
      var profile = communityProfile(metrics.community);
      out.push({
        metric: "Research community", value: String(metrics.community),
        meaning: "Louvain community detection places this node in community " + metrics.community +
          (profile ? " — '" + profile.name + "' (" + profile.paper_count + " papers)." : "."),
      });
    }
    return out;
  }

  function nodeDetail(nodeId) {
    var model = graph();
    var node = model.nodes.get(nodeId);
    if (!node) throw fail(404, "node " + nodeId + " not found");
    var grouped = neighboursOf(nodeId);
    var counts = {};
    Object.keys(grouped).forEach(function (rel) { counts[rel] = grouped[rel].length; });
    var capped = {};
    Object.keys(grouped).forEach(function (rel) { capped[rel] = grouped[rel].slice(0, 40); });
    var metrics = metricsFor(node);

    var detail = {
      id: node.id, label: node.name, type: node.type,
      properties: node.props, metrics: metrics,
      metrics_explained: explainMetrics(node, metrics),
      neighbours: capped, neighbour_counts: counts,
    };
    if (node.type === "Paper") {
      detail.authors = (grouped.AUTHORED || []).map(function (n) { return n.name; });
      detail.topics = (grouped.STUDIES || []).map(function (n) { return n.name; });
      detail.methods = (grouped.USES_METHOD || []).map(function (n) { return n.name; });
      detail.datasets = (grouped.USES_DATASET || []).map(function (n) { return n.name; });
      detail.citations_out = (grouped.CITES || []).filter(function (n) { return n.direction === "out"; }).map(function (n) { return n.id; });
      detail.cited_by = (grouped.CITES || []).filter(function (n) { return n.direction === "in"; }).map(function (n) { return n.id; });
      detail.in_graph_citation_degree = detail.citations_out.length + detail.cited_by.length;
      detail.similar_papers = (grouped.SIMILAR_TO || []).map(function (n) {
        return { id: n.id, name: n.name, similarity: n.properties.similarity === undefined ? null : n.properties.similarity };
      });
      detail.claims = (grouped.MAKES_CLAIM || []).map(function (n) {
        var claim = model.nodes.get(n.id);
        return { id: n.id, text: claim ? claim.props.text : null, stance: claim ? claim.props.stance : null };
      });
    }
    return detail;
  }

  function paperDetail(paperId) {
    var model = graph();
    var detail = nodeDetail(paperId);
    if (detail.type !== "Paper") throw fail(404, "paper " + paperId + " not found");
    detail.community = communityProfile(model.nodes.get(paperId).community);
    var predicted = predictedLinks();
    detail.predicted_links = predicted.filter(function (link) {
      return paperId === link.source || paperId === link.target;
    }).map(function (link) {
      return Object.assign({}, link, { direction: link.source === paperId ? "out" : "in" });
    });
    var all = conflicts();
    detail.conflicts = all.filter(function (conflict) {
      return paperId === conflict.paper_a || paperId === conflict.paper_b;
    });
    detail.provenance = {
      record: "arXiv metadata (id, title, url) as listed in the public corpus index",
      summary: detail.properties.summary_source || "unknown",
      authors: detail.properties.author_status || "not-collected",
    };
    return detail;
  }

  function conflicts() {
    var recorded = state.datasets["conflicts.json"];
    return (recorded && recorded.conflicts) || [];
  }

  function predictedLinks() {
    var recorded = state.datasets["predictions.json"];
    return (recorded && recorded.predictions) || [];
  }

  /* --------------------------------------------------------------- explorer */

  function recencyOf(years) {
    var keys = Object.keys(years).map(Number).sort(function (a, b) { return a - b; });
    var recent = keys.filter(function (year) { return year >= 2023; })
      .reduce(function (total, year) { return total + years[year]; }, 0);
    var total = keys.reduce(function (sum, year) { return sum + years[year]; }, 0);
    return {
      papers_since_2023: recent,
      share_since_2023: total ? round(recent / total, 3) : 0,
      newest_year: keys.length ? keys[keys.length - 1] : null,
      oldest_year: keys.length ? keys[0] : null,
    };
  }

  function topBy(metric, label, limit) {
    return nodeLabelList(label).slice()
      .sort(function (a, b) {
        var va = metric === "degree" ? a.degree : (metric === "betweenness" ? a.betweenness : a.pagerank);
        var vb = metric === "degree" ? b.degree : (metric === "betweenness" ? b.betweenness : b.pagerank);
        return vb - va;
      })
      .slice(0, limit)
      .map(function (node) {
        return {
          id: node.id, label: node.name, type: node.type,
          value: round(metric === "degree" ? node.degree : (metric === "betweenness" ? node.betweenness : node.pagerank), 6),
          degree: node.degree, year: node.props.year === undefined ? null : node.props.year,
          community: node.community, url: node.props.url === undefined ? null : node.props.url,
        };
      });
  }

  /* browser-side scope resolution: topic phrases → matching topic nodes → their papers.
     The build records the gap engine's own resolution for the demo scopes (data/
     explorer--<scope>.json); this is the documented fallback for any other phrase. */
  function browserScope(topic, yearMin, yearMax, field) {
    var model = graph();
    var matched = [];
    if (topic) {
      var needle = String(topic).toLowerCase();
      nodeLabelList("Topic").forEach(function (node) {
        var name = node.name.toLowerCase();
        if (name === needle || name.indexOf(needle) !== -1 || needle.indexOf(name) !== -1) matched.push(node);
      });
    }
    var paperIds = new Set();
    var methodIds = new Set();
    if (!topic) {
      nodeLabelList("Paper").forEach(function (node) { paperIds.add(node.id); });
      nodeLabelList("Method").forEach(function (node) { methodIds.add(node.id); });
    } else {
      matched.forEach(function (topicNode) {
        (model.adjacency.get(topicNode.id) || []).forEach(function (entry) {
          if (entry.edge.type !== "STUDIES" && entry.edge.type !== "BELONGS_TO") return;
          var other = model.nodes.get(entry.other);
          if (!other) return;
          if (other.type === "Paper") paperIds.add(other.id);
          if (other.type === "Method") methodIds.add(other.id);
        });
      });
      Array.from(paperIds).forEach(function (paperId) {
        (model.adjacency.get(paperId) || []).forEach(function (entry) {
          if (entry.edge.type === "USES_METHOD" && model.nodes.get(entry.other)) methodIds.add(entry.other);
        });
      });
    }
    var papers = Array.from(paperIds).filter(function (id) {
      var node = model.nodes.get(id);
      if (!node) return false;
      var year = node.props.year;
      if (yearMin !== null && yearMin !== undefined && (year || 0) < yearMin) return false;
      if (yearMax !== null && yearMax !== undefined && (year || 9999) > yearMax) return false;
      if (field && node.props.field !== field) return false;
      return true;
    });
    return {
      papers: papers, topics: matched.map(function (node) { return node.id; }),
      methods: Array.from(methodIds),
      resolution: { query: topic, matched_topics: matched.map(function (node) { return node.name; }),
                    strategy: topic ? "browser-topic-match" : "whole-corpus" },
    };
  }

  function explorerOverview(query) {
    var q = Object.assign({ topic: null, year_min: null, year_max: null, field: null }, query || {});
    var model = graph();
    var scope = browserScope(q.topic, q.year_min, q.year_max, q.field);
    var papers = scope.papers.map(function (id) { return model.nodes.get(id); }).filter(Boolean);

    var authors = new Set();
    var datasets = new Set();
    var communities = new Set();
    papers.forEach(function (paper) {
      (model.adjacency.get(paper.id) || []).forEach(function (entry) {
        if (entry.edge.type === "AUTHORED" && entry.direction === "in") authors.add(entry.other);
        if (entry.edge.type === "USES_DATASET" && entry.direction === "out") datasets.add(entry.other);
      });
      if (paper.community >= 0) communities.add(paper.community);
    });

    var years = {};
    papers.forEach(function (paper) {
      var year = paper.props.year;
      if (year) years[year] = (years[year] || 0) + 1;
    });
    var ordered = {};
    Object.keys(years).map(Number).sort(function (a, b) { return a - b; })
      .forEach(function (year) { ordered[year] = years[year]; });

    var conflictsPayload = state.datasets["conflicts.json"] || {};
    var predictionsPayload = state.datasets["predictions.json"] || {};
    var status = (state.datasets["health.json"] || {}).status;

    return {
      scope: {
        topic: q.topic, year_min: q.year_min, year_max: q.year_max, field: q.field,
        resolution: scope.resolution,
      },
      counts: {
        papers: papers.length, authors: authors.size, topics: scope.topics.length,
        methods: scope.methods.length, datasets: datasets.size, institutions: 0,
        communities: communities.size,
        claims: nodeLabelList("Claim").length,
      },
      years: ordered,
      recency: recencyOf(ordered),
      top_papers: topBy("pagerank", "Paper", 8),
      top_topics: topBy("pagerank", "Topic", 10),
      top_methods: topBy("degree", "Method", 10),
      top_bridge_papers: topBy("betweenness", "Paper", 8),
      communities: (model.communities || []).filter(function (profile) {
        return !communities.size || communities.has(profile.community_index);
      }).slice(0, 10),
      conflicts: (conflictsPayload.conflicts || []).slice(0, 6),
      predicted_links: (predictionsPayload.links || []).slice(0, 8),
      dataset_labels: {
        institutions: "No affiliation data in this snapshot — affiliations are seeded by ingestion when they exist.",
        papers: "Papers are the curated demo corpus records; every row links to its arXiv listing.",
      },
      status: {
        backend: "static-snapshot", requested_backend: "static-snapshot", degraded: false,
        reason: "Published snapshot: analytics recomputed in the browser from data/graph.json.",
        warnings: [], engine: "browser-snapshot", revision: 1, uptime_seconds: 0,
      },
      static_snapshot: { note: "Explorer metrics recomputed in the browser over the shipped graph " +
        "(the demo scopes are served verbatim from the build).", recorded_scope: null },
    };
  }

  /* --------------------------------------------------------------- timeline */

  function timeline(topic) {
    var model = graph();
    var scoped = null;
    if (topic) {
      var scope = browserScope(topic, null, null, null);
      scoped = new Set(scope.papers);
    }
    var years = {};
    var topicYears = {};
    model.edges.forEach(function (edge) {
      if (edge.type !== "STUDIES") return;
      var paper = model.nodes.get(edge.src);
      if (!paper || paper.type !== "Paper") return;
      if (scoped && !scoped.has(paper.id)) return;
      var year = paper.props.year;
      if (!year) return;
      years[year] = (years[year] || 0) + 1;
      var topicNode = model.nodes.get(edge.dst);
      if (!topicNode) return;
      if (!topicYears[topicNode.name]) topicYears[topicNode.name] = {};
      topicYears[topicNode.name][year] = (topicYears[topicNode.name][year] || 0) + 1;
    });
    var ordered = {};
    Object.keys(years).map(Number).sort(function (a, b) { return a - b; })
      .forEach(function (year) { ordered[year] = years[year]; });
    var top = Object.keys(topicYears).sort(function (a, b) {
      var sum = function (entry) { return Object.keys(entry).reduce(function (t, y) { return t + entry[y]; }, 0); };
      return sum(topicYears[b]) - sum(topicYears[a]);
    }).slice(0, 12);
    var series = {};
    top.forEach(function (name) { series[name] = topicYears[name]; });
    return { topic: topic || null, papers_per_year: ordered, topics_over_time: series };
  }

  /* ------------------------------------------------------------------ gaps */

  function gapScopeFiles(payload) {
    var files = Object.keys(payload || {}).filter(function (name) {
      return name.indexOf("gaps--") === 0 && name.indexOf("gaps-evidence--") !== 0;
    });
    return files.map(function (name) { return name.replace("gaps--", "").replace(".json", ""); });
  }

  function gapsNotPrecomputed(body) {
    var index = state.index || {};
    var topics = (index.precomputed && index.precomputed.topics) || [];
    return {
      topic: body.topic || null, engine: "nexus-gap-engine (recorded at build time)",
      safety_notice: (state.datasets["dashboard.json"] || {}).safety_notice ||
        "AI-generated hypotheses over a curated demo corpus. Candidates are research directions, " +
        "never claims that a topic is unstudied — validate independently before acting.",
      score_label: "NEXUS Opportunity Score",
      score_formula: {},
      methodology: ["The published site replays the gap engine's build-time output for the demo scopes."],
      limitations: ["This snapshot only contains the scopes computed at build time."],
      scope: { topic: body.topic || null, papers: 0, resolution: { strategy: "not-precomputed" } },
      resolution: { query: body.topic || null, matched_topics: [], strategy: "not-precomputed" },
      opportunities: [], filtered_pairs: [], clusters: [],
      score_distribution: { candidates_considered: 0, unique_candidates: 0 },
      ranking: { note: "Static snapshot: no pairs were scored in the browser." },
      reason: "This scope was not precomputed. The published build scores these topics: " +
              topics.join(", ") + ". Run the app locally (python run.py) " +
              "or via docker compose to score any other scope live.",
      static_snapshot: { mode: "not-precomputed", topics: topics },
    };
  }

  /* ----------------------------------------------------------------- agent */

  function contentTokens(text) {
    var stop = new Set(("a an and are as at be by do does for from how i in into is it its me my of on or " +
      "should that the their them then there these this to us was were what when where which who why will " +
      "with you your we our").split(" "));
    return String(text || "").toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/\s+/)
      .filter(function (token) { return token.length > 2 && !stop.has(token); });
  }

  function jaccard(a, b) {
    var left = new Set(a), right = new Set(b);
    if (!left.size || !right.size) return 0;
    var shared = 0;
    left.forEach(function (token) { if (right.has(token)) shared += 1; });
    return shared / (left.size + right.size - shared);
  }

  function answeredQuestions(index) {
    return (index.precomputed && index.precomputed.questions) || [];
  }

  function matchQuestion(question, index) {
    var questions = answeredQuestions(index);
    var needle = String(question || "").trim().toLowerCase();
    for (var i = 0; i < questions.length; i += 1) {
      if (questions[i].trim().toLowerCase() === needle) return { question: questions[i], score: 1 };
    }
    var tokens = contentTokens(question);
    var best = null;
    questions.forEach(function (candidate) {
      var score = jaccard(tokens, contentTokens(candidate));
      if (!best || score > best.score) best = { question: candidate, score: score };
    });
    return best && best.score >= 0.5 ? best : null;
  }

  /* A handful of agent answers are graph computations, not text generation, so they can be
     recomputed here. They mirror the server's offline templates: the wording is the
     server's, the numbers come from the shipped snapshot. */
  async function offlineAnswer(question) {
    var model = graph();
    var safety = "This answer was computed from the shipped graph snapshot in your browser; " +
      "every number is a graph metric over the curated demo corpus, not a literature claim.";
    var tokens = contentTokens(question);
    var topic = null;
    nodeLabelList("Topic").forEach(function (node) {
      if (topic) return;
      var name = node.name.toLowerCase();
      if (tokens.indexOf(name) !== -1 || (name.length > 5 && question.toLowerCase().indexOf(name) !== -1)) topic = node;
    });

    var intent = "overview";
    if (/contradict|conflict|inconsist/.test(question.toLowerCase())) intent = "conflicts";
    else if (/gap|opportunit|unexplored/.test(question.toLowerCase())) intent = "gaps";
    else if (/central|influential|important|read first/.test(question.toLowerCase())) intent = "centrality";
    else if (/communit|cluster/.test(question.toLowerCase())) intent = "communities";
    else if (/predict|hypothes/.test(question.toLowerCase())) intent = "predictions";

    if (intent === "conflicts") {
      var rows = conflicts().slice(0, 5);
      return buildAnswer(question, intent,
        rows.length
          ? "The corpus contains " + conflicts().length + " claim pairs the resolver flags as potential contradictions. " +
            "They are AI-detected tensions for a human to read, not established contradictions:\n\n" +
            rows.map(function (row) { return "- “" + String(row.text_a || "").slice(0, 120) + "…” vs “" +
              String(row.text_b || "").slice(0, 120) + "…” (score " + row.score + ")"; }).join("\n")
          : "No contradictory claim pairs were recorded in this snapshot.",
        ["find_conflicting_claims"], safety);
    }
    if (intent === "gaps") {
      var key = topic ? slug(topic.name) : "default";
      var recorded = await load("gaps--" + key + ".json");
      if (recorded && recorded.opportunities && recorded.opportunities.length) {
        return buildAnswer(question, intent,
          "The gap engine scored " + (recorded.score_distribution && recorded.score_distribution.candidates_considered) +
          " candidate pairs for “" + (recorded.topic || "the whole corpus") + "”. Top candidates (" +
          recorded.score_label + "):\n\n" +
          recorded.opportunities.slice(0, 3).map(function (opp) {
            return "- " + opp.title + " — " + opp.opportunity_score + "/100 (rank " + opp.rank + ")";
          }).join("\n") + "\n\n" + (recorded.safety_notice || ""),
          ["find_research_gaps"], safety);
      }
      return buildAnswer(question, intent,
        "This snapshot precomputes gap scores for the demo topics; “" + (topic ? topic.name : "that scope") +
        "” is not one of them. Running NEXUS locally scores any scope live.", ["find_research_gaps"], safety);
    }
    if (intent === "communities") {
      var profiles = (model.communities || []).slice(0, 5);
      return buildAnswer(question, intent,
        "Louvain detected " + (model.communities || []).length + " communities over the corpus. The largest:\n\n" +
        profiles.map(function (profile) {
          return "- " + profile.name + " — " + profile.paper_count + " papers, " + profile.topic_count + " topics";
        }).join("\n"),
        ["detect_communities"], safety);
    }
    if (intent === "predictions") {
      var links = predictedLinks().slice(0, 5);
      return buildAnswer(question, intent,
        links.length
          ? "Link prediction proposes " + predictedLinks().length + " connections that are not in the graph. " +
            "These are hypotheses with a score, never facts:\n\n" +
            links.map(function (link) {
              return "- " + (link.label || (link.source + " → " + link.target)) + " (Adamic-Adar " +
                (link.adamic_adar || 0) + ")";
            }).join("\n")
          : "No predicted links in this snapshot.", ["find_potential_connections"], safety);
    }
    var top = topBy("pagerank", "Paper", 5);
    return buildAnswer(question, intent,
      "By PageRank within this corpus, the most central papers are:\n\n" +
      top.map(function (paper, index) {
        return (index + 1) + ". " + paper.label + " (" + (paper.year || "n/a") + ") — PageRank " + paper.value;
      }).join("\n") + "\n\nBetweenness (bridging) is a different ranking — " +
      topBy("betweenness", "Paper", 3).map(function (paper) { return paper.label; }).join(", ") + ".",
      ["rank_papers"], safety);
  }

  function buildAnswer(question, intent, text, tools, safety) {
    var model = graph();
    var top = topBy("pagerank", "Paper", 3);
    return {
      question: question, intent: intent, answer: text,
      answer_engine: "browser-snapshot", used_llm: false,
      confidence: "medium", confidence_basis: "graph metrics over the shipped snapshot",
      evidence: top.map(function (paper) {
        return { id: paper.id, label: paper.label, year: paper.year, url: paper.url,
                 reason: "high PageRank in this corpus (graph metric, not a citation count)" };
      }),
      tool_calls: tools.map(function (tool) { return { tool: tool, arguments: {}, ok: true, summary: "completed (browser)" }; }),
      context: { nodes: model.nodes.size, edges: model.edges.length, source: "data/graph.json" },
      paths: [], predicted_links: [], conflicts: [],
      explainability: {
        claim: text.split("\n")[0],
        reasoning: [
          "The question was matched to a graph intent (" + intent + ") by keywords.",
          "The answer is computed from the shipped graph snapshot in the browser.",
          "Every number is a graph metric over the curated corpus and can be traced in the views.",
        ],
        graph_evidence: top.map(function (paper) { return paper.id + " (" + paper.value + " PageRank)"; }),
        supporting_papers: top.map(function (paper) { return { id: paper.id, label: paper.label, year: paper.year, url: paper.url }; }),
        confidence_basis: "medium — browser-side graph computation, no language model involved",
        safety_notice: safety,
      },
      follow_ups: [
        "Which claims contradict each other about agent memory?",
        "What are the most important research gaps in AI agents?",
      ],
      static_snapshot: { mode: "computed-in-browser", note: safety },
    };
  }

  /* ------------------------------------------------------------------ plan */

  /* the API's own note for a plan payload (served verbatim by both engines) */
  var PLANNER_NOTE = "Cypher here is generated, not executed: labels and relationship types come " +
    "from the whitelist and every value is a bind parameter.";

  var RE_TOPIC = /topic\s*:\s*"([^"]+)"/i;
  var RE_AUTHOR = /author\s*:\s*"([^"]+)"/i;
  var RE_YEAR_MIN = /year\s*>=\s*(\d{4})/i;
  var RE_YEAR_MAX = /year\s*<=\s*(\d{4})/i;
  var RE_DEPTH = /depth\s*<=?\s*(\d{1,2})/i;
  var RE_LIMIT = /limit\s+(\d{1,4})/i;
  var RE_TYPE = /type\s+in\s*\(([^)]*)\)/i;
  var RE_REL = /rel\s+in\s*\(([^)]*)\)/i;

  function pick(raw, allowed, kind) {
    var value = String(raw || "").trim();
    var match = allowed.filter(function (candidate) { return candidate.toLowerCase() === value.toLowerCase(); })[0];
    if (!match) {
      throw { dsl: true, error: "Unknown " + kind + " '" + value + "'. Allowed: " + allowed.join(", ") };
    }
    return match;
  }

  function items(raw) {
    return String(raw || "").split(",").map(function (piece) { return piece.trim(); }).filter(Boolean);
  }

  function parseDsl(dsl) {
    var text = String(dsl || "");
    var filters = [];
    var labels = [];
    var relationships = [];
    var match;

    var topicSeed = null;
    match = RE_TOPIC.exec(text);
    if (match) { topicSeed = match[1].trim(); text = text.replace(match[0], " "); }

    match = RE_AUTHOR.exec(text);
    if (match) {
      filters.push({ kind: "TextContains", field: "name", value: match[1].trim() });
      text = text.replace(match[0], " ");
    }
    match = RE_YEAR_MIN.exec(text);
    if (match) {
      filters.push({ kind: "IntAtLeast", field: "year", value: Number(match[1]) });
      text = text.replace(match[0], " ");
    }
    match = RE_YEAR_MAX.exec(text);
    if (match) {
      filters.push({ kind: "IntAtMost", field: "year", value: Number(match[1]) });
      text = text.replace(match[0], " ");
    }
    var depth = DEFAULT_DEPTH;
    match = RE_DEPTH.exec(text);
    if (match) { depth = Math.min(Math.max(Number(match[1]), 1), 3); text = text.replace(match[0], " "); }
    var limit = DEFAULT_LIMIT;
    match = RE_LIMIT.exec(text);
    if (match) { limit = Math.min(Math.max(Number(match[1]), 1), 500); text = text.replace(match[0], " "); }
    match = RE_TYPE.exec(text);
    if (match) {
      labels = items(match[1]).map(function (piece) { return pick(piece, NODE_LABELS, "node type"); });
      text = text.replace(match[0], " ");
    }
    match = RE_REL.exec(text);
    if (match) {
      relationships = items(match[1]).map(function (piece) { return pick(piece, REL_TYPES, "relationship"); });
      text = text.replace(match[0], " ");
    }
    /* whatever is left over is the free-text seed, verbatim — the Python planner does not
       interpret phrases like "text contains", it hands them to the full-text index. */
    var free = text.replace(/\s+/g, " ").trim().replace(/^"|"$/g, "");
    var seed = [topicSeed, free || null].filter(Boolean).join(" ");
    return { text: seed || null, labels: labels, relationships: relationships,
             filters: filters, depth: depth, limit: limit };
  }

  function estimateCost(query) {
    var seeds = Math.min(query.limit, 200);
    var hops = Math.max(query.depth - 1, 0);
    var raw = seeds * (1 + hops * FANOUT);
    return {
      depth: query.depth, fanout: FANOUT, seeds: seeds,
      estimated_nodes_visited: Math.min(Math.round(raw), COST_BUDGET),
      budget: COST_BUDGET,
      strategy: query.text ? "fulltext -> expand" : "label scan -> rank",
      safe: raw <= COST_BUDGET,
    };
  }

  function planner(dsl) {
    var query;
    try {
      query = parseDsl(dsl);
    } catch (error) {
      if (error && error.dsl) return { ok: false, engine: "nexus-js-planner", error: error.error };
      throw error;
    }
    if (!query.text && !query.filters.length && !query.labels.length && !query.relationships.length) {
      return { ok: false, engine: "nexus-js-planner",
               error: 'empty query: give free text, a topic:"…" phrase, or a structured clause' };
    }

    var params = {};
    var why = [];
    var labels = query.labels;
    var labelClause = !labels.length ? "n"
      : (labels.length === 1 ? "n:" + labels[0] : "n:" + labels.join("|"));
    if (labels.length) why.push("Restricted scan to labels: " + labels.join(", ") + ".");

    var predicates = [];
    query.filters.forEach(function (filt) {
      var field = filt.field, kind = filt.kind, value = filt.value;
      if (kind === "TextEquals") { predicates.push("toLower(n." + field + ") = toLower($" + field + "Exact)"); params[field + "Exact"] = value; }
      else if (kind === "TextContains") { predicates.push("toLower(n." + field + ") CONTAINS toLower($" + field + ")"); params[field] = value; }
      else if (kind === "IntAtLeast") { predicates.push("n." + field + " >= $" + field + "Min"); params[field + "Min"] = value; }
      else if (kind === "IntAtMost") { predicates.push("n." + field + " <= $" + field + "Max"); params[field + "Max"] = value; }
      else if (kind === "InList") { predicates.push("n." + field + " IN $" + field + "List"); params[field + "List"] = value; }
    });
    if (query.filters.length) {
      why.push("Applied structured filters: " + query.filters.map(function (filt) {
        return filt.kind + "(field=" + filt.field + ", value=" + filt.value + ")";
      }).join(", "));
    }
    if (query.relationships.length) {
      predicates.push("(n)-[" + query.relationships.join("|") + "]-()");
      why.push("Relationship filter: edge type must be one of " + query.relationships.join(", ") + ".");
    }
    var where = predicates.join(" AND ");
    var cypher;
    if (query.text) {
      params.q = query.text;
      params.limit = query.limit;
      why.push("Free text '" + query.text + "' handled by the full-text index (tokenised, relevance-ranked).");
      var header = ["CALL db.index.fulltext.queryNodes('nexus-fulltext', $q) YIELD node AS n, score",
                    "WHERE score > 0.0"];
      if (labels.length) header[1] += " AND (" + labels.map(function (name) { return "'" + name + "' IN labels(n)"; }).join(" OR ") + ")";
      var lines = header.slice();
      if (where) {
        lines.push("WITH n, score WHERE " + where);
        why.push("Post-filter applied on indexed properties (bound parameters only).");
      }
      lines.push("RETURN n, score ORDER BY score DESC LIMIT $limit");
      cypher = lines.join("\n");
    } else {
      params.limit = query.limit;
      why.push("Limit " + query.limit + " keeps the first paint under a second (progressive expansion).");
      var parts = ["MATCH (" + labelClause + ")"];
      if (where) parts.push("WHERE " + where);
      parts = parts.concat(["RETURN n", "ORDER BY coalesce(n.pagerank, 0.0) DESC", "LIMIT $limit"]);
      cypher = parts.join("\n");
    }

    return {
      ok: true, engine: "nexus-js-planner",
      query: { text: query.text, labels: labels, relationships: query.relationships,
               depth: query.depth, limit: query.limit },
      cypher: cypher, params: params, explanation: why, cost: estimateCost(query),
    };
  }

  /* ---------------------------------------------------------------- router */

  function queryPairs(search) {
    var params = new URLSearchParams(search || "");
    var out = {};
    params.forEach(function (value, key) { out[key] = value; });
    return out;
  }

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function textOrNull(value) {
    if (value === null || value === undefined) return null;
    var trimmed = String(value).trim();
    return trimmed ? trimmed : null;
  }

  async function api(path, options) {
    var opts = options || {};
    var withBody = opts.body !== undefined && opts.body !== null;
    var body = withBody && typeof opts.body === "string" ? safeJson(opts.body) : (opts.body || {});
    var parts = String(path).split("?");
    var route = parts[0];
    var params = queryPairs(parts[1]);

    await ready();
    var index = state.index || {};

    /* ---------------------------------------------------------- recorded */
    if (route === "/api/health") {
      var health = Object.assign({}, snapshotOf("health.json"));
      health.store = Object.assign({}, health.store, { engine: "static-snapshot" });
      health.static_snapshot = {
        mode: "recorded",
        note: "The engines below ran at build time (" + ((state.index || {}).built || "unknown") +
              "); this page serves their snapshot and recomputes the interactive views in your browser.",
      };
      return health;
    }
    if (route === "/api/dashboard") {
      return snapshotOf("dashboard.json");
    }
    if (route === "/api/services") {
      var services = Object.assign({}, snapshotOf("services.json"));
      services.note = "Published snapshot: the sidecar sidecars below are stopped in this build. " +
        "Their engines ran at build time and their output is what this site serves. " + (services.note || "");
      services.static_snapshot = true;
      return services;
    }
    if (route === "/api/algorithms") return snapshotOf("algorithms.json");
    if (route === "/api/safety") return snapshotOf("safety.json");
    if (route === "/api/tools") return snapshotOf("tools.json");
    if (route === "/api/mcp") return snapshotOf("mcp.json");
    if (route === "/api/opportunity-score") return snapshotOf("opportunity-score.json");
    if (route === "/api/communities") return snapshotOf("communities.json");
    if (route === "/api/conflicts") return snapshotOf("conflicts.json");
    if (route === "/api/predictions") return snapshotOf("predictions.json");
    if (route === "/api/centrality") return snapshotOf("centrality.json");
    if (route === "/api/export/cypher") {
      var cypher = await loadText("export-cypher.txt");
      if (!cypher) throw notPrecomputed("the Cypher export");
      return cypher;
    }

    /* ------------------------------------------------------------- graph */
    if (route === "/api/graph") {
      var isPost = opts.method === "POST";
      var graphRequest = isPost ? resolveGraphRequest(body) : {
        query: textOrNull(params.topic) || textOrNull(params.query),
        depth: numberOrNull(params.depth) === null ? 1 : numberOrNull(params.depth),
        year_min: numberOrNull(params.year_min), year_max: numberOrNull(params.year_max),
        max_nodes: numberOrNull(params.max_nodes) || 260,
      };
      var payload = subgraph(graphRequest);
      payload.status = statusPayload();
      payload.legend = legend();
      if (isPost) payload.request = echoGraphRequest(graphRequest);
      return payload;
    }
    if (route === "/api/graph/expand") {
      var expanded = expand(body.node_ids || [], body.rel_types || null, body.limit);
      expanded.status = statusPayload();
      return expanded;
    }
    if (route.indexOf("/api/graph/neighbours/") === 0) {
      var nodeId = decodeURIComponent(route.slice("/api/graph/neighbours/".length));
      var model = graph();
      var center = model.nodes.get(nodeId);
      if (!center) throw fail(404, "node " + nodeId + " not found");
      var all = neighboursRawExtended(nodeId);
      return {
        center: nodeJsonPlain(center),
        neighbours: all.slice(0, Math.max(1, Math.min(Number(params.limit || 60), 300))),
        count: all.length,
      };
    }
    if (route === "/api/graph/path") {
      return shortestPath(params.source, params.target, numberOrNull(params.max_hops));
    }

    /* ------------------------------------------------------------ papers */
    if (route === "/api/papers") {
      var sortKey = params.sort || "pagerank";
      var papers = listNodes({
        label: "Paper", sort: sortKey, order: params.order || "desc",
        year_min: numberOrNull(params.year_min), year_max: numberOrNull(params.year_max),
        field: textOrNull(params.field), limit: Number(params.limit || 40),
        offset: Number(params.offset || 0),
      });
      if (params.topic) {
        var scope = browserScope(params.topic, numberOrNull(params.year_min),
                                 numberOrNull(params.year_max), null);
        var keep = new Set(scope.papers);
        papers.items = papers.items.filter(function (item) { return keep.has(item.id); });
        papers.total = papers.items.length;
        papers.filtered_by_topic = params.topic;
      }
      if (params.q) {
        var paperHits = new Set(search(params.q, ["Paper"], 500).map(function (hit) { return hit.id; }));
        papers.items = papers.items.filter(function (item) { return paperHits.has(item.id); });
        papers.filtered_by_query = params.q;
        papers.total = papers.items.length;   // `total` describes the rows the caller gets
      }
      papers.sort = sortKey;
      return papers;
    }
    if (route.indexOf("/api/papers/") === 0) {
      var paperId = decodeURIComponent(route.slice("/api/papers/".length));
      if (paperId.indexOf("paper:") !== 0) paperId = "paper:" + paperId;
      return paperDetail(paperId);
    }

    /* ------------------------------------------------------------- nodes */
    if (route === "/api/nodes") {
      var label = textOrNull(params.label);
      var nodePage = listNodes({
        label: label, sort: params.sort || "pagerank", order: params.order || "desc",
        year_min: numberOrNull(params.year_min), year_max: numberOrNull(params.year_max),
        field: textOrNull(params.field), limit: Number(params.limit || 50),
        offset: Number(params.offset || 0),
      });
      if (params.q) {
        var nodeHits = new Set(search(params.q, label ? [label] : null, 500).map(function (hit) { return hit.id; }));
        nodePage.items = nodePage.items.filter(function (item) { return nodeHits.has(item.id); });
        nodePage.total = nodePage.items.length;
      }
      return nodePage;
    }
    if (route.indexOf("/api/nodes/") === 0) {
      return nodeDetail(decodeURIComponent(route.slice("/api/nodes/".length)));
    }
    if (route === "/api/search") {
      var results = search(params.q, params.labels ? params.labels.split(",") : null, Number(params.limit || 25));
      return { query: params.q || "", count: results.length, results: results };
    }

    /* ---------------------------------------------------------- timeline */
    if (route === "/api/timeline") {
      var topicName = textOrNull(params.topic);
      if (topicName) {
        var recordedTimeline = await load("timeline--" + slug(topicName) + ".json");
        if (recordedTimeline) { inject("timeline--" + slug(topicName) + ".json", recordedTimeline, route); return recordedTimeline; }
      } else {
        var defaultTimeline = await load("timeline.json");
        if (defaultTimeline) { inject("timeline.json", defaultTimeline, route); return defaultTimeline; }
      }
      var computed = timeline(topicName);
      computed.static_snapshot = { mode: "computed-in-browser",
        note: "Publishing timeline recomputed in the browser from the shipped STUDIES edges." };
      return computed;
    }

    /* ---------------------------------------------------------- explorer */
    if (route === "/api/explorer") {
      var explorerScope = {
        topic: textOrNull(params.topic), year_min: numberOrNull(params.year_min),
        year_max: numberOrNull(params.year_max), field: textOrNull(params.field),
      };
      var unfiltered = !explorerScope.year_min && !explorerScope.year_max && !explorerScope.field;
      var name = explorerScope.topic ? slug(explorerScope.topic) : "default";
      if (unfiltered) {
        var recordedExplorer = await load("explorer--" + name + ".json");
        if (recordedExplorer) {
          inject("explorer--" + name + ".json", recordedExplorer, route);
          return recordedExplorer;
        }
      }
      return explorerOverview(explorerScope);
    }

    /* -------------------------------------------------------------- gaps */
    if (route === "/api/gaps") {
      var gapBody = body || {};
      var key = gapBody.topic ? slug(gapBody.topic) : "default";
      var gapFilters = Boolean(gapBody.year_min || gapBody.year_max);
      var recordedGaps = gapFilters ? null : await load("gaps--" + key + ".json");
      if (recordedGaps) {
        inject("gaps--" + key + ".json", recordedGaps, route);
        var served = recordedGaps;
        if (gapBody.top_k && gapBody.top_k < (served.opportunities || []).length) {
          served = Object.assign({}, served, { opportunities: served.opportunities.slice(0, gapBody.top_k) });
        }
        served.static_snapshot = {
          mode: "recorded",
          note: "Scored by the gap engine at build time (" + ((state.index || {}).built || "") + "). " +
                "The published site serves that result verbatim" +
                (gapBody.top_k && gapBody.top_k < (served.opportunities || []).length
                  ? " (trimmed to the " + gapBody.top_k + " candidates you asked for)." : "."),
        };
        return served;
      }
      var reason = gapFilters
        ? "Year-filtered gap scopes are computed live by the gap engine; the published snapshot " +
          "contains the unfiltered demo scopes."
        : null;
      var missing = gapsNotPrecomputed(gapBody);
      if (reason) missing.reason = reason;
      return missing;
    }
    if (route.indexOf("/api/gaps/") === 0) {
      throw notPrecomputed("the evidence bundle for that candidate",
        "Every opportunity carries its evidence inline; the standalone bundle exists only in the live API.");
    }

    /* ------------------------------------------------------------ report */
    if (route === "/api/report") {
      var reportTopic = body && body.topic ? slug(body.topic) : "default";
      var reportFilters = Boolean(body && (body.year_min || body.year_max));
      var report = reportFilters ? null : await load("report--" + reportTopic + ".json");
      if (!report) {
        throw notPrecomputed("that opportunity report",
          "The published build renders reports for these scopes: " +
          topicsOf("report--") + ". Run python run.py locally to render any other scope.");
      }
      inject("report--" + reportTopic + ".json", report, route);
      if (body && body.top_k && body.top_k < (report.gaps || []).length) {
        report = Object.assign({}, report, { gaps: report.gaps.slice(0, body.top_k) });
      }
      return report;
    }
    if (route === "/api/report/markdown") {
      var markdownTopic = body && body.topic ? slug(body.topic) : "default";
      var markdown = reportFilters ? null : await loadText("report--" + markdownTopic + ".md");
      if (!markdown) throw notPrecomputed("that opportunity report (markdown)",
        "The published build renders reports for the unfiltered demo scopes; year-filtered reports " +
        "are computed live (python run.py).");
      return markdown;
    }

    /* ------------------------------------------------------------- agent */
    if (route === "/api/agent") {
      return agentAnswer(body.question, index);
    }
    if (route === "/api/agent/stream") {
      return agentStream(body.question, index);
    }

    /* -------------------------------------------------------------- plan */
    if (route === "/api/plan") {
      var dsl = (opts.method === "POST" ? (body && body.query) : params.q) || "";
      var plan = planner(dsl);
      if (!plan.ok) throw fail(422, plan.error);
      plan.dsl = dsl;
      plan.source = "browser";
      plan.note = PLANNER_NOTE;
      plan.static_snapshot = {
        mode: "computed-in-browser",
        note: "Planned in your browser by the JS port of the Kotlin/Python planner, over the same " +
              "whitelists. The Cypher is generated, not executed: nothing here touches a database.",
      };
      return plan;
    }

    throw notPrecomputed("that endpoint (" + route + ")");
  }

  var GRAPH_REQUEST_DEFAULTS = { depth: 1, include_predicted: true, max_nodes: 260, max_edges: 800 };
  var GRAPH_REQUEST_OPTIONAL = ["seeds", "query", "node_types", "rel_types", "year_min", "year_max", "focus"];

  function resolveGraphRequest(raw) {
    var source = raw || {};
    var req = Object.assign({}, GRAPH_REQUEST_DEFAULTS);
    GRAPH_REQUEST_OPTIONAL.forEach(function (key) {
      if (source[key] !== null && source[key] !== undefined) req[key] = source[key];
    });
    ["depth", "include_predicted", "max_nodes", "max_edges"].forEach(function (key) {
      if (source[key] !== null && source[key] !== undefined) req[key] = source[key];
    });
    return req;
  }

  /* the server echoes the request model with exclude_none: only the fields that were set. */
  function echoGraphRequest(req) {
    var out = { depth: req.depth, include_predicted: req.include_predicted,
                max_nodes: req.max_nodes, max_edges: req.max_edges };
    GRAPH_REQUEST_OPTIONAL.forEach(function (key) {
      if (req[key] !== null && req[key] !== undefined) out[key] = req[key];
    });
    return out;
  }

  function safeJson(text) {
    try { return JSON.parse(text); } catch (error) { return {}; }
  }

  function topicsOf(prefix) {
    var files = Object.keys((state.index || {}).files || {});
    return files.filter(function (name) { return name.indexOf(prefix) === 0; })
      .map(function (name) { return name.replace(prefix, "").replace(".json", "").replace(".md", ""); })
      .join(", ") || "(none)";
  }

  /* Mark a recorded payload with where it came from, so the UI can say so. */
  function inject(name, payload, route) {
    if (!payload || typeof payload !== "object") return payload;
    var served = (state.served = state.served || {});
    served[route] = name;
    return payload;
  }

  function snapshotOf(name) {
    var payload = state.datasets[name];
    if (!payload) throw fail(500, "static snapshot is incomplete: data/" + name + " is missing");
    return payload;
  }

  function statusPayload() {
    return {
      backend: "static-snapshot", requested_backend: "static-snapshot", degraded: false,
      reason: "Published snapshot: the graph is recomputed in your browser from data/graph.json; " +
              "gap scoring, reports and agent answers were recorded at build time.",
      warnings: [], engine: "browser-snapshot", revision: 1, uptime_seconds: 0,
    };
  }

  function legend() {
    var recorded = state.datasets["graph.json"] || {};
    if (recorded.legend) return recorded.legend;
    /* Fallback only: the shipped graph.json always carries the legend the API emitted. */
    return {
      node_colors: { Paper: "#4cc9f0", Author: "#b892ff", Topic: "#f4a261", Method: "#2ec4b6",
                     Dataset: "#8ecae6", Institution: "#94a3b8", Claim: "#ff6b6b",
                     Community: "#f9c74f", Metric: "#a3e635" },
      rel_types: GRAPH_REL_TYPES,
      predicted_rels: ["BELONGS_TO", "MEASURED_BY", "RELATED_TO", "STUDIES", "USES_DATASET", "USES_METHOD"],
    };
  }

  /* store.neighbours(): one entry per incident edge, in store order. */
  function neighboursRawExtended(nodeId) {
    var model = graph();
    return (model.adjacency.get(nodeId) || []).map(function (entry) {
      var other = model.nodes.get(entry.other);
      if (!other) return null;
      return {
        id: other.id, label: other.name, type: other.type,
        rel: entry.edge.type, properties: entry.edge.props,
      };
    }).filter(Boolean);
  }

  /* ------------------------------------------------------------ agent glue */

  async function agentAnswer(question, index) {
    if (!question) throw fail(422, "question is required");
    var match = matchQuestion(question, index);
    if (match && match.score === 1) {
      var recorded = await load("agent--" + slug(match.question) + ".json");
      if (recorded) return Object.assign({}, recorded, {
        static_snapshot: { mode: "recorded", question: match.question,
          note: "This answer, with its reasoning trail, was produced by the research agent at build " +
                "time and is served verbatim." },
      });
    }
    if (match) {
      var close = await load("agent--" + slug(match.question) + ".json");
      if (close) {
        return Object.assign({}, close, {
          question: question,
          static_snapshot: { mode: "recorded-nearest", question: match.question, similarity: round(match.score, 2),
            note: "No build-time answer for this exact wording. Serving the closest recorded question " +
                  "(“" + match.question + "”, token overlap " + round(match.score, 2) + ")." },
        });
      }
    }
    return offlineAnswer(question);
  }

  async function agentStream(question, index) {
    var match = matchQuestion(question, index);
    if (match) {
      var trace = await loadText("agent-stream--" + slug(match.question) + ".txt");
      if (trace) return trace;
    }
    var answer = await agentAnswer(question, index);
    var frame = function (stage, detail) {
      return "event: " + stage + "\ndata: " + JSON.stringify(Object.assign({ stage: stage }, detail)) + "\n\n";
    };
    return frame("intent", { detail: { intent: answer.intent, source: "browser-snapshot" } }) +
      frame("plan", { detail: { engine: "nexus-js-planner", tools: (answer.tool_calls || []).map(function (call) { return call.tool; }) } }) +
      (answer.tool_calls || []).map(function (call) { return frame("tool", { detail: call }); }).join("") +
      frame("retrieval", { detail: answer.context || {} }) +
      frame("synthesis", { detail: { engine: answer.answer_engine } }) +
      frame("done", { detail: { ok: true } }) +
      "event: answer\ndata: " + JSON.stringify(answer) + "\n\n";
  }

  /* -------------------------------------------------------------- lifecycle */

  async function ready() {
    if (state.graph) return true;
    var results = await Promise.all([load("graph.json"), load("index.json")]);
    require$_( "graph.json", results[0]);
    buildGraph(results[0]);
    state.index = results[1] || {};
    inject("index.json", state.index, "/api/health");
    await Promise.all([load("health.json"), load("dashboard.json"), load("conflicts.json"),
                       load("predictions.json"), load("communities.json"), load("services.json")]);
    return true;
  }

  function configure(options) {
    Object.assign(state, options || {});
    return state;
  }

  function status() {
    return {
      mode: "static", snapshot: state.snapshot, datasets: Object.keys(state.datasets),
      missing: state.missing, served: state.served || {},
      graph: state.graph ? { nodes: state.graph.nodes.size, edges: state.graph.edges.length } : null,
    };
  }

  /* --------------------------------------------------------------- browser */

  function install() {
    if (!root || !root.document) return;
    var nativeFetch = root.fetch ? root.fetch.bind(root) : null;
    state.fetcher = nativeFetch;
    root.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var options = init || (typeof input === "object" ? input : {});
      if (url.indexOf("/api/") !== 0) return nativeFetch ? nativeFetch(input, init) : Promise.reject(new Error("no fetch"));
      var method = (options.method || "GET").toUpperCase();
      var body = options.body;
      return api(url, { method: method, body: body }).then(function (payload) {
        var isText = typeof payload === "string";
        return new Response(isText ? payload : JSON.stringify(payload), {
          status: 200,
          headers: { "content-type": isText ? "text/plain; charset=utf-8" : "application/json" },
        });
      }).catch(function (error) {
        return new Response(JSON.stringify({ detail: error.detail || error.message || "static snapshot error" }),
          { status: error.status || 500, headers: { "content-type": "application/json" } });
      });
    };

    if ("serviceWorker" in navigator && location.protocol !== "file:") {
      root.addEventListener("load", function () {
        navigator.serviceWorker.register("sw.js").catch(function () { /* file:// or no SW support */ });
      });
    }

    var deferred = null;
    var button = document.getElementById("install-app");
    root.addEventListener("beforeinstallprompt", function (event) {
      event.preventDefault();
      deferred = event;
      if (button) button.hidden = false;
    });
    if (button) {
      button.addEventListener("click", function () {
        if (!deferred) {
          alert("Use your browser's menu → “Add to Home Screen” (iOS Safari) or “Install app” (desktop).");
          return;
        }
        deferred.prompt();
        deferred = null;
        button.hidden = true;
      });
      if (matchMedia("(display-mode: standalone)").matches) button.hidden = true;
    }
    var close = document.getElementById("static-banner-close");
    if (close) {
      close.addEventListener("click", function () {
        document.getElementById("static-banner").hidden = true;
      });
    }
    markStaticStatus();
  }

  function markStaticStatus() {
    var text = document.getElementById("status-text");
    if (text && /connecting|static/i.test(text.textContent || "")) {
      text.textContent = "static snapshot";
    }
    var dot = document.getElementById("status-dot");
    if (dot) dot.title = "Published snapshot: computed in your browser + build-time engine output";
  }

  var NEXUSStatic = {
    api: api, configure: configure, status: status, install: install, ready: ready,
    slug: slug, planner: planner,
  };
  NEXUSStatic.snapshot = function () { return state.snapshot; };
  NEXUSStatic.search = function (text, labels, limit) { return search(text, labels, limit); };
  NEXUSStatic.graph = function (request) { return subgraph(request); };
  NEXUSStatic.nodeDetail = function (id) { return nodeDetail(id); };
  NEXUSStatic.paperDetail = function (id) { return paperDetail(id); };
  NEXUSStatic.listNodes = function (query) { return listNodes(query); };
  NEXUSStatic.neighbours = function (id) { return neighboursRawExtended(id); };
  NEXUSStatic.path = function (source, target, hops) { return shortestPath(source, target, hops); };
  NEXUSStatic.timeline = function (topic) { return timeline(topic); };
  NEXUSStatic.explorer = function (query) { return explorerOverview(query); };
  NEXUSStatic.expand = function (ids, rels, limit) { return expand(ids, rels, limit); };

  root.NEXUSStatic = NEXUSStatic;
  if (typeof module !== "undefined" && module.exports) module.exports = NEXUSStatic;

  /* Browser default: the published page sets #nexus-snapshot and calls install(). */
  if (root && root.document) {
    var boot = root.document.getElementById("nexus-snapshot");
    if (boot) {
      try { state.snapshot = JSON.parse(boot.textContent || "{}"); } catch (error) { state.snapshot = {}; }
      install();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
