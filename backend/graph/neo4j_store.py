"""Neo4j implementation of the NEXUS graph store.

Neo4j is the configured store of record: the shipped corpus is imported with stable IDs
and idempotent `UNWIND`/`MERGE` statements, then nodes and edges are hydrated into a local
working projection. Scope selection, GraphRAG, gap/candidate analysis, and most Python graph
workflows operate on that projection. Optional GDS routines run in Neo4j for supported
metrics when enabled and available. Investigator evidence paths are separately checked
against Neo4j using a bounded, time-limited Cypher traversal.

The class subclasses the in-process store to reuse graph enrichment and API semantics, not
to imply that every endpoint executes remotely. Data values and IDs are bound parameters.
Taxonomy labels/relationship types come only from fixed code, and the path query uses a
static four-hop syntax ceiling plus a bound caller limit. Credentials are never included in
query text or safe connection errors.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Sequence

from backend.algorithms import kernel
from backend.config import get_settings
from backend.ingestion.graph_builder import claim_id, dataset_id, method_id, paper_id, topic_id
from backend.models.graph import GEdge, GNode
from backend.store.memory_store import MemoryGraphStore

log = logging.getLogger("nexus.neo4j")


class Neo4jError(RuntimeError):
    """Raised when Neo4j is configured but the graph cannot be prepared."""


#: Relationship types the store knows how to write (kept next to the writer).
REL_TYPES = (
    "STUDIES",
    "USES_METHOD",
    "USES_DATASET",
    "AUTHORED",
    "AFFILIATED_WITH",
    "CITES",
    "MAKES_CLAIM",
    "SUPPORTS",
    "CONTRADICTS",
    "SIMILAR_TO",
    "RELATED_TO",
    "PREDICTED_LINK",
    "BELONGS_TO",
    "MEASURED_BY",
)


class Neo4jGraphStore(MemoryGraphStore):
    engine_name = "neo4j"

    def __init__(self, corpus_path=None, *, enable_similarity: bool = True) -> None:
        super().__init__(corpus_path, enable_similarity=enable_similarity)
        self.settings = get_settings()
        self._driver = None
        self.gds_available = False
        self.connection_verified = False
        self.cypher_statements = 0
        self.schema_warnings: list[str] = []
        self.bootstrap_report: dict[str, Any] = {}

    # ------------------------------------------------------------- driver
    def connect(self):
        from neo4j import GraphDatabase, Query  # imported lazily: memory mode needs no driver

        cfg = self.settings.neo4j
        if not cfg.password:
            raise Neo4jError("NEO4J_PASSWORD is empty — refusing to connect with no credential")
        try:
            self._driver = GraphDatabase.driver(
                cfg.uri,
                auth=(cfg.username, cfg.password),
                connection_timeout=max(0.1, cfg.timeout),
                max_connection_lifetime=300,
            )
            # verify_connectivity forces an actual connection/auth check instead of
            # accepting an untested driver object and failing much later in a route.
            self._driver.verify_connectivity()
            self.gds_available = False
            if cfg.use_gds:
                try:
                    with self._driver.session(database=cfg.database) as session:
                        record = session.run(
                            Query("CALL gds.version() YIELD gdsVersion RETURN gdsVersion",
                                  timeout=max(0.1, cfg.query_timeout))
                        ).single()
                        self.gds_available = bool(record)
                except Exception:  # noqa: BLE001 - GDS is optional
                    self.gds_available = False
            self.connection_verified = True
            return self
        except Exception as exc:  # noqa: BLE001 - convert connection failures to safe actionable status
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:  # noqa: BLE001 - preserve the original connection error
                    pass
            self._driver = None
            self.connection_verified = False
            if isinstance(exc, Neo4jError):
                raise
            raise Neo4jError(
                f"Neo4j connectivity verification failed ({type(exc).__name__}) for database "
                f"'{cfg.database}'. Check NEO4J_URI, NEO4J_DATABASE, NEO4J_USERNAME, "
                "NEO4J_PASSWORD, network access, and the server's Bolt endpoint."
            ) from exc

    def run_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute one parameterised, time-bounded statement and return rows as dicts."""
        from neo4j import Query

        if self._driver is None:
            raise Neo4jError("Neo4j driver is not connected")
        self.cypher_statements += 1
        statement = Query(query, timeout=max(0.1, self.settings.neo4j.query_timeout))
        with self._driver.session(database=self.settings.neo4j.database) as session:
            result = session.run(statement, params or {})
            return [dict(row) for row in result]

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ------------------------------------------------------------- schema
    def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (n:Paper) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT topic_id IF NOT EXISTS FOR (n:Topic) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT method_id IF NOT EXISTS FOR (n:Method) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT dataset_id IF NOT EXISTS FOR (n:Dataset) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (n:Author) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT community_id IF NOT EXISTS FOR (n:Community) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX paper_year IF NOT EXISTS FOR (n:Paper) ON (n.year)",
            "CREATE INDEX paper_field IF NOT EXISTS FOR (n:Paper) ON (n.field)",
            "CREATE INDEX paper_pagerank IF NOT EXISTS FOR (n:Paper) ON (n.pagerank)",
            "CREATE FULLTEXT INDEX nexus_fulltext IF NOT EXISTS FOR (n:Paper|Topic|Method|Dataset|Claim) "
            "ON EACH [n.name, n.title, n.abstract, n.text]",
        ]
        self.schema_warnings = []
        for statement in statements:
            try:
                self.run_cypher(statement)
            except Exception as exc:  # noqa: BLE001 - permissions/version may prevent DDL
                words = statement.split()
                marker = "CONSTRAINT" if "CONSTRAINT" in words else "INDEX"
                object_name = words[words.index(marker) + 1] if marker in words else "unknown"
                warning = (
                    f"Could not ensure Neo4j schema object {object_name} ({type(exc).__name__}); "
                    "verify database version and schema privileges."
                )
                self.schema_warnings.append(warning)
                log.warning("%s", warning)

    # --------------------------------------------------------------- load
    def load(self) -> None:
        with self._lock:
            try:
                if not self.corpus_path.exists():
                    raise FileNotFoundError(
                        f"corpus not found at {self.corpus_path} — run `python3 scripts/build_corpus.py`"
                    )
                self._corpus = json.loads(self.corpus_path.read_text(encoding="utf-8"))
                if self._driver is None:
                    self.connect()
                self.ensure_schema()
                self._load_corpus_into_neo4j()

                self.bootstrap_report = self._database_report()
                self._hydrate()
                self._add_corpus_taxonomy_nodes()
                self._resolve_claims()

                self.analytics = self.analytics_engine.compute(
                    self.revision, list(self.nodes.values()), self.edges, force=True
                )
                if self.enable_similarity:
                    self._build_similarity()
                self._assign_communities()
                self._rebuild_community_profiles()
                self.analytics = self.analytics_engine.compute(
                    self.revision, list(self.nodes.values()), self.edges, force=True
                )
                self._run_gds_analytics()
                self._write_back()
                log.info(
                    "neo4j graph ready: %d nodes / %d edges (gds=%s, statements=%d)",
                    len(self.nodes), len(self.edges), self.gds_available, self.cypher_statements,
                )
            except Exception:
                self.close()
                raise

    #: Relationship-type whitelist for the generic loader (no dynamic concatenation
    #: from user input: the value comes from a fixed tuple).
    def _load_corpus_into_neo4j(self) -> None:
        papers = self._corpus["papers"]
        self.run_cypher(
            """
            UNWIND $rows AS row
            MERGE (p:Paper {id: row.id})
            SET p.name = row.title, p.title = row.title, p.abstract = row.summary,
                p.year = row.year, p.url = row.url, p.arxiv_id = row.arxiv_id,
                p.field = row.field, p.summary_source = row.summary_source,
                p.author_status = row.author_status, p.corpus_tier = row.corpus_tier
            """,
            {"rows": [
                {
                    "id": paper_id(p["arxiv_id"]),
                    "arxiv_id": p["arxiv_id"],
                    **{key: p.get(key) for key in (
                        "title", "summary", "year", "url", "field", "summary_source",
                        "author_status", "corpus_tier",
                    )},
                }
                for p in papers
            ]},
        )

        # Older NEXUS builds used raw arXiv claim IDs in Neo4j. Migrate them once
        # to the same stable claim:<paper>:<ordinal> IDs emitted by GraphBuilder.
        self.run_cypher(
            """
            MATCH (c:Claim)
            WHERE c.id IS NOT NULL AND NOT c.id STARTS WITH 'claim:'
            SET c.id = 'claim:' + c.id
            RETURN count(c) AS migrated
            """
        )

        taxonomy_rows = []
        for paper in papers:
            pid = paper_id(paper["arxiv_id"])
            if paper.get("field"):
                taxonomy_rows.append({
                    "paper": pid,
                    "name": paper["field"],
                    "id": topic_id(paper["field"]),
                    "topic_kind": "field",
                    "weight": 0.6,
                    "provenance": "paper.field taxonomy",
                })
            for name in paper.get("topics", []):
                taxonomy_rows.append({
                    "paper": pid,
                    "name": name,
                    "id": topic_id(name),
                    "topic_kind": "concept",
                    "weight": 1.0,
                    "provenance": "curated taxonomy",
                })
        if taxonomy_rows:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (p:Paper {id: row.paper})
                MERGE (n:Topic {id: row.id})
                SET n.name = row.name,
                    n.topic_kind = CASE WHEN n.topic_kind = 'field' OR row.topic_kind = 'field'
                                        THEN 'field' ELSE 'concept' END
                MERGE (p)-[r:STUDIES]->(n)
                SET r.weight = row.weight, r.provenance = row.provenance
                """,
                {"rows": taxonomy_rows},
            )

        taxonomy = (
            ("USES_METHOD", "methods", "Method", method_id),
            ("USES_DATASET", "datasets", "Dataset", dataset_id),
        )
        for rel, key, label, id_fn in taxonomy:
            rows = [
                {"paper": paper_id(p["arxiv_id"]), "name": name, "id": id_fn(name),
                 "provenance": "curated taxonomy"}
                for p in papers
                for name in p.get(key, [])
            ]
            if not rows:
                continue
            self.run_cypher(
                f"""
                UNWIND $rows AS row
                MATCH (p:Paper {{id: row.paper}})
                MERGE (n:{label} {{id: row.id}})
                SET n.name = row.name
                MERGE (p)-[r:{rel}]->(n)
                SET r.weight = 1.0, r.provenance = row.provenance
                """,
                {"rows": rows},
            )

        author_rows = [
            {"paper": paper_id(p["arxiv_id"]), "name": author, "id": f"author:{_slug(author)}"}
            for p in papers
            for author in p.get("authors", [])
        ]
        if author_rows:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (p:Paper {id: row.paper})
                MERGE (a:Author {id: row.id})
                SET a.name = row.name
                MERGE (a)-[r:AUTHORED]->(p)
                SET r.provenance = 'arXiv public listing'
                """,
                {"rows": author_rows},
            )
        citation_rows = [
            {"source": paper_id(c["source"]), "target": paper_id(c["target"]),
             "provenance": c.get("citation_source", "curated-lineage")}
            for c in self._corpus.get("citations", [])
        ]
        if citation_rows:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (a:Paper {id: row.source})
                MATCH (b:Paper {id: row.target})
                MERGE (a)-[r:CITES]->(b)
                SET r.provenance = row.provenance, r.weight = 1.0
                """,
                {"rows": citation_rows},
            )
        papers_by_id = {p.get("arxiv_id"): p for p in papers}
        claim_rows = []
        for claim in self._corpus.get("claims", []):
            source_paper = papers_by_id.get(claim.get("paper"), {})
            raw_provenance = claim.get("provenance")
            provenance = dict(raw_provenance) if isinstance(raw_provenance, dict) else {}
            provenance.setdefault("paper_id", paper_id(claim["paper"]))
            if source_paper.get("url"):
                provenance.setdefault("url", source_paper["url"])
            provenance.setdefault("annotation_source", claim.get("source", "corpus"))
            claim_rows.append({
                "id": claim_id(claim["id"]),
                "paper": paper_id(claim["paper"]),
                "text": claim["text"],
                "stance": claim.get("stance", "reports"),
                "source": claim.get("source", "corpus"),
                "provenance": json.dumps(provenance, ensure_ascii=False),
            })
        if claim_rows:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (p:Paper {id: row.paper})
                MERGE (c:Claim {id: row.id})
                SET c.name = row.text, c.text = row.text, c.stance = row.stance,
                    c.source = row.source, c.provenance = row.provenance
                MERGE (p)-[r:MAKES_CLAIM]->(c)
                SET r.provenance = row.source
                """,
                {"rows": claim_rows},
            )

    def _database_report(self) -> dict[str, Any]:
        try:
            rows = self.run_cypher(
                "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY n DESC"
            )
            return {"labels": {row["label"]: row["n"] for row in rows}}
        except Exception as exc:  # noqa: BLE001
            return {"error": type(exc).__name__}

    # ------------------------------------------------------------ hydrate
    def _hydrate(self) -> None:
        """Read the graph back out of Neo4j into the working set used by the engines."""
        self.nodes = {}
        self.edges = []
        node_rows = self.run_cypher(
            """
            MATCH (n)
            WHERE NOT n:Community
            RETURN n.id AS id, labels(n) AS labels, coalesce(n.title, n.name, n.id) AS name,
                   properties(n) AS props
            """
        )
        for row in node_rows:
            labels = [label for label in (row["labels"] or []) if label != "Community"]
            if not labels or not row["id"]:
                continue
            props = {k: v for k, v in (row["props"] or {}).items() if k not in {"id", "name", "title"}}
            if labels[0] == "Claim" and isinstance(props.get("provenance"), str):
                try:
                    decoded_provenance = json.loads(props["provenance"])
                except (TypeError, json.JSONDecodeError):
                    decoded_provenance = {}
                props["provenance"] = decoded_provenance if isinstance(decoded_provenance, dict) else {}
            self.nodes[row["id"]] = GNode(id=row["id"], label=labels[0], name=row["name"] or row["id"], props=props)

        edge_rows = self.run_cypher(
            """
            MATCH (a)-[r]->(b)
            WHERE NOT a:Community AND NOT b:Community AND type(r) <> 'BELONGS_TO'
            RETURN a.id AS src, b.id AS dst, type(r) AS type, properties(r) AS props
            LIMIT 200000
            """
        )
        for row in edge_rows:
            if row["src"] in self.nodes and row["dst"] in self.nodes:
                self.edges.append(GEdge(src=row["src"], dst=row["dst"], type=row["type"],
                                        props=row["props"] or {}))
        # Existing SIMILAR_TO/RELATED_TO edges from an earlier run are re-derivable:
        # keep them out of the working set so the enrichment pass is the single
        # source of truth (and re-writes the same values back).
        self.edges = [e for e in self.edges if e.type not in {"SIMILAR_TO", "RELATED_TO"}]
        self.revision += 1

    def trace_path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_hops: int = 4,
        allowed_types: Sequence[str] | None = None,
    ) -> dict[str, Any] | None:
        """Verify a short evidence path against Neo4j itself using bound IDs.

        This is intentionally not a local-hydration fallback: when Neo4j was selected,
        the returned path (or its absence) comes from a bounded Cypher traversal of the
        configured database. Association/prediction edges are excluded.
        """
        if self._driver is None:
            raise Neo4jError("Neo4j driver is not connected")
        if source_id == target_id and source_id in self.nodes:
            return {
                "nodes": [{"id": source_id, "type": self.nodes[source_id].label, "label": self.nodes[source_id].name}],
                "hops": [],
                "hops_count": 0,
                "source_engine": "neo4j",
                "method": "parameterized Neo4j Cypher shortest path",
            }
        max_hops = max(1, min(int(max_hops), 4))
        observed_types = {
            "STUDIES", "USES_METHOD", "USES_DATASET", "CITES", "AUTHORED",
            "MAKES_CLAIM", "AFFILIATED_WITH",
        }
        allowed = sorted(observed_types.intersection(allowed_types or observed_types))
        if not allowed:
            return None
        query = """
            MATCH (source {id: $source_id}), (target {id: $target_id})
            MATCH path = shortestPath((source)-[rels:STUDIES|USES_METHOD|USES_DATASET|CITES|AUTHORED|MAKES_CLAIM|AFFILIATED_WITH*1..4]-(target))
            WHERE length(path) <= $max_hops
              AND all(rel IN rels WHERE type(rel) IN $allowed_types
                      AND coalesce(rel.predicted, false) = false)
            RETURN
                [n IN nodes(path) | {id: n.id, type: head(labels(n)),
                                     label: coalesce(n.title, n.name, n.id)}] AS path_nodes,
                [rel IN relationships(path) | {source: startNode(rel).id,
                                                target: endNode(rel).id,
                                                type: type(rel),
                                                properties: properties(rel)}] AS path_edges
            LIMIT 1
        """
        rows = self.run_cypher(query, {
            "source_id": source_id,
            "target_id": target_id,
            "max_hops": max_hops,
            "allowed_types": allowed,
        })
        if not rows:
            return None
        row = rows[0]
        nodes = row.get("path_nodes") or []
        relationships = row.get("path_edges") or []
        if not nodes or len(relationships) + 1 != len(nodes):
            return None
        hops = []
        for index, relationship in enumerate(relationships):
            current_id = nodes[index]["id"]
            next_id = nodes[index + 1]["id"]
            properties = relationship.get("properties") or {}
            hops.append({
                "from": current_id,
                "to": next_id,
                "type": relationship.get("type"),
                "relationship_source": relationship.get("source"),
                "relationship_target": relationship.get("target"),
                "direction": "outgoing" if relationship.get("source") == current_id else "incoming",
                "provenance": properties.get("provenance"),
                "properties": {
                    key: properties[key]
                    for key in ("provenance", "weight", "score", "similarity", "predicted")
                    if properties.get(key) is not None
                },
            })
        return {
            "nodes": nodes,
            "hops": hops,
            "hops_count": len(hops),
            "source_engine": "neo4j",
            "method": f"parameterized Neo4j Cypher shortest path (maximum {max_hops} hops)",
        }

    # ------------------------------------------------------------ analytics
    def _run_gds_analytics(self) -> None:
        """Prefer GDS for PageRank / Louvain / betweenness; else keep local metrics."""
        if not self.gds_available:
            return
        try:
            self.run_cypher("CALL gds.graph.drop('nexus', false) YIELD graphName RETURN graphName")
        except Exception:  # noqa: BLE001 - dropping a missing projection is fine
            pass
        try:
            self.run_cypher(
                """
                CALL gds.graph.project(
                    'nexus',
                    ['Paper','Author','Topic','Method','Dataset','Claim'],
                    {
                      STUDIES:        {orientation: 'UNDIRECTED', properties: 'weight'},
                      USES_METHOD:    {orientation: 'UNDIRECTED', properties: 'weight'},
                      USES_DATASET:   {orientation: 'UNDIRECTED', properties: 'weight'},
                      AUTHORED:       {orientation: 'UNDIRECTED'},
                      CITES:          {orientation: 'UNDIRECTED', properties: 'weight'},
                      MAKES_CLAIM:    {orientation: 'UNDIRECTED'},
                      SIMILAR_TO:     {orientation: 'UNDIRECTED', properties: 'similarity'},
                      RELATED_TO:     {orientation: 'UNDIRECTED', properties: 'weight'},
                      PREDICTED_LINK: {orientation: 'UNDIRECTED', properties: 'weight'}
                    }
                )
                YIELD graphName, nodeCount, relationshipCount
                RETURN graphName, nodeCount, relationshipCount
                """
            )
            id_map = {row["neo"]: row["id"] for row in self.run_cypher(
                "MATCH (n) RETURN id(n) AS neo, n.id AS id"
            )}
            pagerank = {id_map[r["nodeId"]]: r["score"] for r in self.run_cypher(
                "CALL gds.pageRank.stream('nexus', {maxIterations: 40, dampingFactor: 0.85}) "
                "YIELD nodeId, score RETURN nodeId, score"
            ) if r["nodeId"] in id_map}
            communities = {id_map[r["nodeId"]]: r["communityId"] for r in self.run_cypher(
                "CALL gds.louvain.stream('nexus', {maxLevels: 10}) YIELD nodeId, communityId "
                "RETURN nodeId, communityId"
            ) if r["nodeId"] in id_map}
            betweenness = {id_map[r["nodeId"]]: r["score"] for r in self.run_cypher(
                "CALL gds.betweenness.stream('nexus') YIELD nodeId, score RETURN nodeId, score"
            ) if r["nodeId"] in id_map}
            if self.analytics:
                self.analytics.pagerank.update(pagerank)
                self.analytics.communities.update(communities)
                self.analytics.betweenness.update(betweenness)
                self.analytics.engines.update({
                    "pagerank": "neo4j-gds",
                    "louvain": "neo4j-gds",
                    "betweenness": "neo4j-gds",
                })
            self._assign_communities()
            self._rebuild_community_profiles()
            self.analytics = self.analytics_engine.compute(
                self.revision, list(self.nodes.values()), self.edges, force=True
            )
            if self.analytics:
                self.analytics.pagerank.update(pagerank)
                self.analytics.communities.update(communities)
                self.analytics.betweenness.update(betweenness)
                self.analytics.engines.update({
                    "pagerank": "neo4j-gds", "louvain": "neo4j-gds", "betweenness": "neo4j-gds",
                })
        except Exception as exc:  # noqa: BLE001 - retain the local analytics fallback
            self.gds_available = False
            log.info("GDS analytics unavailable (%s) — keeping local-projection metrics", type(exc).__name__)

    def _write_back(self) -> None:
        """Persist derived metrics, communities, conflicts and predictions to Neo4j."""
        if not self.analytics:
            return
        rows = [
            {
                "id": node_id,
                "pagerank": round(self.analytics.pagerank.get(node_id, 0.0), 8),
                "betweenness": round(self.analytics.betweenness.get(node_id, 0.0), 8),
                "degree": self.analytics.degree.get(node_id, 0),
                "community": self.analytics.communities.get(node_id, -1),
            }
            for node_id in self.nodes
        ]
        self.run_cypher(
            """
            UNWIND $rows AS row
            MATCH (n {id: row.id})
            SET n.pagerank = row.pagerank, n.betweenness = row.betweenness,
                n.degree = row.degree, n.community = row.community
            """,
            {"rows": rows},
        )
        self.run_cypher("MATCH (c:Community) DETACH DELETE c")
        if self.community_profiles:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MERGE (c:Community {id: row.id})
                SET c.name = row.name, c.community_index = row.index, c.paper_count = row.papers,
                    c.top_topics = row.topics, c.rank = row.rank
                """,
                {"rows": [
                    {"id": profile["id"], "name": profile["name"],
                     "index": profile["community_index"], "papers": profile["paper_count"],
                     "topics": profile["top_topics"][:8], "rank": profile.get("rank", 0)}
                    for profile in self.community_profiles
                ]},
            )
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (n {id: row.id})
                MATCH (c:Community {community_index: row.community})
                MERGE (n)-[r:BELONGS_TO]->(c)
                SET r.provenance = row.provenance
                """,
                {"rows": [
                    {"id": node_id, "community": community, "provenance": "louvain"}
                    for node_id, community in self.analytics.communities.items()
                    if node_id in self.nodes and community is not None
                ]},
            )
        if self.conflicts:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (a:Claim {id: row.a})
                MATCH (b:Claim {id: row.b})
                MERGE (a)-[r:CONTRADICTS]->(b)
                SET r.score = row.score, r.kind = row.kind, r.reasons = row.reasons,
                    r.provenance = 'claim-conflict-resolver', r.predicted = true
                """,
                {"rows": [
                    {"a": claim_id(conflict["claim_a"]), "b": claim_id(conflict["claim_b"]),
                     "score": conflict["score"], "kind": conflict.get("kind"),
                     "reasons": conflict.get("reasons", [])}
                    for conflict in self.conflicts
                ]},
            )
        if self.predicted_links:
            self.run_cypher(
                """
                UNWIND $rows AS row
                MATCH (a {id: row.source})
                MATCH (b {id: row.target})
                MERGE (a)-[r:PREDICTED_LINK]->(b)
                SET r.adamic_adar = row.adamic_adar, r.jaccard = row.jaccard,
                    r.common_neighbors = row.common_neighbors, r.predicted = true,
                    r.provenance = 'link-prediction'
                """,
                {"rows": self.predicted_links[:200]},
            )

    # ---------------------------------------------------------------- info
    def health(self) -> dict[str, Any]:
        from urllib.parse import urlsplit

        base = super().health()
        cfg = self.settings.neo4j
        try:
            parsed = urlsplit(cfg.uri)
            host = parsed.hostname or "configured"
            if parsed.port:
                host += f":{parsed.port}"
            safe_uri = f"{parsed.scheme}://{host}" if parsed.scheme else "configured"
        except ValueError:
            safe_uri = "configured"
        base.update({
            "engine": "neo4j" + ("+gds" if self.gds_available else ""),
            "neo4j_uri": safe_uri,
            "database": cfg.database,
            "connection_verified": self.connection_verified,
            "graph_projection": "Neo4j-hydrated local projection for Python/GraphRAG/GapEngine computations; Investigator evidence paths are checked against Neo4j with bounded parameterized Cypher.",
            "gds_available": self.gds_available,
            "query_timeout_seconds": cfg.query_timeout,
            "schema_warnings": list(self.schema_warnings),
            "cypher_statements": self.cypher_statements,
            "bootstrap": self.bootstrap_report,
        })
        return base

    def stats(self) -> dict[str, Any]:
        base = super().stats()
        base["engine"] = "neo4j" + ("+gds" if self.gds_available else "")
        return base

    # ---------------------------------------------------- cypher utilities
    def parameterised_examples(self) -> list[dict[str, str]]:
        """The queries shipped in `cypher/queries/`, surfaced for the UI/API docs."""
        from pathlib import Path

        directory = Path(__file__).resolve().parents[2] / "cypher" / "queries"
        if not directory.exists():
            return []
        return [
            {"name": path.stem, "query": path.read_text(encoding="utf-8").strip()}
            for path in sorted(directory.glob("*.cypher"))
        ]


def _slug(text: str) -> str:
    from backend.ingestion.graph_builder import _slug as slug

    return slug(text)
