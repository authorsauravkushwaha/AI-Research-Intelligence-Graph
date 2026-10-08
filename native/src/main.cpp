// NEXUS native graph analytics kernel — CLI entry point.
//
// Protocol (line-oriented, one JSON job per invocation):
//   stdin : {"job":"pagerank","nodes":[...],"edges":[[u,v,w],...],"params":{...}}
//   stdout: {"ok":true,"job":"pagerank","result":{...},"metrics":{"ms":1.2,...}}
//
// Jobs: ping | pagerank | louvain | betweenness | degree | components |
//       shortest_path | link_prediction | knn
#include <chrono>
#include <cstdio>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include "graph.hpp"
#include "minijson.hpp"

using namespace nexus;

namespace {

Graph build_graph(const JValue& root) {
  Graph g;
  const JValue* p = root.find("params");
  const JValue* dv = p && p->is_obj() ? p->find("directed") : nullptr;
  const bool directed = dv && dv->is_num() && dv->num > 0.5;
  const JValue* nodes = root.find("nodes");
  if (nodes && nodes->is_arr()) {
    for (const auto& nv : nodes->arr) {
      if (nv.is_str()) {
        g.add_node(nv.str);
      } else if (nv.is_obj()) {
        const JValue* id = nv.find("id");
        if (id && id->is_str()) g.add_node(id->str);
      }
    }
  }
  const JValue* edges = root.find("edges");
  if (edges && edges->is_arr()) {
    for (const auto& ev : edges->arr) {
      if (!ev.is_arr() || ev.arr.size() < 2) continue;
      std::string a, b;
      double w = 1.0;
      if (ev.arr[0].is_str()) a = ev.arr[0].str;
      if (ev.arr[1].is_str()) b = ev.arr[1].str;
      if (ev.arr.size() > 2) w = ev.arr[2].as_num(1.0);
      if (w <= 0) w = 1e-6;
      if (a.empty() || b.empty()) continue;
      g.add_node(a);
      g.add_node(b);
      g.add_edge(g.id_of(a), g.id_of(b), w, directed);
    }
  }
  return g;
}

std::string emit_nodes_values(const Graph& g, const std::vector<double>& v) {
  std::string out = "[";
  for (int i = 0; i < g.n; ++i) {
    if (i) out += ",";
    out += "[\"" + jesc(g.names[i]) + "\"," + jnum(v[i]) + "]";
  }
  out += "]";
  return out;
}

std::string emit_ints(const Graph& g, const std::vector<int>& v) {
  std::string out = "[";
  for (int i = 0; i < g.n; ++i) {
    if (i) out += ",";
    out += "[\"" + jesc(g.names[i]) + "\"," + jnum((double)v[i]) + "]";
  }
  out += "]";
  return out;
}

double param(const JValue& root, const std::string& key, double dflt) {
  const JValue* p = root.find("params");
  if (p && p->is_obj()) {
    const JValue* v = p->find(key);
    if (v && v->is_num()) return v->num;
  }
  return dflt;
}

}  // namespace

namespace nexus {
bool self_test_impl();
}

