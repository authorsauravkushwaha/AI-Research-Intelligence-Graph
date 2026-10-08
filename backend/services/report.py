"""Opportunity Report (§28) and export (§NICE-TO-HAVE).

The report is assembled from the same payloads the UI renders — it is a
serialisation of graph evidence, not an additional generation step, so the report
can never contain something the graph did not return. When an LLM is configured,
an executive summary is written *over* the evidence (and labelled as such);
otherwise the summary is composed from the numbers.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from backend.engine.explorer import Explorer


class ReportService:
    def __init__(self, store: Any, explorer: Explorer, agent: Any | None = None) -> None:
        self.store = store
        self.explorer = explorer
        self.agent = agent

    def build(
        self,
        topic: str,
        year_min: int | None = None,
        year_max: int | None = None,
        top_k: int = 5,
        executive_summary: str | None = None,
    ) -> dict[str, Any]:
        gaps = self.explorer.gaps.find_gaps(topic, year_min, year_max, top_k=top_k)
        overview = self.explorer.overview(topic, year_min, year_max)
        store = self.store

        opportunities = gaps.get("opportunities", [])
        sources = sorted(
            {
                paper["url"]
                for opportunity in opportunities
                for paper in opportunity.get("evidence_papers", [])
                if paper.get("url")
            }
        )

        summary = executive_summary or self._summary(topic, overview, opportunities, gaps.get("reason"))

        return {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "topic": topic,
            "scope": gaps.get("scope", {}),
            "executive_summary": summary,
            "landscape": {
                "papers": overview["counts"]["papers"],
                "authors": overview["counts"]["authors"],
                "topics": overview["counts"]["topics"],
                "methods": overview["counts"]["methods"],
                "datasets": overview["counts"]["datasets"],
                "communities": overview["counts"]["communities"],
                "recency": overview.get("recency", {}),
            },
            "communities": [
                {
                    "name": profile["name"],
                    "paper_count": profile["paper_count"],
                    "top_topics": profile["top_topics"][:6],
                    "top_methods": profile.get("top_methods", []),
                    "top_papers": [p["title"] for p in profile["top_papers"][:5]],
                    "year_range": profile.get("year_range"),
                }
                for profile in overview["communities"][:6]
            ],
            "important_papers": [
                {
                    "id": p["id"],
                    "title": p["label"],
                    "year": p.get("year"),
                    "pagerank": p["value"],
                    "url": p.get("url"),
                    "why": "high structural influence (PageRank) inside the analyzed corpus",
                }
                for p in overview["top_papers"]
            ],
            "bridge_papers": [
                {
                    "id": p["id"],
                    "title": p["label"],
                    "year": p.get("year"),
                    "betweenness": p["value"],
                    "url": p.get("url"),
                    "why": "sits on many shortest paths between other nodes (structural bridge)",
                }
                for p in overview["top_bridge_papers"]
            ],
            "methods": [m["label"] for m in overview["top_methods"]],
            "datasets": sorted(
                {
                    store.nodes[e.dst].name
                    for e in store.edges
                    if e.type == "USES_DATASET" and e.dst in store.nodes
                }
            ),
            "conflicting_evidence": overview["conflicts"],
            "gaps": opportunities,
            "potential_research_directions": [
                {
                    "title": opportunity["title"],
                    "direction": opportunity["hypothesis"],
                    "experiment": opportunity["experiment"],
                    "score": opportunity["opportunity_score"],
                    "trajectory": opportunity["trajectory"]["status"],
                }
                for opportunity in opportunities
            ],
            "gap_scope_note": gaps.get("reason"),
            "excluded_pairs": gaps.get("filtered_pairs", [])[:12],
            "limitations": gaps.get("limitations", []),
            "confidence": opportunities[0]["confidence"] if opportunities else "Low",
            "safety_notice": gaps.get("safety_notice"),
            "sources": sources,
            "provenance": store.health().get("provenance", {}),
            "agents_note": (
                "Executive summary written by the configured LLM over graph evidence."
                if executive_summary
                else "Executive summary composed deterministically from graph metrics (no LLM key configured)."
            ),
        }

    @staticmethod
    def _summary(
        topic: str,
        overview: dict[str, Any],
        opportunities: list[dict[str, Any]],
        gap_reason: str | None = None,
    ) -> str:
        counts = overview["counts"]
        parts = [
            f"The analyzed corpus contains {counts['papers']} papers, {counts['topics']} topics, "
            f"{counts['methods']} methods and {counts['communities']} research communities related to "
            f"'{topic}'.",
        ]
        recency = overview.get("recency") or {}
        if recency:
            parts.append(
                f"{recency.get('papers_since_2023', 0)} of those papers ({recency.get('share_since_2023', 0):.0%}) "
                f"appeared in 2023 or later."
            )
        if opportunities:
            top = opportunities[0]
            parts.append(
                f"The strongest candidate research opportunity is {top['title']} with a prototype "
                f"opportunity score of {top['opportunity_score']}/100 (confidence {top['confidence']}), "
                f"trajectory: {top['trajectory']['status']}."
            )
            parts.append(
                f"It is supported by {len(top['evidence_papers'])} evidence papers and "
                f"{len(top['bridge_papers'])} bridge papers, with {len(top['conflicts'])} claim-level tensions."
            )
        else:
            short = (gap_reason or "").split("(see")[0].strip().rstrip(".")
            parts.append(
                "No cluster pair in this scope produced a candidate opportunity"
                + (f": {short}." if short else ".")
            )
        parts.append(
            "All findings are hypotheses derived from this corpus and must be validated against the wider "
            "literature."
        )
        return " ".join(parts)

    # ----------------------------------------------------------- exports ---
    def to_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# NEXUS RESEARCH OPPORTUNITY REPORT",
            "",
            f"*Generated {report['generated_at']} · NEXUS {report.get('provenance', {}).get('notice', '')}*",
            "",
            "## Topic",
            "",
            f"**{report['topic']}**",
            "",
            "## Executive Summary",
            "",
            report["executive_summary"],
            "",
            "## Research Landscape",
            "",
        ]
        for key, value in report["landscape"].items():
            lines.append(f"- **{key}**: {value if not isinstance(value, dict) else ', '.join(f'{k}={v}' for k, v in value.items())}")
        lines += ["", "## Major Communities", ""]
        for community in report["communities"]:
            lines.append(f"### {community['name']} ({community['paper_count']} papers)")
            lines.append(f"- Top topics: {', '.join(community['top_topics'])}")
            lines.append(f"- Representative papers: {'; '.join(community['top_papers'])}")
            lines.append("")
        lines += ["## Important Papers", ""]
        for paper in report["important_papers"]:
            lines.append(f"- [{paper['title']}]({paper.get('url') or '#'}) ({paper.get('year')}) — PageRank {paper['pagerank']:.5f}")
        lines += ["", "## Bridge Papers", ""]
        for paper in report["bridge_papers"]:
            lines.append(f"- [{paper['title']}]({paper.get('url') or '#'}) — betweenness {paper['betweenness']:.5f}")
        lines += ["", "## Methods", "", ", ".join(report["methods"]) or "—"]
        lines += ["", "## Datasets", "", ", ".join(report["datasets"]) or "—"]
        lines += ["", "## Conflicting Evidence", ""]
        if report["conflicting_evidence"]:
            for conflict in report["conflicting_evidence"]:
                lines.append(
                    f"- (score {conflict['score']}, {conflict['kind']}) \"{conflict['text_a'][:180]}\" "
                    f"vs \"{conflict['text_b'][:180]}\""
                )
        else:
            lines.append("- No claim-level tension detected in this scope.")
        lines += ["", "## Potential Research Gaps", ""]
        if report.get("safety_notice"):
            lines += [f"> {report['safety_notice']}", ""]
        if not report["gaps"]:
            lines += [
                "No candidate pair in this scope was separated enough to score. That is a result, not a failure:",
                "",
                f"- Engine reason: {report.get('gap_scope_note') or 'scope too small for cluster analysis'}",
            ]
            excluded = report.get("excluded_pairs") or []
            if excluded:
                lines += ["", "Pairs the engine refused to count, and why:", ""]
                lines += [f"- {row['a']} × {row['b']} — {row['reason']}" for row in excluded]
            lines.append("")
        for gap in report["gaps"]:
            lines += [
                f"### {gap['title']}",
                "",
                f"- Opportunity score (prototype heuristic): **{gap['opportunity_score']}/100**",
                f"- Confidence: **{gap['confidence']}** — {gap['confidence_reason']}",
                f"- Trajectory: **{gap['trajectory']['status']}** — {gap['trajectory']['note']}",
                "",
                gap["hypothesis"],
                "",
                "Score components:",
                "",
                "| Component | Score | Weight |",
                "|---|---|---|",
            ]
            for component in gap["score_components"]:
                lines.append(f"| {component['label']} | {component['value']:.0f} | {component['weight']} |")
            lines += ["", "Evidence:", ""]
            for paper in gap["evidence_papers"]:
                lines.append(f"- [{paper['title']}]({paper.get('url') or '#'}) ({paper.get('year')}) — {paper['reason']}")
            lines += ["", f"*Candidate experiment:* {gap['experiment']}", ""]
        lines += ["## Candidate Research Directions", ""]
        for direction in report["potential_research_directions"]:
            lines.append(f"- **{direction['title']}** (score {direction['score']}) — {direction['direction'][:220]}")
        lines += ["", "## Limitations", ""]
        for limitation in report["limitations"]:
            lines.append(f"- {limitation}")
        lines += ["", "## Sources", ""]
        for source in report["sources"]:
            lines.append(f"- {source}")
        lines += ["", "---", "", f"*{report['agents_note']}*", ""]
        return "\n".join(lines)
