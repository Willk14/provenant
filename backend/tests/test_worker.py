from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from provenant.api.auth import Principal
from provenant.api.db import Document, Job, make_engine, make_sessionmaker, tenant_session
from provenant.api.services import upload_document
from provenant.api.storage import BlobStore
from provenant import worker
from tests.test_api import LEASE_LINES, make_pdf

PREPARER = Principal("firm-a", "pat", frozenset({"preparer"}))


class Flaky:
    name = "flaky@0"

    def __init__(self, failures: int) -> None:
        self.failures = failures

    def extract(self, pdf, sha, kind="lease"):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient")
        return []


def setup(database_url, tmp_path):
    factory = make_sessionmaker(make_engine(database_url))
    store = BlobStore(tmp_path / "s")
    with tenant_session(factory, "firm-a") as s:
        doc, _ = upload_document(s, store, PREPARER, "lease.pdf", make_pdf(LEASE_LINES))
    return factory, store, doc.id


def job_and_doc(factory, doc_id):
    with tenant_session(factory, "firm-a") as s:
        return s.scalars(select(Job)).one(), s.get(Document, doc_id)


def test_transient_failure_succeeds_on_retry(database_url, tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "BACKOFF", timedelta(0))
    factory, store, doc_id = setup(database_url, tmp_path)
    assert worker.drain(factory, store, Flaky(failures=2)) == 3
    job, doc = job_and_doc(factory, doc_id)
    assert (job.status, job.attempts, doc.status) == ("done", 3, "extracted")


def test_gives_up_after_max_attempts(database_url, tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "BACKOFF", timedelta(0))
    factory, store, doc_id = setup(database_url, tmp_path)
    assert worker.drain(factory, store, Flaky(failures=99)) == worker.MAX_ATTEMPTS
    job, doc = job_and_doc(factory, doc_id)
    assert (job.status, doc.status) == ("dead", "failed") and "transient" in job.last_error


def test_reclaims_a_job_whose_worker_died(database_url, tmp_path):
    factory, store, doc_id = setup(database_url, tmp_path)
    with factory() as s:
        job = s.scalars(select(Job)).one()
        job.status, job.attempts = "running", 1
        job.locked_at = datetime.now(UTC) - worker.STALE_AFTER - timedelta(seconds=1)
        s.commit()
    assert worker.drain(factory, store, Flaky(failures=0)) == 1
    assert job_and_doc(factory, doc_id)[0].status == "done"
