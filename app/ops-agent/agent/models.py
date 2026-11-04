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


class Report(BaseModel):
    id: str
    created_at: str = Field(default_factory=utcnow)
    engine: str = "rules"
    findings: list[Finding]
    bundle: Bundle