int main(int argc, char** argv) {
  if (argc > 1 && std::string(argv[1]) == "--selftest") {
    return nexus::self_test_impl() ? 0 : 1;
  }
  if (argc > 1 && std::string(argv[1]) == "--version") {
    std::cout << "nexus-kernel " NEXUS_VERSION " (C++" << (__cplusplus / 100 % 100) << ")" << std::endl;
    return 0;
  }
  std::ios::sync_with_stdio(false);
  std::ostringstream buf;
  buf << std::cin.rdbuf();
  const std::string raw = buf.str();

  JValue root;
  JParser parser(raw);
  if (!parser.parse(root) || !root.is_obj()) {
    std::cout << "{\"ok\":false,\"error\":\"invalid JSON input: " << jesc(parser.error()) << "\"}" << std::endl;
    return 2;
  }
  const JValue* jobv = root.find("job");
  const std::string job = jobv && jobv->is_str() ? jobv->str : "";
  if (job.empty()) {
    std::cout << "{\"ok\":false,\"error\":\"missing job\"}" << std::endl;
    return 2;
  }
  if (job == "ping") {
    std::cout << "{\"ok\":true,\"job\":\"ping\",\"result\":{\"engine\":\"nexus-native\",\"version\":\"" << NEXUS_VERSION
              << "\",\"cxx\":" << __cplusplus << "}}" << std::endl;
    return 0;
  }

  const auto t0 = std::chrono::steady_clock::now();
  Graph g = build_graph(root);
  std::string payload;

  if (job == "pagerank") {
    auto r = pagerank(g, param(root, "damping", 0.85), (int)param(root, "iterations", 60));
    payload = "{\"pagerank\":" + emit_nodes_values(g, r) + "}";
  } else if (job == "louvain") {
    auto r = louvain(g, param(root, "resolution", 1.0), (int)param(root, "max_rounds", 20),
                     (uint32_t)param(root, "seed", 42));
    int k = 0;
    for (int c : r) k = std::max(k, c + 1);
    payload = "{\"communities\":" + emit_ints(g, r) + ",\"community_count\":" + jnum((double)k) + "}";
  } else if (job == "betweenness") {
    auto r = betweenness(g, param(root, "weighted", 1.0) > 0.5, (int)param(root, "samples", 0));
    payload = "{\"betweenness\":" + emit_nodes_values(g, r) + "}";
  } else if (job == "degree") {
    std::vector<double> deg(g.n, 0.0), wdeg(g.n, 0.0);
    for (int i = 0; i < g.n; ++i) {
      deg[i] = (double)g.adj[i].size();
      for (const auto& e : g.adj[i]) wdeg[i] += e.second;
    }
    const int max_deg = *std::max_element(deg.begin(), deg.end(), [](double a, double b) { return a < b; });
    (void)max_deg;
    std::string deg_json = "[";
    for (int i = 0; i < g.n; ++i) {
      if (i) deg_json += ",";
      deg_json += "[\"" + jesc(g.names[i]) + "\"," + jnum(deg[i]) + "," + jnum(wdeg[i]) + "]";
    }
    deg_json += "]";
    payload = "{\"degree\":" + deg_json + "}";
  } else if (job == "components") {
    auto r = components(g);
    int k = 0;
    for (int c : r) k = std::max(k, c + 1);
    payload = "{\"components\":" + emit_ints(g, r) + ",\"component_count\":" + jnum((double)k) + "}";
  } else if (job == "shortest_path") {
    const JValue* src = root.find("source");
    const JValue* dst = root.find("target");
    const int s = src && src->is_str() ? g.id_of(src->str) : -1;
    const int t = dst && dst->is_str() ? g.id_of(dst->str) : -1;
    auto path = shortest_path(g, s, t, param(root, "weighted", 0.0) > 0.5);
    std::string arr = "[";
    for (size_t i = 0; i < path.size(); ++i) {
      if (i) arr += ",";
      arr += "\"" + jesc(g.names[path[i]]) + "\"";
    }
    arr += "]";
    payload = "{\"path\":" + arr + ",\"hops\":" + jnum((double)(path.empty() ? 0 : path.size() - 1)) + "}";
  } else if (job == "link_prediction") {
    const JValue* pairs = root.find("pairs");
    std::string arr = "[";
    bool first = true;
    if (pairs && pairs->is_arr()) {
      for (const auto& pv : pairs->arr) {
        if (!pv.is_arr() || pv.arr.size() < 2) continue;
        const int u = g.id_of(pv.arr[0].as_str());
        const int v = g.id_of(pv.arr[1].as_str());
        if (u < 0 || v < 0) continue;
        LinkPrediction lp = link_score(g, u, v);
        if (!first) arr += ",";
        first = false;
        arr += "[\"" + jesc(g.names[u]) + "\",\"" + jesc(g.names[v]) + "\"," + jnum(lp.adamic_adar) + "," +
               jnum(lp.jaccard) + "," + jnum(lp.common_neighbors) + "]";
      }
    }
    arr += "]";
    payload = "{\"predictions\":" + arr + "}";
  } else if (job == "knn") {
    const JValue* vecs = root.find("vectors");
    std::vector<std::vector<float>> data;
    std::vector<std::string> ids;
    if (vecs && vecs->is_arr()) {
      for (const auto& vv : vecs->arr) {
        if (!vv.is_obj()) continue;
        const JValue* id = vv.find("id");
        const JValue* vec = vv.find("vector");
        if (!id || !vec || !vec->is_arr()) continue;
        std::vector<float> row;
        row.reserve(vec->arr.size());
        for (const auto& x : vec->arr) row.push_back((float)x.as_num());
        ids.push_back(id->as_str());
        data.push_back(std::move(row));
      }
    }
    if (g.n > 0) g.names = ids;  // reuse the name table for kNN payload emission
    auto rows = knn_cosine(data, (int)param(root, "k", 10), param(root, "min_similarity", 0.05));
    std::string arr = "[";
    for (size_t i = 0; i < rows.size(); ++i) {
      if (i) arr += ",";
      arr += "[\"" + jesc(ids[i]) + "\",[";
      for (size_t j = 0; j < rows[i].size(); ++j) {
        if (j) arr += ",";
        arr += "[\"" + jesc(ids[rows[i][j].id]) + "\"," + jnum(rows[i][j].sim) + "]";
      }
      arr += "]]";
    }
    arr += "]";
    payload = "{\"neighbors\":" + arr + "}";
  } else {
    std::cout << "{\"ok\":false,\"error\":\"unknown job '" << jesc(job) << "'\"}" << std::endl;
    return 2;
  }

  const auto t1 = std::chrono::steady_clock::now();
  const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
  JWriter w;
  w.raw("{\"ok\":true,\"job\":");
  w.str(job);
  w.raw(",\"result\":");
  w.raw(payload);
  w.raw(",\"metrics\":{\"nodes\":");
  w.num((double)g.n);
  w.raw(",\"edges\":");
  w.num((double)g.m());
  w.raw(",\"ms\":");
  w.num(ms);
  w.raw("},\"engine\":\"nexus-native/" NEXUS_VERSION "\"}");
  std::cout << w.str() << std::endl;
  return 0;
}
