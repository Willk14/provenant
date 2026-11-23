import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev.py"


def _dev():
    spec = importlib.util.spec_from_file_location("provenant_dev_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_server_still_recovering_is_retried_with_a_fresh_handle(monkeypatch):
    # Three calls: the start times out, then the server is up but still recovering, then it is ready. A handle
    # left in the cache by a failed call must be gone before the next one, or the half-built handle is returned.
    dev = _dev()
    monkeypatch.setattr("time.sleep", lambda _: None)
    key, ready, cache, seen = dev.PG_DIR.expanduser().resolve(), object(), {}, []

    def get_server(path, cleanup_mode):
        seen.append(dict(cache))
        if key in cache:
            return cache[key]  # what pgserver does with a cached handle, even a broken one
        cache[key] = "half-built"
        if len(seen) == 1:
            raise subprocess.TimeoutExpired("pg_ctl", 10)
        if len(seen) == 2:
            raise AssertionError("status is starting")
        cache[key] = ready
        return ready

    pgserver = SimpleNamespace(get_server=get_server,
                               postgres_server=SimpleNamespace(PostgresServer=SimpleNamespace(_instances=cache)))
    assert dev.start_postgres(pgserver, attempts=3) is ready
    assert seen == [{}, {}, {}]
