"""The AI agents page: whether Claude is on, what each agent does and its recent work. Never exposes a credential."""
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from provenant.ai.harness import MODEL
from provenant.api.db import ContractDraftRow, ContractFindingRow, ContractQuestionRow, ContractReviewRow, Document

ACTIVITY_LIMIT = 25


class EngineOut(BaseModel):
    mode: str  # keyword | claude | needs_setup
    label: str
    model: str | None
    detail: str


class AgentOut(BaseModel):
    key: str
    name: str
    description: str
    tab: str
    available: bool
    note: str | None
    runs: int
    waiting: int


class ActivityOut(BaseModel):
    kind: str
    id: str
    document_id: str
    filename: str
    title: str
    status: str
    agent: str | None
    by: str
    at: datetime


class ContractOut(BaseModel):
    id: str
    filename: str
    kind: str


class AgentsOut(BaseModel):
    engine: EngineOut
    agents: list[AgentOut]
    contracts: list[ContractOut]
    activity: list[ActivityOut]


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


def engine_status() -> EngineOut:
    """What this server is set to use. It makes no API call, so `claude` means configured, not proven reachable."""
    choice = os.environ.get("PROVENANT_LEGAL_AGENT", "baseline")
    if choice == "baseline":
        return EngineOut(mode="keyword", label="Free keyword reader", model=None,
                         detail="Finds where topics are mentioned and builds draft skeletons. Claude is not on.")
    if choice not in ("claude", "claude-api"):
        return EngineOut(mode="needs_setup", label="Unknown agent setting", model=None,
                         detail="PROVENANT_LEGAL_AGENT must be baseline, claude-api or claude.")
    if not _has_credentials():
        return EngineOut(mode="needs_setup", label="Claude needs an API key", model=MODEL,
                         detail="Claude is selected, but this server has no Anthropic credentials, so runs will fail.")
    if choice == "claude" and not (os.environ.get("PROVENANT_LEGAL_AGENT_ID")
                                   and os.environ.get("PROVENANT_LEGAL_ENVIRONMENT_ID")):
        return EngineOut(mode="needs_setup", label="Managed agent not set up", model=MODEL,
                         detail="Run scripts/setup_legal_agent.py and set the two ids it prints.")
    how = ("Every agent runs through Provenant's own harness." if choice == "claude-api" else
           "Contract agents run through Managed Agents; practice agents through Provenant's own harness.")
    return EngineOut(mode="claude", label="Claude is on", model=MODEL,
                     detail=f"{how} Each run costs money and sends what that agent reads (a contract, a court notice, "
                            "party names, unmatched trust items) to Anthropic.")


def _count(s: Session, row, *where) -> int:
    return s.scalar(select(func.count()).select_from(row).where(*where)) or 0


def overview(s: Session, tenant_id: str) -> AgentsOut:
    managed = os.environ.get("PROVENANT_LEGAL_AGENT") == "claude"
    agents = [
        AgentOut(key="reviewer", name="Contract reviewer", tab="review", available=True, note=None,
                 description="Reads a contract against the firm's playbook and records cited findings for a lawyer.",
                 runs=_count(s, ContractReviewRow, ContractReviewRow.tenant_id == tenant_id),
                 waiting=_count(s, ContractFindingRow, ContractFindingRow.tenant_id == tenant_id,
                                ContractFindingRow.decision.is_(None))),
        AgentOut(key="questions", name="Contract Q&A", tab="review", available=True,
                 note="Ask after a contract has been reviewed.",
                 description="Answers questions from the contract and quotes the wording it relied on.",
                 runs=_count(s, ContractQuestionRow, ContractQuestionRow.tenant_id == tenant_id), waiting=0),
        AgentOut(key="drafter", name="Paralegal drafter", tab="drafts", available=not managed,
                 note="Needs the claude-api setting." if managed else None,
                 description="Prepares notices, amendments and letters for a lawyer to edit and approve.",
                 runs=_count(s, ContractDraftRow, ContractDraftRow.tenant_id == tenant_id),
                 waiting=_count(s, ContractDraftRow, ContractDraftRow.tenant_id == tenant_id,
                                ContractDraftRow.status.in_(("ready", "returned")))),
    ]
    contracts = s.scalars(select(Document).where(Document.tenant_id == tenant_id, Document.kind != "amendment",
                                                 Document.status == "extracted").order_by(Document.filename)).all()
    names = {d.id: d.filename for d in s.scalars(select(Document).where(Document.tenant_id == tenant_id))}
    newest = lambda row, at: s.scalars(select(row).where(row.tenant_id == tenant_id).order_by(at.desc()).limit(ACTIVITY_LIMIT))
    activity = [ActivityOut(kind="review", id=r.id, document_id=r.document_id, filename=names.get(r.document_id, ""),
                            title="Contract review", status=r.status, agent=r.agent, by=r.requested_by, at=r.created_at)
                for r in newest(ContractReviewRow, ContractReviewRow.created_at)]
    activity += [ActivityOut(kind="question", id=q.id, document_id=q.document_id, filename=names.get(q.document_id, ""),
                             title=q.question[:120], status=q.status, agent=None, by=q.asked_by, at=q.created_at)
                 for q in newest(ContractQuestionRow, ContractQuestionRow.created_at)]
    activity += [ActivityOut(kind="draft", id=d.id, document_id=d.document_id, filename=names.get(d.document_id, ""),
                             title=d.title or f"{d.kind.capitalize()} draft", status=d.status, agent=d.agent,
                             by=d.requested_by, at=d.created_at)
                 for d in newest(ContractDraftRow, ContractDraftRow.created_at)]
    activity.sort(key=lambda a: a.at, reverse=True)
    return AgentsOut(engine=engine_status(), agents=agents,
                     contracts=[ContractOut(id=d.id, filename=d.filename, kind=d.kind) for d in contracts],
                     activity=activity[:ACTIVITY_LIMIT])


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/ai/agents", response_model=AgentsOut)
    def get_agents(p: Auth, s: Db) -> AgentsOut:
        return overview(s, p.tenant_id)
