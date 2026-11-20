"""Shared helpers: dates/timezones, Google auth, the entries file."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config

ROOT = Path(__file__).resolve().parent.parent
CACHE = config.HOME / "drafts"

# Google: separate logins so each account only gets what it needs.
SCOPES = {
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
    "sheets": ["https://www.googleapis.com/auth/spreadsheets"],
}
CREDENTIALS_PATH = config.GOOGLE / "credentials.json"


def token_path(kind: str) -> Path:
    return config.GOOGLE / f"token-{kind}.json"


# ---------------------------------------------------------------- dates
def tz(settings: dict | None = None) -> ZoneInfo:
    return ZoneInfo((settings or config.personal()).get("timezone") or "UTC")


def parse_day(s: str | None, settings: dict | None = None) -> date:
    today = datetime.now(tz(settings)).date()
    if not s or s == "today":
        return today
    if s == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(s)


def parse_range(s: str | None, settings: dict | None = None) -> list[date]:
    """'today' | 'yesterday' | 'YYYY-MM-DD' | 'A..B' -> list of dates."""
    if s and ".." in s:
        a, b = (parse_day(x, settings) for x in s.split("..", 1))
        return [a + timedelta(days=i) for i in range((b - a).days + 1)]
    return [parse_day(s, settings)]


def day_bounds(d: date, settings: dict | None = None) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=tz(settings))
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------- google
def google_creds(kind: str, interactive: bool = False):
    """Stored Google creds for `kind` (calendar|sheets). None if not set up (unless interactive)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scopes, tok = SCOPES[kind], token_path(kind)
    sa = config.GOOGLE / "service-account.json"
    if kind == "sheets" and sa.exists():
        from google.oauth2.service_account import Credentials as SACredentials
        return SACredentials.from_service_account_file(str(sa), scopes=scopes)
    creds = Credentials.from_authorized_user_file(str(tok), scopes) if tok.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tok.write_text(creds.to_json())
            return creds
        except Exception as e:  # revoked / expired refresh token
            if not interactive:
                print(f"warning: Google {kind} token refresh failed ({e}); run scripts/auth.py {kind}", file=sys.stderr)
                return None
    if not interactive:
        return None
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS_PATH.exists():
        sys.exit(f"error: put your OAuth Desktop client JSON at {CREDENTIALS_PATH}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes)
    creds = flow.run_local_server(port=0, prompt="select_account consent")
    config.GOOGLE.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json())
    os.chmod(tok, 0o600)
    return creds


# ---------------------------------------------------------------- entries
# The approved draft every exporter consumes. One entry = one timesheet row.
#   {"profile": "acme-web",            project slug (routes to that project's layout/destinations)
#    "date": "2026-11-19",             required
#    "person": "...",                  optional, defaults to the project's person
#    "task": "- Did X\n- Did Y",       may be empty (date-only row)
#    "hours": 6.5,                     number or empty
#    "remarks": "",
#    "project": "group/repo",          code project the work happened in
#    "refs": ["https://.../-/merge_requests/42"],  links backing the entry
#    "fields": {"Work package": "123"},            answers for `ask` columns, or per-column overrides
#    "kind": "work" | "meeting" | "empty", "estimated": true}
def load_entries(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    entries = data["entries"] if isinstance(data, dict) else data
    default_profile = data.get("profile") if isinstance(data, dict) else None
    for e in entries:
        if "date" not in e:
            sys.exit(f"error: entry without date: {e}")
        date.fromisoformat(e["date"])
        e.setdefault("profile", default_profile)
        if not e["profile"]:
            sys.exit(f"error: entry for {e['date']} has no 'profile' (project slug)")
        e.setdefault("person", config.settings(e["profile"]).get("person", ""))
        e.setdefault("task", "")
        e.setdefault("remarks", "")
        e.setdefault("refs", [])
        e.setdefault("fields", {})
        h = e.get("hours")
        e["hours"] = "" if h in (None, "") else float(h)
    return entries
