"""NEXUS Research Gap Engine.

Given a topic (plus optional year range, field and minimum corpus size), the
engine looks for pairs of *concept clusters* inside the analyzed corpus that are
semantically close but structurally weakly connected, and reports them as
CANDIDATE research opportunities — never as established gaps.

Pipeline (§13 of the product spec):

    1. scope the corpus to the topic                (graph query)
    2. cluster its concepts                         (Louvain over concept graph)
    3. score every cluster pair                     (transparent heuristic)
    4. find bridge papers                           (papers touching both clusters)
    5. collect conflicting claims                   (claim-conflict resolver)
    6. list supporting evidence                     (ranked papers, with sources)
    7. emit hedged hypothesis text + candidate experiment
    8. attach methodology, limitations and a safety notice

The opportunity score is a *prototype heuristic*, published in full:

    30%  community separation    — do the two clusters sit apart in the graph?
    25%  semantic similarity     — are they conceptually close?
    20%  relationship sparsity   — how few direct links exist between them?
    15%  research activity       — how active is the area (recent paper share)?
    10%  bridge potential        — how structurally bridgeable are they?

Every component is returned with its raw value so the UI can show the maths and
a researcher can disagree with it.
"""

from __future__ import annotations

import logging
import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.algorithms import kernel
from backend.ingestion.claims import normalise

from backend.models.graph import GEdge, GNode
from backend.rag.embeddings import cosine

log = logging.getLogger("nexus.gaps")

SCORE_COMPONENT_EXPLANATIONS = {
    "community_separation": (
        "How strongly the two clusters sit in different Louvain communities, measured against the "
        "community structure of the scoped corpus."
    ),
    "semantic_similarity": (
        "Embedding cosine similarity between the clusters' topic centroids — related enough to be worth "
        "combining, distinct enough to be a real combination."
    ),
    "relationship_sparsity": (
        "How few recorded edges connect the clusters, compared with the density inside them."
    ),
    "research_activity": (
        "How much recent activity the clusters carry in the corpus: fertile areas with weak cross-talk "
        "are the interesting case."
    ),
    "bridge_potential": (
        "Whether any paper in the corpus already touches both clusters, and how much graph structure "
        "could carry a new connection."
    ),
}


def score_model() -> dict[str, object]:
    """The published opportunity-score model, in one inspectable place."""
    return {
        "scale": "0-100 per component; the total is the weighted sum, also 0-100",
        "label": "prototype heuristic — not a validated scientific metric",
        "components": [
            {
                "key": key,
                "label": SCORE_LABELS[key],
                "weight": f"{int(weight * 100)}%",
                "weight_value": weight,
                "explanation": SCORE_COMPONENT_EXPLANATIONS[key],
            }
            for key, weight in SCORE_WEIGHTS.items()
        ],
        "tie_break_rule": (
            "Results are ordered by 0.75 x opportunity score + 0.25 x query relevance, so the published "
            "score itself is never altered by the query wording."
        ),
        "safety_notice": SAFETY_NOTICE,
    }


def component_explanations() -> dict[str, str]:
    return dict(SCORE_COMPONENT_EXPLANATIONS)


SAFETY_NOTICE = (
    "This is an AI-generated hypothesis based on the analyzed research corpus and should be "
    "independently validated. It does not claim that nobody has researched this topic, and it "
    "cannot detect work outside the analyzed corpus."
)

SCORE_WEIGHTS = {
    "community_separation": 0.30,
    "semantic_similarity": 0.25,
    "relationship_sparsity": 0.20,
    "research_activity": 0.15,
    "bridge_potential": 0.10,
}

SCORE_LABELS = {
    "community_separation": "Community Separation",
    "semantic_similarity": "Semantic Similarity",
    "relationship_sparsity": "Relationship Sparsity",
    "research_activity": "Research Activity",
    "bridge_potential": "Bridge Potential",
}


class MemoryGraphStoreHelper:
    """Small holder so module-level helpers can share the query stopword set."""

    QUERY_STOP = {"research", "topic", "the", "of", "in", "and", "for", "on", "study", "studies"}


@dataclass(slots=True)
class ConceptCluster:
    id: int
    topics: list[str]
    methods: list[str]
    paper_ids: list[str]
    name: str
    label_counts: dict[str, int] = field(default_factory=dict)
    parent: str | None = None
    granularity: str = "cluster"

    def to_json(self, store: Any) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "topics": [store.nodes[t].name for t in self.topics if t in store.nodes][:10],
            "methods": [store.nodes[m].name for m in self.methods if m in store.nodes][:8],
            "paper_count": len(self.paper_ids),
            "label_counts": self.label_counts,
            "granularity": self.granularity,
            "parent": self.parent,
        }


@dataclass(slots=True)
class Opportunity:
    topic: str
    cluster_a: ConceptCluster
    cluster_b: ConceptCluster
    components: dict[str, float]
    score: float
    bridge_papers: list[str]
    evidence_papers: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    hypothesis: str
    why: list[str]
    experiment: str
    confidence: str
    confidence_reason: str
    trajectory: dict[str, Any] = field(default_factory=dict)
    relevance: float = 0.0

    def to_json(self, store: Any) -> dict[str, Any]:
        return {
            "id": f"gap:{self.cluster_a.id}-{self.cluster_b.id}:{self.topic}",
            "title": f"{self.cluster_a.name} × {self.cluster_b.name}",
            "topic": self.topic,
            "cluster_a": self.cluster_a.to_json(store),
            "cluster_b": self.cluster_b.to_json(store),
            "opportunity_score": round(self.score, 1),
            "score_components": [
                {
                    "key": key,
                    "label": SCORE_LABELS[key],
                    "value": round(value, 1),
                    "weight": f"{int(SCORE_WEIGHTS[key] * 100)}%",
                    "raw": round(self.components.get(f"{key}_raw", value / 100.0), 4),
                }
                for key, value in self.components.items()
                if key in SCORE_WEIGHTS
            ],
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "trajectory": self.trajectory,
            "query_relevance": self.relevance,
            "granularity": (
                self.cluster_a.granularity
                if self.cluster_a.granularity == self.cluster_b.granularity
                else f"{self.cluster_a.granularity}+{self.cluster_b.granularity}"
            ),
            "hypothesis": self.hypothesis,
            "why": self.why,
            "experiment": self.experiment,
            "bridge_papers": self.bridge_papers,
            "evidence_papers": self.evidence_papers,
            "conflicts": self.conflicts,
            "labels": {
                "score": "NEXUS Opportunity Score — prototype heuristic",
                "finding": "Potentially underexplored in the analyzed corpus",
                "status": "candidate research opportunity",
            },
        }


