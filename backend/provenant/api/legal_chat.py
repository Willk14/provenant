"""Contract chat: a person asks, and the agent answers from the contract with checked citations (ADR 0016)."""
import json
import logging
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import ContractChatMessageRow, ContractChatRow, Job, tenant_session
from provenant.api.legal import _citation, _contract, _documents, _failure, _retriever
from provenant.api.legal_routes import CitationOut
from provenant.api.services import Conflict, Invalid, NotFound, list_amendments, require
from provenant.api.storage import BlobStore
from provenant.legal.agent import ReviewAgent
from provenant.legal.chat import MAX_MESSAGE, MIN_MESSAGE, ChatToolbox

log = logging.getLogger(__name__)
CHAT_JOB = "legal_chat"
BUSY = ("queued", "running")
MAX_CHATS_LISTED = 30


def _clean(message: str) -> str:
    message = message.strip()
    if not MIN_MESSAGE <= len(message) <= MAX_MESSAGE:
        raise Invalid(f"a message must be {MIN_MESSAGE} to {MAX_MESSAGE} characters")
    return message


def _ready(s: Session, tenant_id: str, document_id: str):
    doc = _contract(s, tenant_id, document_id)
    if any(d.status != "extracted" for d in (doc, *list_amendments(s, tenant_id, doc.id))):
        raise Conflict("wait until the contract and its amendments have been read")
    return doc


def _append(s: Session, chat: ContractChatRow, role: str, text: str, author: str, citations: list | None = None,
            not_stated: bool = False) -> None:
    position = s.scalar(select(func.count()).select_from(ContractChatMessageRow).where(
        ContractChatMessageRow.tenant_id == chat.tenant_id, ContractChatMessageRow.chat_id == chat.id)) or 0
    s.add(ContractChatMessageRow(tenant_id=chat.tenant_id, chat_id=chat.id, position=position, role=role, text=text,
                                 citations=json.dumps(citations or []), not_stated=not_stated, author=author))


def start_chat(s: Session, p: Principal, document_id: str, message: str) -> ContractChatRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _ready(s, p.tenant_id, document_id)
    message = _clean(message)
    chat = ContractChatRow(tenant_id=p.tenant_id, document_id=doc.id, title=" ".join(message.split())[:200],
                           status="queued", created_by=p.user)
    s.add(chat)
    s.flush()
    _append(s, chat, "user", message, p.user)
    s.add(Job(tenant_id=p.tenant_id, kind=CHAT_JOB, document_id=doc.id))  # commits with the message (outbox)
    s.commit()
    return chat


def get_chat(s: Session, p: Principal, document_id: str, chat_id: str) -> ContractChatRow:
    chat = s.scalar(select(ContractChatRow).where(
        ContractChatRow.tenant_id == p.tenant_id, ContractChatRow.document_id == document_id,
        ContractChatRow.id == chat_id, ContractChatRow.created_by == p.user))
    if chat is None:
        raise NotFound("chat not found")
    return chat


def send_message(s: Session, p: Principal, document_id: str, chat_id: str, message: str) -> ContractChatRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    chat = get_chat(s, p, document_id, chat_id)
    _ready(s, p.tenant_id, document_id)
    message = _clean(message)
    if chat.status in BUSY:
        raise Conflict("wait for the current answer")
    _append(s, chat, "user", message, p.user)
    chat.status, chat.error = "queued", None
    s.add(Job(tenant_id=p.tenant_id, kind=CHAT_JOB, document_id=chat.document_id))
    s.commit()
    return chat


def list_chats(s: Session, p: Principal, document_id: str) -> list[ContractChatRow]:
    doc = _contract(s, p.tenant_id, document_id)
    return list(s.scalars(select(ContractChatRow).where(
        ContractChatRow.tenant_id == p.tenant_id, ContractChatRow.document_id == doc.id,
        ContractChatRow.created_by == p.user).order_by(ContractChatRow.updated_at.desc()).limit(MAX_CHATS_LISTED)))


def messages(s: Session, tenant_id: str, chat_id: str) -> list[ContractChatMessageRow]:
    return list(s.scalars(select(ContractChatMessageRow).where(
        ContractChatMessageRow.tenant_id == tenant_id, ContractChatMessageRow.chat_id == chat_id,
    ).order_by(ContractChatMessageRow.position)))


def run_chat_job(factory: sessionmaker[Session], store: BlobStore, agent: ReviewAgent, tenant_id: str,
                 document_id: str) -> None:
    """A failure is recorded and not retried, so a bad turn is paid for once. No connection is held while it runs."""
    with tenant_session(factory, tenant_id) as s:
        chat = s.scalar(select(ContractChatRow).where(
            ContractChatRow.tenant_id == tenant_id, ContractChatRow.document_id == document_id,
            ContractChatRow.status.in_(BUSY)).order_by(ContractChatRow.updated_at).limit(1))
        if chat is None:
            return
        chat_id = chat.id
        chat.status, chat.error = "running", None
        rows = messages(s, tenant_id, chat_id)
        question = rows[-1].text
        history = [(r.role, r.text) for r in rows[:-1]]
        s.commit()
    try:
        with tenant_session(factory, tenant_id) as s:
            documents = _documents(s, store, tenant_id, document_id)
        toolbox = ChatToolbox(documents, question, retriever=_retriever(factory, store, tenant_id, documents))
        agent.chat(question, history, toolbox)
        if toolbox.answer is None:
            raise RuntimeError("the agent stopped before answering")
    except Exception as exc:
        log.exception("contract chat %s failed", chat_id)
        with tenant_session(factory, tenant_id) as s:
            chat = s.get(ContractChatRow, chat_id)
            chat.status, chat.error = "failed", _failure(exc)
            s.commit()
        return
    answer = toolbox.answer
    with tenant_session(factory, tenant_id) as s:
        chat = s.get(ContractChatRow, chat_id)
        _append(s, chat, "assistant", answer.text, agent.name[:64], [_citation(c) for c in answer.citations],
                answer.not_stated)
        chat.status = "idle"
        s.commit()


class ChatMessageOut(BaseModel):
    id: str
    role: str
    text: str
    citations: list[CitationOut]
    not_stated: bool
    author: str
    at: datetime


class ChatOut(BaseModel):
    id: str
    document_id: str
    title: str
    status: str
    error: str | None
    created_at: datetime
    messages: list[ChatMessageOut]


class ChatIn(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE)


def _out(s: Session, chat: ContractChatRow) -> ChatOut:
    return ChatOut(id=chat.id, document_id=chat.document_id, title=chat.title, status=chat.status, error=chat.error,
                   created_at=chat.created_at, messages=[ChatMessageOut(
                       id=m.id, role=m.role, text=m.text, citations=json.loads(m.citations), not_stated=m.not_stated,
                       author=m.author, at=m.at) for m in messages(s, chat.tenant_id, chat.id)])


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/documents/{document_id}/chats", response_model=list[ChatOut])
    def get_chats(document_id: str, p: Auth, s: Db) -> list[ChatOut]:
        return [_out(s, c) for c in list_chats(s, p, document_id)]

    @app.post("/documents/{document_id}/chats", response_model=ChatOut, status_code=202)
    def post_chat(document_id: str, body: ChatIn, p: Auth, s: Db) -> ChatOut:
        return _out(s, start_chat(s, p, document_id, body.message))

    @app.post("/documents/{document_id}/chats/{chat_id}/messages", response_model=ChatOut, status_code=202)
    def post_message(document_id: str, chat_id: str, body: ChatIn, p: Auth, s: Db) -> ChatOut:
        return _out(s, send_message(s, p, document_id, chat_id, body.message))
