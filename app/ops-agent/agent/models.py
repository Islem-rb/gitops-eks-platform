from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence(BaseModel):
    """One piece of collected data. A failed collection is recorded, never hidden."""

    id: str
    source: str
    collected_at: str = Field(default_factory=utcnow)
    ok: bool = True
    error: str | None = None
    data: Any = None


class Bundle(BaseModel):
    """Everything an investigation saw. Rules are a pure function of this, so it can be replayed."""

    namespace: str = "demo"
    app: str = "api"
    trigger: str = "manual"
    alerts: list[str] = []
    collected_at: str = Field(default_factory=utcnow)
    evidence: list[Evidence] = []

    def get(self, evidence_id: str) -> Evidence | None:
        return next((e for e in self.evidence if e.id == evidence_id), None)

    def data(self, evidence_id: str, default: Any = None) -> Any:
        item = self.get(evidence_id)
        return item.data if item and item.ok and item.data is not None else default


class Finding(BaseModel):
    category: str
    title: str
    explanation: str
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str] = []
    unknowns: list[str] = []
    next_steps: list[str] = []


class Hypothesis(BaseModel):
    cause: str
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str]


class LLMAnalysis(BaseModel):
    summary: str
    hypotheses: list[Hypothesis]
    unknowns: list[str]
    next_steps: list[str]


class LLMUsage(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    estimated_usd: float


class RevertProposal(BaseModel):
    status: Literal[
        "not_run",
        "disabled",
        "not_applicable",
        "no_candidate",
        "ambiguous",
        "not_eligible",
        "conflict",
        "already_proposed",
        "proposed",
        "error",
    ] = "not_run"
    commit: str | None = None
    pr_url: str | None = None


class Report(BaseModel):
    id: str
    created_at: str = Field(default_factory=utcnow)
    engine: str = "rules"
    passes: int = 1
    llm_status: str = "not_run"
    analysis: LLMAnalysis | None = None
    llm_usage: LLMUsage | None = None
    revert: RevertProposal = RevertProposal()
    findings: list[Finding]
    bundle: Bundle
