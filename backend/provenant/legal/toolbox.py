"""The only things a review agent can do: read this contract and record cited findings. Pure, with no I/O.

Contract text is data. The worst a malicious clause can do is produce a finding that quotes printed text, and that
finding still waits for a lawyer. A quote that is not on the cited page is refused here, whatever the model says.
"""
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from provenant.core.formats import check_value
from provenant.extraction.claude import locate
from provenant.extraction.pdftext import PageText
from provenant.legal.playbook import Topic
from provenant.provenance import SourceRef

SEVERITIES = ("high", "medium", "low", "info")
MIN_QUOTE = 12
MAX_QUOTE = 1000
MAX_TEXT = 2000
MAX_FINDINGS = 60
MAX_CALLS = 200
MAX_SEARCH_RESULTS = 20


@dataclass(frozen=True)
class ContractDocument:
    id: str
    filename: str
    kind: str
    sha256: str
    pages: tuple[PageText, ...]
    terms: frozenset[str] = frozenset()  # the terms of this kind of document that a finding may propose


@dataclass(frozen=True)
class Citation:
    document_id: str
    filename: str
    quote: str
    source: SourceRef


@dataclass(frozen=True)
class Finding:
    topic: str
    severity: str
    title: str
    explanation: str
    suggestion: str
    citation: Citation
    proposed_term: str | None = None
    proposed_value: str | None = None


@dataclass(frozen=True)
class Answer:
    text: str
    citations: tuple[Citation, ...]
    not_stated: bool


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_S = {"type": "string"}
_CITE = {
    "document": {"type": "string", "description": "Document key from list_documents, e.g. D1"},
    "page": {"type": "integer", "description": "1-based page number"},
    "quote": {"type": "string", "description": "Exact words copied from that page; at least 12 characters"},
}
TOOLS: tuple[dict, ...] = (
    {"name": "list_documents", "description": "List the contract and its amendments with their page counts.",
     "input_schema": _obj({}, [])},
    {"name": "read_page", "description": "Read the text of one page.",
     "input_schema": _obj({"document": _CITE["document"], "page": _CITE["page"]}, ["document", "page"])},
    {"name": "search", "description": "Find literal words across every page. Case-insensitive.",
     "input_schema": _obj({"query": {"type": "string", "description": "Words to find"}}, ["query"])},
    {"name": "record_finding",
     "description": "Record one issue for a lawyer to decide. The quote must be exactly on the cited page or it is "
                    "refused. Set proposed_term and proposed_value only when the clause states a value for a term "
                    "listed for that document.",
     "input_schema": _obj({
         "topic": {"type": "string", "description": "Playbook topic key"},
         "severity": {"type": "string", "enum": list(SEVERITIES)},
         "title": {"type": "string", "description": "Short name of the issue"},
         "explanation": {"type": "string", "description": "What the clause does and how it differs from the position"},
         "suggestion": {"type": "string", "description": "Wording or action for a lawyer to consider; may be empty"},
         **_CITE,
         "proposed_term": {"type": ["string", "null"]},
         "proposed_value": {"type": ["string", "null"]},
     }, ["topic", "severity", "title", "explanation", "suggestion", "document", "page", "quote",
         "proposed_term", "proposed_value"])},
    {"name": "finish_review",
     "description": "End the review. Every playbook topic must have a finding or be listed in not_found.",
     "input_schema": _obj({
         "summary": {"type": "string", "description": "Short overview for the reviewing lawyer"},
         "not_found": {"type": "array", "items": _S, "description": "Topic keys the contract does not address"},
     }, ["summary", "not_found"])},
    {"name": "answer_question",
     "description": "Give the final answer to the question. Cite the wording it rests on, or set not_stated to true.",
     "input_schema": _obj({
         "answer": {"type": "string"},
         "not_stated": {"type": "boolean", "description": "True when the documents do not answer the question"},
         "citations": {"type": "array", "items": _obj(_CITE, ["document", "page", "quote"])},
     }, ["answer", "not_stated", "citations"])},
)


