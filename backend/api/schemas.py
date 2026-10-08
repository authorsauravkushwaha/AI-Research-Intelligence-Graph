"""Request/response schemas for the NEXUS API.

Pydantic models give the OpenAPI docs (§33) real types, and they bound every
user-supplied number *before* it reaches an algorithm — a request cannot ask the
server for an unbounded traversal.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Label = Literal["Paper", "Author", "Topic", "Method", "Dataset", "Claim", "Community"]
Metric = Literal["pagerank", "betweenness", "degree"]


class GapRequest(BaseModel):
    topic: str | None = Field(default=None, max_length=160, examples=["AI Agents"])
    year_min: int | None = Field(default=None, ge=1900, le=2100)
    year_max: int | None = Field(default=None, ge=1900, le=2100)
    field: str | None = Field(default=None, max_length=120)
    min_papers: int = Field(default=3, ge=1, le=50)
    top_k: int = Field(default=5, ge=1, le=25)


class AgentRequest(BaseModel):
    question: str = Field(min_length=2, max_length=600, examples=["What are the most important research gaps in AI agents?"])
    depth: int = Field(default=2, ge=1, le=3)
    top_k: int = Field(default=12, ge=3, le=30)
    topic_hint: str | None = Field(default=None, max_length=160)


class ReportRequest(GapRequest):
    executive_summary: str | None = Field(default=None, max_length=4000)


class GraphRequest(BaseModel):
    seeds: list[str] | None = Field(default=None, max_length=60)
    query: str | None = Field(default=None, max_length=200)
    depth: int = Field(default=1, ge=0, le=3)
    node_types: list[Label] | None = None
    rel_types: list[str] | None = Field(default=None, max_length=20)
    year_min: int | None = Field(default=None, ge=1900, le=2100)
    year_max: int | None = Field(default=None, ge=1900, le=2100)
    include_predicted: bool = True
    focus: str | None = Field(default=None, max_length=120, description="Highlight this node and its neighbourhood")
    max_nodes: int = Field(default=260, ge=10, le=400)
    max_edges: int = Field(default=800, ge=10, le=1200)


class ExpandRequest(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=40)
    rel_types: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(default=60, ge=1, le=200)


class ExportRequest(BaseModel):
    format: Literal["graphml", "csv", "json", "cypher"] = "graphml"
    node_types: list[Label] | None = None
    rel_types: list[str] | None = Field(default=None, max_length=24)
    limit: int = Field(default=800, ge=10, le=5000)


class PlanRequest(BaseModel):
    """A NEXUS query-DSL request for the planner route (§4.3)."""

    query: str = Field(
        min_length=1,
        max_length=400,
        description='Query DSL, e.g. topic:"AI Agents" type in (Paper, Method) rel in (CITES) limit 50',
    )


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[dict[str, Any]]
