"""HTTP routes for contract review: the firm's playbook, agent reviews, decisions and follow-up questions."""
import json
from datetime import datetime
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from provenant.api import legal, retrieval
from provenant.legal import DEFAULT_TOPICS, PLAYBOOK_VERSION, fingerprint
from provenant.legal.embeddings import embedder_from_env


class PlaybookTopicOut(BaseModel):
    key: str
    label: str
    position: str
    standard: bool


class PlaybookOut(BaseModel):
    version: str
    sha256: str
    updated_by: str | None
    topics: list[PlaybookTopicOut]


class PlaybookIn(BaseModel):
    positions: dict[str, str] = Field(max_length=32)


class CitationOut(BaseModel):
    document_id: str
    filename: str
    quote: str
    page: int
    bbox: tuple[float, float, float, float]


class FindingOut(BaseModel):
    id: str
    topic: str
    severity: str
    title: str
    explanation: str
    suggestion: str
    citation: CitationOut
    proposed_term: str | None
    proposed_value: str | None
    decision: str | None
    decision_note: str | None
    decided_by: str | None
    field_id: str | None

    @classmethod
    def of(cls, r) -> "FindingOut":
        return cls(id=r.id, topic=r.topic, severity=r.severity, title=r.title, explanation=r.explanation,
                   suggestion=r.suggestion, citation=CitationOut(
                       document_id=r.document_id, filename=r.filename, quote=r.quote, page=r.page,
                       bbox=(r.x0, r.top, r.x1, r.bottom)),
                   proposed_term=r.proposed_term, proposed_value=r.proposed_value, decision=r.decision,
                   decision_note=r.decision_note, decided_by=r.decided_by, field_id=r.field_id)


class QuestionOut(BaseModel):
    id: str
    question: str
    status: str
    answer: str | None
    not_stated: bool
    citations: list[CitationOut]
    error: str | None
    asked_by: str
    created_at: datetime

    @classmethod
    def of(cls, r) -> "QuestionOut":
        return cls(id=r.id, question=r.question, status=r.status, answer=r.answer, not_stated=r.not_stated,
                   citations=json.loads(r.citations), error=r.error, asked_by=r.asked_by, created_at=r.created_at)


class ReviewSummaryOut(BaseModel):
    id: str
    status: str
    agent: str | None
    playbook_version: str
    playbook_sha256: str
    summary: str | None
    not_found: list[str]
    error: str | None
    requested_by: str
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def fields(cls, r) -> dict:
        return dict(id=r.id, status=r.status, agent=r.agent, playbook_version=r.playbook_version,
                    playbook_sha256=r.playbook_sha256, summary=r.summary, not_found=json.loads(r.not_found),
                    error=r.error, requested_by=r.requested_by, created_at=r.created_at, finished_at=r.finished_at)

    @classmethod
    def of(cls, r) -> "ReviewSummaryOut":
        return cls(**cls.fields(r))


class ReviewOut(ReviewSummaryOut):
    findings: list[FindingOut]
    questions: list[QuestionOut]


class DecisionIn(BaseModel):
    decision: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=1000)


class QuestionIn(BaseModel):
    question: str = Field(max_length=1000)


class IndexStateOut(BaseModel):
    model: str
    semantic: bool
    documents: int
    ready: int
    running: int
    failed: int
    queued: int = 0


class SearchIn(BaseModel):
    query: str = Field(max_length=1000)  # in the body, so contract questions stay out of URLs and access logs
    document_id: str | None = Field(default=None, max_length=36)
    limit: int = Field(default=8, ge=1, le=20)


class SearchHitOut(BaseModel):
    citation: CitationOut
    text: str
    score: float
    similarity: float | None
    matched: list[str]


