"""One-time Google OAuth consent (only for the gsheet_api destination and Calendar). Tokens go to ~/.config/clockout/google/.

  auth.py calendar   # sign in with your WORK account  -> read-only calendar
  auth.py sheets     # sign in with your PERSONAL account -> spreadsheets
"""
import sys

from common import SCOPES, google_creds, token_path

if __name__ == "__main__":
    kinds = sys.argv[1:] or list(SCOPES)
    for kind in kinds:
        if kind not in SCOPES:
            sys.exit(f"usage: auth.py [{'|'.join(SCOPES)}]")
        print(f"\n>>> Signing in for {kind!r} — pick the "
              f"{'WORK' if kind == 'calendar' else 'PERSONAL'} Google account in the browser.")
        token_path(kind).unlink(missing_ok=True)  # force fresh consent
        google_creds(kind, interactive=True)
        print(f"ok: saved {token_path(kind)}")
