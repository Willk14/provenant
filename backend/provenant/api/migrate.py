"""Schema migrations. CLI: python -m provenant.api.migrate [revision]  (uses DATABASE_URL)."""
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


if __name__ == "__main__":
    upgrade(os.environ.get("DATABASE_URL", "sqlite:///./provenant.db"), *sys.argv[1:2])
