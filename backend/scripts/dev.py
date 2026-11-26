"""Local development: migrate, then run the API with an in-process worker and header auth.

    python scripts/dev.py            # Postgres (embedded), http://127.0.0.1:8000  (docs at /docs)
    python scripts/dev.py --reset    # start from an empty database and storage
    python scripts/dev.py --sqlite   # the old single-file SQLite database instead
    python scripts/dev.py --supabase # the hosted Supabase database, using the settings in backend/.env

Postgres runs embedded (pgserver, no Docker), in the same data directory the Postgres test suite uses, as a
separate `provenant` database. Migrations run as the owner; the API connects as `provenant_app`, a non-superuser,
so row-level security applies exactly as deployed. The server's port can change between restarts; this script
asks the server for it every time.

Never use this outside your own machine: identity comes from request headers.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DATA = BACKEND / ".dev"
PG_DIR = Path(os.environ.get("PROVENANT_DEV_PGDATA", Path(tempfile.gettempdir()) / "pv-pg"))
APP_ROLE, APP_PASSWORD, DATABASE = "provenant_app", "provenant_app_dev", "provenant"

LOCAL_SETTINGS = ("PROVENANT_SMTP_", "PROVENANT_INBOUND_", "PROVENANT_APP_URL", "PROVENANT_DEV_EMAILS",
                  "PROVENANT_SUPABASE_URL", "PROVENANT_SUPABASE_SECRET_KEY")


def load_local_settings(env_file: Path = BACKEND / ".env") -> None:
    """Email, link and sign-in settings from the git-ignored backend/.env. Values already in the environment win, empty
    values are skipped, and nothing is printed."""
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        if key.startswith(LOCAL_SETTINGS) and value:
            os.environ.setdefault(key, value)



def configure_environment() -> None:
    """Settings for the local server. Called from main(), not at import, so tests and helper scripts that import this
    module do not pick up the real email and sign-in settings from backend/.env."""
    os.environ.setdefault("PROVENANT_STORAGE_DIR", str(DATA / "storage"))
    os.environ["PROVENANT_INLINE_WORKER"] = "1"
    load_local_settings()
    # Development personas, unless real Supabase sign-in is configured (the two cannot be combined).
    os.environ["PROVENANT_DEV_AUTH"] = "0" if os.environ.get("PROVENANT_SUPABASE_URL") else "1"


def start_postgres(pgserver, attempts: int = 36):
    """Start or attach to the embedded server. After the machine sleeps or restarts, Postgres replays its log
    before accepting connections, which can outlast pgserver's 10-second start timeout: keep trying for up to
    three minutes instead of failing (the server keeps recovering in the background meanwhile).

    A failed start leaves a handle with no server details in pgserver's cache, and attaching to a server that is
    still recovering fails its readiness assertion, so each retry drops the cached handle and asks again."""
    import subprocess
    import time

    for attempt in range(1, attempts + 1):
        try:
            return pgserver.get_server(str(PG_DIR), cleanup_mode=None)  # attaches if it is already running
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, AssertionError) as exc:
            pgserver.postgres_server.PostgresServer._instances.pop(PG_DIR.expanduser().resolve(), None)
            if attempt == attempts:
                raise RuntimeError(f"embedded Postgres did not start; see {PG_DIR / 'log'}") from exc
            print(f"Postgres is still starting (recovering after a restart?); retrying ({attempt}/{attempts})")
            time.sleep(5)


def postgres(reset: bool) -> tuple[str, str]:
    """(owner URL for migrations, app-role URL for the API), creating the role and database if needed."""
    import pgserver
    import psycopg
    from sqlalchemy.engine import make_url

    server = start_postgres(pgserver)
    admin = make_url(server.get_uri())
    with psycopg.connect(admin.set(drivername="postgresql").render_as_string(hide_password=False),
                         autocommit=True) as c:
        if not c.execute("select 1 from pg_roles where rolname = %s", (APP_ROLE,)).fetchone():
            c.execute(f"create role {APP_ROLE} login password '{APP_PASSWORD}'")
        if reset:
            c.execute(f"drop database if exists {DATABASE} with (force)")
        if not c.execute("select 1 from pg_database where datname = %s", (DATABASE,)).fetchone():
            c.execute(f"create database {DATABASE}")
    owner = admin.set(drivername="postgresql+psycopg", database=DATABASE)
    app = owner.set(username=APP_ROLE, password=APP_PASSWORD)
    return owner.render_as_string(hide_password=False), app.render_as_string(hide_password=False)


def supabase_urls(env_file: Path = BACKEND / ".env") -> tuple[str, str]:
    """(owner URL for migrations, app-role URL for the API) through the IPv4 session pooler. Secrets stay in the
    git-ignored file and are never printed."""
    from sqlalchemy import URL

    if not env_file.exists():
        raise SystemExit(f"--supabase needs {env_file}")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    need = ("PROVENANT_SUPABASE_REF", "PROVENANT_SUPABASE_HOST", "PROVENANT_SUPABASE_OWNER_PASSWORD",
            "PROVENANT_SUPABASE_APP_PASSWORD")
    if missing := [k for k in need if not values.get(k)]:
        raise SystemExit(f"--supabase: missing from {env_file.name}: {', '.join(missing)}")

    def url(role: str, password: str) -> str:
        return URL.create("postgresql+psycopg", username=f"{role}.{values['PROVENANT_SUPABASE_REF']}", password=password,
                          host=values["PROVENANT_SUPABASE_HOST"], port=5432, database="postgres",
                          query={"sslmode": "require"}).render_as_string(hide_password=False)

    return url("provenant_owner", values["PROVENANT_SUPABASE_OWNER_PASSWORD"]), url(APP_ROLE, values["PROVENANT_SUPABASE_APP_PASSWORD"])


def main() -> None:
    import uvicorn

    from provenant.api.migrate import upgrade

    configure_environment()
    reset = "--reset" in sys.argv
    if "--supabase" in sys.argv and (reset or "--sqlite" in sys.argv):
        raise SystemExit("--supabase cannot be combined with --reset or --sqlite")
    if reset:
        shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(exist_ok=True)
    if "--supabase" in sys.argv:
        owner, app = supabase_urls()
        os.environ["PROVENANT_APP_ROLE"] = APP_ROLE
        os.environ.setdefault("PROVENANT_DB_POOL_SIZE", "3")  # the free plan's pooler allows few connections
        os.environ.setdefault("PROVENANT_DB_MAX_OVERFLOW", "2")
        upgrade(owner)
        os.environ["DATABASE_URL"] = app
        print(f"Postgres: Supabase session pooler as {APP_ROLE} (row-level security on)")
    elif "--sqlite" in sys.argv:
        os.environ.setdefault("DATABASE_URL", f"sqlite:///{(DATA / 'provenant.db').as_posix()}")
        upgrade(os.environ["DATABASE_URL"])
    elif "DATABASE_URL" in os.environ:
        upgrade(os.environ.get("MIGRATION_DATABASE_URL", os.environ["DATABASE_URL"]))
    else:
        owner, app = postgres(reset)
        os.environ["PROVENANT_APP_ROLE"] = APP_ROLE  # migrations grant the app role what it needs
        upgrade(owner)
        os.environ["DATABASE_URL"] = app
        print(f"Postgres: {app.split('@')[-1]} as {APP_ROLE} (row-level security on)")
    if os.environ.get("PROVENANT_SUPABASE_URL"):
        print(f"Sign-in: Supabase at {os.environ['PROVENANT_SUPABASE_URL']}; people sign in with their own email")
    else:
        print("Sign-in: demo people (Pat, Rae, Pam); set PROVENANT_SUPABASE_URL in backend/.env for real sign-in")
    if os.environ.get("PROVENANT_SMTP_HOST") and os.environ.get("PROVENANT_SMTP_FROM"):
        print(f"Email: sending through {os.environ['PROVENANT_SMTP_HOST']} as {os.environ['PROVENANT_SMTP_FROM']}")
    else:
        print("Email: not configured (set PROVENANT_SMTP_* in backend/.env); notifications are saved, not sent")
    uvicorn.run("provenant.api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
