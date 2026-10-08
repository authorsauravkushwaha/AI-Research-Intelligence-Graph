// NEXUS — community analysis (written back by Louvain / GDS)

// Community sizes with their representative topics
MATCH (c:Community)<-[:BELONGS_TO]-(m)
WHERE NOT m:Community
WITH c, m, labels(m) AS labels
RETURN c.id AS community, c.name AS name, c.rank AS rank,
       count(m) AS members,
       sum(CASE WHEN 'Paper' IN labels THEN 1 ELSE 0 END) AS papers,
       sum(CASE WHEN 'Topic' IN labels THEN 1 ELSE 0 END) AS topics
ORDER BY papers DESC;

// Cross-community connectivity — high numbers mean the areas do talk to each other
MATCH (a:Community)<-[:BELONGS_TO]-(p:Paper)-[:BELONGS_TO]->(b:Community)
WHERE a.id < b.id
RETURN a.name AS community_a, b.name AS community_b, count(DISTINCT p) AS shared_papers
ORDER BY shared_papers DESC
LIMIT $limit;

// Papers that touch two communities (candidate bridges)
MATCH (a:Community)<-[:BELONGS_TO]-(p:Paper)-[:BELONGS_TO]->(b:Community)
WHERE a.id = $community_a AND b.id = $community_b
RETURN p.id AS id, p.title AS title, p.year AS year, p.betweenness AS betweenness
ORDER BY coalesce(p.betweenness, 0) DESC;
