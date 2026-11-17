"""Local development: migrate, seed demo leases, run the API with an in-process worker and header auth.

    python scripts/dev.py            # http://127.0.0.1:8000  (docs at /docs)
    python scripts/dev.py --reset    # start from an empty database and storage

Never use this outside your own machine: identity comes from request headers.
"""
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DATA = BACKEND / ".dev"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(DATA / 'provenant.db').as_posix()}")
os.environ.setdefault("PROVENANT_STORAGE_DIR", str(DATA / "storage"))
os.environ["PROVENANT_DEV_AUTH"] = "1"
os.environ["PROVENANT_INLINE_WORKER"] = "1"


def main() -> None:
    import uvicorn

    from provenant.api.migrate import upgrade

    if "--reset" in sys.argv:
        shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(exist_ok=True)
    upgrade(os.environ["DATABASE_URL"])
    uvicorn.run("provenant.api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
