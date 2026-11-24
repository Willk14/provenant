"""Template-driven drafting from the firm's locked precedents (ADR 0019). Pure and deterministic.

A precedent is text with `{{field}}` placeholders and optional clauses between `[[optional:name]]` and
`[[/optional]]`. Drafting fills it from the matter's facts: nothing is generated, so nothing can be invented. A field
with no value stays visible as `[[field]]`, which blocks approval. Any change a person makes to the filled text is a
deviation from the precedent, shown to the approving lawyer paragraph by paragraph.
"""
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

FIELD = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,40})\s*\}\}")
OPTIONAL = re.compile(r"\[\[optional:([a-z][a-z0-9_]{0,40})\]\](.*?)\[\[/optional\]\]", re.DOTALL)
MAX_BODY = 100_000


def fields(body: str) -> list[str]:
    """The placeholders a precedent uses, in order of first appearance."""
    return list(dict.fromkeys(FIELD.findall(body)))


def options(body: str) -> list[str]:
    return list(dict.fromkeys(name for name, _ in OPTIONAL.findall(body)))


def check(body: str) -> None:
    """A precedent must be non-empty, bounded, and have no stray markers that would print into a client document."""
    if not body.strip() or len(body) > MAX_BODY:
        raise ValueError(f"a precedent must be 1 to {MAX_BODY} characters")
    bare = FIELD.sub("", OPTIONAL.sub(lambda m: m[2], body))
    if "{{" in bare or "}}" in bare or "[[" in bare or "]]" in bare:
        raise ValueError("a precedent has a malformed {{field}} or [[optional:name]]...[[/optional]] marker")


def render(body: str, values: Mapping[str, str], include: set[str]) -> str:
    """Fill the precedent. Optional clauses not chosen are removed; empty fields stay as [[field]]."""
    text = OPTIONAL.sub(lambda m: m[2] if m[1] in include else "", body)
    text = FIELD.sub(lambda m: values.get(m[1], "").strip() or f"[[{m[1]}]]", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Deviation:
    kind: str  # changed | added | removed
    standard: str
    edited: str


def _paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def deviations(standard: str, edited: str) -> list[Deviation]:
    """Paragraphs that differ from the precedent as filled. Whitespace differences do not count."""
    a, b = _paragraphs(standard), _paragraphs(edited)
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                old = a[i1 + k] if i1 + k < i2 else ""
                new = b[j1 + k] if j1 + k < j2 else ""
                out.append(Deviation("changed" if old and new else "removed" if old else "added", old, new))
        elif tag == "delete":
            out += [Deviation("removed", x, "") for x in a[i1:i2]]
        else:
            out += [Deviation("added", "", x) for x in b[j1:j2]]
    return out


def unfilled(text: str) -> list[str]:
    return re.findall(r"\[\[([a-z][a-z0-9_]{0,40})\]\]", text)
