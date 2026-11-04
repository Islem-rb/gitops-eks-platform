from collections.abc import Callable

import httpx

from ..models import Evidence, utcnow


def safely(evidence_id: str, source: str, fetch: Callable[[], object]) -> Evidence:
    """Run one collection. A failure becomes evidence that it failed, not a crash."""
    try:
        return Evidence(id=evidence_id, source=source, collected_at=utcnow(), data=fetch())
    except httpx.HTTPStatusError as exc:  # a 403 here usually means missing RBAC
        error = f"HTTP {exc.response.status_code}"
        return Evidence(id=evidence_id, source=source, ok=False, error=error)
    except Exception as exc:  # any collector failure must leave the others running
        return Evidence(id=evidence_id, source=source, ok=False, error=type(exc).__name__)
