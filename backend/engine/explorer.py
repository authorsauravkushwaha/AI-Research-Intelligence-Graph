"""Research Explorer metrics (§9) and the dashboard payload (§27).

Everything here is derived from the graph — no hardcoded numbers — so the
dashboard moves when the corpus, the filters or the algorithms change.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

from backend.engine.gaps import GapEngine


class Explorer:
    def __init__(self, store: Any, gap_engine: GapEngine | None = None) -> None:
        self.store = store
        self.gaps = gap_engine or GapEngine(store)

    # ------------------------------------------------------------- overview
    def overview(
        self,
        topic: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        field: str | None = None,
    ) -> dict[str, Any]:
        store = self.store
        paper_ids, topic_ids, method_ids = self.gaps.scope(topic, year_min, year_max, field, min_papers=1)

        papers = [store.nodes[p] for p in paper_ids if p in store.nodes]
        authors: set[str] = set()
        datasets: set[str] = set()
        institutions: set[str] = set()  # seeded by ingestion when affiliations exist
        communities: set[int] = set()

        for node in papers:
            for edge in store.edges:
                if edge.type == "AUTHORED" and edge.dst == node.id:
                    authors.add(edge.src)
                elif edge.type == "USES_DATASET" and edge.src == node.id:
                    datasets.add(edge.dst)
                elif edge.type == "AFFILIATED_WITH" and edge.dst == node.id:
                    institutions.add(edge.src)
            community = store.community_of(node.id)
            if community is not None and community >= 0:
                communities.add(community)

        years = Counter(node.props.get("year") for node in papers if node.props.get("year"))
        recency = self._recency(years)

        return {
            "scope": {
                "topic": topic,
                "year_min": year_min,
                "year_max": year_max,
                "field": field,
                "resolution": getattr(self.gaps, "last_resolution", {}),
            },
            "counts": {
                "papers": len(papers),
                "authors": len(authors),
                "topics": len(topic_ids),
                "methods": len(method_ids),
                "datasets": len(datasets),
                "institutions": len(institutions),
                "communities": len(communities),
                "claims": len([n for n in store.nodes.values() if n.label == "Claim"]),
            },
            "years": dict(sorted(years.items())) if years else {},
            "recency": recency,
            "top_papers": store.top("pagerank", "Paper", 8),
            "top_topics": store.top("pagerank", "Topic", 10),
            "top_methods": store.top("degree", "Method", 10),
            "top_bridge_papers": store.top("betweenness", "Paper", 8),
            "communities": [
                profile for profile in store.communities() if not communities or profile["community_index"] in communities
            ][:10],
            "conflicts": store.conflicts_payload(limit=6),
            "predicted_links": self._predicted_labels(store.predicted_links[:8]),
            "dataset_labels": {
                "institutions": "Institution affiliations are stored when the source metadata provides them; "
                "the demo corpus records authorship only for verified classic papers, so this count can be 0.",
                "papers": "Papers in the analyzed corpus (real arXiv records).",
            },
        }

    @staticmethod
    def _recency(years: Counter) -> dict[str, Any]:
        if not years:
            return {}
        total = sum(years.values())
        recent = sum(count for year, count in years.items() if year >= 2023)
        return {
            "papers_since_2023": recent,
            "share_since_2023": round(recent / total, 3) if total else 0.0,
            "newest_year": max(years),
            "oldest_year": min(years),
        }

    def _predicted_labels(self, links: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for link in links:
            if link["source"] not in self.store.nodes or link["target"] not in self.store.nodes:
                continue
            out.append(
                {
                    **link,
                    "source_label": self.store.nodes[link["source"]].name,
                    "target_label": self.store.nodes[link["target"]].name,
                    "label": "Predicted research connection — hypothesis, not an observed relationship",
                }
            )
        return out

    # ------------------------------------------------------------ dashboard
    def dashboard(self) -> dict[str, Any]:
        store = self.store
        counts = store.stats()
        gaps = self.gaps.find_gaps("AI Agents", year_min=2019, year_max=2026, top_k=5)

        # emerging topics: growth in paper counts across the two most recent windows
        topic_years: dict[str, Counter] = defaultdict(Counter)
        for edge in store.edges:
            if edge.type != "STUDIES" or not edge.src.startswith("paper:"):
                continue
            paper = store.nodes.get(edge.src)
            topic = store.nodes.get(edge.dst)
            if paper is None or topic is None or topic.props.get("topic_kind") == "field":
                continue
            year = paper.props.get("year")
            if year:
                topic_years[edge.dst][year] += 1

        emerging = []
        for topic_id, years in topic_years.items():
            recent = sum(c for y, c in years.items() if y >= 2023)
            older = sum(c for y, c in years.items() if y < 2023)
            if recent >= 2:
                growth = recent / max(1, older)
                emerging.append(
                    {
                        "id": topic_id,
                        "label": store.nodes[topic_id].name,
                        "papers_recent": recent,
                        "papers_earlier": older,
                        "growth_ratio": round(growth, 2),
                        "timeline": dict(sorted(years.items())),
                    }
                )
        emerging.sort(key=lambda e: (-e["growth_ratio"], -e["papers_recent"]))

        return {
            "cards": {
                "papers": counts["labels"].get("Paper", 0),
                "authors": counts["labels"].get("Author", 0),
                "topics": counts["labels"].get("Topic", 0),
                "methods": counts["labels"].get("Method", 0),
                "datasets": counts["labels"].get("Dataset", 0),
                "communities": counts["communities"],
                "potential_opportunities": len(gaps.get("opportunities", [])),
                "claims": counts["labels"].get("Claim", 0),
                "predicted_links": counts["predicted_links"],
                "conflicts": counts["conflicts"],
            },
            "engines": {
                "graph": counts["engine"],
                "analytics": store.analytics.engines if store.analytics else {},
                "embeddings": store.embedding_engine,
                "claims": getattr(store, "claim_engine", "unknown"),
            },
            "communities": store.communities()[:8],
            "community_chart": [
                {
                    "name": profile["name"],
                    "papers": profile["paper_count"],
                    "topics": profile["topic_count"],
                    "avg_pagerank": profile["avg_pagerank"],
                }
                for profile in store.communities()[:10]
            ],
            "emerging_topics": emerging[:10],
            "most_connected_topics": store.top("pagerank", "Topic", 10),
            "bridge_papers": store.top("betweenness", "Paper", 8),
            "opportunities": gaps.get("opportunities", []),
            "safety_notice": gaps.get("safety_notice"),
            "provenance": store.health().get("provenance", {}),
        }
