from types import SimpleNamespace

import pytest

import provenant.worker as worker
from provenant.extraction.pdftext import PageText
from provenant.legal import DEFAULT_TOPICS, ContractDocument, ReviewToolbox
from provenant.legal.chunks import MAX_CHARS, chunk_pages
from provenant.legal.embeddings import DIMENSIONS, HashingEmbedder, VoyageEmbedder, embedder_from_env, tokens
from provenant.legal.retrieval import cosine, keyword_score, reciprocal_rank_fusion
from tests.test_api import OTHER_FIRM, PREPARER, run_jobs
from tests.test_covenants_api import AGREEMENT_LINES, upload_agreement
from tests.test_legal_api import ScriptedAgent


def page(text: str, number: int = 1) -> PageText:
    chars = tuple({"x0": i, "x1": i + 1, "top": 10, "bottom": 20} if not c.isspace() else None for i, c in enumerate(text))
    return PageText(number, text, chars)


def test_chunks_point_at_their_exact_text_and_never_cross_a_page():
    first, second = page("1. Definitions\nThe Lender means the bank."), page("2. Default\nFailure to pay is a default.", 2)
    chunks = chunk_pages([first, second], "a" * 64)
    assert [(c.ordinal, c.page) for c in chunks] == [(0, 1), (1, 2)]
    for c in chunks:
        assert [first, second][c.page - 1].text[c.char_start:c.char_end] == c.text


def test_a_heading_starts_a_new_chunk_once_the_current_one_is_substantial():
    body = "The Borrower shall comply with this obligation at all times."  # 61 characters with the heading far over 60
    text = f"1. First clause\n{body}\n2. Second clause\nShort text.\n3. Third"
    chunks = chunk_pages([page(text)], "a" * 64)
    assert [c.text.split(".")[0] for c in chunks] == ["1", "2"]  # clause 2 is under 60 characters, so 3 stays with it
    assert chunks[1].text.startswith("2. Second clause") and chunks[1].text.endswith("3. Third")


def test_a_long_line_is_cut_at_spaces_with_overlap():
    text = " ".join(f"word{i}" for i in range(1000))  # about 6,900 characters on one line
    chunks = chunk_pages([page(text)], "a" * 64)
    assert len(chunks) > 5 and all(len(c.text) <= MAX_CHARS for c in chunks)
    assert all(b.char_start < a.char_end for a, b in zip(chunks, chunks[1:]))  # consecutive chunks overlap
    assert chunks[-1].char_end == len(text) and all(c.text.startswith("word") for c in chunks)  # no cut inside a word


def test_rrf_rewards_agreement_between_methods():
    # With k=60: "both" is first in each list, 1/61 + 1/61 = 2/61; the others are second in one list, 1/62.
    scores = reciprocal_rank_fusion([["both", "vector"], ["both", "keyword"]])
    assert scores["both"] == pytest.approx(2 / 61)
    assert scores["vector"] == pytest.approx(1 / 62) and scores["keyword"] == pytest.approx(1 / 62)


def test_keyword_score_is_the_share_of_query_words_found():
    # "change", "control" and "lender" remain after stop words; the text has two of the three.
    assert keyword_score("change of control for the lender", "On a change of control") == pytest.approx(2 / 3)


def test_hashing_embeddings_are_deterministic_unit_vectors():
    embedder = HashingEmbedder()
    a, b = embedder.embed_query("change of control"), embedder.embed_documents(["change of control"])[0]
    assert a == b and len(a) == DIMENSIONS and cosine(a, a) == pytest.approx(1)
    near = cosine(a, embedder.embed_query("on a change of control the lender may cancel"))
    far = cosine(a, embedder.embed_query("monthly rental payable in advance"))
    assert near > embedder.min_similarity > far
    assert "the" not in tokens("the lender")


def test_voyage_uses_the_legal_model_with_document_and_query_types():
    calls = []

    class Client:
        def embed(self, texts, model, input_type):
            calls.append((len(texts), model, input_type))
            return SimpleNamespace(embeddings=[[1.0] + [0.0] * (DIMENSIONS - 1) for _ in texts])

    embedder = VoyageEmbedder(Client())
    assert len(embedder.embed_documents(["clause"] * 65)) == 65  # 64 + 1 in two batches
    embedder.embed_query("question")
    assert calls == [(64, "voyage-law-2", "document"), (1, "voyage-law-2", "document"), (1, "voyage-law-2", "query")]


def test_voyage_refuses_the_wrong_vector_shape():
    client = SimpleNamespace(embed=lambda *a, **k: SimpleNamespace(embeddings=[[1.0, 0.0]]))
    with pytest.raises(ValueError, match="unexpected shape"):
        VoyageEmbedder(client).embed_query("question")