class GapEngine:
    """Detects candidate research opportunities inside the analyzed corpus."""

    def __init__(self, store: Any, analytics: Any | None = None) -> None:
        self.store = store
        self.cache: dict[tuple, dict[str, Any]] = {}
        self._neighbours_cache: dict[str, set[str]] = {}
        #: 90th-percentile similarity reference per concept set (scope-aware scaling)
        self._similarity_reference_cache: dict[frozenset, float] = {}
        self._scope_recent_share = 0.6

    # ------------------------------------------------------------- scoping
    #: Query tokens that must not drive topic matching on their own.
    QUERY_STOP = MemoryGraphStoreHelper.QUERY_STOP
    #: Aliases let "AI" reach the vocabulary the corpus actually uses.
    ALIASES = {
        "ai": {"llm", "language", "model", "agent", "intelligent", "artificial"},
        "agentic": {"agent", "agents"},
        "llm": {"language", "model", "ai"},
        "rl": {"reinforcement", "learning"},
    }

    @staticmethod
    def _norm_tokens(text: str) -> set[str]:
        from backend.ingestion.claims import normalise, tokenize

        return {normalise(t) for t in tokenize(text) if t not in MemoryGraphStoreHelper.QUERY_STOP}

    def scope(
        self,
        topic: str | None,
        year_min: int | None = None,
        year_max: int | None = None,
        field: str | None = None,
        min_papers: int = 3,
    ) -> tuple[list[str], list[str], list[str]]:
        """Resolve the user's topic string to a set of papers/topics/methods.

        Topic resolution is scored rather than string-matched, so "AI Agents"
        reaches "LLM Agents", "Multi-Agent Systems" and "Agent Memory" — and the
        resolution itself is reported back to the user for inspection.
        """
        needle = (topic or "").strip().lower()
        paper_ids: set[str] = set()
        topic_ids: set[str] = set()
        self.last_resolution = {"query": topic, "matched_topics": [], "strategy": "all-papers"}

        if needle:
            query_tokens = self._norm_tokens(needle)
            ordered_tokens = [t for t in re.split(r"[^a-z0-9]+", needle.lower()) if t and t not in MemoryGraphStoreHelper.QUERY_STOP]
            last_token = normalise(ordered_tokens[-1]) if ordered_tokens else ""
            expanded = set(query_tokens)
            for token in list(query_tokens):
                expanded |= self.ALIASES.get(token, set())

            scored: list[tuple[float, str, str]] = []
            for node in self.store.nodes.values():
                if node.label != "Topic":
                    continue
                name_lower = node.name.lower()
                node_tokens = self._norm_tokens(node.name)
                if not node_tokens:
                    continue
                if needle in name_lower:
                    score = 1.0
                else:
                    direct = len(query_tokens & node_tokens) / max(1, len(query_tokens))
                    alias_hit = 1.0 if (expanded - query_tokens) & node_tokens else 0.0
                    # Multi-word queries: the head noun (last token) matching the
                    # topic name is a strong signal ("Agent Memory" -> "Memory
                    # Management"), a single shared generic token is not.
                    head_bonus = 0.3 if query_tokens and last_token in node_tokens else 0.0
                    score = min(1.0, direct + (0.25 * alias_hit if direct > 0 else 0.0) + head_bonus)
                threshold = 0.75 if len(query_tokens) >= 2 else 0.5
                if score >= threshold:
                    scored.append((score, node.id, node.name))

            if not scored:
                # fall back to a text match over papers (title + editorial summary)
                for node in self.store.nodes.values():
                    if node.label != "Paper":
                        continue
                    haystack = f"{node.name} {node.props.get('abstract', '')}".lower()
                    if needle in haystack or query_tokens & self._norm_tokens(haystack):
                        paper_ids.add(node.id)
                self.last_resolution["strategy"] = "text-match"
            else:
                scored.sort(key=lambda t: (-t[0], t[2]))
                topic_ids = {node_id for _s, node_id, _n in scored}
                self.last_resolution["strategy"] = "scored-topic-resolution"
                self.last_resolution["matched_topics"] = [name for _s, _i, name in scored[:14]]

        for node_id in list(topic_ids):
            for other, rel in self._edges_of(node_id, "STUDIES"):
                if other.startswith("paper:"):
                    paper_ids.add(other)

        if not needle:
            paper_ids = {n.id for n in self.store.nodes.values() if n.label == "Paper"}

        if field:
            paper_ids = {p for p in paper_ids if self.store.nodes[p].props.get("field") == field}

        if year_min is not None:
            paper_ids = {p for p in paper_ids if (self.store.nodes[p].props.get("year") or 0) >= year_min}
        if year_max is not None:
            paper_ids = {p for p in paper_ids if (self.store.nodes[p].props.get("year") or 9999) <= year_max}

        concept_ids = set(topic_ids)
        for pid in paper_ids:
            for other, _rel in self._edges_of(pid, "STUDIES"):
                if other.startswith("topic:"):
                    concept_ids.add(other)

        if len(paper_ids) < min_papers:
            return [], sorted(concept_ids), []

        method_ids = set()
        for pid in paper_ids:
            for other, _rel in self._edges_of(pid, "USES_METHOD"):
                if other.startswith("method:"):
                    method_ids.add(other)
        return sorted(paper_ids), sorted(concept_ids), sorted(method_ids)

    def _edges_of(self, node_id: str, rel: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for e in self.store.edges:
            if e.type != rel:
                continue
            if e.src == node_id:
                out.append((e.dst, "out"))
            elif e.dst == node_id:
                out.append((e.src, "in"))
        return out

    # --------------------------------------------------------- clustering
    def _concept_graph(self, concept_ids: Sequence[str]) -> tuple[list[str], list[tuple[str, str, float]]]:
        # Concepts only: a paper's *field* label ("Agent Architecture") is a corpus
        # taxonomy tag, not a research concept, and including it glues unrelated
        # clusters together. Field labels still scope the analysis, they just do
        # not define clusters.
        id_set = {
            cid
            for cid in concept_ids
            if not (
                cid.startswith("topic:")
                and self.store.nodes.get(cid) is not None
                and self.store.nodes[cid].props.get("topic_kind") == "field"
            )
        }
        edge_map: dict[tuple[str, str], float] = {}
        for e in self.store.edges:
            if e.type == "RELATED_TO" and e.src in id_set and e.dst in id_set:
                key = (e.src, e.dst) if e.src < e.dst else (e.dst, e.src)
                edge_map[key] = max(edge_map.get(key, 0.0), float(e.props.get("weight", 1.0)))
            elif e.type == "SIMILAR_TO" and e.src in id_set and e.dst in id_set:
                key = (e.src, e.dst) if e.src < e.dst else (e.dst, e.src)
                edge_map[key] = max(edge_map.get(key, 0.0), float(e.props.get("similarity", 0.5)) * 0.8)

        # embedding-similarity fusion keeps clusters meaningful in small scopes
        vectors = {i: self.store.embeddings[i] for i in concept_ids if i in self.store.embeddings}
        ids = list(vectors)
        if ids:
            neighbours, _engine = self.store.analytics_engine.semantic_neighbours(vectors, k=4, min_similarity=0.35)
            for src, pairs in neighbours.items():
                for dst, sim in pairs:
                    key = (src, dst) if src < dst else (dst, src)
                    edge_map[key] = max(edge_map.get(key, 0.0), sim * 0.7)

        edges = [(a, b, w) for (a, b), w in edge_map.items() if w > 0]
        return list(concept_ids), edges

    def clusters(self, concept_ids: Sequence[str], paper_ids: Sequence[str]) -> list[ConceptCluster]:
        ids, edges = self._concept_graph(concept_ids)
        if len(ids) < 2:
            return []
        communities, _engine = kernel.louvain(ids, edges, resolution=1.2)

        grouped: dict[int, list[str]] = defaultdict(list)
        for node_id, comm in communities.items():
            grouped[comm].append(node_id)

        clusters: list[ConceptCluster] = []
        for comm, members in grouped.items():
            topics = [m for m in members if m.startswith("topic:")]
            methods = [m for m in members if m.startswith("method:")]
            papers: set[str] = set()
            scoped = set(paper_ids)
            for member in members:
                for other, _rel in self._edges_of(member, "STUDIES"):
                    if other in scoped:
                        papers.add(other)
                for other, _rel in self._edges_of(member, "USES_METHOD"):
                    if other in scoped:
                        papers.add(other)
            if len(topics) < 2 or len(papers) < 3:
                continue
            labels = Counter(self.store.nodes[m].label for m in members)
            name_topics = self._name_cluster(topics, papers, members)
            clusters.append(
                ConceptCluster(
                    id=comm,
                    topics=topics,
                    methods=methods,
                    paper_ids=sorted(papers),
                    name=name_topics,
                    label_counts=dict(labels),
                )
            )
        # Fine-grained pass: a single large cluster can hide two weakly connected
        # research threads. Re-running Louvain *inside* the cluster reveals them,
        # which is what lets "Agent Memory" and "Multi-Agent Systems" show up as
        # separate candidates even when they share a coarse community.
        for cluster in list(clusters):
            if len(cluster.topics) < 12:
                continue
            sub_ids = [t for t in cluster.topics]
            _ids, sub_edges = self._concept_graph(sub_ids)
            if len(sub_ids) < 4 or not sub_edges:
                continue
            sub_comm, _engine = kernel.louvain(sub_ids, sub_edges, resolution=1.35)
            groups: dict[int, list[str]] = defaultdict(list)
            for node_id, comm in sub_comm.items():
                groups[comm].append(node_id)
            if len(groups) < 2:
                continue
            made = 0
            for comm, members_sub in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                sub_topics = [m for m in members_sub if m.startswith("topic:")]
                sub_methods = [m for m in members_sub if m.startswith("method:")]
                if len(sub_topics) < 3:
                    continue
                sub_papers: set[str] = set()
                scoped = set(paper_ids)
                for member in members_sub:
                    for other, _rel in self._edges_of(member, "STUDIES"):
                        if other in scoped:
                            sub_papers.add(other)
                    for other, _rel in self._edges_of(member, "USES_METHOD"):
                        if other in scoped:
                            sub_papers.add(other)
                if len(sub_papers) < 4:
                    continue
                name = self._name_cluster(sub_topics, sub_papers, members_sub)
                if not name or name == "Mixed concepts":
                    continue
                clusters.append(
                    ConceptCluster(
                        id=comm + 1000,  # keep ids unique across granularities
                        topics=sub_topics,
                        methods=sub_methods,
                        paper_ids=sorted(sub_papers),
                        name=name,
                        label_counts=dict(Counter(self.store.nodes[m].label for m in members_sub)),
                        parent=cluster.name,
                        granularity="sub-cluster",
                    )
                )
                made += 1
                if made >= 3:
                    break

        # de-duplicate very similar clusters (same name or near-identical papers)
        seen: list[ConceptCluster] = []
        for cluster in sorted(clusters, key=lambda c: (c.granularity == "sub-cluster", -len(c.paper_ids))):
            duplicate = False
            for kept in seen:
                if kept.granularity != cluster.granularity:
                    continue  # a sub-cluster is *meant* to overlap its parent cluster
                if kept.name == cluster.name:
                    duplicate = True
                    break
                overlap = len(set(kept.paper_ids) & set(cluster.paper_ids))
                if overlap / max(1, min(len(kept.paper_ids), len(cluster.paper_ids))) > 0.8:
                    duplicate = True
                    break
            if not duplicate:
                seen.append(cluster)
        return seen[:12]

    def _name_cluster(self, topics: Sequence[str], papers: Iterable[str], members: Sequence[str] = ()) -> str:
        """Name a cluster from the topics its papers actually use.

        Primary key is in-cluster paper support (a topic must be used by >=3 of the
        cluster's papers to name it); distinctiveness against the wider corpus is
        the tiebreaker. Field-taxonomy tags are excluded so clusters are named
        after research concepts rather than corpus bookkeeping.
        """
        paper_list = list(papers)
        paper_total = max(1, len(paper_list))
        topic_paper_counts: Counter[str] = Counter()
        for pid in paper_list:
            for other, _rel in self._edges_of(pid, "STUDIES"):
                topic_paper_counts[other] += 1

        global_counts: Counter[str] = Counter()
        for node in self.store.nodes.values():
            if node.label == "Paper":
                for other, _rel in self._edges_of(node.id, "STUDIES"):
                    global_counts[other] += 1

        scored: list[tuple[float, int, str]] = []
        for topic in topics:
            node = self.store.nodes.get(topic)
            if node is None or node.props.get("topic_kind") == "field":
                continue
            if node.name.lower() in {"ai agents", "llm agents", "survey", "agents", "agent architecture"}:
                continue
            local = topic_paper_counts.get(topic, 0)
            if local < 3:
                continue
            total = max(1, global_counts.get(topic, 1))
            # Support inside the cluster comes first: a topic shared by half the
            # cluster describes it, while a topic used by three papers does not —
            # even when those three papers use it exclusively. Exclusivity against
            # the wider corpus is the secondary signal.
            support = local / paper_total
            exclusivity = local / total
            distinctiveness = support * (0.5 + 0.5 * exclusivity) * math.log(2 + local)
            scored.append((distinctiveness, local, node.name))

        scored.sort(key=lambda t: (-t[0], -t[1]))
        names: list[str] = []
        chosen_tokens: list[set[str]] = []
        for _d, _l, name in scored:
            tokens = self._norm_tokens(name)
            if not tokens:
                continue
            if any(tokens <= other or other <= tokens for other in chosen_tokens):
                continue  # "Tool Use" adds nothing after "Tool Use and APIs"
            names.append(name)
            chosen_tokens.append(tokens)
            if len(names) >= 3:
                break
        if names:
            return " · ".join(names) if len(names) > 1 else names[0]

        # fallback: whatever concepts the cluster does contain (still real names)
        fallback = [
            self.store.nodes[m].name
            for m in members
            if m in self.store.nodes and self.store.nodes[m].label in {"Topic", "Method"}
        ][:3]
        if fallback:
            return " · ".join(fallback)
        papers = [m for m in members if m.startswith("paper:") and m in self.store.nodes]
        if papers:
            anchor = max(papers, key=lambda p: self.store.analytics.pagerank.get(p, 0.0) if self.store.analytics else 0.0)
            return f"{self.store.nodes[anchor].name} (cluster)"
        return "Mixed concepts"

    # ------------------------------------------------------------ scoring
    def _topic_candidates(
        self,
        topic_ids: Sequence[str],
        paper_ids: Sequence[str],
        scoped_pagerank: dict[str, float],
        limit: int = 14,
        min_papers: int = 3,
    ) -> list[ConceptCluster]:
        """Single-concept candidates: the precise pairs a researcher can verify by hand.

        Broad corpus labels *are* allowed here. A field-style label ("Agent Memory")
        is often the exact concept the user asked about, and pairing it with a
        specific concept ("Evaluation") is a meaningful, checkable candidate. The
        naming pass still refuses to name a whole cluster after a field label, but a
        pair of concepts is a different object from a cluster name.
        """
        scoped = set(paper_ids)
        floor = max(3, min_papers)
        candidates: list[ConceptCluster] = []
        ranked = sorted(
            (t for t in topic_ids if t in self.store.nodes),
            key=lambda t: -scoped_pagerank.get(t, 0.0),
        )
        for topic in ranked:
            node = self.store.nodes[topic]
            papers: set[str] = set()
            for other, _rel in self._edges_of(topic, "STUDIES"):
                if other in scoped:
                    papers.add(other)
            if len(papers) < floor:
                continue
            candidates.append(
                ConceptCluster(
                    id=-(len(candidates) + 1),
                    topics=[topic],
                    methods=[],
                    paper_ids=sorted(papers),
                    name=node.name,
                    label_counts={"Topic": 1},
                    granularity="topic",
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    def _pair_rejection(self, a: ConceptCluster, b: ConceptCluster) -> str | None:
        """Reject a candidate pair that cannot honestly be shown as a research gap.

        Three cases are filtered, and the reason is reported in the payload so the
        filter is auditable rather than silent:

          * **nested concepts** — one cluster's name contains the other's
            (``Chain-of-Thought`` x ``Chain-of-Thought Prompting``). Those clusters
            are parent and child, not two separate areas.
          * **entangled clusters** — most papers of the smaller cluster also study
            the other concept (``Safety`` x ``Security`` in a corpus where safety
            topics are studied by the security literature). A "missing bridge" would
            be a false positive: the bridge is inside the papers already.
          * **identical vocabularies** — two distinct node ids that describe the same
            concept words.
        """
        name_a, name_b = self._norm_tokens(a.name), self._norm_tokens(b.name)
        if name_a and name_b and (name_a <= name_b or name_b <= name_a):
            return f"'{a.name}' and '{b.name}' are nested concepts, not two competing areas"
        if name_a and name_a == name_b:
            return f"'{a.name}' and '{b.name}' describe the same concept vocabulary"
        smaller, other = (set(a.paper_ids), set(b.paper_ids)) if len(a.paper_ids) <= len(b.paper_ids) else (set(b.paper_ids), set(a.paper_ids))
        if smaller:
            shared = len(smaller & other) / len(smaller)
            if shared >= 0.35:
                return (
                    f"{shared:.0%} of the papers in the smaller cluster also study the other concept, "
                    "so the two are entangled rather than separated"
                )
        return None

    def _cluster_pair_features(
        self,
        a: ConceptCluster,
        b: ConceptCluster,
        paper_ids: Sequence[str],
        scoped_pagerank: dict[str, float],
        scoped_betweenness: dict[str, float],
        scoped_communities: dict[str, int],
    ) -> dict[str, float]:
        """Raw, interpretable features for a cluster pair.

        Every component is on a meaningful 0..1 scale *by itself* — there is no
        cross-pair normalisation, so a score of 78 means the same thing whether
        the user searched for "AI Agents" or "Computer Vision".
        """
        a_ids, b_ids = set(a.topics), set(b.topics)
        paper_set = set(paper_ids)
        members_a = set(a.paper_ids) & paper_set
        members_b = set(b.paper_ids) & paper_set

        if len(a_ids) == 1 and len(b_ids) == 1 and a_ids == b_ids:
            return {}
        # --- cross edges in the concept graph (related/similar/predicted)
        cross = 0.0
        for e in self.store.edges:
            if e.type not in {"RELATED_TO", "SIMILAR_TO", "PREDICTED_LINK"}:
                continue
            if (e.src in a_ids and e.dst in b_ids) or (e.src in b_ids and e.dst in a_ids):
                cross += float(e.props.get("weight", e.props.get("similarity", 0.5)) or 0.5)
        # Sparsity is measured *relative to within-cluster density*, which is what
        # "the two clusters do not talk to each other" actually means.
        possible = max(1.0, float(len(a_ids) * len(b_ids)))
        cross_density = cross / possible
        internal = 0.5 * (self._internal_density(a_ids) + self._internal_density(b_ids))
        sparsity = 1.0 - min(1.0, (cross_density / (internal * 0.5)) if internal > 0 else 0.0)

        # --- semantic similarity: embedding cosine fused with shared neighbourhood
        sims: list[float] = []
        for x in a_ids:
            vx = self.store.embeddings.get(x)
            if not vx:
                continue
            for y in b_ids:
                vy = self.store.embeddings.get(y)
                if vy:
                    sims.append(cosine(vx, vy))
        embed_sim = sum(sims) / len(sims) if sims else 0.0
        neighbours_a = self._neighbourhood(a_ids)
        neighbours_b = self._neighbourhood(b_ids)
        union = neighbours_a | neighbours_b
        jaccard = len(neighbours_a & neighbours_b) / len(union) if union else 0.0
        # Scale similarity against the scope's own similarity distribution: a pair
        # is "semantically close" relative to how close concept pairs typically are
        # in this topic. Both the raw cosine and the reference percentile are
        # reported, so the number can always be audited.
        embed_ref = self._scope_similarity_reference(a_ids | b_ids)
        semantic = 0.6 * min(1.0, embed_sim / embed_ref) + 0.4 * min(1.0, jaccard / 0.6)

        # --- community separation: overlap of the two clusters' community distributions
        separation = 1.0
        if members_a and members_b:
            dist_a: Counter = Counter(scoped_communities.get(p, -1) for p in members_a)
            dist_b: Counter = Counter(scoped_communities.get(p, -1) for p in members_b)
            total_a = sum(dist_a.values()) or 1
            total_b = sum(dist_b.values()) or 1
            shared_mass = sum(min(dist_a[c] / total_a, dist_b[c] / total_b) for c in set(dist_a) | set(dist_b))
            separation = 1.0 - shared_mass

        # --- research activity: recent share of the two clusters
        years = [self.store.nodes[p].props.get("year") or 0 for p in (members_a | members_b)]
        recent = sum(1 for y in years if y >= 2023)
        scope_recent_share = getattr(self, "_scope_recent_share", 0.6)
        activity = min(1.0, (recent / len(years)) / max(0.05, scope_recent_share) * 0.75) if years else 0.0

        # --- bridge potential: how central are these papers inside the scope?
        bridge_ids = [
            pid
            for pid in paper_ids
            if ({t for t, _r in self._edges_of(pid, "STUDIES")} & a_ids)
            and ({t for t, _r in self._edges_of(pid, "STUDIES")} & b_ids)
        ]
        nodes = sorted(members_a | members_b)
        if nodes:
            # Percentile rank inside the scope: "are these papers structurally
            # central *for this topic*?" — robust to scale, unlike raw values.
            bc_rank = self._percentile_map(scoped_betweenness)
            pr_rank = self._percentile_map(scoped_pagerank)
            bc_mean = sum(scoped_betweenness.get(n, 0.0) for n in nodes) / len(nodes)
            pr_mean = sum(scoped_pagerank.get(n, 0.0) for n in nodes) / len(nodes)
            rank_mean = 0.5 * (
                sum(bc_rank.get(n, 0.0) for n in nodes) / len(nodes)
                + sum(pr_rank.get(n, 0.0) for n in nodes) / len(nodes)
            )
            # +0.15 when the corpus already contains a paper touching both clusters:
            # the structure to carry a new connection exists.
            bridge_potential = min(1.0, rank_mean + (0.15 if bridge_ids else 0.0))
        else:
            bc_mean = pr_mean = bridge_potential = 0.0

        # --- temporal trajectory: when did bridging actually happen?
        bridge_years = Counter(self.store.nodes[p].props.get("year") or 0 for p in bridge_ids)
        recent_bridges = sum(c for y, c in bridge_years.items() if y >= 2024)
        older_bridges = sum(c for y, c in bridge_years.items() if 0 < y < 2024)

        return {
            "community_separation": separation,
            "semantic_similarity": semantic,
            "relationship_sparsity": sparsity,
            "research_activity": activity,
            "bridge_potential": min(1.0, bridge_potential),
            "cross_edge_weight": cross,
            "bridge_papers": float(len(bridge_ids)),
            "bridge_papers_recent": float(recent_bridges),
            "bridge_papers_older": float(older_bridges),
            "bridge_year_histogram": dict(sorted(bridge_years.items())),
            "embedded_similarity": embed_sim,
            "neighbourhood_jaccard": jaccard,
            "avg_betweenness": bc_mean,
            "avg_pagerank": pr_mean,
            "recent_papers": float(recent),
            "scoped_papers": float(len(years)),
            "shared_papers": float(len(members_a & members_b)),
        }

    def _internal_density(self, ids: set[str]) -> float:
        """Observed edge weight between members, per possible member pair."""
        if len(ids) < 2:
            return 0.0
        weight = 0.0
        for e in self.store.edges:
            if e.type not in {"RELATED_TO", "SIMILAR_TO"}:
                continue
            if e.src in ids and e.dst in ids:
                weight += float(e.props.get("weight", e.props.get("similarity", 0.5)) or 0.5)
        possible = len(ids) * (len(ids) - 1) / 2
        return weight / possible if possible else 0.0

    def _scope_similarity_reference(self, ids: set[str]) -> float:
        """90th-percentile concept-pair cosine inside the scope (similarity scale)."""
        ids = {i for i in ids if i in self.store.embeddings}
        if len(ids) < 2:
            return 0.35
        cached = self._similarity_reference_cache.get(frozenset(ids))
        if cached is not None:
            return cached
        values: list[float] = []
        id_list = sorted(ids)
        for i, x in enumerate(id_list):
            for y in id_list[i + 1:]:
                values.append(cosine(self.store.embeddings[x], self.store.embeddings[y]))
        values.sort()
        ref = values[int(len(values) * 0.9)] if values else 0.35
        ref = max(0.12, min(0.85, ref))
        self._similarity_reference_cache[frozenset(ids)] = ref
        if len(self._similarity_reference_cache) > 64:
            self._similarity_reference_cache.pop(next(iter(self._similarity_reference_cache)))
        return ref

    def _query_relevance(self, cluster: "ConceptCluster", query_tokens: set[str], expanded: set[str]) -> float:
        """How close is this cluster to the user's query? (0..1, used for ranking only)."""
        if not query_tokens:
            return 1.0
        node_tokens: set[str] = set()
        for topic in cluster.topics:
            node = self.store.nodes.get(topic)
            if node:
                node_tokens |= self._norm_tokens(node.name)
        if not node_tokens:
            return 0.2
        direct = len(query_tokens & node_tokens) / len(query_tokens)
        alias = len((expanded - query_tokens) & node_tokens) / max(1, len(expanded - query_tokens)) if expanded else 0.0
        return max(0.0, min(1.0, direct + 0.5 * alias))

    @staticmethod
    def _percentile_map(values: dict[str, float]) -> dict[str, float]:
        if not values:
            return {}
        ordered = sorted(values.items(), key=lambda kv: kv[1])
        n = len(ordered)
        return {key: (idx / max(1, n - 1)) for idx, (key, _v) in enumerate(ordered)}

    def _neighbourhood(self, ids: set[str]) -> set[str]:
        out: set[str] = set()
        for node_id in ids:
            for other, _rel in self._edges_of(node_id, "RELATED_TO"):
                out.add(other)
            for other, _rel in self._edges_of(node_id, "RESEARCH_AFFINITY"):
                out.add(other)
        return out - ids

    # -------------------------------------------------------------- main
    def find_gaps(
        self,
        topic: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        field: str | None = None,
        min_papers: int = 3,
        top_k: int = 5,
        max_clusters: int = 8,
    ) -> dict[str, Any]:
        key = (topic, year_min, year_max, field, min_papers, top_k)
        if key in self.cache:
            return self.cache[key]

        paper_ids, topic_ids, method_ids = self.scope(topic, year_min, year_max, field, min_papers)
        store = self.store
        assert store.analytics is not None

        base = {
            "topic": topic or "all research in the corpus",
            "scope": {
                "papers": len(paper_ids),
                "topics": len(topic_ids),
                "methods": len(method_ids),
                "year_min": year_min,
                "year_max": year_max,
                "field": field,
                "min_papers": min_papers,
            },
            "resolution": getattr(self, "last_resolution", {}),
            "methodology": [
                "Scope the corpus to the requested topic, field and year range (graph query).",
                "Cluster the scope's concepts with Louvain community detection over the concept graph "
                "(co-occurrence edges fused with embedding similarity).",
                "For every cluster pair, measure separation, similarity, sparsity, activity and bridge potential.",
                "Score with the published NEXUS heuristic (30/25/20/15/10) and rank.",
                "Sparsity is measured against within-cluster density and similarity against the scope's own "
                "similarity distribution, both reported as raw values next to their normalised score.",
                "Attach the evidence: papers, bridge papers and any detected claim conflicts.",
            ],
            "score_formula": {SCORE_LABELS[k]: f"{int(v * 100)}%" for k, v in SCORE_WEIGHTS.items()},
            "score_label": "NEXUS Opportunity Score — prototype heuristic",
            "safety_notice": SAFETY_NOTICE,
            "limitations": [
                "The analyzed corpus is a curated demo slice; absence of connections here is not absence of "
                "connections in the literature.",
                "Similarity uses an embedding model plus graph co-occurrence; both can be wrong.",
                "Cluster names are generated from the most distinctive topics and are descriptive, not canonical.",
            ],
            "generated_at": None,  # filled by the API layer with a real timestamp
            "engine": {
                "graph": store.engine_name,
                "analytics": store.analytics.engines,
                "embeddings": store.embedding_engine,
                "claims": getattr(store, "claim_engine", "unknown"),
            },
        }

        filtered: list[dict[str, str]] = []

        def no_candidates(reason: str) -> dict[str, Any]:
            """Empty result, but never an empty *explanation*."""
            base.update(
                {
                    "opportunities": [],
                    "clusters": base.get("clusters", []),
                    "reason": reason,
                    "filtered_pairs": filtered,
                    "ranking": {
                        "method": "ranked by the published NEXUS Opportunity Score",
                        "candidates_considered": 0,
                    },
                    "score_distribution": {
                        "candidates_considered": 0,
                        "returned": 0,
                        "max": None,
                        "median": None,
                    },
                }
            )
            self.cache[key] = base
            return base

        if len(paper_ids) < min_papers:
            return no_candidates("scope too small for cluster analysis")

        # recompute structure *within the scope* so clusters reflect the user's topic
        scoped_nodes = [store.nodes[p] for p in paper_ids if p in store.nodes]
        scoped_nodes += [store.nodes[t] for t in topic_ids if t in store.nodes]
        scoped_edges = [
            e
            for e in store.edges
            if e.src in {n.id for n in scoped_nodes} and e.dst in {n.id for n in scoped_nodes}
        ]
        scoped_ids, scoped_proj = store.analytics_engine._project(scoped_nodes, scoped_edges)
        scoped_pr, _pr_engine = kernel.pagerank(scoped_ids, scoped_proj)
        scoped_bc, _bc_engine = kernel.betweenness(
            scoped_ids, scoped_proj, samples=0 if len(scoped_ids) < 400 else 200
        )

        scope_years = [store.nodes[p].props.get("year") or 0 for p in paper_ids]
        self._scope_recent_share = (
            sum(1 for y in scope_years if y >= 2023) / len(scope_years) if scope_years else 0.6
        )
        clusters = self.clusters(topic_ids, paper_ids)[:max_clusters]
        # Topic-level candidates: precise concept pairs (e.g. "Agent Memory" x
        # "Multi-Agent Systems"). These are the most actionable outputs and the
        # easiest for a researcher to verify by hand, so they are scored with the
        # same published formula as the cluster-level candidates.
        clusters = clusters + self._topic_candidates(topic_ids, paper_ids, scoped_pr, min_papers=min_papers)
        # A concept reached by both the cluster pass and the precise topic pass
        # would otherwise appear twice under the same name.
        seen_names: set[str] = set()
        deduped = []
        for cluster in sorted(clusters, key=lambda c: (-len(c.paper_ids), c.name)):
            key = cluster.name.strip().lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            deduped.append(cluster)
        clusters = deduped
        base["clusters"] = [c.to_json(store) for c in clusters]

        scoped_communities, _lv_engine = kernel.louvain(scoped_ids, scoped_proj, resolution=1.0)
        features: dict[tuple[int, int], dict[str, float]] = {}
        for i, a in enumerate(clusters):
            for b in clusters[i + 1:]:
                if a.name == b.name:  # same concept cannot be a pair with itself
                    continue
                if set(a.topics) & set(b.topics):
                    # A pair must be disjoint: otherwise a coarse cluster is being
                    # paired with one of its own members ("X · Y" x "X").
                    filtered.append({"a": a.name, "b": b.name, "reason": "the two clusters share a topic, so they overlap"})
                    continue
                rejection = self._pair_rejection(a, b)
                if rejection:
                    filtered.append({"a": a.name, "b": b.name, "reason": rejection})
                    continue
                features[(a.id, b.id)] = self._cluster_pair_features(
                    a, b, paper_ids, scoped_pr, scoped_bc, scoped_communities
                )

        if not features:
            return no_candidates(
                "no pair of concept clusters in this scope is separated enough to be a candidate "
                "(see filtered_pairs for what was excluded and why)"
            )

        query_tokens = self._norm_tokens(topic or "")
        expanded = set(query_tokens)
        for token in list(query_tokens):
            expanded |= self.ALIASES.get(token, set())

        opportunities: list[Opportunity] = []
        lookup = {c.id: c for c in clusters}
        relevance: dict[tuple[int, int], float] = {}
        for key_pair, feats in features.items():
            # Absolute scale: each component is already 0..1 with a fixed meaning.
            components = {comp: feats[comp] * 100.0 for comp in SCORE_WEIGHTS}
            for comp in SCORE_WEIGHTS:
                components[f"{comp}_raw"] = feats[comp]
            score = sum(components[comp] * SCORE_WEIGHTS[comp] for comp in SCORE_WEIGHTS)

            a, b = lookup[key_pair[0]], lookup[key_pair[1]]
            bridges = self._bridge_papers(a, b, paper_ids)
            evidence = self._evidence_papers(a, b, bridges, scoped_pr)
            conflicts = self._conflicts_for(a, b)
            confidence, reason = self._confidence(score, feats, bridges, conflicts)
            trajectory = self._trajectory(feats)

            rel = 0.5 * (
                self._query_relevance(a, query_tokens, expanded)
                + self._query_relevance(b, query_tokens, expanded)
            )
            relevance[key_pair] = rel
            opportunities.append(
                Opportunity(
                    topic=base["topic"],
                    cluster_a=a,
                    cluster_b=b,
                    components=components,
                    score=score,
                    bridge_papers=bridges,
                    evidence_papers=evidence,
                    conflicts=conflicts,
                    hypothesis=self._hypothesis(a, b, feats, bridges),
                    why=self._why(a, b, feats, components),
                    experiment=self._experiment(a, b, feats),
                    confidence=confidence,
                    confidence_reason=reason,
                    trajectory=trajectory,
                )
            )

        # Ordering: the published score decides, and the candidate's closeness to
        # the user's wording only breaks ties inside a 2.5-point window ("Agent
        # Memory" surfaces memory-first without the score ever being rewritten).
        TIE_WINDOW = 2.5

        def rank_key(item: tuple[Opportunity, float]) -> tuple[float, float]:
            opp, rel = item
            return (-round(opp.score / TIE_WINDOW), -rel)

        ranked = sorted(
            ((opp, relevance.get((opp.cluster_a.id, opp.cluster_b.id), 0.0)) for opp in opportunities),
            key=rank_key,
        )
        for opp, rel in ranked:
            opp.relevance = round(rel, 3)
        # final de-duplication on the unordered pair of cluster names (the same
        # concept pair can be produced at two granularities; keep the best score)
        deduped: list[Opportunity] = []
        seen_pairs: set[tuple[str, str]] = set()
        for opp, _rel in ranked:
            key_pair_names = tuple(sorted((opp.cluster_a.name, opp.cluster_b.name)))
            if key_pair_names in seen_pairs:
                continue
            seen_pairs.add(key_pair_names)
            deduped.append(opp)
        payload_rows = []
        for rank, opp in enumerate(deduped[:top_k], start=1):
            row = opp.to_json(store)
            row["rank"] = rank
            payload_rows.append(row)
        base["opportunities"] = payload_rows
        base["ranking"] = {
            "method": "ranked by the published NEXUS Opportunity Score",
            "tie_break": f"candidates within {TIE_WINDOW} points are ordered by closeness to the user's wording "
            "(query relevance); the score itself is never altered",
            "relevance_definition": "token/alias overlap between the query and the candidate's cluster names",
            "note": "Because relevance breaks ties inside the window, rank 1 is not always the numerically highest "
            "score in the payload; the published score of every candidate is unchanged.",
        }
        base["filtered_pairs"] = filtered[:24]
        # Statistics are reported over the de-duplicated candidate set — the numbers the
        # ranking actually ordered — so "max" can never disagree with what is displayed.
        unique_scores = sorted(o.score for o in deduped)
        base["score_distribution"] = {
            "candidates_considered": len(opportunities),
            "unique_candidates": len(unique_scores),
            "returned": len(payload_rows),
            "max": round(unique_scores[-1], 1),
            "median": round(unique_scores[len(unique_scores) // 2], 1),
            "rank_1_score": round(payload_rows[0]["opportunity_score"], 1) if payload_rows else None,
        }
        self.cache[key] = base
        if len(self.cache) > 24:
            self.cache.pop(next(iter(self.cache)))
        return base

    # -------------------------------------------------------- evidence
    def _bridge_papers(self, a: ConceptCluster, b: ConceptCluster, paper_ids: Sequence[str]) -> list[str]:
        a_ids, b_ids = set(a.topics), set(b.topics)
        out = []
        for pid in paper_ids:
            topics = {t for t, _r in self._edges_of(pid, "STUDIES")}
            if (topics & a_ids) and (topics & b_ids):
                out.append(pid)
        return out

    def _evidence_papers(
        self,
        a: ConceptCluster,
        b: ConceptCluster,
        bridges: Sequence[str],
        scoped_pagerank: dict[str, float],
    ) -> list[dict[str, Any]]:
        store = self.store
        ranked_a = sorted(a.paper_ids, key=lambda p: -scoped_pagerank.get(p, 0.0))
        ranked_b = sorted(b.paper_ids, key=lambda p: -scoped_pagerank.get(p, 0.0))
        picks: list[tuple[str, str]] = []
        picks += [(p, f"strongest paper in cluster A ({a.name})") for p in ranked_a[:3]]
        picks += [(p, f"strongest paper in cluster B ({b.name})") for p in ranked_b[:3]]
        picks += [(p, "bridge paper: touches both clusters") for p in bridges[:3]]

        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for pid, reason in picks:
            if pid in seen or pid not in store.nodes:
                continue
            seen.add(pid)
            node = store.nodes[pid]
            out.append(
                {
                    "id": pid,
                    "title": node.name,
                    "year": node.props.get("year"),
                    "url": node.props.get("url"),
                    "field": node.props.get("field"),
                    "summary_source": node.props.get("summary_source"),
                    "author_status": node.props.get("author_status"),
                    "reason": reason,
                    "pagerank": round(scoped_pagerank.get(pid, 0.0), 5),
                    "community": store.community_of(pid),
                }
            )
        return out

    def _conflicts_for(self, a: ConceptCluster, b: ConceptCluster) -> list[dict[str, Any]]:
        papers = set(a.paper_ids) | set(b.paper_ids)
        out = []
        for conflict in self.store.conflicts:
            pa, pb = conflict.get("paper_a"), conflict.get("paper_b")
            if not pa or not pb:
                continue
            if pa in papers and pb in papers:
                payload = conflict
                out.append(payload)
        return out[:5]

    # ------------------------------------------------------- language
    def _hypothesis(self, a: ConceptCluster, b: ConceptCluster, feats: dict[str, float], bridges: Sequence[str]) -> str:
        bridge_note = (
            f"{len(bridges)} paper(s) in the analyzed corpus touch both clusters"
            if bridges
            else "no paper in the analyzed corpus touches both clusters directly"
        )
        return (
            f"Combining {a.name} with {b.name} appears relatively underexplored within the selected "
            f"research corpus. The two concept clusters are semantically related "
            f"(similarity {feats['semantic_similarity']:.2f}) yet structurally weakly connected "
            f"(sparsity {feats['relationship_sparsity']:.2f}), and {bridge_note}. "
            "A candidate direction is to investigate whether the mechanisms developed in one cluster "
            "transfer to the problems studied in the other."
        )

    def _why(self, a: ConceptCluster, b: ConceptCluster, feats: dict[str, float], components: dict[str, float]) -> list[str]:
        return [
            f"Cluster A ({a.name}) contains {len(a.paper_ids)} papers in scope; "
            f"cluster B ({b.name}) contains {len(b.paper_ids)}.",
            f"Community separation is {components['community_separation']:.0f}/100 "
            f"({feats['community_separation']:.2f} raw): the clusters occupy different regions of the graph.",
            f"Semantic similarity is {components['semantic_similarity']:.0f}/100 — cosine "
            f"{feats['embedded_similarity']:.3f} over embeddings plus neighbourhood Jaccard "
            f"{feats['neighbourhood_jaccard']:.3f}.",
            f"Relationship sparsity is {components['relationship_sparsity']:.0f}/100: only "
            f"{feats['cross_edge_weight']:.2f} weighted cross-cluster connections were collected.",
            f"{int(feats['bridge_papers'])} bridge paper(s) and {int(feats['recent_papers'])} paper(s) "
            f"from 2023 onwards were found in the two clusters.",
            "This is a graph-derived signal, not a literature review.",
        ]

    def _experiment(self, a: ConceptCluster, b: ConceptCluster, feats: dict[str, float]) -> str:
        a_topics = ", ".join(a.name.split(" · ")[:2]) or a.name
        b_topics = ", ".join(b.name.split(" · ")[:2]) or b.name
        return (
            f"Candidate experiment: build a controlled comparison in which the mechanism most used in "
            f"'{a_topics}' is transferred to the setting studied in '{b_topics}'. Compare against the "
            f"strongest baseline reported by the papers in cluster B, keep everything else fixed, and "
            f"report both effectiveness and cost. Treat this as a starting point for design, not as a "
            f"validated research plan — the bridge papers listed here are the best place to look for an "
            f"existing partial answer."
        )

    def _trajectory(self, feats: dict[str, float]) -> dict[str, Any]:
        """Is the candidate gap being closed, or has it persisted?

        This is the honest answer to "how underexplored is this really?" — it
        shows *when* bridge papers appeared instead of asserting novelty.
        """
        histogram = feats.get("bridge_year_histogram", {})
        recent = int(feats.get("bridge_papers_recent", 0))
        older = int(feats.get("bridge_papers_older", 0))
        total = recent + older
        if total == 0:
            status = "no bridges in corpus"
            note = "No paper in the analyzed corpus touches both clusters — the connection is untried *here*."
        elif recent == 0:
            status = "persistent gap"
            note = (
                f"All {older} bridge paper(s) predate 2024 and nothing recent connects the clusters in "
                "the analyzed corpus."
            )
        elif older == 0:
            status = "emerging bridge"
            note = (
                f"Every bridge paper ({recent}) appeared in 2024 or later — the two clusters are being "
                "connected right now, which is the strongest signal in this report."
            )
        else:
            ratio = recent / total
            status = "closing gap" if ratio >= 0.5 else "slowly closing gap"
            note = (
                f"{recent} bridge paper(s) since 2024 versus {older} before — the connection is "
                f"{'accelerating' if ratio >= 0.5 else 'growing slowly'}."
            )
        return {
            "status": status,
            "note": note,
            "bridge_papers_by_year": histogram,
            "recent_bridges": recent,
            "older_bridges": older,
        }

    def _confidence(
        self,
        score: float,
        feats: dict[str, float],
        bridges: Sequence[str],
        conflicts: Sequence[dict[str, Any]],
    ) -> tuple[str, str]:
        evidence_volume = feats["scoped_papers"]
        recent_bridges = feats.get("bridge_papers_recent", 0.0)
        if score >= 68 and evidence_volume >= 8:
            if recent_bridges > 0:
                return "Medium-High", (
                    f"Score {score:.0f}/100 over {int(evidence_volume)} scoped papers. {int(recent_bridges)} "
                    "bridge paper(s) appeared in 2024 or later, so the connection is emerging rather than "
                    "untouched — treat this as an active frontier, not an empty space."
                )
            return "Medium-High", (
                f"Score {score:.0f}/100 over {int(evidence_volume)} scoped papers with no recent bridge "
                "paper in the corpus. Verify with a literature search before investing."
            )
        if score >= 68 and evidence_volume >= 8 and feats["cross_edge_weight"] > 0:
            level = "Medium-High"
            reason = (
                f"Score {score:.0f}/100 with {int(evidence_volume)} scoped papers, "
                f"{len(bridges)} bridge papers and {len(conflicts)} claim-level tensions detected. "
                "Structural signal is consistent across independent components of the score."
            )
        elif score >= 55 and evidence_volume >= 5:
            level = "Medium"
            reason = (
                f"Score {score:.0f}/100 over {int(evidence_volume)} scoped papers. Several score "
                "components agree, but the bridge evidence is thin — worth a literature check first."
            )
        else:
            level = "Low"
            reason = (
                f"Score {score:.0f}/100 with {int(evidence_volume)} scoped papers. The corpus slice "
                "is small for this pair, so the signal may be an artefact of coverage."
            )
        return level, reason
