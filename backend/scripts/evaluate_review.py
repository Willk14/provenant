"""The contract review scorecard: how often each reader catches, misses or invents a clause.

    python scripts/evaluate_review.py                       # free: keyword reader and local index
    python scripts/evaluate_review.py --check               # free: fail if the result is worse than the saved baseline
    python scripts/evaluate_review.py --update-baseline     # accept the current free result as the new baseline
    python scripts/evaluate_review.py --real                # also the draft key on real agreements (local cache)
    python scripts/evaluate_review.py --reader claude --allow-paid     # Managed Agent: costs money, sends contract text
    python scripts/evaluate_review.py --reader claude-api --allow-paid # Messages API harness: costs money, sends contract text
    python scripts/evaluate_review.py --embedder voyage --allow-paid   # Voyage: costs money, sends contract text
"""
import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fpdf import FPDF

from provenant.extraction.pdftext import PageText, open_pages
from provenant.legal import DEFAULT_TOPICS, ContractDocument, ReviewToolbox
from provenant.legal.agent import BaselineAgent, agent_from_env
from provenant.legal.chunks import chunk_pages
from provenant.legal.embeddings import Embedder, HashingEmbedder, embedder_from_env
from provenant.legal.evaluation import (
    ClauseLabel,
    ReviewScore,
    attack_held,
    resolve_label,
    score_retrieval,
    score_review,
)
from provenant.legal.retrieval import cosine, fuse, keyword_score

GOLDEN = Path(__file__).resolve().parent.parent / "golden"
KEY = GOLDEN / "contracts" / "key.json"
BASELINE = GOLDEN / "contracts" / "baseline.json"
REAL_KEY = GOLDEN / "real" / "clauses.json"
MODES = ("keyword", "vector", "hybrid")
CANDIDATES = 40
# Plain questions, deliberately not the wording of a clause heading, so retrieval has to do real work.
QUERIES = {
    "change_of_control": "what happens if the ownership of the company changes hands",
    "assignment": "can rights under this contract be passed to someone else",
    "termination": "when can the contract be ended",
    "indemnity": "who must cover the other side's losses and claims",
    "liability_cap": "the most a party can be responsible for paying",
    "cross_default": "does non-payment of other debt trigger a default",
    "material_adverse_change": "a serious decline in the business",
    "governing_law": "which country's law and courts apply to disputes",
    "ebitda_definition": "how earnings before interest and tax are calculated and adjusted",
}


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    pages: tuple[PageText, ...]
    sha256: str
    labels: tuple[ClauseLabel, ...]
    absent: tuple[str, ...]
    complete: bool
    attack: dict | None = None


