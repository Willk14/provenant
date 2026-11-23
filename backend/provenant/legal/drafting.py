"""The drafting tools of the paralegal agent. Pure, with no I/O (ADR 0015).

A draft may state a figure only if the words it cites, or the person's instructions, contain that figure. This proves
where a figure came from. It does not prove the draft's legal effect, which is why a lawyer must approve it.
"""
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from provenant.legal.toolbox import _CITE, TOOLS, Citation, ContractDocument, ReviewToolbox, _obj

DRAFT_KINDS = {"notice": "Notice", "amendment": "Amendment", "letter": "Letter"}
MAX_PARAGRAPHS = 40
MAX_PARAGRAPH = 3000
MAX_OPEN_POINTS = 20
MAX_OPEN_POINT = 500
MIN_INSTRUCTIONS = 10
MAX_INSTRUCTIONS = 2000

PLACEHOLDER = re.compile(r"\[\[[^\[\]\n]{2,120}\]\]")
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_NUMBER_WORDS = {
    "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "hundred", "thousand": "thousand", "million": "million",
    "billion": "billion",
}
_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_CURRENCY = re.compile(r"\b(KES|KSHS?|USD|EUR|GBP|UGX|TZS|ZAR)\b|([$€£])", re.IGNORECASE)
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "KSH": "KES", "KSHS": "KES"}

WRITE_DRAFT = {
    "name": "write_draft",
    "description": "Submit the finished draft. A number, date, number word or currency in a paragraph must appear in that "
                   "paragraph's cited quotes or the instructions, or the draft is refused. Write [[what is needed]] for "
                   "any fact you do not have.",
    "input_schema": _obj({
        "title": {"type": "string"},
        "paragraphs": {"type": "array", "items": _obj({
            "text": {"type": "string"},
            "citations": {"type": "array", "items": _obj(_CITE, ["document", "page", "quote"])},
        }, ["text", "citations"])},
        "open_points": {"type": "array", "items": {"type": "string"},
                        "description": "What the lawyer must check, decide or supply"},
    }, ["title", "paragraphs", "open_points"]),
}
DRAFT_TOOLS: tuple[dict, ...] = (*TOOLS[:4], WRITE_DRAFT)  # the read-only tools, then the one way to finish


@dataclass(frozen=True)
class DraftParagraph:
    text: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Draft:
    title: str
    paragraphs: tuple[DraftParagraph, ...]
    open_points: tuple[str, ...]


def facts(text: str) -> set[str]:
    """The figures a passage states: numbers without separators, number words as digits, and currencies."""
    found = {n.replace(",", "") for n in _NUMBER.findall(text)}
    found |= {_NUMBER_WORDS[w.lower()] for w in _WORD.findall(text)}
    for code, symbol in _CURRENCY.findall(text):
        key = (code or symbol).upper()
        found.add(_SYMBOLS.get(key, key))
    return found


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text)


def content_hash(title: str, texts: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps({"title": title, "paragraphs": list(texts)}, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class DraftToolbox(ReviewToolbox):
    """Reads the contract and accepts one grounded draft. Mistakes come back as text the agent can correct."""

    def __init__(self, documents: Sequence[ContractDocument], instructions: str,
                 retriever: Callable[[str], Sequence[Any]] | None = None) -> None:
        super().__init__(documents, (), mode="draft", retriever=retriever)
        self.instruction_facts = facts(instructions)
        self.draft: Draft | None = None

    @property
    def finished(self) -> bool:
        return self.draft is not None

    def _handlers(self) -> dict[str, Callable[[Mapping[str, Any]], str]]:
        return {"list_documents": self._list, "read_page": self._read, "search": self._search,
                "find_relevant_clauses": self._find, "write_draft": self._write}

    @staticmethod
    def _bare(text: str, where: str) -> str:
        bare = PLACEHOLDER.sub(" ", text)
        if "[[" in bare or "]]" in bare:
            raise ValueError(f"{where} has a malformed placeholder; use [[what is needed]]")
        return bare

    def _write(self, tool_input: Mapping[str, Any]) -> str:
        title = " ".join(str(tool_input["title"]).split())
        if not 3 <= len(title) <= 200:
            raise ValueError("the title must be 3 to 200 characters")
        raw = tool_input["paragraphs"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_PARAGRAPHS:
            raise ValueError(f"a draft has 1 to {MAX_PARAGRAPHS} paragraphs")
        paragraphs, cited = [], set()
        for number, item in enumerate(raw, start=1):
            text = str(item["text"]).strip()
            if not 1 <= len(text) <= MAX_PARAGRAPH:
                raise ValueError(f"paragraph {number} must be 1 to {MAX_PARAGRAPH} characters")
            citations = tuple(self._cite(c) for c in item["citations"])
            evidence = self.instruction_facts.union(*(facts(c.quote) for c in citations))
            if unsupported := sorted(facts(self._bare(text, f"paragraph {number}")) - evidence):
                raise ValueError(f"paragraph {number} states {', '.join(unsupported)}, which is not in its cited quotes "
                                 "or the instructions; cite it or use a [[placeholder]]")
            cited |= evidence
            paragraphs.append(DraftParagraph(text, citations))
        if unsupported := sorted(facts(self._bare(title, "the title")) - cited):
            raise ValueError(f"the title states {', '.join(unsupported)}, which no paragraph supports")
        points = [" ".join(str(p).split()) for p in tool_input["open_points"]]
        if len(points) > MAX_OPEN_POINTS or any(not 3 <= len(p) <= MAX_OPEN_POINT for p in points):
            raise ValueError(f"list at most {MAX_OPEN_POINTS} open points of 3 to {MAX_OPEN_POINT} characters")
        self.draft = Draft(title, tuple(paragraphs), tuple(points))
        return "Draft recorded for a lawyer to review."
