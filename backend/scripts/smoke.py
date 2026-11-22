"""Smoke test a deployed Provenant: is it up, migrated, signed-in requests work, and isolation holds.

    python scripts/smoke.py https://staging-api.example.com
    SMOKE_TOKEN=<an OIDC access token for a staging test user> python scripts/smoke.py https://...
    SMOKE_DEV_AUTH=1 python scripts/smoke.py http://localhost:8000          (local compose, dev auth)

Standard library only, so it runs in the API image, in CI or on a laptop. Exit code 0 when every check passes;
otherwise each failure is printed and the exit code is 1. It only reads, so it is safe against staging after
every deploy. Never point it at production with a real user's token.
"""
import json
import os
import sys
import urllib.error
import urllib.request

RESULTS: list[bool] = []


def get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read(400).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(400).decode(errors="replace")
    except OSError as exc:
        return 0, str(exc)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} {name}{f': {detail}' if detail and not ok else ''}")


def main(base: str) -> int:
    base = base.rstrip("/")
    if os.environ.get("SMOKE_DEV_AUTH") == "1":
        headers = {"X-Tenant-Id": "smoke-firm", "X-User": "smoke@provenant", "X-Roles": "preparer"}
    elif token := os.environ.get("SMOKE_TOKEN"):
        headers = {"Authorization": f"Bearer {token}"}
    else:
        headers = {}

    status, body = get(f"{base}/health")
    check("API is up (/health)", status == 200 and json.loads(body or "{}").get("status") == "ok", f"{status} {body[:120]}")
    status, _ = get(f"{base}/clients")
    check("unauthenticated requests are refused", status in (401, 403), f"got {status}")
    if headers:
        status, body = get(f"{base}/clients", headers)
        check("signed-in request works (schema migrated, database reachable)", status == 200, f"{status} {body[:200]}")
        status, _ = get(f"{base}/clients/00000000-0000-0000-0000-000000000000/years/2026-12-31/mapping", headers)
        check("a client outside the firm is not found", status == 404, f"got {status}")
    else:
        print("skip signed-in checks: set SMOKE_TOKEN (or SMOKE_DEV_AUTH=1 for local compose)")
    print(f"{sum(RESULTS)}/{len(RESULTS)} checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
