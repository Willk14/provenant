"""Send one test email with the SMTP settings in backend/.env, to check them before relying on notifications.

    python scripts/send_test_email.py you@example.com
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dev import load_local_settings  # noqa: E402

from provenant.api.notifications import send_email, smtp_configured  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        raise SystemExit("usage: python scripts/send_test_email.py you@example.com")
    load_local_settings()
    if not smtp_configured():
        raise SystemExit("Email is not configured: set PROVENANT_SMTP_HOST and PROVENANT_SMTP_FROM in backend/.env")
    send_email(sys.argv[1], "Provenant test email",
               "This is a test from Provenant. If you can read it, work notifications will reach your team.")
    print(f"Sent a test email to {sys.argv[1]}.")


if __name__ == "__main__":
    main()
