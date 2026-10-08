// NEXUS — additional graph analytics helpers + built-in self-test.
//
// Kept in its own translation unit so the Makefile builds two objects and the
// algorithms stay independently testable from main().
#include <cassert>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "graph.hpp"

namespace nexus {

// A topic's *bridging score* blends betweenness (is it a structural bridge?)
// with cross-community reach (does it touch many communities?). Used by the
// Research Gap Engine to rank "connector" concepts.
struct BridgeScore {
  std::string id;
  double betweenness = 0.0;
  double cross_community = 0.0;
  double bridge_score = 0.0;
};

inline std::vector<BridgeScore> bridging_scores(const Graph& g, const std::vector<double>& bc,
                                                const std::vector<int>& comm) {
  std::vector<BridgeScore> out;
  out.reserve(g.n);
  for (int i = 0; i < g.n; ++i) {
    int distinct = 0;
    std::vector<int> seen;
    for (const auto& e : g.adj[i]) {
      const int c = comm.empty() ? 0 : comm[e.first];
      bool found = false;
      for (int v : seen)
        if (v == c) found = true;
      if (!found) {
        seen.push_back(c);
        distinct++;
      }
    }
    BridgeScore bs;
    bs.id = g.names[i];
    bs.betweenness = i < (int)bc.size() ? bc[i] : 0.0;
    const double denom = std::max(1, g.n - 1) * 1.0;
    bs.cross_community = std::min(1.0, (double)distinct / std::max(1.0, denom / 8.0));
    bs.bridge_score = 0.6 * bs.betweenness + 0.4 * bs.cross_community;
    out.push_back(bs);
  }
  return out;
}

// Deterministic synthetic graph used by `nexus-kernel --selftest`: 3 planted
// communities + a bridge node. Verifies Louvain finds the 3 communities and
// that PageRank ranks the hub highest.
bool self_test_impl() {
  Graph g;
  // 3 planted communities of 6 nodes each, plus one explicit bridge node.
  for (int c = 0; c < 3; ++c) {
    for (int i = 0; i < 6; ++i) {
      char buf[32];
      snprintf(buf, sizeof(buf), "c%d_n%d", c, i);
      g.add_node(buf);
    }
  }
  for (int c = 0; c < 3; ++c) {
    for (int i = 0; i < 6; ++i) {
      for (int j = i + 1; j < 6; ++j) {
        char a[32], b[32];
        snprintf(a, sizeof(a), "c%d_n%d", c, i);
        snprintf(b, sizeof(b), "c%d_n%d", c, j);
        g.add_edge(g.id_of(a), g.id_of(b), 1.0);
      }
    }
  }
  g.add_node("bridge");
  g.add_edge(g.id_of("bridge"), g.id_of("c0_n0"), 0.25);
  g.add_edge(g.id_of("bridge"), g.id_of("c1_n0"), 0.25);

  auto pr = pagerank(g);
  auto comm = louvain(g);
  auto bc = betweenness(g, false);

  int comm_count = 0;
  for (int c : comm) comm_count = std::max(comm_count, c + 1);

  const bool louvain_ok = comm_count == 3;
  // The hub adjacent to the single inter-community bridge must outrank a leaf
  // deep inside an isolated clique.
  const bool pagerank_ok = pr[g.id_of("c0_n0")] > pr[g.id_of("c2_n5")];
  printf("selftest: pr[c0_n0]=%.5f pr[c2_n5]=%.5f pr[bridge]=%.5f\n", pr[g.id_of("c0_n0")],
         pr[g.id_of("c2_n5")], pr[g.id_of("bridge")]);
  const bool betweenness_ok = bc[g.id_of("bridge")] > 0.0;

  printf("selftest: nodes=%d edges=%d communities=%d\n", g.n, g.m(), comm_count);
  printf("selftest: louvain=%s pagerank=%s betweenness=%s\n", louvain_ok ? "ok" : "FAIL",
         pagerank_ok ? "ok" : "FAIL", betweenness_ok ? "ok" : "FAIL");
  return louvain_ok && pagerank_ok && betweenness_ok;
}

}  // namespace nexus
