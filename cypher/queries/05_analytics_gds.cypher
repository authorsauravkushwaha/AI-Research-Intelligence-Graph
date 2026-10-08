// NEXUS — Neo4j Graph Data Science equivalents of the native C++ kernel.
// Run these when the GDS plugin is installed (docker compose enables it); the
// engine writes the same metrics back so plain Cypher sees them as properties.

// 1. project the graph (undirected, weights where they exist)
CALL gds.graph.drop('nexus', false) YIELD graphName RETURN graphName;

CALL gds.graph.project(
  'nexus',
  ['Paper','Author','Topic','Method','Dataset','Claim'],
  {STUDIES: {orientation: 'UNDIRECTED', properties: 'weight'},
   USES_METHOD: {orientation: 'UNDIRECTED', properties: 'weight'},
   USES_DATASET: {orientation: 'UNDIRECTED', properties: 'weight'},
   CITES: {orientation: 'UNDIRECTED', properties: 'weight'},
   SIMILAR_TO: {orientation: 'UNDIRECTED', properties: 'similarity'},
   RELATED_TO: {orientation: 'UNDIRECTED', properties: 'weight'}}
)
YIELD graphName, nodeCount, relationshipCount;

// 2. centrality
CALL gds.pageRank.stream('nexus', {maxIterations: 40, dampingFactor: 0.85})
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).id AS id, score
ORDER BY score DESC LIMIT 20;

CALL gds.betweenness.stream('nexus')
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).id AS id, score
ORDER BY score DESC LIMIT 20;

// 3. community detection
CALL gds.louvain.stream('nexus', {maxLevels: 10})
YIELD nodeId, communityId
RETURN communityId, count(*) AS members
ORDER BY members DESC;

// 4. link prediction (candidate research connections — label them as hypotheses)
CALL gds.nodeSimilarity.stream('nexus', {topK: 15, similarityCutoff: 0.4})
YIELD node1, node2, similarity
RETURN gds.util.asNode(node1).id AS a, gds.util.asNode(node2).id AS b, similarity
ORDER BY similarity DESC LIMIT 50;

// 5. write the metrics back so the UI and plain Cypher agree
CALL gds.pageRank.write('nexus', {writeProperty: 'pagerank'});
CALL gds.louvain.write('nexus', {writeProperty: 'community'});
