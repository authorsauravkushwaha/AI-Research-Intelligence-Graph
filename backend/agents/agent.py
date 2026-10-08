"""The NEXUS research agent.

Workflow (exactly what the UI shows in the "reasoning trace" panel):

    question
      -> intent detection                     (rules + optional LLM)
      -> tool selection + execution           (graph queries, algorithms, gaps)
      -> GraphRAG retrieval + expansion       (evidence assembly)
      -> grounded synthesis                   (LLM when configured, template otherwise)
      -> explainability bundle                (claim / reasoning / evidence / confidence)

The agent never answers from parametric knowledge alone: if the graph returns
nothing, it says so and suggests a query that would work.
"""

from __future__ import annotations

import time

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from backend.agents.llm import LLMClient
from backend.agents.tools import ToolRegistry
from backend.rag.graphrag import GraphContext, GraphRAG

log = logging.getLogger("nexus.agent")


@dataclass
class Intent:
    name: str
    confidence: float
    matched: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    question: str
    intent: Intent
    tool_calls: list[dict[str, Any]]
    context: GraphContext
    answer: str
    answer_engine: str
    confidence: str
    confidence_basis: str
    explainability: dict[str, Any]
    follow_ups: list[str]
    used_llm: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": {"name": self.intent.name, "confidence": self.intent.confidence, "matched": self.intent.matched},
            "answer": self.answer,
            "answer_engine": self.answer_engine,
            "used_llm": self.used_llm,
            "confidence": self.confidence,
            "confidence_basis": self.confidence_basis,
            "explainability": self.explainability,
            "tool_calls": [
                {
                    "tool": call["tool"],
                    "arguments": call.get("arguments", {}),
                    "ok": call.get("ok", True),
                    "summary": call.get("summary", ""),
                }
                for call in self.tool_calls
            ],
            "context": {
                "seeds": [
                    {"id": s.id, "label": s.label, "type": s.type, "score": round(s.score, 4), "reason": s.reason}
                    for s in self.context.seeds[:12]
                ],
                "metrics": self.context.metrics,
                "communities": [
                    {"id": c["id"], "name": c["name"], "paper_count": c["paper_count"]}
                    for c in self.context.communities[:6]
                ],
                "retrieval_engine": self.context.retrieval_engine,
                "expansion_engine": self.context.expansion_engine,
            },
            "evidence": self.context.evidence_list(),
            "paths": self.context.paths[:4],
            "predicted_links": self.context.predicted_links[:5],
            "conflicts": self.context.conflicts[:5],
            "follow_ups": self.follow_ups,
        }


INTENT_RULES: list[tuple[str, list[str]]] = [
    ("research_gaps", [r"\bgaps?\b", r"underexplored", r"underexploit", r"missing research", r"what.{0,12}not.*(connected|explored)", r"opportunit"]),
    ("evidence_for_gap", [r"show .{0,12}evidence", r"why did you", r"justify", r"prove it", r"support.*(this|it)", r"source"]),
    ("experiment_design", [r"\bexperiment", r"study design", r"how would you test", r"research plan", r"test this"]),
    ("bridge_papers", [r"bridge", r"connect(s|ing)? .{0,20}(areas|communities|fields|topics)", r"links? between", r"interdisciplin"]),
    ("communities", [r"communit(y|ies)", r"cluster", r"research groups?", r"subfields?"]),
    ("methods_across_fields", [r"methods?.{0,20}(across|multiple|different)", r"shared methods", r"techniques? (used|shared)"]),
    ("growing_topics", [r"growing", r"emerging", r"trend", r"hot topics?", r"momentum", r"over time"]),
    ("important_papers", [r"important", r"influential", r"seminal", r"most (cited|connected|central)", r"key papers?"]),
    ("centrality", [r"pagerank", r"betweenness", r"central", r"structurally important"]),
    ("connect_two_areas", [r"connect .{0,25} and ", r"papers? (that )?(connect|link)", r"between .{0,20} and "]),
    ("contradictions", [r"contradict", r"conflict", r"disagree", r"inconsistent", r"tension"]),
    ("summarize", [r"summar", r"overview", r"what.{0,12}(known|state of)", r"landscape"]),
]


