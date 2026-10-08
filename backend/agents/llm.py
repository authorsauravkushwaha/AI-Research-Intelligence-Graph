"""LLM client + prompt construction.

Design rules that matter for a research tool:

* **Grounding first.** The system prompt forbids answering from parametric
  knowledge when graph evidence is missing, and requires the model to say so.
* **Never invent citations.** The model may only cite the papers it was given,
  and it is told the corpus is a curated demo slice.
* **Graceful degradation.** With no API key the caller gets `None` and the agent
  falls back to the deterministic graph-template answer — no fake "AI" text.
* **Provider-agnostic.** Any OpenAI-compatible `/chat/completions` endpoint
  (OpenAI, Azure-style gateways, Ollama, vLLM, LM Studio) works by setting
  `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from backend.config import get_settings

log = logging.getLogger("nexus.llm")

SYSTEM_PROMPT = """You are NEXUS, a research intelligence assistant working over a KNOWLEDGE GRAPH of research papers.

NON-NEGOTIABLE RULES
1. Ground every factual statement in the GRAPH CONTEXT you are given. Never use outside knowledge as if it were evidence.
2. Never invent papers, authors, years, arXiv ids, URLs or citations. Only cite records that appear in the context, using the exact ids/titles provided.
3. Never claim that something is unresearched. Say "potentially underexplored", "few connections in the analyzed corpus", or "limited evidence found here".
4. The analyzed corpus is a curated demo slice of ~150 real papers. State this limitation when it affects the answer.
5. When the context is insufficient, say what is missing and which graph query would find it.
6. Distinguish clearly between: (a) what the corpus contains, (b) what the graph algorithms measured, (c) what you infer. Label inference as inference.
7. Be concise and structured: short paragraphs or bullets. Prefer numbers from the context (pagerank, betweenness, community, scores) over vague wording.

ANSWER SHAPE
- Direct answer (2-4 sentences).
- Evidence: bullet list, each bullet naming a specific paper/claim id from the context.
- Graph reasoning: which structural facts (communities, bridges, sparsity, centrality) support the answer.
- Confidence: high / medium / low, with a one-line reason.
- What would falsify this: one line describing the check a researcher should run."""


@dataclass(slots=True)
class LLMResult:
    text: str
    engine: str
    model: str
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def available(self) -> bool:
        return self.settings.llm.available

    def describe(self) -> dict[str, Any]:
        s = self.settings.llm
        return {
            "configured": self.available,
            "provider": s.provider,
            "model": s.model if self.available else None,
            "base_url": s.base_url if self.available else None,
            "mode": "llm" if self.available else "graph-template (no LLM key configured)",
        }

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult | None:
        s = self.settings.llm
        if not self.available:
            return None
        payload: dict[str, Any] = {
            "model": s.model,
            "messages": list(messages),
            "temperature": s.temperature if temperature is None else temperature,
            "max_tokens": s.max_tokens if max_tokens is None else max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {s.api_key}"}
        try:
            with httpx.Client(timeout=s.timeout) as client:
                resp = client.post(f"{s.base_url}/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001 - any failure must degrade, never 500 the API
            log.warning("LLM call failed: %s", exc)
            return None

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("LLM response missing choices: %s", str(data)[:300])
            return None
        return LLMResult(
            text=text,
            engine=f"llm:{s.provider}",
            model=s.model,
            usage=data.get("usage"),
            raw=data,
        )

    def complete(self, prompt: str, context: str, *, temperature: float | None = None) -> LLMResult | None:
        return self.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{context}\n\nTASK: {prompt}"},
            ],
            temperature=temperature,
        )

    def plan_tools(self, question: str, tool_catalogue: list[dict[str, Any]], context_summary: str) -> list[dict[str, Any]] | None:
        """Ask the model which tools to run. Returns None when unavailable/invalid.

        The model may only choose tools from the catalogue, and arguments are
        validated by the executor before anything touches the graph.
        """
        catalogue = json.dumps(tool_catalogue, indent=1)
        result = self.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You route a research question to graph tools. Reply with JSON only: "
                        '{"tool_calls":[{"tool":"<name>","arguments":{...}}],"intent":"<short label>"}. '
                        "Choose 1-3 tools. Use only the tools listed. Do not answer the question."
                    ),
                },
                {
                    "role": "user",
                    "content": f"TOOLS:\n{catalogue}\n\nCORPUS SUMMARY:\n{context_summary}\n\nQUESTION: {question}",
                },
            ],
            temperature=0.0,
            max_tokens=400,
            json_mode=True,
        )
        if result is None:
            return None
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError:
            log.info("tool plan was not valid JSON; using rule-based routing")
            return None
        calls = payload.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            return None
        return calls
