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

    def put_file(self, sha256: str, source: Path) -> None:
        """Store a large file by moving it in (a general ledger must not pass through memory)."""
        path = self._path(sha256)
        if path.exists():
            source.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, path)

    def local_path(self, sha256: str) -> Path:
        """For engines that read files directly (DuckDB over Parquet). shortcut: local disk only; a cloud store
        would download to a cache first."""
        return self._path(sha256)
