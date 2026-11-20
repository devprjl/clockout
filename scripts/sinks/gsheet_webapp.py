"""Append rows to a Google Sheet through the Apps Script web app in apps-script/Code.gs.

No Google Cloud project needed; the sheet can stay private.
dest: {type: gsheet_webapp, url_env: SHEET_WEBAPP_URL, token_env: SHEET_WEBAPP_TOKEN, tab: Timesheet}
The web app checks that the tab's header row matches the layout, appends below the last row,
and (only with --replace-date) first removes this person's rows for the same dates.
"""
import sys

import requests

import layout as L
from config import secret

DESCRIPTION = "append to a Google Sheet via the Apps Script web app"
REQUIRED = ["url_env", "token_env"]


def check(dest) -> list[str]:
    out = [f"gsheet_webapp: missing '{k}'" for k in REQUIRED if not dest.get(k)]
    out += [f"gsheet_webapp: secret {dest[k]} is not set" for k in REQUIRED if dest.get(k) and not secret(dest[k])]
    return out


def export(entries, dest, layout, opts) -> str:
    person_col, date_col = L.key_columns(layout)
    payload = {
        "action": "append",
        "token": secret(dest["token_env"]),
        "tab": dest.get("tab", "Timesheet"),
        "headers": L.headers(layout),
        "columns": L.column_kinds(layout),
        "person_col": person_col, "date_col": date_col,
        "person": entries[0]["person"] if entries else "",
        "replace": bool(getattr(opts, "replace_date", False)),
        "rows": L.typed_rows(entries, layout),
        "dates": sorted({e["date"] for e in entries}),
    }
    r = requests.post(secret(dest["url_env"]), json=payload, timeout=60)
    r.raise_for_status()
    try:
        out = r.json()
    except ValueError:
        sys.exit(f"error: web app did not return JSON (deployed with access 'Anyone'?): {r.text[:300]}")
    if out.get("error"):
        sys.exit(f"error: gsheet_webapp: {out['error']}" + (f" (sheet rows {out['rows']})" if out.get("rows") else "")
                 + ("; re-run with --replace-date to replace them" if out.get("rows") else ""))
    return f"appended {out['appended']} (replaced {out['replaced']}) — {out['sheet_url']}"
