// NEXUS — queries the Research Gap Finder relies on.
// The scoring itself runs in the engine; these queries gather the evidence so a
// judge can reproduce every number directly in Neo4j Browser.

// Concept co-occurrence inside a scope (feeds cluster structure)
MATCH (p:Paper)-[:STUDIES]->(t:Topic)
WHERE ($year_min IS NULL OR p.year >= $year_min)
  AND ($year_max IS NULL OR p.year <= $year_max)
WITH t, count(DISTINCT p) AS papers
WHERE papers >= $min_papers
RETURN t.id AS topic, t.name AS name, papers
ORDER BY papers DESC;

// Learned/similar edges between two concept sets (cross-cluster density)
MATCH (a)-[r:RELATED_TO|SIMILAR_TO|PREDICTED_LINK]-(b)
WHERE a.id IN $cluster_a AND b.id IN $cluster_b
RETURN a.id AS a, b.id AS b, type(r) AS rel, coalesce(r.weight, r.similarity) AS weight
ORDER BY weight DESC
LIMIT $limit;

// Claim-level tensions (CONTRADICTS edges are written back with their scores)
MATCH (c1:Claim)-[r:CONTRADICTS]->(c2:Claim)
OPTIONAL MATCH (p1:Paper)-[:MAKES_CLAIM]->(c1)
OPTIONAL MATCH (p2:Paper)-[:MAKES_CLAIM]->(c2)
RETURN c1.text AS claim_a, c2.text AS claim_b, r.score AS score, r.kind AS kind,
       r.reasons AS reasons, p1.title AS paper_a, p2.title AS paper_b
ORDER BY r.score DESC
LIMIT $limit;

// Predicted research connections (always hypotheses — never reported as facts)
MATCH (a)-[r:PREDICTED_LINK]->(b)
RETURN a.id AS source, coalesce(a.title, a.name) AS source_label,
       b.id AS target, coalesce(b.title, b.name) AS target_label,
       r.adamic_adar AS adamic_adar, r.jaccard AS jaccard
ORDER BY coalesce(r.adamic_adar, 0) DESC
LIMIT $limit;
