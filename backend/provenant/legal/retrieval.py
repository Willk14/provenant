"""Combine vector and keyword rankings. Pure; the database or a test supplies the rankings."""
import math
from collections.abc import Hashable, Sequence

from provenant.legal.embeddings import tokens

RRF_K = 60


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    length = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / length if length else 0.0


def keyword_score(query: str, text: str) -> float:
    """The share of the query's distinct words that the text contains."""
    wanted = set(tokens(query))
    return len(wanted & set(tokens(text))) / len(wanted) if wanted else 0.0


def reciprocal_rank_fusion(rankings: Sequence[Sequence[Hashable]], k: int = RRF_K) -> dict[Hashable, float]:
    """Each list adds 1 / (k + rank), so an item that both methods rank well beats one only a single method likes."""
    scores: dict[Hashable, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return scores