class ReviewToolbox:
    """Runs one agent's tool calls. Mistakes come back as text the agent can act on, never as exceptions."""

    def __init__(self, documents: Sequence[ContractDocument], topics: Sequence[Topic], mode: str = "review") -> None:
        if mode not in ("review", "question"):
            raise ValueError("mode must be review or question")
        self.documents = {f"D{i}": d for i, d in enumerate(documents, start=1)}
        self.topics = {t.key: t for t in topics}
        self.mode = mode
        self.calls = 0
        self.findings: list[Finding] = []
        self.summary: str | None = None
        self.not_found: tuple[str, ...] = ()
        self.answer: Answer | None = None

    @property
    def finished(self) -> bool:
        return self.summary is not None if self.mode == "review" else self.answer is not None

    def call(self, name: str, tool_input: Mapping[str, Any]) -> str:
        self.calls += 1
        if self.calls > MAX_CALLS:
            raise RuntimeError(f"the agent exceeded {MAX_CALLS} tool calls")
        handler = {"list_documents": self._list, "read_page": self._read, "search": self._search,
                   "record_finding": self._record, "finish_review": self._finish,
                   "answer_question": self._answer}.get(name)
        if handler is None:
            return f"ERROR: unknown tool {name}"
        if self.finished:
            return "ERROR: this task is already finished"
        try:
            return handler(tool_input)
        except (KeyError, TypeError, ValueError) as exc:
            return f"ERROR: {exc}"

    def _document(self, key: Any) -> ContractDocument:
        if key not in self.documents:
            raise ValueError(f"no document {key!r}; use a key from list_documents")
        return self.documents[key]

    def _page(self, document: ContractDocument, number: Any) -> PageText:
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(document.pages):
            raise ValueError(f"page must be between 1 and {len(document.pages)}")
        return document.pages[number - 1]

    def _cite(self, tool_input: Mapping[str, Any]) -> Citation:
        document = self._document(tool_input["document"])
        page = self._page(document, tool_input["page"])
        quote = str(tool_input["quote"]).strip()
        if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
            raise ValueError(f"a quote must be {MIN_QUOTE} to {MAX_QUOTE} characters")
        span = locate(page, quote)
        source = span and page.source(document.sha256, *span)
        if not source:
            raise ValueError(f"that quote is not on page {page.number}; copy the words exactly")
        return Citation(document.id, document.filename, page.text[span[0]:span[1]], source)

    def _list(self, _: Mapping[str, Any]) -> str:
        return json.dumps([{"document": k, "filename": d.filename, "kind": d.kind, "pages": len(d.pages),
                            "terms_a_finding_may_propose": sorted(d.terms)} for k, d in self.documents.items()])

    def _read(self, tool_input: Mapping[str, Any]) -> str:
        document = self._document(tool_input["document"])
        page = self._page(document, tool_input["page"])
        return f'<contract_page document="{tool_input["document"]}" page="{page.number}">\n{page.text}\n</contract_page>'

    def _search(self, tool_input: Mapping[str, Any]) -> str:
        query = " ".join(str(tool_input["query"]).split())
        if len(query) < 2:
            raise ValueError("search for at least two characters")
        pattern = re.compile(r"\s+".join(re.escape(w) for w in query.split()), re.IGNORECASE)
        hits = []
        for key, document in self.documents.items():
            for page in document.pages:
                for match in pattern.finditer(page.text):
                    start, end = max(match.start() - 100, 0), min(match.end() + 100, len(page.text))
                    hits.append({"document": key, "page": page.number, "text": page.text[start:end]})
                    if len(hits) == MAX_SEARCH_RESULTS:
                        return json.dumps({"results": hits, "more": True})
        return json.dumps({"results": hits, "more": False})

    def _record(self, tool_input: Mapping[str, Any]) -> str:
        if self.mode != "review":
            raise ValueError("findings are recorded in a review")
        if len(self.findings) >= MAX_FINDINGS:
            raise ValueError(f"a review holds at most {MAX_FINDINGS} findings; merge the rest")
        topic, severity = tool_input["topic"], tool_input["severity"]
        if topic not in self.topics:
            raise ValueError(f"unknown topic {topic!r}")
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
        title, explanation = " ".join(str(tool_input["title"]).split()), str(tool_input["explanation"]).strip()
        suggestion = str(tool_input.get("suggestion") or "").strip()
        if not 3 <= len(title) <= 200 or not 10 <= len(explanation) <= MAX_TEXT or len(suggestion) > MAX_TEXT:
            raise ValueError("the title, explanation or suggestion has the wrong length")
        citation = self._cite(tool_input)
        term, value = tool_input.get("proposed_term") or None, tool_input.get("proposed_value") or None
        if (term is None) != (value is None):
            raise ValueError("a proposed term needs both its name and its value")
        if term is not None:
            document = self._document(tool_input["document"])
            if term not in document.terms:
                raise ValueError(f"{term} is not a term that can be proposed for that document")
            value = check_value(term, str(value))
        if any(f.topic == topic and f.citation.source == citation.source for f in self.findings):
            raise ValueError("that clause is already recorded for this topic")
        self.findings.append(Finding(topic, severity, title, explanation, suggestion, citation, term, value))
        return f"Recorded finding {len(self.findings)}."

    def _finish(self, tool_input: Mapping[str, Any]) -> str:
        if self.mode != "review":
            raise ValueError("this task ends with answer_question")
        summary = str(tool_input["summary"]).strip()
        if not 10 <= len(summary) <= MAX_TEXT:
            raise ValueError(f"the summary must be 10 to {MAX_TEXT} characters")
        not_found = tuple(dict.fromkeys(tool_input["not_found"]))
        if unknown := [t for t in not_found if t not in self.topics]:
            raise ValueError(f"unknown topic {unknown[0]!r}")
        covered = {f.topic for f in self.findings}
        if both := sorted(covered & set(not_found)):
            raise ValueError(f"{both[0]} has a finding, so it cannot be listed as not found")
        if missing := sorted(set(self.topics) - covered - set(not_found)):
            raise ValueError(f"account for every topic; still open: {', '.join(missing)}")
        self.summary, self.not_found = summary, not_found
        return "Review finished."

    def _answer(self, tool_input: Mapping[str, Any]) -> str:
        if self.mode != "question":
            raise ValueError("this task ends with finish_review")
        text = str(tool_input["answer"]).strip()
        if not 2 <= len(text) <= MAX_TEXT:
            raise ValueError(f"the answer must be 2 to {MAX_TEXT} characters")
        not_stated = tool_input["not_stated"] is True
        citations = tuple(self._cite(c) for c in tool_input["citations"])
        if not_stated == bool(citations):
            raise ValueError("cite the wording the answer rests on, or set not_stated with no citations")
        self.answer = Answer(text, citations, not_stated)
        return "Answer recorded."