def test_embedder_choice(monkeypatch):
    monkeypatch.delenv("PROVENANT_EMBEDDINGS", raising=False)
    assert isinstance(embedder_from_env(), HashingEmbedder)
    monkeypatch.setenv("PROVENANT_EMBEDDINGS", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        embedder_from_env()


def _toolbox(retriever):
    doc = ContractDocument("doc-1", "f.pdf", "credit_agreement", "a" * 64, (page("The Lender may cancel the facility."),))
    return ReviewToolbox([doc], DEFAULT_TOPICS, retriever=retriever)


def test_retrieved_text_is_marked_as_data_and_other_documents_are_dropped():
    hits = [SimpleNamespace(document_id="doc-1", page=1, text="The Lender may cancel"),
            SimpleNamespace(document_id="someone-else", page=1, text="secret")]
    out = _toolbox(lambda q: hits).call("find_relevant_clauses", {"query": "cancel"})
    assert '<contract_excerpt document="D1" page="1">' in out and "secret" not in out


def test_retrieval_outage_leaves_the_review_able_to_read_pages():
    def broken(_):
        raise ConnectionError("down")

    tools = _toolbox(broken)
    assert "use search and read_page" in tools.call("find_relevant_clauses", {"query": "cancel"})
    assert "cancel the facility" in tools.call("read_page", {"document": "D1", "page": 1})
    assert "unavailable" in _toolbox(None).call("find_relevant_clauses", {"query": "cancel"})


LINES = [*AGREEMENT_LINES, "On a Change of Control the Lender may cancel the facility and demand repayment."]


def _hits(client, query, **extra):
    r = client.post("/legal/search", headers=extra.pop("headers", PREPARER), json={"query": query, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_index_and_hybrid_search_with_page_citations(client, app):
    doc_id = upload_agreement(client, app, LINES)
    assert _hits(client, "change of control") == []  # nothing indexed yet
    state = client.post("/legal/index", headers=PREPARER).json()
    assert state["queued"] == 1 and state["model"] == "hashing-v1" and state["semantic"] is False
    assert client.post("/legal/index", headers=PREPARER).json()["queued"] == 0  # already queued
    run_jobs(app)
    assert client.get("/legal/index", headers=PREPARER).json()["ready"] == 1
    assert client.post("/legal/index", headers=PREPARER).json()["queued"] == 0  # already indexed

    hit = _hits(client, "change of control", document_id=doc_id)[0]
    assert "Change of Control" in hit["text"] and hit["citation"]["page"] == 1
    assert hit["citation"]["document_id"] == doc_id and hit["citation"]["bbox"][2] > hit["citation"]["bbox"][0]
    assert "keyword" in hit["matched"]
    assert client.post("/legal/search", headers=PREPARER, json={"query": "x"}).status_code == 422


def test_search_never_crosses_firms(client, app):
    doc_id = upload_agreement(client, app, LINES)
    client.post("/legal/index", headers=PREPARER)
    run_jobs(app)
    assert _hits(client, "change of control", headers=OTHER_FIRM) == []
    r = client.post("/legal/search", headers=OTHER_FIRM, json={"query": "change of control", "document_id": doc_id})
    assert r.status_code == 404


def test_a_review_indexes_its_contract_and_the_agent_can_retrieve(client, app, monkeypatch):
    seen = []

    class Retrieving(ScriptedAgent):
        def review(self, topics, toolbox):
            seen.append(toolbox.call("find_relevant_clauses", {"query": "change of control"}))
            toolbox.call("finish_review", {"summary": "Nothing recorded here.", "not_found": [t.key for t in topics]})

    monkeypatch.setattr(worker, "agent_from_env", Retrieving)
    doc_id = upload_agreement(client, app, LINES)
    assert client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).status_code == 202
    run_jobs(app)
    assert "Change of Control" in seen[0] and "<contract_excerpt" in seen[0]
    assert client.get("/legal/index", headers=PREPARER).json()["ready"] == 1


def test_a_failed_index_does_not_fail_the_review(client, app, monkeypatch):
    import provenant.api.legal as legal

    def broken():
        raise ValueError("no embedding service")

    monkeypatch.setattr(legal, "embedder_from_env", broken)
    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    doc_id = upload_agreement(client, app, [*LINES, "EBITDA add-backs are capped at 20% of EBITDA.",
                                            "On a Change of Control the Lender may cancel the facility."])
    started = client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).json()
    run_jobs(app)
    assert client.get(f"/documents/{doc_id}/reviews/{started['id']}", headers=PREPARER).json()["status"] == "done"
