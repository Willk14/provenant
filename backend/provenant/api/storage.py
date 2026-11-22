"""Content-addressed file store: a document's name is its SHA-256, so stored evidence cannot drift.

Two implementations with the same four methods: local disk (development, single-machine installs) and any
S3-compatible object store (AWS S3, Cloudflare R2, Supabase Storage, MinIO) for deployed environments, where
container disks do not survive a restart. `make_store()` picks one from the environment.
"""
import os
import tempfile
from pathlib import Path


class BlobStore:
    """Local disk."""

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
        """For engines that read files directly (DuckDB over Parquet)."""
        return self._path(sha256)


class S3BlobStore:
    """An S3-compatible bucket. Objects are keyed by hash, so a file is never overwritten with different bytes,
    and `local_path` downloads once into a cache that can never go stale."""

    def __init__(self, bucket: str, prefix: str = "", client=None, cache_dir: Path | None = None) -> None:
        if client is None:  # boto3 is an optional extra: pip install -e ".[s3]"
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=os.environ.get("PROVENANT_S3_ENDPOINT") or None,
                region_name=os.environ.get("PROVENANT_S3_REGION") or None,
            )
        self.client, self.bucket = client, bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.cache = cache_dir or Path(tempfile.gettempdir()) / "provenant-cache"

    def _key(self, sha256: str) -> str:
        return f"{self.prefix}{sha256[:2]}/{sha256}"

    def _exists(self, sha256: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(sha256))
            return True
        except Exception as exc:  # botocore ClientError 404; anything else is re-raised below
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put(self, sha256: str, data: bytes) -> None:
        if not self._exists(sha256):
            self.client.put_object(Bucket=self.bucket, Key=self._key(sha256), Body=data)

    def get(self, sha256: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(sha256))["Body"].read()

    def put_file(self, sha256: str, source: Path) -> None:
        if not self._exists(sha256):
            self.client.upload_file(str(source), self.bucket, self._key(sha256))  # multipart for large ledgers
        source.unlink(missing_ok=True)

    def local_path(self, sha256: str) -> Path:
        path = self.cache / sha256[:2] / sha256
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            self.client.download_file(self.bucket, self._key(sha256), str(tmp))
            os.replace(tmp, path)
        return path


def make_store(storage_dir: Path | None = None):
    """PROVENANT_STORAGE=s3 with PROVENANT_S3_BUCKET (and optional PROVENANT_S3_PREFIX, PROVENANT_S3_ENDPOINT,
    PROVENANT_S3_REGION, PROVENANT_CACHE_DIR; credentials from the usual AWS_* variables); otherwise local disk
    at PROVENANT_STORAGE_DIR."""
    if os.environ.get("PROVENANT_STORAGE", "local") == "s3":
        bucket = os.environ.get("PROVENANT_S3_BUCKET")
        if not bucket:
            raise RuntimeError("PROVENANT_STORAGE=s3 needs PROVENANT_S3_BUCKET")
        cache = os.environ.get("PROVENANT_CACHE_DIR")
        return S3BlobStore(bucket, os.environ.get("PROVENANT_S3_PREFIX", ""), cache_dir=Path(cache) if cache else None)
    return BlobStore(storage_dir or Path(os.environ.get("PROVENANT_STORAGE_DIR", "./storage")))
