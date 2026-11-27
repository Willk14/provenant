"""Release step for hosted deploys (Fly's release_command): run migrations as the table owner.

Reads MIGRATION_DATABASE_URL (the owner's URL) and PROVENANT_APP_ROLE (the role the API connects as, which the
migrations grant access to and put under row-level security). The API itself only ever gets the app role's URL.
"""
import os
import sys

from provenant.api.migrate import upgrade


def main() -> None:
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit("MIGRATION_DATABASE_URL is not set: the release step needs the table owner's database URL")
    if not os.environ.get("PROVENANT_APP_ROLE"):
        raise SystemExit("PROVENANT_APP_ROLE is not set: migrations must know which role the API connects as")
    upgrade(url)
    print("Migrations are up to date.", file=sys.stderr)


if __name__ == "__main__":
    main()
