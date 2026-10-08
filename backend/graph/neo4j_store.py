"""Neo4j implementation of the NEXUS graph store.

Neo4j is the **store of record**: `corpus → Cypher (UNWIND/MERGE, parameters only)
→ Neo4j`, and every algorithm either runs *inside* Neo4j (GDS, when the plugin is
available) or is computed on a hydrated working set and then written back, so a
plain Cypher query in Neo4j Browser sees exactly the numbers the UI shows:

    MATCH (p:Paper) RETURN p.title, p.pagerank, p.betweenness, p.community
    ORDER BY p.pagerank DESC LIMIT 10;

Why it subclasses the in-process store: the enrichment pipeline (embeddings,
similarity, community profiling, claim conflicts) is identical for both engines,
so the API, GraphRAG, agent and gap engine never need to know which one answered.
Only `load()`, the analytics step and the write-back differ. Nothing here contains
a credential or a concatenated Cypher string — values are always bound parameters.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Sequence

from backend.algorithms import kernel
from backend.config import get_settings
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
        self.cypher_statements = 0
        self.bootstrap_report: dict[str, Any] = {}

    # ------------------------------------------------------------- driver
    def connect(self):
        from neo4j import GraphDatabase  # imported lazily: the demo runs without the driver

        cfg = self.settings.neo4j
        if not cfg.password:
            raise Neo4jError("NEO4J_PASSWORD is empty — refusing to connect with no credential")
        self._driver = GraphDatabase.driver(
            cfg.uri,
            auth=(cfg.username, cfg.password),
            connection_timeout=cfg.timeout,
            max_connection_lifetime=300,
        )
        self._driver.verify_connectivity()
        with self._driver.session(database=cfg.database) as session:
            self.gds_available = False
            if cfg.use_gds:
                try:
                    record = session.run(
                        "CALL gds.version() YIELD gdsVersion RETURN gdsVersion"
                    ).single()
                    self.gds_available = bool(record)
                except Exception:  # noqa: BLE001 - GDS is optional
                    self.gds_available = False
        return self

    def run_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute one parameterised statement and return rows as dicts."""
        if self._driver is None:
            raise Neo4jError("Neo4j driver is not connected")
        self.cypher_statements += 1
        with self._driver.session(database=self.settings.neo4j.database) as session:
            result = session.run(query, params or {})
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
        for statement in statements:
            try:
                self.run_cypher(statement)
            except Exception as exc:  # noqa: BLE001 - constraints may need admin rights
                log.debug("schema statement skipped: %s (%s)", statement[:60], exc)

    # --------------------------------------------------------------- load
    def load(self) -> None:
        with self._lock:
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

    #: Relationship-type whitelist for the generic loader (no dynamic concatenation
    #: from user input: the value comes from a fixed tuple).
    def _load_corpus_into_neo4j(self) -> None:
        papers = self._corpus.get("papers", [])
        self.run_cypher(
            """
            UNWIND $rows AS row
            MERGE (p:Paper {id: row.id})
            SET p.name = row.title, p.title = row.title, p.abstract = row.summary,
                p.year = row.year, p.url = row.url, p.arxiv_id = row.arxiv_id,
                p.field = row.field, p.summary_source = row.summary_source,
                p.author_status = row.author_status, p.corpus_tier = row.corpus_tier
            """,
            {"rows": [{"id": f"paper:{p['arxiv_id']}", **{
                k: p.get(k) for k in ("title", "summary", "year", "url", "field",
                                      "summary_source", "author_status", "corpus_tier")
            }, "arxiv_id": p["arxiv_id"]} for p in papers]},
        )
        taxonomy = {
            "STUDIES": ("topics", "Topic"),
            "USES_METHOD": ("methods", "Method"),
            "USES_DATASET": ("datasets", "Dataset"),
        }
        for rel, (key, label) in taxonomy.items():
            rows = [
                {"paper": f"paper:{p['arxiv_id']}", "name": name, "id": f"{label.lower()}:{_slug(name)}",
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
            {"paper": f"paper:{p['arxiv_id']}", "name": author, "id": f"author:{_slug(author)}"}
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
                SET r.provenance = 'arxiv listing'
                """,
                {"rows": author_rows},
            )
        citation_rows = [
            {"source": f"paper:{c['source']}", "target": f"paper:{c['target']}",
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
        claim_rows = [
            {"id": c["id"], "paper": f"paper:{c['paper']}", "text": c["text"],
             "stance": c.get("stance", "reports"), "source": c.get("source", "corpus"),
             "provenance": json.dumps(c.get("provenance", {}))}
            for c in self._corpus.get("claims", [])
        ]
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
            return {"error": str(exc)}

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

    # ------------------------------------------------------------ analytics
    def _run_gds_analytics(self) -> None:
        """Prefer GDS for PageRank / Louvain / betweenness; else keep the native kernel."""
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
            # re-derive community nodes/profiles from the GDS partition
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
        except Exception as exc:  # noqa: BLE001 - fall back to the native kernel
            self.gds_available = False
            log.info("GDS analytics unavailable (%s) — keeping native-kernel metrics", exc)

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
                    {"id": p["id"], "name": p["name"], "index": p["community_index"],
                     "papers": p["paper_count"], "topics": p["top_topics"][:8], "rank": p.get("rank", 0)}
                    for p in self.community_profiles
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
                    {"a": c["claim_a"], "b": c["claim_b"], "score": c["score"],
                     "kind": c.get("kind"), "reasons": c.get("reasons", [])}
                    for c in self.conflicts
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
        base = super().health()
        cfg = self.settings.neo4j
        base.update({
            "engine": "neo4j" + ("+gds" if self.gds_available else ""),
            "neo4j_uri": cfg.uri,
            "database": cfg.database,
            "gds_available": self.gds_available,
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
        directory = self.settings.repo_root / "cypher" / "queries"
        examples = []
        if directory.exists():
            for path in sorted(directory.glob("*.cypher")):
                examples.append({"name": path.stem, "query": path.read_text(encoding="utf-8").strip()})
        return examples


def _slug(text: str) -> str:
    from backend.ingestion.graph_builder import _slug as slug

    return slug(text)
