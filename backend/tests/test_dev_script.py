import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev.py"


def _dev():
    spec = importlib.util.spec_from_file_location("provenant_dev_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supabase_urls_use_the_pooler_and_separate_roles(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PROVENANT_SUPABASE_REF=abc\nPROVENANT_SUPABASE_HOST=pooler.example\n"
                   "PROVENANT_SUPABASE_OWNER_PASSWORD=own/er\nPROVENANT_SUPABASE_APP_PASSWORD=app@1\n", encoding="utf-8")
    owner, app = _dev().supabase_urls(env)
    assert owner == "postgresql+psycopg://provenant_owner.abc:own%2Fer@pooler.example:5432/postgres?sslmode=require"
    assert app == "postgresql+psycopg://provenant_app.abc:app%401@pooler.example:5432/postgres?sslmode=require"


def test_supabase_settings_must_be_complete(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PROVENANT_SUPABASE_REF=abc\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="PROVENANT_SUPABASE_HOST"):
        _dev().supabase_urls(env)


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


def test_email_settings_load_from_the_env_file_without_overriding_the_environment(tmp_path, monkeypatch):
    for key in ("PROVENANT_SMTP_HOST", "PROVENANT_SMTP_PORT", "PROVENANT_SMTP_FROM", "PROVENANT_SMTP_USER"):
        monkeypatch.delenv(key, raising=False)
    dev = _dev()
    monkeypatch.setenv("PROVENANT_SMTP_PORT", "465")  # already set: the file must not change it
    env = tmp_path / ".env"
    env.write_text("PROVENANT_SMTP_HOST=smtp-relay.brevo.com\nPROVENANT_SMTP_PORT=587\n"
                   "# PROVENANT_SMTP_USER=commented-out\nPROVENANT_SMTP_FROM=\nPROVENANT_SUPABASE_REF=abc\n",
                   encoding="utf-8")
    dev.load_local_settings(env)
    import os
    assert os.environ["PROVENANT_SMTP_HOST"] == "smtp-relay.brevo.com"
    assert os.environ["PROVENANT_SMTP_PORT"] == "465"
    assert "PROVENANT_SMTP_FROM" not in os.environ  # empty values are skipped
    assert "PROVENANT_SMTP_USER" not in os.environ  # comments are skipped
    assert "PROVENANT_SUPABASE_REF" not in os.environ  # only email and link settings are loaded
    monkeypatch.delenv("PROVENANT_SMTP_HOST")
