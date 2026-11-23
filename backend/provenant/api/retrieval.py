"""Index contracts into chunks and search them with vectors plus keywords. Results are leads, never evidence alone:
anything the agent relies on must still be quoted from the page."""
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import Document, Job, tenant_session
from provenant.api.services import Invalid, get_document, list_amendments, require
from provenant.api.storage import BlobStore
from provenant.extraction.pdftext import open_pages
from provenant.legal.chunks import chunk_pages
from provenant.legal.embeddings import Embedder
from provenant.legal.retrieval import cosine, keyword_score, reciprocal_rank_fusion

log = logging.getLogger(__name__)
INDEX_JOB = "index_document"
CANDIDATES = 40
MAX_RESULTS = 20
_COLUMNS = "c.id, c.document_id, d.filename, c.ordinal, c.page, c.x0, c.top, c.x1, c.bottom, c.text"


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    filename: str
    ordinal: int
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    score: float
    similarity: float | None
    matched: tuple[str, ...]  # vector, keyword


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


def _vector_type(s: Session) -> str:
    schema = s.scalar(text("select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace "
                           "where e.extname = 'vector'"))
    return f'"{schema}".vector'


def _is_postgres(s: Session) -> bool:
    return s.get_bind().dialect.name == "postgresql"


def _status(s: Session, tenant_id: str, document_id: str, model: str) -> str | None:
    return s.scalar(text("select status from retrieval_indexes where tenant_id = :t and document_id = :d and model = :m"),
                    {"t": tenant_id, "d": document_id, "m": model})


def _set_status(s: Session, tenant_id: str, document_id: str, model: str, status: str, chunks: int = 0,
                error: str | None = None) -> None:
    values = {"t": tenant_id, "d": document_id, "m": model, "s": status, "c": chunks, "e": error,
              "at": datetime.now(UTC) if status == "ready" else None}
    updated = s.execute(text("update retrieval_indexes set status = :s, chunks = :c, error = :e, indexed_at = :at "
                             "where tenant_id = :t and document_id = :d and model = :m"), values)
    if not updated.rowcount:
        s.execute(text("insert into retrieval_indexes (document_id, model, tenant_id, status, chunks, error, indexed_at) "
                       "values (:d, :m, :t, :s, :c, :e, :at)"), values)


def index_document(factory: sessionmaker[Session], store: BlobStore, embedder: Embedder, tenant_id: str,
                   document_id: str) -> None:
    """Idempotent per document and model. Embedding happens with no database transaction open."""
    with tenant_session(factory, tenant_id) as s:
        doc = get_document(s, tenant_id, document_id)
        if doc.status != "extracted" or _status(s, tenant_id, doc.id, embedder.name) == "ready":
            return
        sha = doc.sha256
        _set_status(s, tenant_id, doc.id, embedder.name, "running")
        s.commit()
    try:
        with open_pages(store.get(sha)) as pages:
            chunks = chunk_pages(pages, sha)
        vectors = embedder.embed_documents([c.text for c in chunks]) if chunks else []
        with tenant_session(factory, tenant_id) as s:
            s.execute(text("delete from document_chunks where tenant_id = :t and document_id = :d and model = :m"),
                      {"t": tenant_id, "d": document_id, "m": embedder.name})
            postgres = _is_postgres(s)
            column, value = (("embedding", f"CAST(:v AS {_vector_type(s)})") if postgres else ("embedding_json", ":v"))
            insert = text(f"insert into document_chunks (id, tenant_id, document_id, model, ordinal, page, char_start, "
                          f"char_end, x0, top, x1, bottom, text, {column}) values (:id, :t, :d, :m, :o, :p, :cs, :ce, "
                          f":x0, :y0, :x1, :y1, :body, {value})")
            if chunks:
                s.execute(insert, [{
                    "id": str(uuid4()), "t": tenant_id, "d": document_id, "m": embedder.name, "o": c.ordinal, "p": c.page,
                    "cs": c.char_start, "ce": c.char_end, "x0": c.bbox[0], "y0": c.bbox[1], "x1": c.bbox[2],
                    "y1": c.bbox[3], "body": c.text, "v": _vector_literal(v) if postgres else json.dumps(v),
                } for c, v in zip(chunks, vectors)])
            _set_status(s, tenant_id, document_id, embedder.name, "ready", len(chunks))
            s.commit()
    except Exception as exc:
        with tenant_session(factory, tenant_id) as s:
            _set_status(s, tenant_id, document_id, embedder.name, "failed", error=f"{type(exc).__name__}: {exc}"[:500])
            s.commit()
        raise


def ensure_indexed(factory: sessionmaker[Session], store: BlobStore, embedder: Embedder, tenant_id: str,
                   document_ids: list[str]) -> None:
    for document_id in document_ids:
        index_document(factory, store, embedder, tenant_id, document_id)


def contract_family(s: Session, tenant_id: str, document_id: str) -> list[str]:
    doc = get_document(s, tenant_id, document_id)
    root = doc.parent_id or doc.id
    return [root, *[a.id for a in list_amendments(s, tenant_id, root)]]


def _ready_documents(s: Session, tenant_id: str, model: str) -> list[str]:
    return list(s.scalars(text("select document_id from retrieval_indexes where tenant_id = :t and model = :m "
                               "and status = 'ready'"), {"t": tenant_id, "m": model}))


