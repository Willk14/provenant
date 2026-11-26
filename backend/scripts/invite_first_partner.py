"""Invite the first partner of a firm, who can then invite everyone else from the Team page.

    python scripts/invite_first_partner.py you@example.com [firm-id]

Uses PROVENANT_SUPABASE_URL and PROVENANT_SUPABASE_SECRET_KEY from backend/.env. Supabase emails the invitation; the
link opens Provenant's "Choose a password" page. The firm id defaults to demo-firm, so the demo data stays visible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dev import load_local_settings  # noqa: E402

from provenant.api.members import EMAIL, InviteFailed, send_invitation  # noqa: E402


def main() -> None:
    if len(sys.argv) not in (2, 3) or not EMAIL.fullmatch(sys.argv[1]):
        raise SystemExit("usage: python scripts/invite_first_partner.py you@example.com [firm-id]")
    load_local_settings()
    email, firm = sys.argv[1].strip().lower(), (sys.argv[2] if len(sys.argv) == 3 else "demo-firm")
    try:
        sent = send_invitation(email, firm, ["partner"])
    except InviteFailed as exc:
        raise SystemExit(f"Not invited: {exc}") from exc
    if not sent:
        raise SystemExit("Sign-in is not configured: set PROVENANT_SUPABASE_URL and PROVENANT_SUPABASE_SECRET_KEY")
    print(f"Invited {email} as a partner of {firm}. Open the email from Supabase and choose a password.")


if __name__ == "__main__":
    main()
