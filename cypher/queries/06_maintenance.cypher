// NEXUS — maintenance / inspection helpers

// Index and constraint inventory
SHOW CONSTRAINTS;
SHOW INDEXES;

// Relationship-type census (shows what the loader actually wrote)
MATCH ()-[r]->()
RETURN type(r) AS rel, count(*) AS n
ORDER BY n DESC;

// Provenance audit: every edge records where it came from
MATCH ()-[r]->()
WHERE r.provenance IS NULL
RETURN type(r) AS rel_without_provenance, count(*) AS n;

// Drop the demo graph (keeps constraints) — run before a clean reload
MATCH (n) WHERE NOT n:Community DETACH DELETE n;
MATCH (n:Community) DETACH DELETE n;
