"""Local development: migrate, then run the API with an in-process worker and header auth.

    python scripts/dev.py            # Postgres (embedded), http://127.0.0.1:8000  (docs at /docs)
    python scripts/dev.py --reset    # start from an empty database and storage
    python scripts/dev.py --sqlite   # the old single-file SQLite database instead

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

os.environ.setdefault("PROVENANT_STORAGE_DIR", str(DATA / "storage"))
os.environ["PROVENANT_DEV_AUTH"] = "1"
os.environ["PROVENANT_INLINE_WORKER"] = "1"


def postgres(reset: bool) -> tuple[str, str]:
    """(owner URL for migrations, app-role URL for the API), creating the role and database if needed."""
    import pgserver
    import psycopg
    from sqlalchemy.engine import make_url

    server = pgserver.get_server(str(PG_DIR), cleanup_mode=None)  # attaches if it is already running
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


def main() -> None:
    import uvicorn

    from provenant.api.migrate import upgrade

    reset = "--reset" in sys.argv
    if reset:
        shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(exist_ok=True)
    if "--sqlite" in sys.argv:
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
    uvicorn.run("provenant.api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
