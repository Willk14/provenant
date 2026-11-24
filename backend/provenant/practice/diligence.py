"""Due diligence matrix: every contract in a matter against every playbook topic (ADR 0019). Pure.

Each cell comes from a contract review's cited findings. A cell is a deviation when a finding is high or medium
severity. Silence is shown as "not found", never as "no issue", and an unreviewed contract is shown as such.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}


@dataclass(frozen=True)
class Cell:
    state: str  # finding | not_found | not_reviewed | reviewing | failed
    severity: str | None = None
    findings: int = 0
    title: str = ""
    quote: str = ""
    page: int | None = None

    @property
    def deviation(self) -> bool:
        return self.state == "finding" and self.severity in ("high", "medium")


@dataclass(frozen=True)
class Row:
    document_id: str
    filename: str
    cells: dict[str, Cell]

    @property
    def deviations(self) -> int:
        return sum(1 for c in self.cells.values() if c.deviation)


def build(topics: Sequence[str], documents: Sequence[tuple[str, str]], reviews: Mapping[str, dict]) -> list[Row]:
    """`reviews` maps a document id to its latest review: {"status", "findings": [{topic, severity, title, quote,
    page}], "not_found": [topic]}. Rows are ordered with the most deviations first."""
    rows = []
    for document_id, filename in documents:
        review = reviews.get(document_id)
        cells = {}
        for topic in topics:
            if review is None:
                cells[topic] = Cell("not_reviewed")
            elif review["status"] in ("queued", "running"):
                cells[topic] = Cell("reviewing")
            elif review["status"] == "failed":
                cells[topic] = Cell("failed")
            else:
                found = sorted((f for f in review["findings"] if f["topic"] == topic), key=lambda f: RANK[f["severity"]])
                if found:
                    top = found[0]
                    cells[topic] = Cell("finding", top["severity"], len(found), top["title"], top["quote"], top["page"])
                else:
                    cells[topic] = Cell("not_found")
        rows.append(Row(document_id, filename, cells))
    return sorted(rows, key=lambda r: (-r.deviations, r.filename))
