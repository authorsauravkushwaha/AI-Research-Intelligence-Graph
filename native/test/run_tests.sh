#!/usr/bin/env bash
# NEXUS native kernel self-test suite.
set -euo pipefail
BIN="build/bin/nexus-kernel"
[ -x "$BIN" ] || { echo "build the kernel first: make"; exit 1; }

echo "== C++ self-test (planted communities) =="
"$BIN" --selftest

echo "== JSON protocol: ping =="
echo '{"job":"ping"}' | "$BIN" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['ok'], d; print('  ping ok:', d['result'])"

echo "== JSON protocol: pagerank / louvain / betweenness on a toy graph =="
TOY='{"job":"pagerank","nodes":["a","b","c","d"],"edges":[["a","b",1],["a","c",1],["b","c",1],["c","d",1]]}'
echo "$TOY" | "$BIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['ok'], d
pr = dict(d['result']['pagerank'])
assert pr['a'] > 0 and pr['b'] > 0 and pr['d'] > 0
assert abs(sum(pr.values()) - 1.0) < 1e-6, sum(pr.values())
print('  pagerank ok, sum=%.6f, c=%.4f' % (sum(pr.values()), pr['c']))
"
echo '{"job":"louvain","nodes":["a","b","c","d","e","f"],"edges":[["a","b",1],["b","c",1],["a","c",1],["d","e",1],["e","f",1],["d","f",1],["c","d",1]]}' | "$BIN" | python3 -c "
import sys, json, collections
d = json.load(sys.stdin)
comms = dict(d['result']['communities'])
assert len(set(comms.values())) >= 2, comms
print('  louvain ok:', comms, 'communities=', d['result']['community_count'])
"
echo '{"job":"betweenness","nodes":["a","b","c","d"],"edges":[["a","b",1],["b","c",1],["c","d",1]]}' | "$BIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
bc = dict(d['result']['betweenness'])
assert bc['b'] > bc['a'] and bc['c'] > bc['d']
print('  betweenness ok:', {k: round(v,4) for k,v in bc.items()})
"
echo '{"job":"shortest_path","nodes":["a","b","c","d"],"edges":[["a","b",1],["b","c",1],["c","d",1]],"source":"a","target":"d"}' | "$BIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['result']['path'] == ['a','b','c','d'], d
print('  shortest_path ok:', d['result']['path'])
"
echo '{"job":"link_prediction","nodes":["a","b","c","d"],"edges":[["a","b",1],["b","c",1],["a","c",1],["c","d",1]],"pairs":[["a","c"]]}' | "$BIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['result']['predictions'][0][2] > 0
print('  link_prediction ok:', d['result']['predictions'])
"
echo '{"job":"knn","vectors":[{"id":"x","vector":[1,0,0]},{"id":"y","vector":[0.9,0.1,0]},{"id":"z","vector":[0,0,1]}],"params":{"k":1}}' | "$BIN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
n = dict(d['result']['neighbors'])
assert n['x'][0][0] == 'y', n
print('  knn ok:', n)
"
echo "ALL NATIVE KERNEL TESTS PASSED"
