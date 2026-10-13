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

// Bounded evidence path using observed corpus relationships only.
// The syntax-level expansion ceiling is fixed at four; $max_hops applies the caller's
// smaller bound. IDs and the requested bound are parameters, never query text.
MATCH (a {id: $source_id}), (b {id: $target_id})
MATCH path = shortestPath((a)-[rels:STUDIES|USES_METHOD|USES_DATASET|CITES|AUTHORED|MAKES_CLAIM|AFFILIATED_WITH*1..4]-(b))
WHERE length(path) <= $max_hops
  AND all(rel IN rels WHERE type(rel) IN $allowed_types
          AND coalesce(rel.predicted, false) = false)
RETURN [n IN nodes(path) | {id: n.id, type: head(labels(n)),
                            label: coalesce(n.title, n.name, n.id)}] AS path_nodes,
       [r IN relationships(path) | {source: startNode(r).id, target: endNode(r).id,
                                    type: type(r), properties: properties(r)}] AS path_edges,
       length(path) AS hops
LIMIT 1;
