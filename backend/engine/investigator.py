"""Evidence-first research investigation over the existing NEXUS graph.

The existing GraphRAG retriever intentionally favors recall. This coordinator keeps
that retrieval context for graph expansion, then independently ranks papers against
the question and scope before presenting them as evidence. Opportunity scores remain
the published gap-engine heuristic; question relevance is a separate ranking signal.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Sequence

from backend.engine.gaps import GapEngine, SAFETY_NOTICE
from backend.ingestion.claims import STOPWORDS, normalise, tokenize
from backend.models.graph import GEdge, GNode
from backend.rag.graphrag import GraphRAG


QUERY_NOISE = STOPWORDS | {
    "how", "what", "which", "who", "when", "where", "why", "does", "doing", "did",
    "research", "paper", "papers", "study", "studies", "work", "works", "gap", "gaps",
    "opportunity", "opportunities", "candidate", "find", "identify", "explore", "investigate",
    "important", "most", "best", "could", "would", "should", "please", "using", "useful",
    "potential", "possible", "current", "existing", "area", "areas", "make", "made", "show",
    "tell", "about", "into", "across", "around", "based", "new", "better", "improve", "improving",
}

GENERIC_DOMAIN_TERMS = frozenset({
    "ai", "agent", "agents", "llm", "model", "models", "language", "multi", "multi-agent", "system", "systems",
})

OBSERVED_PATH_RELATIONSHIPS = frozenset(
    {"STUDIES", "USES_METHOD", "USES_DATASET", "CITES", "AUTHORED", "MAKES_CLAIM", "AFFILIATED_WITH"}
)

LIMITATIONS = [
    "This is a curated demo corpus, not a systematic or exhaustive literature search; check the original papers and search outside NEXUS.",
    "An absent relationship, candidate, path, or detected claim tension in this graph does not establish that prior work or disagreement is absent from the literature.",
    "Paper relevance is a disclosed token-overlap reranking heuristic over titles, collected summaries, topics, and methods; it is not calibrated relevance or a semantic judgment.",
    "The NEXUS Opportunity Score is an uncalibrated prototype heuristic, not a probability, proof of novelty, or measure of scientific merit.",
    "Claim conflicts are automated review leads, not verified contradictions. Summary provenance varies by record; use source links and full text before relying on a claim.",
]


class ResearchInvestigator:
    """Compose scoped corpus retrieval, graph paths, candidates, and an experiment."""

    def __init__(self, store: Any, gaps: GapEngine, graphrag: GraphRAG) -> None:
        self.store = store
        self.gaps = gaps
        self.graphrag = graphrag

    @staticmethod
    def _tokens(text: str | None) -> set[str]:
        """Normalise words while keeping both compounds and their useful parts."""
        out: set[str] = set()
        for raw in tokenize(text or ""):
            token = normalise(raw)
            if token and token not in QUERY_NOISE:
                out.add(token)
            # Corpus labels often use forms such as long-term or retrieval-augmented.
            for part in raw.replace("_", "-").split("-"):
                part = normalise(part)
                if len(part) > 1 and part not in QUERY_NOISE:
                    out.add(part)
        return out

    @classmethod
    def _question_terms(cls, text: str, scope: str | None = None) -> set[str]:
        """Drop broad domain/scope words so paper matches must support specifics."""
        terms = cls._tokens(text) - GENERIC_DOMAIN_TERMS
        if scope:
            terms -= cls._tokens(scope)
        return terms

    @classmethod
    def _display_matches(cls, question: str, matched: set[str], scope: str | None = None) -> list[str]:
        """Show original query words instead of light-stem artifacts (e.g. plann)."""
        supported = cls._question_terms(question, scope) & matched
        display: set[str] = set()
        for raw in tokenize(question):
            token = raw.replace("_", "-").lower()
            normalized = normalise(token)
            if normalized in supported:
                display.add(token)
            for part in token.split("-"):
                part_normalized = normalise(part)
                if part_normalized in supported and part not in GENERIC_DOMAIN_TERMS:
                    display.add(part)
        return sorted(display)

    @staticmethod
    def _weighted_coverage(terms: set[str], field_terms: set[str], idf: dict[str, float]) -> float:
        if not terms:
            return 0.0
        total = sum(idf.get(term, 1.0) for term in terms)
        if total <= 0:
            return 0.0
        return sum(idf.get(term, 1.0) for term in terms & field_terms) / total

    def _infer_scope(self, question: str) -> str | None:
        """Pick one corpus topic only when its vocabulary has clear question support."""
        query_tokens = self._tokens(question)
        if not query_tokens:
            return None
        lowered = question.lower()
        scored: list[tuple[float, int, str]] = []
        for node in self.store.nodes.values():
            if node.label != "Topic":
                continue
            name_tokens = self._tokens(node.name)
            if not name_tokens:
                continue
            overlap = query_tokens & name_tokens
            precision = len(overlap) / len(name_tokens)
            recall = len(overlap) / len(query_tokens)
            score = 0.75 * precision + 0.25 * recall
            if node.name.lower() in lowered:
                score = 1.0
            elif len(name_tokens) == 1 and overlap:
                # A one-word concept match must not beat a more specific multi-word label.
                score = min(score, 0.55)
            if not overlap:
                continue
            support = sum(
                1 for edge in self.store.edges
                if edge.type == "STUDIES" and edge.dst == node.id
            )
            scored.append((score, support, node.name))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
        best, _support, name = scored[0]
        return name if best >= 0.38 else None

    def _rank_papers(
        self,
        question: str,
        paper_ids: Sequence[str],
        graph_context: Any,
        limit: int,
        scope: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        terms = self._question_terms(question, scope)
        papers = [
            self.store.nodes[pid]
            for pid in paper_ids
            if pid in self.store.nodes and self.store.nodes[pid].label == "Paper"
        ]
        document_terms: dict[str, set[str]] = {}
        document_frequency: Counter[str] = Counter()
        for paper in papers:
            topics = [self.store.nodes[n].name for n in self._neighbours(paper.id, "STUDIES")]
            methods = [self.store.nodes[n].name for n in self._neighbours(paper.id, "USES_METHOD")]
            all_terms = self._tokens(" ".join([paper.name, paper.props.get("abstract", ""), *topics, *methods]))
            document_terms[paper.id] = all_terms
            document_frequency.update(all_terms)
        total_docs = max(1, len(papers))
        idf = {
            term: math.log(1.0 + (total_docs + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }
        for term in terms:
            idf.setdefault(term, math.log(1.0 + (total_docs + 0.5) / 0.5))

        seeds = {
            seed.id: float(seed.score)
            for seed in getattr(graph_context, "seeds", [])
            if getattr(seed, "type", None) == "Paper"
        }
        max_seed_score = max(seeds.values(), default=0.0)
        ranked: list[dict[str, Any]] = []
        query_hits: set[str] = set()
        source_counts: Counter[str] = Counter()
        year_values: list[int] = []
        field_counts: Counter[str] = Counter()

        for paper in papers:
            title_terms = self._tokens(paper.name)
            summary_terms = self._tokens(str(paper.props.get("abstract", "")))
            taxonomy_names = [self.store.nodes[n].name for n in self._neighbours(paper.id, "STUDIES")]
            taxonomy_names += [self.store.nodes[n].name for n in self._neighbours(paper.id, "USES_METHOD")]
            taxonomy_terms = self._tokens(" ".join(taxonomy_names))
            title_score = self._weighted_coverage(terms, title_terms, idf)
            summary_score = self._weighted_coverage(terms, summary_terms, idf)
            taxonomy_score = self._weighted_coverage(terms, taxonomy_terms, idf)
            matched = sorted(terms & document_terms[paper.id])
            query_hits.update(matched)
            raw_seed = seeds.get(paper.id, 0.0)
            seed_score = raw_seed / max_seed_score if max_seed_score else 0.0
            relevance = (
                0.52 * title_score
                + 0.30 * summary_score
                + 0.18 * taxonomy_score
                + 0.05 * seed_score
            ) / 1.05
            if not matched:
                continue
            year = paper.props.get("year")
            if isinstance(year, int):
                year_values.append(year)
            field = paper.props.get("field")
            if field:
                field_counts[str(field)] += 1
            summary_source = paper.props.get("summary_source") or "not-recorded"
            source_counts[str(summary_source)] += 1
            ranked.append(
                {
                    "id": paper.id,
                    "title": paper.name,
                    "year": year,
                    "field": field,
                    "url": paper.props.get("url"),
                    "summary_source": summary_source,
                    "author_status": paper.props.get("author_status", "not-collected"),
                    "relevance_score": round(relevance, 4),
                    "relevance_method": "weighted token overlap: title 52%, summary 30%, topics/methods 18%, with a small normalized retrieval-seed tie signal",
                    "matched_terms": self._display_matches(question, set(matched), scope),
                    "matched_in": {
                        "title": self._display_matches(question, terms & title_terms, scope),
                        "summary": self._display_matches(question, terms & summary_terms, scope),
                        "topics_or_methods": self._display_matches(question, terms & taxonomy_terms, scope),
                    },
                    "graph_retrieval_seed_score": round(raw_seed, 5) if paper.id in seeds else None,
                }
            )

        ranked.sort(key=lambda row: (-row["relevance_score"], -(row["year"] or 0), row["title"].lower()))
        matched_term_coverage = len(query_hits & terms) / max(1, len(terms))
        coverage = {
            "scope_papers": len(papers),
            "relevant_papers_returned": min(limit, len(ranked)),
            "papers_with_question_term_match": len(ranked),
            "question_terms": self._display_matches(question, terms, scope),
            "matched_question_terms": self._display_matches(question, query_hits & terms, scope),
            "question_term_coverage": round(matched_term_coverage, 3),
            "year_range": [min(year_values), max(year_values)] if year_values else None,
            "fields": dict(field_counts.most_common()),
            "summary_sources": dict(source_counts.most_common()),
            "summary_provenance_note": "Counts show the source labels carried by the corpus; editorial summaries are not paper abstracts.",
        }
        return ranked[:limit], coverage

    def _neighbours(self, node_id: str, rel_type: str) -> list[str]:
        neighbours = []
        for edge in self.store.edges:
            if edge.type != rel_type:
                continue
            if edge.src == node_id:
                neighbours.append(edge.dst)
            elif edge.dst == node_id:
                neighbours.append(edge.src)
        return [node_id for node_id in neighbours if node_id in self.store.nodes]

    def _node_ids_for_cluster(self, cluster: dict[str, Any]) -> list[str]:
        wanted = {str(name).strip().casefold() for name in [*(cluster.get("topics") or []), *(cluster.get("methods") or [])]}
        return sorted(
            node.id for node in self.store.nodes.values()
            if node.label in {"Topic", "Method"} and node.name.strip().casefold() in wanted
        )

    def _cluster_papers(self, cluster: dict[str, Any], scoped_papers: set[str]) -> set[str]:
        node_ids = set(self._node_ids_for_cluster(cluster))
        paper_ids: set[str] = set()
        for edge in self.store.edges:
            if edge.type not in {"STUDIES", "USES_METHOD"}:
                continue
            if edge.src in scoped_papers and edge.dst in node_ids:
                paper_ids.add(edge.src)
            elif edge.dst in scoped_papers and edge.src in node_ids:
                paper_ids.add(edge.dst)
        return paper_ids

    def _paths_for_candidate(self, opportunity: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        cluster_a = opportunity.get("cluster_a") or {}
        cluster_b = opportunity.get("cluster_b") or {}
        a_ids = self._node_ids_for_cluster(cluster_a)
        b_ids = self._node_ids_for_cluster(cluster_b)
        bridge_ids = list(opportunity.get("bridge_papers") or [])
        path_pairs: list[tuple[str, str, int]] = []

        # Prefer endpoints that both touch an explicit bridge paper in the loaded graph.
        for paper_id in bridge_ids[:3]:
            left = [node_id for node_id in a_ids if self._has_relationship(paper_id, node_id, {"STUDIES", "USES_METHOD"})]
            right = [node_id for node_id in b_ids if self._has_relationship(paper_id, node_id, {"STUDIES", "USES_METHOD"})]
            if left and right:
                path_pairs.append((left[0], right[0], 3))
        if not path_pairs:
            path_pairs = [(a, b, 4) for a in a_ids[:4] for b in b_ids[:4]]

        paths: list[dict[str, Any]] = []
        errors: list[str] = []
        seen: set[tuple[str, ...]] = set()
        for source_id, target_id, max_hops in path_pairs[:3]:
            try:
                path = self.store.trace_path(source_id, target_id, max_hops=max_hops,
                                             allowed_types=sorted(OBSERVED_PATH_RELATIONSHIPS))
            except Exception as exc:  # a live Neo4j query can fail after the initial health check
                errors.append(f"Graph path verification failed: {type(exc).__name__}.")
                break
            if not path:
                continue
            ids = tuple(node.get("id", "") for node in path.get("nodes", []))
            if not ids or ids in seen:
                continue
            seen.add(ids)
            path["candidate_id"] = opportunity.get("id")
            path["path_kind"] = "observed corpus relationships; predicted/associative links excluded"
            path["bridge_paper_ids"] = [pid for pid in bridge_ids if pid in ids]
            paths.append(path)
            if len(paths) >= 2:
                break
        return paths, errors

    def _has_relationship(self, paper_id: str, concept_id: str, rel_types: set[str]) -> bool:
        return any(
            edge.type in rel_types
            and ((edge.src == paper_id and edge.dst == concept_id) or (edge.dst == paper_id and edge.src == concept_id))
            for edge in self.store.edges
        )

    def _candidate_tokens(self, opportunity: dict[str, Any]) -> set[str]:
        # Candidate relevance is based on the gap's concept labels, not evidence-paper
        # titles. Otherwise an incidental word in a source title could make an
        # unrelated cluster pair appear question-relevant.
        parts: list[str] = [opportunity.get("title", "")]
        for key in ("cluster_a", "cluster_b"):
            cluster = opportunity.get(key) or {}
            parts.extend([cluster.get("name", ""), *(cluster.get("topics") or []), *(cluster.get("methods") or [])])
        return self._tokens(" ".join(str(part) for part in parts))

    def _candidate_relevance(self, question: str, scope: str | None, opportunity: dict[str, Any]) -> tuple[float, list[str], bool]:
        scope_terms = self._tokens(scope)
        candidate_terms = self._candidate_tokens(opportunity)
        scope_hits = scope_terms & candidate_terms
        specific_terms = self._question_terms(question, scope)
        specific_hits = specific_terms & candidate_terms
        query_score = len(specific_hits) / max(1, len(specific_terms)) if specific_terms else 0.0
        scope_score = len(scope_hits) / max(1, len(scope_terms)) if scope_terms else 0.0
        score = (0.82 * query_score + 0.18 * scope_score) if specific_terms and scope_terms else (
            query_score if specific_terms else scope_score
        )
        # Require support in the candidate concepts themselves; general domain words
        # and the scope label alone cannot make every pair in a broad corpus relevant.
        enough_specific_support = bool(specific_hits) and (
            len(specific_hits) >= min(2, len(specific_terms))
            or (len(specific_terms) == 1 and query_score >= 1.0)
        )
        broad_scope_match = not specific_terms and scope_score >= 0.5
        supported = enough_specific_support or broad_scope_match
        return round(score, 4), self._display_matches(question, specific_hits, scope), supported

    def _counterevidence(self, opportunity: dict[str, Any], scoped_papers: set[str]) -> list[dict[str, Any]]:
        candidate_papers = self._cluster_papers(opportunity.get("cluster_a") or {}, scoped_papers)
        candidate_papers |= self._cluster_papers(opportunity.get("cluster_b") or {}, scoped_papers)
        candidate_papers.update(opportunity.get("bridge_papers") or [])
        conflicts = []
        for conflict in self.store.conflicts_payload(limit=100):
            if conflict.get("source_a") in candidate_papers or conflict.get("source_b") in candidate_papers:
                conflicts.append({
                    **conflict,
                    "evidence_status": "AI-detected hypothesis; verify both source claims in the original papers",
                })
        return conflicts[:5]

    @staticmethod
    def _experiment(opportunity: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        cluster_a = (opportunity.get("cluster_a") or {}).get("name", "the first research area")
        cluster_b = (opportunity.get("cluster_b") or {}).get("name", "the second research area")
        combined = f"{cluster_a} {cluster_b}".lower()
        if any(term in combined for term in ("tool", "api", "calling")):
            primary = "Task completion rate on a predeclared, task-matched tool-use evaluation; report valid and invalid tool calls separately."
        elif any(term in combined for term in ("coordination", "multi-agent", "debate")):
            primary = "Task completion rate on a predeclared coordination/evaluation set, with the unit of analysis and aggregation fixed before runs."
        elif "memory" in combined:
            primary = "Recall accuracy on a predeclared, task-matched memory evaluation, using the same context and query protocol for both arms."
        elif any(term in combined for term in ("retrieval", "factuality", "grounding")):
            primary = "Claim-level support/attribution rate under a predeclared, blinded source-checking rubric on a task-matched evaluation set."
        else:
            primary = "One task-appropriate primary outcome chosen from the source literature and preregistered before runs."
        evidence_ids = list(dict.fromkeys(paper.get("id") for paper in evidence if paper.get("id")))
        return {
            "label": "proposed experiment — design hypothesis, not a reported result",
            "research_hypothesis": (
                f"In the target setting represented by {cluster_b}, applying a method or mechanism from "
                f"{cluster_a} will improve the preregistered primary outcome relative to a reproducible "
                "target-setting baseline, when data, evaluation, and compute conditions are held constant."
            ),
            "intervention": f"Select one concrete method/mechanism from {cluster_a} after reviewing its source papers; document the implementation before testing.",
            "comparison": f"Reproduce the strongest comparable baseline supported by the source papers for {cluster_b}; select it by full-text review, not by the NEXUS score.",
            "primary_outcome": primary,
            "secondary_outcomes": [
                "Report inference/training cost and latency under the same measurement protocol.",
                "Report failures and subgroup results defined before looking at outcomes.",
            ],
            "controls": [
                "Use the same data split, task prompts, model/backbone, and compute budget in both arms where applicable.",
                "Pre-register the outcome, exclusions, and analysis; repeat stochastic runs and report uncertainty.",
            ],
            "falsification_criterion": (
                "Define Δ = intervention outcome − baseline outcome before running the study. The directional "
                "hypothesis is not supported if Δ is zero or negative on the held-out evaluation under the "
                "predeclared analysis; report the estimate and uncertainty rather than relabeling the result."
            ),
            "evidence_to_review_first": evidence_ids,
            "unknowns": [
                "NEXUS does not have effect sizes, benchmark protocols, full-text methods, or a validated baseline for this pair; obtain these from the linked source papers before specifying the final protocol.",
                "The metric wording above is a proposal for a testable outcome, not a measurement reported by the corpus.",
            ],
        }

    def investigate(
        self,
        question: str,
        *,
        scope_topic: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        field: str | None = None,
        top_k: int = 5,
        retrieval_top_k: int = 24,
        depth: int = 2,
    ) -> dict[str, Any]:
        question = question.strip()
        selected_scope = (scope_topic or "").strip() or self._infer_scope(question)
        inferred = not bool(scope_topic and scope_topic.strip())
        paper_ids, topic_ids, method_ids = self.gaps.scope(
            selected_scope, year_min=year_min, year_max=year_max, field=field, min_papers=1
        )
        scope_resolution = dict(getattr(self.gaps, "last_resolution", {}))

        # GraphRAG contributes graph-expansion context and a named retrieval engine;
        # its broad raw paper order is not used as the final evidence ranking.
        context = self.graphrag.retrieve(
            question,
            labels=("Paper", "Topic", "Method", "Dataset", "Claim"),
            top_k=retrieval_top_k,
            depth=depth,
            include_conflicts=True,
        )
        relevant_papers, coverage = self._rank_papers(
            question, paper_ids, context, limit=max(6, min(16, retrieval_top_k)), scope=selected_scope
        )

        gap_payload = self.gaps.find_gaps(
            selected_scope,
            year_min=year_min,
            year_max=year_max,
            field=field,
            min_papers=3,
            top_k=max(12, min(25, top_k * 4)),
        )
        scoped_papers = set(paper_ids)
        gap_scope = gap_payload.get("scope", {})
        scope_resolution = gap_payload.get("resolution") or scope_resolution
        scope_token_set = self._tokens(selected_scope)
        candidates: list[dict[str, Any]] = []
        for original in gap_payload.get("opportunities", []):
            candidate = dict(original)
            relevance, matched_terms, supported = self._candidate_relevance(question, selected_scope, candidate)
            if not supported:
                continue
            candidate["question_relevance"] = relevance
            candidate["question_matched_terms"] = matched_terms
            candidate["question_relevance_note"] = "Separate token-overlap signal; the published opportunity score is unchanged."
            candidates.append(candidate)
        candidates.sort(key=lambda row: (-row["question_relevance"], -row.get("opportunity_score", 0), row.get("title", "")))
        candidates = candidates[:max(1, min(8, top_k))]

        relevance_by_id = {paper["id"]: paper for paper in relevant_papers}
        graph_path_status = {
            "backend": getattr(self.store, "engine_name", "unknown"),
            "method": (
                "bounded, parameterized Neo4j Cypher shortest-path query over observed relationships"
                if getattr(self.store, "engine_name", "") == "neo4j"
                else "bounded breadth-first search over the in-process graph's observed relationships"
            ),
            "max_hops": 4,
            "paths_found": 0,
            "verification_error": None,
        }
        final_candidates: list[dict[str, Any]] = []
        evidence_paths: list[dict[str, Any]] = []
        for rank, candidate in enumerate(candidates, start=1):
            candidate["investigation_rank"] = rank
            evidence: list[dict[str, Any]] = []
            for record in (candidate.get("evidence_papers") or [])[:8]:
                paper = self.store.nodes.get(record.get("id"))
                if paper is None:
                    continue
                ranked = relevance_by_id.get(paper.id)
                evidence.append({
                    **record,
                    "summary_source": paper.props.get("summary_source") or "not-recorded",
                    "url": paper.props.get("url"),
                    "source_type": "paper record with corpus taxonomy; summary provenance shown separately",
                    "question_relevance": ranked.get("relevance_score") if ranked else None,
                    "matched_terms": ranked.get("matched_terms", []) if ranked else [],
                })
            candidate["evidence"] = evidence
            candidate["graph_paths"], path_errors = self._paths_for_candidate(candidate)
            graph_path_status["paths_found"] += len(candidate["graph_paths"])
            if path_errors and not graph_path_status["verification_error"]:
                graph_path_status["verification_error"] = path_errors[0]
            evidence_paths.extend(candidate["graph_paths"])
            candidate["counterevidence"] = self._counterevidence(candidate, scoped_papers)
            candidate["counterevidence_status"] = (
                "Detected claim tensions are review hypotheses, not verified contradictions."
                if candidate["counterevidence"]
                else "No detected claim-tension record was attached in this analyzed scope; this is not evidence of consensus."
            )
            candidate["limitations"] = [
                "The score is a prototype heuristic and the candidate requires an independent literature review.",
                "An absent graph edge is not evidence that no relevant paper exists outside this corpus.",
                "Bridge papers, if present, are potentially important prior or partial work and must be read before claiming a gap.",
                "A graph path is structural evidence about corpus records, not causal evidence or a scientific conclusion.",
            ]
            candidate["experiment_proposal"] = self._experiment(candidate, evidence)
            final_candidates.append(candidate)

        corpus_papers = self.store.stats().get("labels", {}).get("Paper", 0)
        if not scoped_papers:
            empty_reason = "No corpus papers matched the selected scope and filters. Try a broader topic or remove a filter."
        elif len(scoped_papers) < 3:
            empty_reason = f"Only {len(scoped_papers)} paper record(s) matched this scope; the gap engine needs at least 3 for its cluster analysis. No opportunity is inferred from the missing result."
        elif not final_candidates:
            engine_reason = gap_payload.get("reason")
            empty_reason = (
                "The scoped gap engine returned candidates, but none had enough question-term support to show as relevant. Broaden the selected scope or refine the question."
                if gap_payload.get("opportunities")
                else (engine_reason or "The gap engine found no supported opportunity pair in this scope. This does not establish that related work is absent.")
            )
        else:
            empty_reason = None

        result = {
            "question": question,
            "scope": {
                "requested_topic": scope_topic,
                "selected_topic": selected_scope,
                "selected_by": "user" if not inferred else ("question-topic match" if selected_scope else "whole corpus; no clear topic match"),
                "resolution": scope_resolution,
                "year_min": year_min,
                "year_max": year_max,
                "field": field,
                "papers": len(scoped_papers),
                "topics": len(topic_ids),
                "methods": len(method_ids),
                "gap_engine_scope": gap_scope,
            },
            "retrieval": {
                "engine": getattr(context, "retrieval_engine", "unknown"),
                "graph_expansion_engine": getattr(context, "expansion_engine", "unknown"),
                "raw_context_papers": len(getattr(context, "papers", [])),
                "seed_count": len(getattr(context, "seeds", [])),
                "note": "GraphRAG supplies recall-oriented graph context. The paper evidence shown below is independently reranked against the question and selected corpus scope.",
            },
            "backend": {
                "graph_store": getattr(self.store, "engine_name", "unknown"),
                "graph_computation_scope": (
                    "Neo4j-hydrated local projection for scope selection, GraphRAG expansion, candidate ranking, and analytics; reported evidence paths are separately verified by bounded parameterized Neo4j traversals."
                    if getattr(self.store, "engine_name", "") == "neo4j"
                    else "In-process graph projection for scope selection, GraphRAG expansion, candidate ranking, analytics, and bounded evidence paths."
                ),
                "analytics": getattr(getattr(self.store, "analytics", None), "engines", {}),
                "embedding_engine": getattr(self.store, "embedding_engine", "unknown"),
                "claim_engine": getattr(self.store, "claim_engine", "unknown"),
            },
            "coverage": {
                "corpus_papers": corpus_papers,
                **coverage,
                "scoped_papers_without_question_term_match": max(0, len(scoped_papers) - coverage["papers_with_question_term_match"]),
                "claim_tensions_in_corpus": len(getattr(self.store, "conflicts", [])),
                "opportunity_candidates_from_gap_engine": len(gap_payload.get("opportunities", [])),
                "opportunity_candidates_relevant_to_question": len(final_candidates),
            },
            "relevant_papers": relevant_papers,
            "opportunities": final_candidates,
            "evidence_paths": evidence_paths,
            "graph_path_status": graph_path_status,
            "score_explanation": {
                "published_score": gap_payload.get("score_label", "NEXUS Opportunity Score — prototype heuristic"),
                "formula": gap_payload.get("score_formula", {}),
                "ranking": "Candidates are filtered/ranked by a separate question token-overlap score; their published opportunity_score is unchanged.",
                "safety_notice": SAFETY_NOTICE,
            },
            "counterevidence_note": "Claim-tension records are AI-detected hypotheses, not established facts. Absence of a detected tension is not evidence that the literature agrees.",
            "limitations": LIMITATIONS,
            "empty_reason": empty_reason,
            "method": [
                "Scope the existing graph by topic, field, and year; report the topic resolver's matches.",
                "Call the existing GraphRAG retriever for recall-oriented seeds and graph expansion, but independently rerank papers by weighted title, summary, and taxonomy token overlap.",
                "Run the existing scoped GapEngine; keep its prototype score distinct from question relevance and discard candidates without question-term support.",
                "Attach candidate source papers, corpus relationship paths, automated claim-tension leads, coverage, limitations, and a falsifiable experiment proposal.",
            ],
        }
        return result
