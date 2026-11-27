"""The S3-compatible store keeps the local store's guarantees: content addressing, no overwrite, a real file
path for DuckDB from a cache."""
import hashlib
import io

import pytest

from provenant.api.storage import BlobStore, S3BlobStore, make_store


class NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    """The five calls the store makes, against a dict."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[str] = []

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NotFound()

    def put_object(self, Bucket, Key, Body):
        self.calls.append(f"put {Key}")
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def upload_file(self, Filename, Bucket, Key):
        self.calls.append(f"upload {Key}")
        with open(Filename, "rb") as f:
            self.objects[(Bucket, Key)] = f.read()

    def download_file(self, Bucket, Key, Filename):
        self.calls.append(f"download {Key}")
        with open(Filename, "wb") as f:
            f.write(self.objects[(Bucket, Key)])


def test_s3_store_is_content_addressed_and_caches_for_duckdb(tmp_path):
    s3 = FakeS3()
    store = S3BlobStore("provenant-staging", prefix="blobs", client=s3, cache_dir=tmp_path / "cache")
    data = b"%PDF-1.7 lease"
    sha = hashlib.sha256(data).hexdigest()
    store.put(sha, data)
    store.put(sha, data)  # already there: not written twice
    assert s3.calls == [f"put blobs/{sha[:2]}/{sha}"] and store.get(sha) == data

    ledger = tmp_path / "gl.parquet"
    ledger.write_bytes(b"PAR1 parquet bytes")
    big = hashlib.sha256(ledger.read_bytes()).hexdigest()
    store.put_file(big, ledger)
    assert not ledger.exists()  # moved in, like the local store
    path = store.local_path(big)
    assert path.read_bytes() == b"PAR1 parquet bytes"
    store.local_path(big)  # cached: content-addressed files never go stale
    assert sum(c.startswith("download") for c in s3.calls) == 1


def test_make_store_reads_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVENANT_STORAGE", raising=False)
    assert isinstance(make_store(tmp_path), BlobStore)
    monkeypatch.setenv("PROVENANT_STORAGE", "s3")
    monkeypatch.delenv("PROVENANT_S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="PROVENANT_S3_BUCKET"):
        make_store()


def test_s3_compatible_endpoints_use_path_style_addressing(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    seen = []
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: seen.append(kw) or object())
    monkeypatch.setenv("PROVENANT_S3_ENDPOINT", "https://project.supabase.co/storage/v1/s3")
    S3BlobStore("bucket")
    assert seen[-1]["endpoint_url"] == "https://project.supabase.co/storage/v1/s3"
    assert seen[-1]["config"].s3 == {"addressing_style": "path"}
    monkeypatch.delenv("PROVENANT_S3_ENDPOINT")
    S3BlobStore("bucket")  # AWS itself: the default addressing
    assert seen[-1]["endpoint_url"] is None and seen[-1]["config"] is None