def _pdf(pages: Sequence[Sequence[str | dict]]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            text = line["text"] if isinstance(line, dict) else line
            pdf.multi_cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def synthetic_cases() -> list[Case]:
    key = json.loads(KEY.read_text(encoding="utf-8"))
    cases = []
    for entry in key["cases"]:
        data = _pdf(entry["pages"])
        with open_pages(data) as read:
            pages = tuple(read)
        labels = tuple(
            resolve_label(line["id"], line["topic"], pages[number], line["text"], whole_anchor=True,
                          critical=line.get("critical", False))
            for number, lines in enumerate(entry["pages"]) for line in lines if isinstance(line, dict)
        )
        cases.append(Case(entry["id"], entry["kind"], pages, hashlib.sha256(data).hexdigest(), labels,
                          tuple(entry["absent"]), True, entry.get("attack")))
    return cases


def real_cases() -> list[Case]:
    key = json.loads(REAL_KEY.read_text(encoding="utf-8"))
    manifest = {e["id"]: e for e in json.loads((GOLDEN / "real" / "manifest.json").read_text(encoding="utf-8"))}
    cases = []
    for case_id, entries in key["contracts"].items():
        path = GOLDEN / "real" / "cache" / f"{case_id}.pdf"
        if not path.exists():
            raise SystemExit(f"{path.name} is not cached: run scripts/evaluate_extractor.py labels --real first")
        data = path.read_bytes()
        with open_pages(data) as read:
            pages = tuple(read)
        labels = tuple(resolve_label(e["id"], e["topic"], pages[e["page"] - 1], e["anchor"], whole_anchor=False,
                                     critical=e.get("critical", False)) for e in entries)
        cases.append(Case(case_id, manifest[case_id]["kind"], pages, hashlib.sha256(data).hexdigest(), labels, (), False))
    return cases


class MemoryIndex:
    """The app's chunker, embedder and fusion over one contract, with no database."""

    def __init__(self, case: Case, embedder: Embedder) -> None:
        self.case, self.embedder = case, embedder
        self.chunks = chunk_pages(case.pages, case.sha256)
        self.vectors = embedder.embed_documents([c.text for c in self.chunks]) if self.chunks else []

    def rank(self, query: str, mode: str) -> list:
        query_vector = self.embedder.embed_query(query)
        near = sorted(((i, cosine(query_vector, v)) for i, v in enumerate(self.vectors)), key=lambda x: (-x[1], x[0]))
        scored = ((i, keyword_score(query, c.text)) for i, c in enumerate(self.chunks))
        words = [i for i, s in sorted(scored, key=lambda x: (-x[1], x[0])) if s > 0][:CANDIDATES]
        near = near[:CANDIDATES]
        ranked = fuse(near if mode != "keyword" else [], words if mode != "vector" else [], self.embedder.min_similarity)
        return [self.chunks[r.id] for r in sorted(ranked, key=lambda r: (-r.score, r.id))]

    def search(self, query: str) -> list:
        return [SimpleNamespace(document_id=self.case.id, page=c.page, text=c.text)
                for c in self.rank(query, "hybrid")[:8]]


def review_case(case: Case, agent, index: MemoryIndex) -> tuple[ReviewScore, bool | None]:
    terms = frozenset()
    document = ContractDocument(case.id, f"{case.id}.pdf", case.kind, case.sha256, case.pages, terms)
    toolbox = ReviewToolbox([document], DEFAULT_TOPICS, retriever=index.search)
    agent.review(DEFAULT_TOPICS, toolbox)
    if not toolbox.finished:
        raise RuntimeError(f"{case.id}: the reader stopped before accounting for every topic")
    score = score_review(case.labels, toolbox.findings, toolbox.not_found, case.pages, absent_topics=case.absent,
                         complete_key=case.complete)
    held = None
    if case.attack:
        held = attack_held(score, case.attack["must_catch"], toolbox.summary, case.attack["forbidden_summary"],
                           toolbox.blocked_calls)
    return score, held


def _share(top: int, bottom: int) -> float | None:
    return round(top / bottom, 4) if bottom else None


def evaluate(cases: Sequence[Case], agent, embedder: Embedder) -> dict:
    per_case, attacks = {}, {}
    totals = dict.fromkeys(("labels", "caught", "critical", "critical_caught", "findings", "false_alarms",
                            "bad_citations", "wrongly_absent", "wrongly_present"), 0)
    complete_findings = complete_false = 0
    retrieval = {m: [] for m in MODES}
    for case in cases:
        index = MemoryIndex(case, embedder)
        score, held = review_case(case, agent, index)
        per_case[case.id] = asdict(score)
        if held is not None:
            attacks[case.id] = held
        totals["labels"] += score.labels
        totals["caught"] += len(score.caught)
        totals["critical"] += score.critical
        totals["critical_caught"] += score.critical - len(score.missed_critical)
        totals["findings"] += score.findings
        totals["false_alarms"] += len(score.false_alarms)
        totals["bad_citations"] += score.bad_citations
        totals["wrongly_absent"] += len(score.wrongly_absent)
        totals["wrongly_present"] += len(score.wrongly_present)
        if score.complete_key:
            complete_findings += score.findings
            complete_false += len(score.false_alarms)
        for label in case.labels:
            for mode in MODES:
                passages = index.rank(QUERIES[label.topic], mode)
                retrieval[mode].append((label, [(c.page, c.char_start, c.char_end) for c in passages]))
    ranked = {}
    for mode, rows in retrieval.items():
        s = score_retrieval(rows)
        ranked[mode] = {"queries": s.queries, "top_1": _share(s.found_at_1, s.queries),
                        "top_5": _share(s.found_at_5, s.queries), "mrr": _share(s.reciprocal_rank, s.queries)}
    return {
        "reader": agent.name, "embedder": embedder.name, "summary": {
            **totals,
            "recall": _share(totals["caught"], totals["labels"]),
            "critical_recall": _share(totals["critical_caught"], totals["critical"]),
            "precision": _share(complete_findings - complete_false, complete_findings),
            "attacks_held": sum(attacks.values()), "attacks": len(attacks),
        },
        "retrieval": ranked, "attacks": attacks, "cases": per_case,
    }


def snapshot(result: dict) -> dict:
    """What a free run must not get worse at. Holds ids and counts only, never contract text."""
    return {
        "reader": result["reader"], "embedder": result["embedder"],
        "caught": sorted(c for case in result["cases"].values() for c in case["caught"]),
        "false_alarms": result["summary"]["false_alarms"],
        "wrongly_absent": result["summary"]["wrongly_absent"],
        "bad_citations": result["summary"]["bad_citations"],
        "attacks_held": sorted(k for k, held in result["attacks"].items() if held),
        "retrieval_top_5": {m: r["top_5"] for m, r in result["retrieval"].items()},
    }


def regressions(baseline: dict, current: dict) -> list[str]:
    problems = []
    if lost := sorted(set(baseline["caught"]) - set(current["caught"])):
        problems.append(f"clauses no longer caught: {', '.join(lost)}")
    for key, words in (("false_alarms", "false alarms"), ("wrongly_absent", "topics wrongly called absent"),
                       ("bad_citations", "bad citations")):
        if current[key] > baseline[key]:
            problems.append(f"{words} rose from {baseline[key]} to {current[key]}")
    if lost := sorted(set(baseline["attacks_held"]) - set(current["attacks_held"])):
        problems.append(f"planted-instruction tests now fail: {', '.join(lost)}")
    for mode, before in baseline["retrieval_top_5"].items():
        if current["retrieval_top_5"][mode] < before:
            problems.append(f"{mode} top-5 retrieval fell from {before} to {current['retrieval_top_5'][mode]}")
    return problems


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def report(title: str, result: dict) -> None:
    s = result["summary"]
    print(f"\n{title}\nreader {result['reader']} | embeddings {result['embedder']}")
    print(f"  clauses caught      {s['caught']}/{s['labels']} ({_percent(s['recall'])})")
    print(f"  critical caught     {s['critical_caught']}/{s['critical']} ({_percent(s['critical_recall'])})")
    print(f"  findings correct    {_percent(s['precision'])} ({s['false_alarms']} false alarms)")
    print(f"  bad citations       {s['bad_citations']}")
    print(f"  wrongly 'not found' {s['wrongly_absent']} topics")
    if s["attacks"]:
        print(f"  planted instructions resisted {s['attacks_held']}/{s['attacks']}")
    for mode, r in result["retrieval"].items():
        print(f"  retrieval {mode:<8} top-1 {_percent(r['top_1'])}, top-5 {_percent(r['top_5'])}, MRR {r['mrr']:.2f}")
    for case_id, case in result["cases"].items():
        if case["missed"]:
            print(f"  {case_id}: missed {', '.join(case['missed'])}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reader", choices=("baseline", "claude", "claude-api"), default="baseline")
    parser.add_argument("--embedder", choices=("hashing", "voyage"), default="hashing")
    parser.add_argument("--real", action="store_true", help="also score the draft key on real agreements")
    parser.add_argument("--check", action="store_true", help="fail if the free result is worse than the baseline")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--allow-paid", action="store_true", help="required to call Claude or Voyage")
    parser.add_argument("--save", type=Path, help="write the full JSON report here")
    args = parser.parse_args(argv)

    paid = args.reader in ("claude", "claude-api") or args.embedder == "voyage"
    if paid and not args.allow_paid:
        parser.error("this costs money and sends contract text to a provider: add --allow-paid to confirm")
    if paid and (args.check or args.update_baseline):
        parser.error("the regression baseline covers the free readers only")

    if args.reader in ("claude", "claude-api"):
        import os

        os.environ["PROVENANT_LEGAL_AGENT"] = args.reader
        agent = agent_from_env()
    else:
        agent = BaselineAgent()
    if args.embedder == "voyage":
        import os

        os.environ["PROVENANT_EMBEDDINGS"] = "voyage"
        embedder = embedder_from_env()
    else:
        embedder = HashingEmbedder()

    result = evaluate(synthetic_cases(), agent, embedder)
    report("Synthetic answer key (complete)", result)
    output = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "synthetic": result}
    if args.real:
        real = evaluate(real_cases(), agent, embedder)
        report("Real agreements (DRAFT key, not confirmed by a lawyer; precision not scored)", real)
        output["real"] = real
    if args.save:
        args.save.write_text(json.dumps(output, indent=1), encoding="utf-8")

    current = snapshot(result)
    if args.update_baseline:
        BASELINE.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {BASELINE.relative_to(GOLDEN.parent)}")
    if args.check:
        problems = regressions(json.loads(BASELINE.read_text(encoding="utf-8")), current)
        if problems:
            print("\nREGRESSION")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("\nNo regression against the saved baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
