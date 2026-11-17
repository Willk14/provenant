"""Content-addressed file store: a document's name is its SHA-256, so stored evidence cannot drift."""
import os
from pathlib import Path


class BlobStore:
    """shortcut: local disk; swap for S3/GCS with the same two methods when deployed."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / f"{sha256}.pdf"

    def put(self, sha256: str, data: bytes) -> None:
        path = self._path(sha256)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: readers never see a half-written file

    def get(self, sha256: str) -> bytes:
        return self._path(sha256).read_bytes()
