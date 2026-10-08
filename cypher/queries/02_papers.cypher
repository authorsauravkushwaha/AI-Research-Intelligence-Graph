// NEXUS — paper retrieval and neighbourhood

// Full-text search (uses the nexus_fulltext index created by ensure_schema)
CALL db.index.fulltext.queryNodes('nexus_fulltext', $query, {limit: $limit})
YIELD node, score
RETURN node.id AS id, labels(node) AS labels, coalesce(node.title, node.name) AS title, score
ORDER BY score DESC;

// One paper with its typed neighbourhood (bounded, expand-on-demand)
MATCH (p:Paper {id: $paper_id})
OPTIONAL MATCH (p)-[r]-(other)
WHERE type(r) IN $rel_types
RETURN type(r) AS rel, labels(other) AS labels, coalesce(other.title, other.name) AS name,
       other.id AS id, properties(r) AS props
LIMIT $limit;

// Papers that bridge two concepts (used by the gap engine's evidence step)
MATCH (a:Topic {id: $topic_a})<-[:STUDIES]-(p:Paper)-[:STUDIES]->(b:Topic {id: $topic_b})
RETURN DISTINCT p.id AS id, p.title AS title, p.year AS year, p.pagerank AS pagerank
ORDER BY coalesce(p.pagerank, 0) DESC
LIMIT $limit;

// Shortest relationship path between two nodes (evidence path for the UI)
MATCH (a {id: $source_id}), (b {id: $target_id})
MATCH path = shortestPath((a)-[*..$max_hops]-(b))
RETURN [n IN nodes(path) | coalesce(n.title, n.name)] AS nodes,
       [r IN relationships(path) | type(r)] AS rels,
       length(path) AS hops;