def add_routes(app: FastAPI, Auth, Db) -> None:
    def full(s, tenant_id: str, row) -> ReviewOut:
        return ReviewOut(
            **ReviewSummaryOut.fields(row),
            findings=[FindingOut.of(f) for f in legal.findings(s, tenant_id, row.id)],
            questions=[QuestionOut.of(q) for q in legal.questions(s, tenant_id, row.id)],
        )

    def playbook_out(topics, row) -> PlaybookOut:
        standard = {t.key: t.position for t in DEFAULT_TOPICS}
        return PlaybookOut(version=PLAYBOOK_VERSION, sha256=fingerprint(topics), updated_by=row.updated_by if row else None,
                           topics=[PlaybookTopicOut(key=t.key, label=t.label, position=t.position,
                                                    standard=t.position == standard[t.key]) for t in topics])

    @app.get("/legal/playbook", response_model=PlaybookOut)
    def get_playbook(p: Auth, s: Db) -> PlaybookOut:
        return playbook_out(*legal.playbook(s, p.tenant_id))

    @app.put("/legal/playbook", response_model=PlaybookOut)
    def put_playbook(body: PlaybookIn, p: Auth, s: Db) -> PlaybookOut:
        legal.save_playbook(s, p, body.positions)
        return playbook_out(*legal.playbook(s, p.tenant_id))

    @app.get("/legal/index", response_model=IndexStateOut)
    def get_index(p: Auth, s: Db) -> IndexStateOut:
        return IndexStateOut(**retrieval.index_state(s, embedder_from_env(), p.tenant_id))

    @app.post("/legal/index", response_model=IndexStateOut, status_code=202)
    def build_index(p: Auth, s: Db) -> IndexStateOut:
        embedder = embedder_from_env()
        queued = retrieval.request_indexing(s, p, embedder)
        return IndexStateOut(**retrieval.index_state(s, embedder, p.tenant_id), queued=queued)

    @app.post("/legal/search", response_model=list[SearchHitOut])
    def search(body: SearchIn, p: Auth, s: Db) -> list[SearchHitOut]:
        scope = retrieval.contract_family(s, p.tenant_id, body.document_id) if body.document_id else None
        hits = retrieval.search(s, embedder_from_env(), p.tenant_id, body.query, scope, body.limit)
        return [SearchHitOut(
            citation=CitationOut(document_id=h.document_id, filename=h.filename, quote=h.text, page=h.page, bbox=h.bbox),
            text=h.text, score=round(h.score, 6), similarity=None if h.similarity is None else round(h.similarity, 4),
            matched=list(h.matched),
        ) for h in hits]

    @app.get("/documents/{document_id}/reviews", response_model=list[ReviewSummaryOut])
    def list_reviews(document_id: str, p: Auth, s: Db) -> list[ReviewSummaryOut]:
        return [ReviewSummaryOut.of(r) for r in legal.list_reviews(s, p.tenant_id, document_id)]

    @app.post("/documents/{document_id}/reviews", response_model=ReviewOut, status_code=202)
    def start_review(document_id: str, p: Auth, s: Db) -> ReviewOut:
        return full(s, p.tenant_id, legal.start_review(s, p, document_id))

    @app.get("/documents/{document_id}/reviews/{review_id}", response_model=ReviewOut)
    def get_review(document_id: str, review_id: str, p: Auth, s: Db) -> ReviewOut:
        return full(s, p.tenant_id, legal.get_review(s, p.tenant_id, document_id, review_id))

    @app.put("/documents/{document_id}/reviews/{review_id}/findings/{finding_id}", response_model=FindingOut)
    def decide(document_id: str, review_id: str, finding_id: str, body: DecisionIn, p: Auth, s: Db) -> FindingOut:
        return FindingOut.of(legal.decide_finding(s, p, document_id, review_id, finding_id, body.decision, body.note))

    @app.post("/documents/{document_id}/reviews/{review_id}/questions", response_model=QuestionOut, status_code=202)
    def ask(document_id: str, review_id: str, body: QuestionIn, p: Auth, s: Db) -> QuestionOut:
        return QuestionOut.of(legal.ask_question(s, p, document_id, review_id, body.question))
