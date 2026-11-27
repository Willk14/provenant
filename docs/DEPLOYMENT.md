# Deploying Provenant

Provenant deploys as three processes from two images, plus a database and an object store.

| Piece | Image / service | Command |
|---|---|---|
| API | `backend/Dockerfile` | default: `uvicorn provenant.api.main:app` on port 8000 (health: `/health`) |
| Worker | `backend/Dockerfile` | `python -m provenant.worker` (extraction jobs from the outbox) |
| Migrations | `backend/Dockerfile` | `python -m provenant.api.migrate`, as the table owner, before a new version starts |
| Web | `frontend/Dockerfile` | Next.js standalone server on port 3100; forwards `/api` to `PROVENANT_API_URL` (a build argument) |
| Database | PostgreSQL 16 | owner role for migrations; a separate non-owner app role for the API and worker, so row-level security applies |
| Files | S3-compatible bucket | `PROVENANT_STORAGE=s3` (AWS S3, Cloudflare R2, Supabase Storage, MinIO); content-addressed by SHA-256 |
| Sign-in | an OIDC provider | tokens carry the firm (`tenant_id`) and the user's roles; dev auth off |

DuckDB (journal entry tests) runs inside the API; it reads the ledger's Parquet from a local cache of the bucket
(`PROVENANT_CACHE_DIR`), capped at 512 MB of memory.

## Run the whole stack locally

```bash
docker compose up --build                     # Postgres, migrations, API, worker, web: http://localhost:3100
cd backend && .venv/Scripts/python scripts/seed_demo.py http://localhost:8000   # demo data
SMOKE_DEV_AUTH=1 python backend/scripts/smoke.py http://localhost:8000           # smoke test
docker compose down --volumes                 # remove containers and data
```

Compose uses dev auth and local credentials (`deploy/postgres-init.sql` creates the app role). CI runs the same
stack and smoke test on every push (`stack` job in `.github/workflows/ci.yml`), so the images always build and
start.

## Environments

**Staging** is production-shaped and holds demo data only, never client data. Its own database, bucket, secrets and
OIDC client; settings in `deploy/staging.env.example`. Deploy automatically from `main` once CI is green, then run
the smoke test with a staging test user's token (`SMOKE_TOKEN=... python backend/scripts/smoke.py <api url>`).

**Production** is promoted by hand from a tagged release. Order of a release:
1. Take or confirm a recent backup (point-in-time recovery on).
2. Run migrations with the owner's URL and `PROVENANT_APP_ROLE` set (grants and row-level security policies).
3. Start the new API, worker and web versions.
4. Run the smoke test's unauthenticated checks (no token against production).

Before real client files: a restore drill from backup, the bucket private with encryption at rest, secrets in the
host's secret store, and logs that do not contain document contents.

## Fly.io (the chosen host)

`backend/fly.toml` (API, app `provenant-ke-api`) and `frontend/fly.toml` (web, app `provenant-ke`), both in `lhr`, the
Fly region closest to the Supabase database. The database and sign-in are the Supabase project; files go to its
private `provenant-files` bucket over the S3 API (path-style addressing). The worker runs inside the API
(`PROVENANT_INLINE_WORKER=1`), so the API keeps one machine running and is deployed with `--ha=false`; split it into
its own process group when the load needs it. Fly's `release_command` runs `scripts/release.py`, which migrates with
`MIGRATION_DATABASE_URL` (the owner) before the new version starts.

```powershell
fly auth login
powershell -ExecutionPolicy Bypass -File .\deploy\fly\deploy.ps1        # secrets, migrations, API, web
```

The script reads `backend/.env` and `frontend/.env.local`, asks once (hidden) for the Supabase Storage S3 keys, and sends
the secrets straight to Fly's secret store. Then, in Supabase, set the Site URL to `https://provenant-ke.fly.dev` and
add `https://provenant-ke.fly.dev/auth/callback` to the Redirect URLs, and turn on custom SMTP so invitations reach
any address.

## Decisions for the owner

- **Host.** Any container host works (for example Fly.io, Render or Railway for the API, worker and web; Supabase or
  Neon for Postgres; Supabase Storage or Cloudflare R2 for files). Nothing in the code depends on the choice.
- **Where data lives.** Client tax records are personal and financial data under Kenya's Data Protection Act 2019,
  which sets conditions on transferring data outside Kenya. Choose regions with that in mind and confirm the basis
  with a data-protection adviser before production holds client data.
- **Sign-in provider**, and how it puts the firm and roles into tokens (for Supabase Auth, a custom access-token
  hook).

## Not done yet

Publishing images to a registry and deploying (waits for the host), per-pull-request preview environments,
monitoring and alerting.
