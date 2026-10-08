// NEXUS — CSR graph container + analytics kernels (C++17, zero dependencies).
//
// Why a native kernel?
//   The Research Gap Engine runs O(communities^2) pair analysis on top of
//   PageRank / Louvain / Brandes betweenness over the whole corpus. Doing that
//   in pure Python costs seconds per demo click; here it costs milliseconds, so
//   the 3D graph and the gap engine stay interactive during a live demo.
//
// The Python layer (backend/algorithms/kernel.py) talks to this over a
// line-delimited JSON protocol on stdin/stdout, so there is no FFI complexity
// and the same binary can be shipped to any machine with a C++17 compiler.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <queue>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

namespace nexus {

struct Edge {
  int u = 0;
  int v = 0;
  double w = 1.0;
};

// Compressed-sparse-row view of an *undirected weighted* graph plus the raw
// directed edge list (PageRank/Betweenness respect direction, Louvain does not).
class Graph {
 public:
  int n = 0;
  std::vector<std::string> names;
  std::unordered_map<std::string, int> index;
  std::vector<Edge> edges;                 // undirected (w = weight)
  std::vector<std::vector<std::pair<int, double>>> out;  // directed out-neighbours
  std::vector<std::vector<std::pair<int, double>>> adj;  // undirected neighbours
  std::vector<double> out_weight;

  void add_node(const std::string& name) {
    auto it = index.find(name);
    if (it != index.end()) return;
    index[name] = n;
    names.push_back(name);
    out.emplace_back();
    adj.emplace_back();
    out_weight.push_back(0.0);
    n++;
  }

  int id_of(const std::string& name) const {
    auto it = index.find(name);
    return it == index.end() ? -1 : it->second;
  }

  // Adds an edge to the graph.
  //   directed = false (default): undirected edge — appears in both out-neighbour
  //     lists, which is the right model for *co-occurrence* research graphs
  //     (paper→topic, paper→method, topic→topic similarity).
  //   directed = true: a single arc u -> v, which is the right model for
  //     citation graphs (Paper -[:CITES]-> Paper).
  // The undirected `adj` view always receives both directions so that community
  // detection and betweenness can treat the graph structurally either way.
  void add_edge(int u, int v, double w = 1.0, bool directed = false) {
    if (u < 0 || v < 0 || u >= n || v >= n || u == v) return;
    edges.push_back({u, v, w});
    out[u].push_back({v, w});
    out_weight[u] += w;
    adj[u].push_back({v, w});
    adj[v].push_back({u, w});
    if (!directed) {
      out[v].push_back({u, w});
      out_weight[v] += w;
    }
  }

