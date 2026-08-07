"""One-time interactive Garmin login. Run this ONCE (and again only if tokens
expire / are revoked).

    python scripts/bootstrap_login.py

It prompts for your password (never echoed, never written to disk) and, if 2FA
is on, the MFA code. On success it writes an OAuth token cache to the directory
in config.TOKENSTORE (default ~/.garminconnect). After that, every other part
of the app authenticates from that cache with no password and no MFA until the
long-lived refresh token expires (roughly a year, unless revoked).

Optional: `--use-keyring` also stores the password in the OS credential vault
(Windows Credential Manager) so you can re-bootstrap non-interactively later.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/bootstrap_login.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from garmin_coach import auth  # noqa: E402

KEYRING_SERVICE = "garmin-coach"


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time Garmin token bootstrap.")
    parser.add_argument(
        "--use-keyring",
        action="store_true",
        help="Also store the password in the OS credential vault for later re-bootstrap.",
    )
    args = parser.parse_args()

    email = config.GARMIN_EMAIL or input("Garmin email: ").strip()
    if not email:
        print("No email provided. Set GARMIN_EMAIL in .env or type it above.")
        return 1

    password = None
    if args.use_keyring:
        try:
            import keyring

            password = keyring.get_password(KEYRING_SERVICE, email)
            if password:
                print("Using password from OS credential vault.")
        except Exception as exc:  # noqa: BLE001
            print(f"keyring unavailable ({exc}); falling back to prompt.")

    if not password:
        password = getpass.getpass(f"Garmin password for {email}: ")

    def prompt_mfa() -> str:
        return input("MFA code (from your authenticator/SMS): ").strip()

    print("Logging in to Garmin Connect ...")
    try:
        client = auth.bootstrap_login(email, password, prompt_mfa=prompt_mfa)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"\nLogin FAILED: {msg}")
        if "429" in msg or "rate limit" in msg.lower() or "too many" in msg.lower():
            print(
                "\n  --> Garmin has TEMPORARILY rate-limited your IP (HTTP 429).\n"
                "      This is triggered by repeated login attempts in a short window.\n"
                "      Wait ~30-60 minutes, then run this script again ONCE.\n"
                "      Do not retry rapidly; each attempt resets the cool-down."
            )
        elif "authentication" in msg.lower() or "401" in msg or "credential" in msg.lower():
            print("\n  --> Check your email/password. If you use 2FA, enter the current MFA code.")
        return 2

    name = client.get_full_name()
    print(f"\nSuccess. Authenticated as: {name}")
    print(f"Token cache written to: {config.TOKENSTORE}")

    if args.use_keyring:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, email, password)
            print("Password stored in OS credential vault.")
        except Exception as exc:  # noqa: BLE001
            print(f"Could not store password in keyring: {exc}")

    print("\nYou can now run the pipeline unattended:")
    print("  python -m garmin_coach.ingest.run --nightly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
