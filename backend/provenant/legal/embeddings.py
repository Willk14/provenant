"""Turn text into vectors. `hashing` is local and free; `voyage` is semantic and sends text to Voyage AI."""
import hashlib
import math
import os
import re
from collections.abc import Sequence
from typing import Protocol

DIMENSIONS = 1024  # fixed by the database column; both providers produce it
BATCH_SIZE = 64
STOP_WORDS = frozenset(
    "a an and any are as at be been by for from has have if in is it its may of on or shall such that the this to "
    "under was were which will with without".split()
)


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()) if w not in STOP_WORDS]


class Embedder(Protocol):
    name: str
    semantic: bool
    min_similarity: float  # a nearest neighbour below this is noise, not a match

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalise(vector: Sequence[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else [0.0] * len(vector)


class HashingEmbedder:
    """Signed feature hashing of words and character trigrams. Deterministic and offline, so no contract text leaves
    the server. It catches shared and similar words; it does not know that "acquired" relates to "change of control"."""

    name = "hashing-v1"
    semantic = False
    min_similarity = 0.12

    @staticmethod
    def _add(vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.sha256(feature.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        vector[index] += weight if digest[4] & 1 else -weight

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in tokens(text):
            self._add(vector, f"w:{word}", 1.0)
            padded = f" {word} "
            for i in range(len(padded) - 2):
                self._add(vector, f"t:{padded[i:i + 3]}", 0.25)
        return _normalise(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VoyageEmbedder:
    """voyage-law-2, Voyage AI's legal embedding model (1024 dimensions)."""

    model = "voyage-law-2"
    name = "voyage-law-2"
    semantic = True
    min_similarity = 0.2

    def __init__(self, client) -> None:
        self.client = client

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = list(texts[start:start + BATCH_SIZE])
            result = self.client.embed(batch, model=self.model, input_type=input_type)
            if len(result.embeddings) != len(batch) or any(len(v) != DIMENSIONS for v in result.embeddings):
                raise ValueError("the embedding service returned an unexpected shape")
            out.extend(_normalise(v) for v in result.embeddings)
        return out

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def embedder_from_env() -> Embedder:
    """`PROVENANT_EMBEDDINGS=hashing` (default) or `voyage`, which needs VOYAGE_API_KEY and the `voyage` extra."""
    choice = os.environ.get("PROVENANT_EMBEDDINGS", "hashing")
    if choice == "hashing":
        return HashingEmbedder()
    if choice != "voyage":
        raise ValueError("PROVENANT_EMBEDDINGS must be hashing or voyage")
    if not os.environ.get("VOYAGE_API_KEY"):
        raise ValueError("PROVENANT_EMBEDDINGS=voyage needs VOYAGE_API_KEY")
    import voyageai

    return VoyageEmbedder(voyageai.Client())