def detect_intent(question: str) -> Intent:
    q = question.lower()
    best = Intent(name="general_qa", confidence=0.35, matched=[])
    for name, patterns in INTENT_RULES:
        hits = [p for p in patterns if re.search(p, q)]
        if hits:
            confidence = min(0.95, 0.55 + 0.15 * len(hits))
            if confidence > best.confidence:
                best = Intent(name=name, confidence=confidence, matched=hits)
    return best


class ResearchAgent:
    def __init__(self, store: Any, gap_engine: Any, registry: ToolRegistry, graphrag: GraphRAG | None = None) -> None:
        self.store = store
        self.gaps = gap_engine
        self.tools = registry
        self.rag = graphrag or GraphRAG(store)
        self.llm = LLMClient()
        self._last_tool_results: list[dict[str, Any]] = []

    # ------------------------------------------------------------ planning
    def _rule_plan(self, question: str, intent: Intent) -> list[dict[str, Any]]:
        topic = self._topic_from_question(question)
        plan: list[dict[str, Any]] = []
        if intent.name in {"research_gaps", "evidence_for_gap", "experiment_design"}:
            plan.append({"tool": "find_research_gaps", "arguments": {"topic": topic, "top_k": 5}})
            plan.append({"tool": "find_bridge_nodes", "arguments": {"label": "Paper", "limit": 8}})
        elif intent.name == "bridge_papers":
            plan.append({"tool": "find_bridge_nodes", "arguments": {"label": "Paper", "limit": 10}})
            plan.append({"tool": "find_research_communities", "arguments": {"limit": 8}})
        elif intent.name == "communities":
            plan.append({"tool": "find_research_communities", "arguments": {"topic": topic, "limit": 10}})
        elif intent.name == "methods_across_fields":
            plan.append({"tool": "query_graph", "arguments": {"node_type": "Method", "sort": "degree", "limit": 20}})
        elif intent.name == "growing_topics":
            plan.append({"tool": "query_graph", "arguments": {"node_type": "Topic", "sort": "pagerank", "limit": 24}})
            plan.append({"tool": "find_research_gaps", "arguments": {"topic": topic, "top_k": 3}})
        elif intent.name in {"important_papers", "centrality"}:
            plan.append({"tool": "find_bridge_nodes", "arguments": {"label": "Paper", "limit": 10}})
            plan.append({"tool": "query_graph", "arguments": {"node_type": "Paper", "sort": "pagerank", "limit": 12}})
        elif intent.name == "contradictions":
            plan.append({"tool": "find_conflicting_claims", "arguments": {"topic": topic, "limit": 8}})
            plan.append({"tool": "find_potential_connections", "arguments": {"limit": 8}})
        elif intent.name == "connect_two_areas":
            areas = self._two_areas_from_question(question)
            if areas:
                plan.append({"tool": "find_connecting_papers", "arguments": {"area_a": areas[0], "area_b": areas[1]}})
            plan.append({"tool": "find_bridge_nodes", "arguments": {"label": "Paper", "limit": 8}})
        else:
            plan.append({"tool": "search_papers", "arguments": {"query": topic or question, "limit": 12}})
        return plan

    @staticmethod
    def _two_areas_from_question(question: str) -> tuple[str, str] | None:
        """Extract the two areas in "which papers connect X and Y?" style questions."""
        patterns = [
            r"connect(?:s|ing)?\s+(.+?)\s+(?:and|with|to)\s+(.+?)(?:\?|\.|$)",
            r"between\s+(.+?)\s+and\s+(.+?)(?:\?|\.|$)",
            r"link(?:s|ing)?\s+(.+?)\s+(?:and|to)\s+(.+?)(?:\?|\.|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, question.lower())
            if match:
                a = match.group(1).strip(" ?.,")
                b = match.group(2).strip(" ?.,")
                if len(a) > 2 and len(b) > 2:
                    return a, b
        return None

    #: Intents whose default scope may be too narrow to cluster; retried corpus-wide.
    _BROADENABLE = {"research_gaps", "evidence_for_gap", "experiment_design"}

    @staticmethod
    def _topic_from_question(question: str) -> str:
        """Extract the research topic phrase from a natural-language question."""
        patterns = [
            r"(?:about|around|on|in|regarding|within)\s+([a-z0-9\- ]{3,60}?)(?:\?|\.|,| and |$)",
            r"(?:gaps?|opportunities|communities|papers)\s+(?:in|for|about)\s+([a-z0-9\- ]{3,60})",
            r"connect\s+([a-z0-9\- ]{3,40}?)\s+and\s+(?:to\s+)?([a-z0-9\- ]{3,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, question.lower())
            if match:
                candidate = match.group(1).strip(" ?.,")
                if len(candidate) > 2:
                    return candidate
        # fall back to the longest noun-ish phrase
        words = [w for w in re.findall(r"[a-z0-9\-]{3,}", question.lower()) if w not in {
            "what", "which", "where", "there", "these", "those", "show", "give", "tell", "would", "could",
            "research", "papers", "paper", "important", "most", "should", "about", "with", "from", "that",
        }]
        return " ".join(words[:3]) if words else "AI Agents"

    def _plan(self, question: str, intent: Intent) -> tuple[list[dict[str, Any]], str]:
        plan = self._rule_plan(question, intent)
        if self.llm.available:
            summary = (
                f"{self.store.stats()['nodes']} nodes, {self.store.stats()['edges']} edges, "
                f"{self.store.stats()['communities']} communities"
            )
            llm_plan = self.llm.plan_tools(question, self.tools.catalogue(), summary)
            if llm_plan:
                valid: list[dict[str, Any]] = []
                for call in llm_plan:
                    name = str(call.get("tool", ""))
                    if name in self.tools.tools:
                        valid.append({"tool": name, "arguments": call.get("arguments") or {}})
                if valid:
                    return valid, "llm-plan"
        return plan, "rule-plan"

    # ----------------------------------------------------------- execution
    def ask(self, question: str, *, depth: int = 2, top_k: int = 12, topic_hint: str | None = None) -> AgentResult:
        """Full agent run: plan → tools → GraphRAG retrieval → grounded answer."""
        return self._run(question, depth=depth, top_k=top_k, topic_hint=topic_hint, emit=None)

    def ask_stages(
        self, question: str, *, depth: int = 2, top_k: int = 12, topic_hint: str | None = None
    ):
        """Yield the agent's pipeline stages as they happen (for the SSE endpoint).

        Each event is `{"stage": ..., "detail": ..., "elapsed_ms": ...}`; the final
        event is `answer` and carries the same payload `ask()` returns. The UI shows
        the agent thinking with real timings instead of a fake typing animation.
        """
        import queue
        import threading

        events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

        def emit(stage: str, detail: Any) -> None:
            events.put({"stage": stage, "detail": detail})

        def worker() -> None:
            try:
                result = self._run(question, depth=depth, top_k=top_k, topic_hint=topic_hint, emit=emit)
                events.put({"stage": "answer", "detail": result.to_json()})
            except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error event
                events.put({"stage": "error", "detail": {"message": str(exc)}})
            finally:
                events.put(None)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            if event is None:
                break
            yield event

    def _run(
        self,
        question: str,
        *,
        depth: int,
        top_k: int,
        topic_hint: str | None,
        emit: Any = None,
    ) -> AgentResult:
        started = time.time()

        def stage(name: str, detail: Any) -> None:
            if emit is not None:
                emit(name, {**detail, "elapsed_ms": int((time.time() - started) * 1000)})

        intent = detect_intent(question)
        stage("intent", {"intent": intent.name, "question": question})
        plan, plan_engine = self._plan(question, intent)
        stage("plan", {"engine": plan_engine, "steps": [c["tool"] for c in plan], "llm": self.llm.available})

        executed: list[dict[str, Any]] = []
        gap_payload: dict[str, Any] | None = None
        focus_nodes: list[str] = []

        for call in plan[:4]:
            outcome = self.tools.execute(call["tool"], call.get("arguments"))
            if (
                call["tool"] == "find_research_gaps"
                and outcome.get("ok")
                and not outcome["result"].get("opportunities")
                and intent.name in self._BROADENABLE
                and (call.get("arguments") or {}).get("topic")
            ):
                # The extracted phrase was too narrow to cluster: answer from the
                # whole corpus and say so, rather than returning an empty result.
                broadened = self.tools.execute(
                    "find_research_gaps",
                    {**call.get("arguments", {}), "topic": None, "top_k": 5},
                )
                if broadened.get("ok") and broadened["result"].get("opportunities"):
                    outcome = broadened
                    call = {**call, "arguments": {**call.get("arguments", {}), "topic": None}, "broadened": True}
            stage("tool", {"tool": call["tool"], "arguments": call.get("arguments", {}), "ok": outcome.get("ok", False),
                           "broadened": bool(call.get("broadened"))})
            record = {"tool": call["tool"], "arguments": call.get("arguments", {}), "ok": outcome.get("ok", False)}
            if outcome.get("ok"):
                result = outcome.get("result", {})
                if call["tool"] == "find_research_gaps":
                    gap_payload = result
                    for opportunity in result.get("opportunities", [])[:2]:
                        focus_nodes.extend(opportunity.get("bridge_papers", [])[:3])
                        for paper in opportunity.get("evidence_papers", [])[:4]:
                            focus_nodes.append(paper["id"])
                    record["summary"] = (
                        f"{len(result.get('opportunities', []))} candidate opportunities over "
                        f"{result.get('scope', {}).get('papers', 0)} scoped papers"
                    )
                else:
                    record["summary"] = self._summarise_tool_result(call["tool"], result)
                record["result"] = result
            else:
                record["summary"] = outcome.get("error", "tool failed")
            executed.append(record)

        self._last_tool_results = executed
        context = self.rag.retrieve(
            question,
            top_k=top_k,
            depth=depth,
            extra_seed_ids=self._seed_ids_from_tools(executed),
            focus_nodes=focus_nodes[:12],
        )
        stage(
            "retrieval",
            {
                "engine": context.retrieval_engine,
                "expansion": context.expansion_engine,
                "seeds": len(context.seeds),
                "papers": len(context.papers),
                "topics": len(context.topics),
                "claims": len(context.claims),
                "conflicts": len(context.conflicts),
                "predicted_links": len(context.predicted_links),
                "paths": len(context.paths),
            },
        )

        answer_payload = self._synthesise(question, intent, context, gap_payload, plan_engine)
        stage("synthesis", {"engine": answer_payload["answer_engine"], "used_llm": answer_payload["used_llm"]})
        explainability = self._explainability(question, intent, context, gap_payload, answer_payload)

        result = AgentResult(
            question=question,
            intent=intent,
            tool_calls=executed,
            context=context,
            answer=answer_payload["answer"],
            answer_engine=answer_payload["answer_engine"],
            confidence=answer_payload["confidence"],
            confidence_basis=answer_payload["confidence_basis"],
            explainability=explainability,
            follow_ups=self._follow_ups(intent, gap_payload, context),
            used_llm=answer_payload["used_llm"],
        )
        stage("done", {"intent": intent.name, "confidence": result.confidence,
                       "tools": [t["tool"] for t in executed]})
        return result

    @staticmethod
    def _summarise_tool_result(tool: str, result: dict[str, Any]) -> str:
        if tool == "search_papers":
            return f"{result.get('count', 0)} papers matched"
        if tool == "find_bridge_nodes":
            return f"top bridge nodes by betweenness: {result.get('nodes', [{}])[0].get('label', 'n/a')[:60]}"
        if tool == "find_research_communities":
            return f"{result.get('count', 0)} communities"
        if tool == "query_graph":
            return f"{result.get('total', 0)} nodes of type {result.get('query', {}).get('node_type')}"
        if tool == "find_potential_connections":
            return f"{result.get('count', 0)} predicted connections"
        if tool == "find_related_papers":
            return f"{result.get('count', 0)} related papers"
        if tool == "get_evidence":
            return "evidence bundle collected"
        return "completed"

    @staticmethod
    def _seed_ids_from_tools(executed: Sequence[dict[str, Any]]) -> list[str]:
        seeds: list[str] = []
        for call in executed:
            result = call.get("result") or {}
            for key in ("papers", "nodes", "communities", "connections"):
                for item in result.get(key, []) or []:
                    if isinstance(item, dict) and item.get("id"):
                        seeds.append(item["id"])
            for key in ("top_papers", "policy"):
                for item in result.get(key, []) or []:
                    if isinstance(item, dict) and item.get("id") and str(item.get("id")).startswith("paper:"):
                        seeds.append(item["id"])
        return seeds[:20]

    # ---------------------------------------------------------- synthesis
    def _template_answer(
        self,
        intent: Intent,
        context: GraphContext,
        gap_payload: dict[str, Any] | None,
        tool_results: Sequence[dict[str, Any]],
    ) -> str:
        """Intent-aware deterministic answer used when no LLM is configured.

        Deliberately not a fake chatbot: it reports the graph payload in the shape
        the intent asked for, and always names the records it used.
        """
        parts: list[str] = []
        by_tool = {call["tool"]: call.get("result") or {} for call in tool_results if call.get("ok")}

        if intent.name in {"research_gaps", "evidence_for_gap", "experiment_design"} and gap_payload:
            top = (gap_payload.get("opportunities") or [None])[0]
            if top:
                parts.append(
                    f"Top candidate: {top['title']} — NEXUS Opportunity Score {top['opportunity_score']}/100 "
                    f"(confidence {top['confidence']}; trajectory: {top['trajectory']['status']})."
                )
                parts.append(top["hypothesis"])
                if intent.name == "experiment_design":
                    parts.append(top["experiment"])
                parts.append(
                    "Score components: "
                    + ", ".join(f"{c['label']} {c['value']:.0f}" for c in top["score_components"])
                    + ". "
                    + top["trajectory"]["note"]
                )
                if top["evidence_papers"]:
                    parts.append(
                        "Supporting papers: "
                        + "; ".join(
                            f"{p['title']} ({p.get('year') or 'n/a'})" for p in top["evidence_papers"][:5]
                        )
                        + "."
                    )
                if top["bridge_papers"]:
                    parts.append(
                        "Bridge papers touching both clusters: "
                        + ", ".join(self.store.nodes[b].name for b in top["bridge_papers"][:4] if b in self.store.nodes)
                        + "."
                    )
                else:
                    parts.append("No paper in the analyzed corpus touches both clusters directly.")
                if len(gap_payload.get("opportunities", [])) > 1:
                    parts.append(
                        "Next candidates: "
                        + ", ".join(o["title"] for o in gap_payload["opportunities"][1:4])
                        + "."
                    )
            else:
                parts.append(
                    "The gap engine found no cluster pair in this scope. "
                    + str(gap_payload.get("reason", ""))
                )

        elif intent.name == "communities":
            communities = (by_tool.get("find_research_communities") or {}).get("communities") or context.communities
            if communities:
                parts.append(
                    f"{len(communities)} research communities were detected (Louvain). Largest: "
                    + "; ".join(
                        f"{c['name']} ({c.get('paper_count')} papers, top topics: {', '.join(c.get('top_topics', [])[:3])})"
                        for c in communities[:4]
                    )
                    + "."
                )
            else:
                parts.append("No communities were detected in this scope.")

        elif intent.name == "connect_two_areas":
            connecting = by_tool.get("find_connecting_papers") or {}
            bridges = connecting.get("bridge_papers") or []
            if bridges:
                parts.append(
                    f"{len(bridges)} paper(s) in the analyzed corpus study topics from both areas: "
                    + "; ".join(f"{b['title']} ({b.get('year') or 'n/a'})" for b in bridges[:5])
                    + "."
                )
            else:
                parts.append(
                    "No paper in the analyzed corpus studies topics from both areas directly — the connection "
                    "is therefore a candidate hypothesis, not a recorded link."
                )
            if connecting.get("shortest_path"):
                hops = connecting["shortest_path"].get("hops") or []
                if hops:
                    parts.append("Structural path: " + " → ".join(h["label"] for h in hops[:4]) + ".")

        elif intent.name in {"bridge_papers", "important_papers", "centrality"}:
            bridges = (by_tool.get("find_bridge_nodes") or {}).get("nodes") or []
            if bridges:
                parts.append(
                    "Highest-betweenness nodes (structural bridges): "
                    + "; ".join(
                        "{label} (betweenness {value:.4f}, degree {degree})".format(
                            label=b["label"], value=b["value"], degree=b.get("degree", "n/a")
                        )
                        for b in bridges[:5]
                    )
                    + "."
                )
            top_papers = (by_tool.get("query_graph") or {}).get("items") or []
            if top_papers:
                parts.append(
                    "Highest PageRank papers in the corpus: "
                    + "; ".join(f"{p['label']} ({p.get('year') or 'n/a'})" for p in top_papers[:5])
                    + "."
                )
            if context.paths:
                hops = context.paths[0].get("hops") or []
                if hops:
                    parts.append("Evidence path: " + " → ".join(hop["label"] for hop in hops[:4]) + ".")

        elif intent.name == "methods_across_fields":
            methods = (by_tool.get("query_graph") or {}).get("items") or []
            if methods:
                parts.append(
                    "Most widely shared methods by degree: "
                    + "; ".join(
                        "{label} ({degree} links)".format(label=m["label"], degree=m.get("degree", "n/a"))
                        for m in methods[:8]
                    )
                    + "."
                )
            if context.papers:
                parts.append(
                    "They appear across communities: "
                    + ", ".join(c["name"] for c in context.communities[:3])
                    + "."
                )

        elif intent.name == "growing_topics":
            years = sorted(p.get("year") for p in context.papers if p.get("year"))
            if years:
                recent = sum(1 for y in years if y >= 2023)
                parts.append(
                    f"Of the {len(years)} retrieved papers, {recent} are from 2023 or later "
                    f"(range {years[0]}–{years[-1]}), so this area is active in the corpus."
                )
            if gap_payload and gap_payload.get("opportunities"):
                parts.append("Candidate opportunity: " + gap_payload["opportunities"][0]["title"] + ".")

        elif intent.name == "contradictions":
            detected = (by_tool.get("find_conflicting_claims") or {}).get("conflicts") or context.conflicts
            if detected:
                parts.append(
                    f"{len(detected)} claim-level tensions were detected (potential contradictions, not established facts):"
                )
                for conflict in detected[:3]:
                    parts.append(
                        f"• (score {conflict['score']}, {conflict['kind']}) \"{conflict['text_a'][:130]}\" "
                        f"vs \"{conflict['text_b'][:130]}\" — because {'; '.join(conflict.get('reasons', [])[:2])}."
                    )
            else:
                parts.append("No claim-level tension was detected inside the retrieved evidence.")

        if not parts:
            return self.rag.answer_offline(context)["answer"]

        closing = " ".join(
            f"[{p['id']}]" for p in context.papers[:6]
        )
        return (
            " ".join(parts)
            + "\n\nEvidence ids: "
            + closing
            + "\nThis answer was assembled from graph evidence without an LLM "
            "(no LLM key configured) — each statement maps to a node, edge or score in the response."
        )

    def _synthesise(
        self,
        question: str,
        intent: Intent,
        context: GraphContext,
        gap_payload: dict[str, Any] | None,
        plan_engine: str,
    ) -> dict[str, Any]:
        prompt = self._task_prompt(question, intent, gap_payload)
        llm_result = self.llm.complete(prompt, context.to_prompt()) if self.llm.available else None

        if llm_result is not None:
            return {
                "answer": llm_result.text.strip(),
                "answer_engine": llm_result.engine,
                "confidence": self._confidence_from_context(context, gap_payload),
                "confidence_basis": (
                    f"grounded on {len(context.papers)} papers, {len(context.claims)} claims and "
                    f"{len(context.communities)} communities retrieved from the graph; plan={plan_engine}"
                ),
                "used_llm": True,
            }

        offline = self.rag.answer_offline(context)
        answer = self._template_answer(intent, context, gap_payload, self._last_tool_results)
        if gap_payload and intent.name in {"research_gaps", "evidence_for_gap", "experiment_design"}:
            top = (gap_payload.get("opportunities") or [None])[0]
            if top:
                answer = (
                    f"Top candidate opportunity in the analyzed corpus: {top['title']} "
                    f"(NEXUS Opportunity Score {top['opportunity_score']}/100, confidence {top['confidence']}, "
                    f"trajectory: {top['trajectory']['status']}). {top['hypothesis']} "
                    f"Evidence: {len(top['evidence_papers'])} supporting papers, "
                    f"{len(top['bridge_papers'])} bridge papers, {len(top['conflicts'])} claim tensions. "
                    f"{gap_payload.get('safety_notice', '')}"
                )
        return {
            "answer": answer,
            "answer_engine": offline["answer_engine"] if not gap_payload else "graph-template+gap-engine",
            "confidence": self._confidence_from_context(context, gap_payload),
            "confidence_basis": offline["confidence_basis"],
            "used_llm": False,
        }

    @staticmethod
    def _task_prompt(question: str, intent: Intent, gap_payload: dict[str, Any] | None) -> str:
        extra = ""
        if gap_payload and gap_payload.get("opportunities"):
            top = gap_payload["opportunities"][0]
            components = ", ".join(
                "{label}={value}".format(label=comp["label"], value=comp["value"])
                for comp in top["score_components"]
            )
            extra = (
                "\n\nThe gap engine already produced this candidate (use it, do not re-derive it):\n"
                "- title: {title}\n- score: {score}/100 (components: {components})\n"
                "- trajectory: {status} — {note}\n"
                "- confidence: {confidence} — {reason}\n"
                "- bridge papers: {bridges}\n"
                "- safety notice to repeat: {notice}"
            ).format(
                title=top["title"],
                score=top["opportunity_score"],
                components=components,
                status=top["trajectory"]["status"],
                note=top["trajectory"]["note"],
                confidence=top["confidence"],
                reason=top["confidence_reason"],
                bridges=", ".join(top["bridge_papers"]) or "none",
                notice=gap_payload.get("safety_notice"),
            )
        return (
            f"Answer the research question using ONLY the graph context.\n"
            f"Detected intent: {intent.name}.{extra}\n\nQUESTION: {question}"
        )

    @staticmethod
    def _confidence_from_context(context: GraphContext, gap_payload: dict[str, Any] | None) -> str:
        papers = len(context.papers)
        claims = len(context.claims)
        if gap_payload and gap_payload.get("opportunities"):
            level = gap_payload["opportunities"][0]["confidence"]
            return {"Medium-High": "medium-high", "Medium": "medium", "Low": "low"}.get(level, "medium")
        if papers >= 12 and claims >= 4:
            return "medium-high"
        if papers >= 5:
            return "medium"
        return "low"

    # ------------------------------------------------------ explainability
    def _explainability(
        self,
        question: str,
        intent: Intent,
        context: GraphContext,
        gap_payload: dict[str, Any] | None,
        answer_payload: dict[str, Any],
    ) -> dict[str, Any]:
        claim = question
        reasoning: list[str] = []
        if gap_payload and gap_payload.get("opportunities"):
            top = gap_payload["opportunities"][0]
            claim = f"{top['title']} is a candidate underexplored connection in the analyzed corpus."
            reasoning = list(top["why"])
        else:
            reasoning.append(
                f"Retrieved {len(context.papers)} papers, {len(context.topics)} topics and "
                f"{len(context.claims)} claims through vector search plus graph expansion."
            )
            if context.communities:
                reasoning.append(
                    "Retrieved papers span communities: "
                    + ", ".join(c["name"] for c in context.communities[:3])
                    + "."
                )
            if context.paths:
                reasoning.append(
                    "Shortest relationship path between the two most central retrieved papers: "
                    + context.paths[0]["hops"][0]["label"]
                    + " …"
                    if context.paths[0].get("hops")
                    else "Retrieved papers are directly connected."
                )
            if context.conflicts:
                reasoning.append(
                    f"{len(context.conflicts)} claim-level tension(s) were detected inside the retrieved set."
                )

        return {
            "claim": claim,
            "reasoning": reasoning,
            "graph_evidence": {
                "seeds": [
                    {"id": s.id, "label": s.label, "type": s.type, "why": s.reason, "score": round(s.score, 4)}
                    for s in context.seeds[:8]
                ],
                "communities": [{"id": c["id"], "name": c["name"]} for c in context.communities[:4]],
                "paths": context.paths[:3],
                "predicted_links": context.predicted_links[:4],
                "metrics_reference": context.metrics,
            },
            "supporting_papers": [
                {
                    "id": p["id"],
                    "title": p["title"],
                    "year": p.get("year"),
                    "url": p.get("url"),
                    "why": p.get("reason"),
                    "summary_source": p.get("summary_source"),
                }
                for p in context.papers[:10]
            ],
            "confidence": answer_payload["confidence"],
            "confidence_basis": answer_payload["confidence_basis"],
            "safety_notice": (
                gap_payload.get("safety_notice")
                if gap_payload
                else "Answers are generated from the analyzed corpus; treat findings as hypotheses to validate."
            ),
        }

    @staticmethod
    def _follow_ups(intent: Intent, gap_payload: dict[str, Any] | None, context: GraphContext) -> list[str]:
        base = [
            "Show me the evidence for this.",
            "Which papers bridge these areas?",
            "What experiment could investigate this?",
            "Which communities exist around this topic?",
        ]
        if intent.name == "research_gaps" and gap_payload and gap_payload.get("opportunities"):
            second = gap_payload["opportunities"][1] if len(gap_payload["opportunities"]) > 1 else None
            if second:
                base.insert(1, f"Why is '{second['title']}' ranked lower?")
        return base[:4]