def _postgres_rankings(s, tenant_id, model, ids, query, vector) -> tuple[list, list, dict]:
    kind = _vector_type(s)
    scope = "c.tenant_id = :t and c.model = :m and c.document_id in :ids"
    params = {"t": tenant_id, "m": model, "ids": ids, "n": CANDIDATES, "q": query, "v": _vector_literal(vector)}
    distance = f'c.embedding OPERATOR("{kind.split(".")[0].strip(chr(34))}".<=>) CAST(:v AS {kind})'
    near = s.execute(text(f"select {_COLUMNS}, 1 - ({distance}) as sim from document_chunks c "
                          f"join documents d on d.id = c.document_id where {scope} "
                          f"order by {distance}, c.document_id, c.ordinal limit :n")
                     .bindparams(bindparam("ids", expanding=True)), params).mappings().all()
    tsquery = "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery"
    words = s.execute(text(f"select {_COLUMNS}, ts_rank(to_tsvector('english', c.text), {tsquery}) as rank "
                           f"from document_chunks c join documents d on d.id = c.document_id where {scope} "
                           f"and to_tsvector('english', c.text) @@ {tsquery} "
                           f"order by rank desc, c.document_id, c.ordinal limit :n")
                      .bindparams(bindparam("ids", expanding=True)),
                      {k: v for k, v in params.items() if k != "v"}).mappings().all()
    rows = {r["id"]: r for r in [*near, *words]}
    return [(r["id"], float(r["sim"])) for r in near], [r["id"] for r in words], rows


def _sqlite_rankings(s, tenant_id, model, ids, query, vector) -> tuple[list, list, dict]:
    found = s.execute(text(f"select {_COLUMNS}, c.embedding_json from document_chunks c join documents d "
                           "on d.id = c.document_id where c.tenant_id = :t and c.model = :m and c.document_id in :ids")
                      .bindparams(bindparam("ids", expanding=True)),
                      {"t": tenant_id, "m": model, "ids": ids}).mappings().all()
    order = lambda r: (r["document_id"], r["ordinal"])
    near = sorted(((r, cosine(vector, json.loads(r["embedding_json"]))) for r in found),
                  key=lambda x: (-x[1], order(x[0])))[:CANDIDATES]
    scored = [(r, keyword_score(query, r["text"])) for r in found]
    words = sorted((x for x in scored if x[1] > 0), key=lambda x: (-x[1], order(x[0])))[:CANDIDATES]
    return [(r["id"], sim) for r, sim in near], [r["id"] for r, _ in words], {r["id"]: r for r in found}


def search(s: Session, embedder: Embedder, tenant_id: str, query: str, document_ids: list[str] | None = None,
           limit: int = 8) -> list[SearchHit]:
    query = " ".join(query.split())
    if not 3 <= len(query) <= 1000:
        raise Invalid("a search must be 3 to 1000 characters")
    limit = max(1, min(limit, MAX_RESULTS))
    ready = _ready_documents(s, tenant_id, embedder.name)
    ids = [d for d in ready if document_ids is None or d in document_ids]
    if not ids:
        return []
    vector = embedder.embed_query(query)
    rank = _postgres_rankings if _is_postgres(s) else _sqlite_rankings
    near, words, rows = rank(s, tenant_id, embedder.name, ids, query, vector)
    similar = [(chunk, sim) for chunk, sim in near if sim >= embedder.min_similarity]
    similarity = dict(similar)
    scores = reciprocal_rank_fusion([[chunk for chunk, _ in similar], words])
    keyword = set(words)
    best = sorted(scores, key=lambda c: (-scores[c], rows[c]["document_id"], rows[c]["ordinal"]))[:limit]
    return [SearchHit(
        rows[c]["document_id"], rows[c]["filename"], rows[c]["ordinal"], rows[c]["page"],
        (rows[c]["x0"], rows[c]["top"], rows[c]["x1"], rows[c]["bottom"]), rows[c]["text"], scores[c], similarity.get(c),
        tuple(m for m, on in (("vector", c in similarity), ("keyword", c in keyword)) if on),
    ) for c in best]


def index_state(s: Session, embedder: Embedder, tenant_id: str) -> dict:
    total = s.scalar(select(func.count(Document.id)).where(Document.tenant_id == tenant_id,
                                                           Document.status == "extracted")) or 0
    counts = dict(s.execute(text("select r.status, count(*) from retrieval_indexes r join documents d on d.id = "
                                 "r.document_id where r.tenant_id = :t and r.model = :m and d.status = 'extracted' "
                                 "group by r.status"), {"t": tenant_id, "m": embedder.name}).all())
    return {"model": embedder.name, "semantic": embedder.semantic, "documents": total, "ready": counts.get("ready", 0),
            "running": counts.get("running", 0), "failed": counts.get("failed", 0)}


def request_indexing(s: Session, p: Principal, embedder: Embedder) -> int:
    """Queue every read document not yet indexed for the current model. With Voyage this sends their text to it."""
    require(p, PREPARER, REVIEWER, PARTNER)
    documents = s.scalars(select(Document.id).where(Document.tenant_id == p.tenant_id, Document.status == "extracted")).all()
    ready = set(_ready_documents(s, p.tenant_id, embedder.name))
    queued = set(s.scalars(select(Job.document_id).where(Job.tenant_id == p.tenant_id, Job.kind == INDEX_JOB,
                                                         Job.status.in_(("pending", "running")))))
    missing = [d for d in documents if d not in ready and d not in queued]
    s.add_all(Job(tenant_id=p.tenant_id, kind=INDEX_JOB, document_id=d) for d in missing)
    s.commit()
    return len(missing)
