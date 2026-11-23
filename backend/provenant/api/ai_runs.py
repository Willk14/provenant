"""The AI run ledger: stores what each Claude run used, for the tenant it ran for (ADR 0014)."""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from provenant.ai import RunRecord, capture
from provenant.ai.harness import PRICES_VERSION
from provenant.api.auth import PARTNER, Principal
from provenant.api.db import AiRunRow, tenant_session
from provenant.api.services import require

log = logging.getLogger(__name__)


def _row(tenant_id: str, document_id: str | None, run: RunRecord) -> AiRunRow:
    return AiRunRow(
        tenant_id=tenant_id, document_id=document_id, purpose=run.purpose[:32], models="+".join(run.models)[:128],
        status=run.status, stop_reason=(run.stop_reason or None) and run.stop_reason[:32], error_type=run.error_type,
        turns=run.turns, tool_calls=run.tool_calls, input_tokens=run.input_tokens, output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens, cache_write_tokens=run.cache_write_tokens,
        cost_microusd=run.cost_microusd, prices_version=PRICES_VERSION, duration_ms=run.duration_ms,
    )


@contextmanager
def recording(factory: sessionmaker[Session], tenant_id: str, document_id: str | None) -> Iterator[None]:
    """Store every run made inside this block. A ledger failure is logged and never fails work already paid for."""
    with capture() as runs:
        try:
            yield
        finally:
            if runs:
                try:
                    with tenant_session(factory, tenant_id) as s:
                        s.add_all(_row(tenant_id, document_id, run) for run in runs)
                        s.commit()
                except Exception:
                    log.exception("could not record %s AI run(s)", len(runs))


class AiRunOut(BaseModel):
    id: str
    document_id: str | None
    purpose: str
    models: str
    status: str
    stop_reason: str | None
    error_type: str | None
    turns: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_microusd: int | None
    prices_version: str
    duration_ms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AiUsageOut(BaseModel):
    total_runs: int
    failed_runs: int
    unpriced_runs: int  # their cost is not in the total
    estimated_cost_microusd: int
    runs: list[AiRunOut]


def usage(s: Session, p: Principal, limit: int) -> AiUsageOut:
    require(p, PARTNER)
    scope = AiRunRow.tenant_id == p.tenant_id
    total = s.scalar(select(func.count()).select_from(AiRunRow).where(scope)) or 0
    failed = s.scalar(select(func.count()).select_from(AiRunRow).where(scope, AiRunRow.status != "completed")) or 0
    unpriced = s.scalar(select(func.count()).select_from(AiRunRow).where(scope, AiRunRow.cost_microusd.is_(None))) or 0
    cost = s.scalar(select(func.coalesce(func.sum(AiRunRow.cost_microusd), 0)).where(scope))
    rows = s.scalars(select(AiRunRow).where(scope).order_by(AiRunRow.created_at.desc(), AiRunRow.id).limit(limit)).all()
    return AiUsageOut(total_runs=total, failed_runs=failed, unpriced_runs=unpriced, estimated_cost_microusd=int(cost),
                      runs=[AiRunOut.model_validate(r) for r in rows])


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/ai/runs", response_model=AiUsageOut)
    def get_runs(p: Auth, s: Db, limit: int = Query(default=100, ge=1, le=500)) -> AiUsageOut:
        return usage(s, p, limit)
