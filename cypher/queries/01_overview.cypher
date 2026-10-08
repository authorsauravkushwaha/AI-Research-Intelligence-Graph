// NEXUS — corpus overview (all values are parameters: use $year_min etc.)
// Graph size and label distribution
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS nodes
ORDER BY nodes DESC;

// Papers per year inside a scope
MATCH (p:Paper)
WHERE ($year_min IS NULL OR p.year >= $year_min)
  AND ($year_max IS NULL OR p.year <= $year_max)
  AND ($field IS NULL OR p.field = $field)
RETURN p.year AS year, count(*) AS papers
ORDER BY year;

// Most influential papers (PageRank is written back by the engine)
MATCH (p:Paper)
WHERE ($year_min IS NULL OR p.year >= $year_min)
RETURN p.title AS title, p.year AS year, p.pagerank AS pagerank, p.betweenness AS betweenness,
       p.community AS community
ORDER BY coalesce(p.pagerank, 0) DESC
LIMIT $limit;
