"""Drains the jobs outbox. Run one or more: python -m provenant.worker

Postgres: workers claim with FOR UPDATE SKIP LOCKED, so any number can run side by side.
A job whose worker died is reclaimed after STALE_AFTER. Failures retry with backoff, then go `dead`.
"""
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api import ai_runs, legal, legal_chat, legal_drafts, retrieval
from provenant.api.db import Job, make_engine, make_sessionmaker
from provenant.api.services import extract_document
from provenant.api.storage import BlobStore, make_store
from provenant.extraction import Extractor
from provenant.legal.agent import agent_from_env
from provenant.legal.embeddings import embedder_from_env

log = logging.getLogger("provenant.worker")

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=10)
BACKOFF = timedelta(seconds=15)


def _claim(s: Session, now: datetime) -> Job | None:
    job = s.scalar(
        select(Job)
        .where(
            or_(
                and_(Job.status == "pending", Job.run_after <= now),
                and_(Job.status == "running", Job.locked_at < now - STALE_AFTER),
            )
        )
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job:
        job.status, job.locked_at, job.attempts = "running", now, job.attempts + 1
        s.commit()
    return job


def run_once(factory: sessionmaker[Session], store: BlobStore, extractor: Extractor) -> bool:
    """Process one due job. Returns False when there was nothing to do."""
    with factory() as s:
        job = _claim(s, datetime.now(UTC))
        if job is None:
            return False
        job_id, tenant_id, document_id, attempts, kind = job.id, job.tenant_id, job.document_id, job.attempts, job.kind

    error = None
    try:
        with ai_runs.recording(factory, tenant_id, document_id):
            if kind in (legal.REVIEW_JOB, legal.QUESTION_JOB):
                task = legal.run_review_job if kind == legal.REVIEW_JOB else legal.run_question_job
                task(factory, store, agent_from_env(), tenant_id, document_id)
            elif kind == legal_drafts.DRAFT_JOB:
                legal_drafts.run_draft_job(factory, store, agent_from_env(), tenant_id, document_id)
            elif kind == legal_chat.CHAT_JOB:
                legal_chat.run_chat_job(factory, store, agent_from_env(), tenant_id, document_id)
            elif kind == retrieval.INDEX_JOB:
                retrieval.index_document(factory, store, embedder_from_env(), tenant_id, document_id)
            else:
                extract_document(factory, store, extractor, tenant_id, document_id)
    except Exception as exc:
        log.exception("job %s failed (attempt %s)", job_id, attempts)
        error = f"{type(exc).__name__}: {exc}"[:500]

    with factory() as s:
        job = s.get(Job, job_id)
        job.locked_at, job.last_error = None, error
        if error is None:
            job.status = "done"
        elif attempts >= MAX_ATTEMPTS:
            job.status = "dead"
        else:
            job.status, job.run_after = "pending", datetime.now(UTC) + BACKOFF * 2 ** (attempts - 1)
        s.commit()
    return True


def drain(factory: sessionmaker[Session], store: BlobStore, extractor: Extractor, limit: int = 1000) -> int:
    """Process every job that is due now. Used by tests and one-off scripts."""
    done = 0
    while done < limit and run_once(factory, store, extractor):
        done += 1
    return done


def run_forever(
    factory: sessionmaker[Session], store: BlobStore, extractor: Extractor, stop: Event, idle_seconds: float = 2.0
) -> None:
    while not stop.is_set():
        try:
            busy = run_once(factory, store, extractor)
        except Exception:  # e.g. database briefly unavailable: keep the worker alive
            log.exception("worker loop error")
            busy = False
        if not busy:
            stop.wait(idle_seconds)


def main() -> None:
    from provenant.extraction import extractor_from_env

    logging.basicConfig(level=logging.INFO)
    factory = make_sessionmaker(make_engine(os.environ.get("DATABASE_URL", "sqlite:///./provenant.db")))
    store = make_store()
    extractor = extractor_from_env()
    log.info("worker started with extractor %s", extractor.name)
    stop = Event()
    try:
        run_forever(factory, store, extractor, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
