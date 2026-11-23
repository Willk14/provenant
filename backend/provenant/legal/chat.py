"""The contract chat's tools. Pure, with no I/O (ADR 0016).

An answer may state a figure only if the words it cites, or the person's question, contain that figure.
"""
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from provenant.legal.drafting import facts
from provenant.legal.toolbox import TOOLS, ContractDocument, ReviewToolbox

CHAT_TOOLS: tuple[dict, ...] = (*TOOLS[:4], next(t for t in TOOLS if t["name"] == "answer_question"))
MIN_MESSAGE = 2
MAX_MESSAGE = 2000
HISTORY_MESSAGES = 12


class ChatToolbox(ReviewToolbox):
    """Reads one contract family and accepts one cited answer."""

    def __init__(self, documents: Sequence[ContractDocument], question: str,
                 retriever: Callable[[str], Sequence[Any]] | None = None) -> None:
        super().__init__(documents, (), mode="question", retriever=retriever)
        self.question_facts = facts(question)

    def _handlers(self) -> dict[str, Callable[[Mapping[str, Any]], str]]:
        return {"list_documents": self._list, "read_page": self._read, "search": self._search,
                "find_relevant_clauses": self._find, "answer_question": self._answer}

    def _answer(self, tool_input: Mapping[str, Any]) -> str:
        citations = [self._cite(c) for c in tool_input["citations"]]
        evidence = self.question_facts.union(*(facts(c.quote) for c in citations))
        if unsupported := sorted(facts(str(tool_input["answer"])) - evidence):
            raise ValueError(f"the answer states {', '.join(unsupported)}, which is not in its cited quotes or the "
                             "question; cite it or remove it")
        return super()._answer(tool_input)