  int m() const { return (int)edges.size(); }
};

// ---------------------------------------------------------------- PageRank ---
inline std::vector<double> pagerank(const Graph& g, double damping = 0.85, int iters = 60,
                                    double tol = 1e-9) {
  std::vector<double> rank(g.n, g.n > 0 ? 1.0 / g.n : 0.0);
  std::vector<double> next(g.n, 0.0);
  const double teleport = (1.0 - damping) / (g.n > 0 ? g.n : 1);
  for (int it = 0; it < iters; ++it) {
    double dangling = 0.0;
    for (int i = 0; i < g.n; ++i)
      if (g.out_weight[i] <= 0.0) dangling += rank[i];
    for (int i = 0; i < g.n; ++i) next[i] = teleport + damping * dangling / (g.n > 0 ? g.n : 1);
    for (int u = 0; u < g.n; ++u) {
      if (g.out_weight[u] <= 0.0) continue;
      const double share = damping * rank[u] / g.out_weight[u];
      for (const auto& e : g.out[u]) next[e.first] += share * e.second;
    }
    double delta = 0.0;
    for (int i = 0; i < g.n; ++i) delta += std::fabs(next[i] - rank[i]);
    rank.swap(next);
    if (delta < tol) break;
  }
  return rank;
}

// ------------------------------------------------------ Louvain communities ---
// Standard two-phase Louvain: greedy modularity-optimising local moves, then
// aggregation of communities into super-nodes; repeat until the partition stops
// changing. Returns a compacted community id per node (0..k-1).
//
// Self-loops are first-class here: an intra-community edge becomes a loop on the
// aggregated super-node, and a loop contributes 2w to its node degree but only w
// to internal (same-community) weight. Dropping loops between levels is the
// classic Louvain bug — it destroys cohesion and over-merges communities.
inline std::vector<int> louvain(const Graph& g, double resolution = 1.0, int max_levels = 20,
                                uint32_t seed = 42) {
  const int n = g.n;
  std::vector<int> result(n, 0);
  if (n == 0) return result;

  double m = 0.0;
  for (const auto& e : g.edges) m += e.w;
  if (m <= 0.0) return result;
  const double m2 = 2.0 * m;

  std::vector<Edge> level_edges = g.edges;
  int cur_n = n;
  std::vector<int> orig_to_cur(n);
  for (int i = 0; i < n; ++i) orig_to_cur[i] = i;

  std::mt19937 rng(seed);

  for (int level = 0; level < max_levels; ++level) {
    // ---- weighted adjacency of the current level graph ----
    std::vector<std::unordered_map<int, double>> adj(cur_n);
    for (const auto& e : level_edges) {
      if (e.u == e.v) {
        adj[e.u][e.u] += e.w;  // loop: counted once, doubles in the degree
      } else {
        adj[e.u][e.v] += e.w;
        adj[e.v][e.u] += e.w;
      }
    }
    std::vector<double> kdeg(cur_n, 0.0);
    for (int i = 0; i < cur_n; ++i) {
      for (const auto& kv : adj[i]) kdeg[i] += kv.second;
      auto self = adj[i].find(i);
      if (self != adj[i].end()) kdeg[i] += self->second;  // loop counts twice
    }

    // ---- phase 1: local moving ----
    std::vector<int> comm(cur_n);
    for (int i = 0; i < cur_n; ++i) comm[i] = i;
    std::vector<double> tot(cur_n);
    for (int i = 0; i < cur_n; ++i) tot[i] = kdeg[i];

    std::vector<int> order(cur_n);
    for (int i = 0; i < cur_n; ++i) order[i] = i;

    bool moved = true;
    int sweeps = 0;
    while (moved && sweeps < 20) {
      moved = false;
      ++sweeps;
      std::shuffle(order.begin(), order.end(), rng);
      for (int node : order) {
        const int from = comm[node];
        std::unordered_map<int, double> cand;
        for (const auto& kv : adj[node]) {
          if (kv.first == node) continue;  // loops stay in the node's own community
          cand[comm[kv.first]] += kv.second;
        }
        tot[from] -= kdeg[node];
        double best_gain = 0.0;  // gain of "stay put" is zero by construction
        int best = from;
        for (const auto& kv : cand) {
          const double gain = kv.second - resolution * tot[kv.first] * kdeg[node] / m2;
          if (gain > best_gain + 1e-12) {
            best_gain = gain;
            best = kv.first;
          }
        }
        tot[best] += kdeg[node];
        if (best != from) {
          comm[node] = best;
          moved = true;
        }
      }
    }

    // ---- aggregation ----
    std::unordered_map<int, int> remap;
    std::vector<int> compact(cur_n, 0);
    for (int i = 0; i < cur_n; ++i) {
      auto it = remap.find(comm[i]);
      if (it == remap.end()) {
        const int nid = (int)remap.size();
        remap[comm[i]] = nid;
        compact[i] = nid;
      } else {
        compact[i] = it->second;
      }
    }
    const int new_n = (int)remap.size();
    for (int i = 0; i < n; ++i) orig_to_cur[i] = compact[orig_to_cur[i]];
    if (new_n == cur_n) break;  // converged: nothing merged this level

    std::unordered_map<long long, double> agg;
    for (const auto& e : level_edges) {
      int a = compact[e.u], b = compact[e.v];
      if (a > b) std::swap(a, b);
      agg[(long long)a * 1000003LL + b] += e.w;
    }
    level_edges.clear();
    level_edges.reserve(agg.size());
    for (const auto& kv : agg)
      level_edges.push_back({(int)(kv.first / 1000003LL), (int)(kv.first % 1000003LL), kv.second});
    cur_n = new_n;
    if (cur_n <= 1) break;
  }

  std::unordered_map<int, int> remap;
  for (int i = 0; i < n; ++i) {
    auto it = remap.find(orig_to_cur[i]);
    if (it == remap.end()) {
      const int nid = (int)remap.size();
      remap[orig_to_cur[i]] = nid;
      result[i] = nid;
    } else {
      result[i] = it->second;
    }
  }
  return result;
}

// ------------------------------------------- Brandes betweenness centrality ---
inline std::vector<double> betweenness(const Graph& g, bool weighted = true, int samples = 0,
                                       uint32_t seed = 7) {
  const int n = g.n;
  std::vector<double> bc(n, 0.0);
  if (n == 0) return bc;

  std::vector<int> sources;
  if (samples > 0 && samples < n) {
    std::mt19937 rng(seed);
    std::vector<int> all(n);
    for (int i = 0; i < n; ++i) all[i] = i;
    std::shuffle(all.begin(), all.end(), rng);
    sources.assign(all.begin(), all.begin() + samples);
  } else {
    for (int i = 0; i < n; ++i) sources.push_back(i);
  }

  const double INF = std::numeric_limits<double>::infinity();
  std::vector<std::vector<int>> pred(n);
  std::vector<double> dist(n), sigma(n), delta(n);
  std::vector<int> order;

  for (int s : sources) {
    for (int i = 0; i < n; ++i) {
      pred[i].clear();
      dist[i] = -1.0;
      sigma[i] = 0.0;
      delta[i] = 0.0;
    }
    std::vector<int> stack;
    dist[s] = 0.0;
    sigma[s] = 1.0;
    if (!weighted) {
      std::queue<int> q;
      q.push(s);
      while (!q.empty()) {
        int v = q.front();
        q.pop();
        stack.push_back(v);
        for (const auto& e : g.adj[v]) {
          if (dist[e.first] < 0.0) {
            dist[e.first] = dist[v] + 1.0;
            q.push(e.first);
          }
          if (dist[e.first] == dist[v] + 1.0) {
            sigma[e.first] += sigma[v];
            pred[e.first].push_back(v);
          }
        }
      }
    } else {
      // Dijkstra (non-negative weights) over undirected weighted edges
      using QE = std::pair<double, int>;
      std::priority_queue<QE, std::vector<QE>, std::greater<QE>> pq;
      for (int i = 0; i < n; ++i) dist[i] = INF;
      dist[s] = 0.0;
      sigma[s] = 1.0;
      pq.push({0.0, s});
      std::vector<char> settled(n, 0);
      while (!pq.empty()) {
        auto [d, v] = pq.top();
        pq.pop();
        if (settled[v]) continue;
        settled[v] = 1;
        stack.push_back(v);
        for (const auto& e : g.adj[v]) {
          const double w = 1.0 / std::max(1e-6, e.second);  // heavier = cheaper path
          const double nd = d + w;
          if (nd < dist[e.first] - 1e-12) {
            dist[e.first] = nd;
            sigma[e.first] = sigma[v];
            pred[e.first].clear();
            pred[e.first].push_back(v);
            pq.push({nd, e.first});
          } else if (std::fabs(nd - dist[e.first]) <= 1e-12) {
            sigma[e.first] += sigma[v];
            pred[e.first].push_back(v);
          }
        }
      }
    }
    // accumulation
    for (int i = (int)stack.size() - 1; i >= 0; --i) {
      int w = stack[i];
      for (int v : pred[w]) {
        if (sigma[w] > 0.0) delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
      }
      if (w != s) bc[w] += delta[w];
    }
  }

  const double scale = (samples > 0 && samples < n) ? (double)n / (double)sources.size() : 1.0;
  for (int i = 0; i < n; ++i) bc[i] *= scale;
  if (n > 2) {  // undirected normalisation
    const double norm = 1.0 / ((double)(n - 1) * (double)(n - 2));
    for (int i = 0; i < n; ++i) bc[i] *= norm;
  }
  return bc;
}

// ------------------------------------------------------ connected components ---
inline std::vector<int> components(const Graph& g) {
  std::vector<int> comp(g.n, -1);
  int c = 0;
  for (int i = 0; i < g.n; ++i) {
    if (comp[i] != -1) continue;
    std::queue<int> q;
    q.push(i);
    comp[i] = c;
    while (!q.empty()) {
      int v = q.front();
      q.pop();
      for (const auto& e : g.adj[v])
        if (comp[e.first] == -1) {
          comp[e.first] = c;
          q.push(e.first);
        }
    }
    c++;
  }
  return comp;
}

// ------------------------------------------------------------------ BFS path ---
// Returns node indices along the shortest path (inclusive), empty if none.
inline std::vector<int> shortest_path(const Graph& g, int s, int t, bool weighted = false) {
  if (s < 0 || t < 0 || s >= g.n || t >= g.n) return {};
  std::vector<int> prev(g.n, -1);
  std::vector<char> seen(g.n, 0);
  if (!weighted) {
    std::queue<int> q;
    q.push(s);
    seen[s] = 1;
    while (!q.empty()) {
      int v = q.front();
      q.pop();
      if (v == t) break;
      for (const auto& e : g.adj[v])
        if (!seen[e.first]) {
          seen[e.first] = 1;
          prev[e.first] = v;
          q.push(e.first);
        }
    }
  } else {
    using QE = std::pair<double, int>;
    std::vector<double> dist(g.n, std::numeric_limits<double>::infinity());
    std::priority_queue<QE, std::vector<QE>, std::greater<QE>> pq;
    dist[s] = 0.0;
    pq.push({0.0, s});
    while (!pq.empty()) {
      auto [d, v] = pq.top();
      pq.pop();
      if (d > dist[v] + 1e-12) continue;
      for (const auto& e : g.adj[v]) {
        const double nd = d + 1.0 / std::max(1e-6, e.second);
        if (nd < dist[e.first] - 1e-12) {
          dist[e.first] = nd;
          prev[e.first] = v;
          pq.push({nd, e.first});
        }
      }
    }
  }
  if (s != t && prev[t] == -1) return {};
  std::vector<int> path;
  int cur = t;
  int guard = 0;
  while (cur != -1 && guard++ < g.n + 5) {
    path.push_back(cur);
    if (cur == s) break;
    cur = prev[cur];
  }
  if (path.empty() || path.back() != s) return {};
  std::reverse(path.begin(), path.end());
  return path;
}

// ---------------------------------------------------- link prediction scores ---
// Adamic-Adar + Jaccard over the *simple* neighbourhood, computed for a pair of
// node sets (e.g. every Topic<->Topic pair derived from shared papers). The
// Python layer decides which pairs are interesting; C++ just scores them fast.
struct LinkPrediction {
  double adamic_adar = 0.0;
  double jaccard = 0.0;
  double common_neighbors = 0.0;
};

inline LinkPrediction link_score(const Graph& g, int u, int v) {
  LinkPrediction lp;
  if (u == v || u < 0 || v < 0 || u >= g.n || v >= g.n) return lp;
  std::vector<int> a, b;
  for (const auto& e : g.adj[u]) a.push_back(e.first);
  for (const auto& e : g.adj[v]) b.push_back(e.first);
  std::sort(a.begin(), a.end());
  std::sort(b.begin(), b.end());
  std::vector<int> common;
  std::set_intersection(a.begin(), a.end(), b.begin(), b.end(), std::back_inserter(common));
  size_t uni = a.size() + b.size() - common.size();
  lp.common_neighbors = (double)common.size();
  lp.jaccard = uni > 0 ? (double)common.size() / (double)uni : 0.0;
  for (int w : common) {
    const double deg = (double)g.adj[w].size() + 1.0;
    lp.adamic_adar += 1.0 / std::log(deg + 1.0);
  }
  return lp;
}

// ----------------------------------------------------------------- cosine kNN ---
struct Neighbor {
  int id = 0;
  double sim = 0.0;
};

inline std::vector<std::vector<Neighbor>> knn_cosine(const std::vector<std::vector<float>>& vecs,
                                                     int k = 10, double min_sim = 0.05) {
  const int n = (int)vecs.size();
  std::vector<std::vector<Neighbor>> out(n);
  if (n == 0) return out;
  // Pre-normalise.
  std::vector<std::vector<float>> unit(n);
  for (int i = 0; i < n; ++i) {
    double s = 0.0;
    for (float x : vecs[i]) s += (double)x * x;
    const double inv = s > 0 ? 1.0 / std::sqrt(s) : 0.0;
    unit[i].resize(vecs[i].size());
    for (size_t j = 0; j < vecs[i].size(); ++j) unit[i][j] = (float)(vecs[i][j] * inv);
  }
  for (int i = 0; i < n; ++i) {
    std::vector<Neighbor> row;
    for (int j = 0; j < n; ++j) {
      if (i == j) continue;
      double dot = 0.0;
      const size_t dim = std::min(unit[i].size(), unit[j].size());
      for (size_t d = 0; d < dim; ++d) dot += (double)unit[i][d] * unit[j][d];
      if (dot >= min_sim) row.push_back({j, dot});
    }
    std::sort(row.begin(), row.end(), [](const Neighbor& a, const Neighbor& b) { return a.sim > b.sim; });
    if ((int)row.size() > k) row.resize(k);
    out[i] = std::move(row);
  }
  return out;
}

}  // namespace nexus
