"""Append rows to a Google Sheet with the Sheets API.

Auth: a service account at <config>/google/service-account.json that the sheet is shared with,
or OAuth via `scripts/auth.py sheets`.
dest: {type: gsheet_api, sheet_id: <id from URL>, tab: Timesheet}
Checks the header row against the layout, appends below the last row; only --replace-date
removes rows (this person's rows on the same dates).
"""
import re
import sys
from datetime import date, timedelta

import layout as L
from common import google_creds

DESCRIPTION = "append to a Google Sheet via the Sheets API"
REQUIRED = ["sheet_id"]
EPOCH = date(1899, 12, 30)


def _cell_date(v):
    if isinstance(v, (int, float)):
        return EPOCH + timedelta(days=int(v))
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        return None


def export(entries, dest, layout, opts) -> str:
    creds = google_creds("sheets")
    if not creds:
        sys.exit("error: gsheet_api: Google not authorised (scripts/auth.py sheets, or google/service-account.json)")
    from googleapiclient.discovery import build

    sid, tab = dest["sheet_id"], dest.get("tab", "Timesheet")
    heads, kinds = L.headers(layout), L.column_kinds(layout)
    ncol = len(heads)
    last_col = chr(ord("A") + ncol - 1)
    api = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
    rng = lambda a1: f"'{tab}'!{a1}"

    meta = api.get(spreadsheetId=sid, fields="sheets.properties").execute()
    gid = next((s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == tab), None)
    if gid is None:
        resp = api.batchUpdate(spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
            "title": tab, "gridProperties": {"frozenRowCount": 1}}}}]}).execute()
        gid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    values = api.values().get(spreadsheetId=sid, range=rng(f"A:{last_col}"), valueRenderOption="UNFORMATTED_VALUE",
                              dateTimeRenderOption="SERIAL_NUMBER").execute().get("values", [])
    if not values:
        api.values().update(spreadsheetId=sid, range=rng(f"A1:{last_col}1"), valueInputOption="RAW",
                            body={"values": [heads]}).execute()
    elif [str(c).strip() for c in values[0][:ncol]] != heads:
        sys.exit(f"error: gsheet_api: header row is {values[0][:ncol]} but the layout expects {heads}. "
                 "The sheet changed — update the project's layout (/clockout setup) before writing.")

    pcol, dcol = L.key_columns(layout)
    person = entries[0]["person"] if entries else ""
    dates = {date.fromisoformat(e["date"]) for e in entries}
    clashes = []
    if dcol is not None:
        for i, r in enumerate(values[1:], start=1):
            if len(r) > dcol and _cell_date(r[dcol]) in dates and (pcol is None or (len(r) > pcol and str(r[pcol]).strip() == person)):
                clashes.append(i)
    if clashes and not getattr(opts, "replace_date", False):
        sys.exit(f"error: gsheet_api: rows already exist for these dates (sheet rows {[i + 1 for i in clashes]}); "
                 "re-run with --replace-date to replace them")
    if clashes:
        api.batchUpdate(spreadsheetId=sid, body={"requests": [
            {"deleteDimension": {"range": {"sheetId": gid, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1}}}
            for i in sorted(clashes, reverse=True)]}).execute()

    rows = []
    for row in L.typed_rows(entries, layout):
        rows.append([(date.fromisoformat(v) - EPOCH).days if k["kind"] == "date" and v else v for v, k in zip(row, kinds)])
    resp = api.values().append(spreadsheetId=sid, range=rng(f"A:{last_col}"), valueInputOption="RAW",
                               insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
    updated = resp["updates"]["updatedRange"]
    m = re.search(r"![A-Z]+(\d+):[A-Z]+(\d+)", updated)
    first, last = int(m.group(1)) - 1, int(m.group(2))
    grid = lambda c0, c1: {"sheetId": gid, "startRowIndex": first, "endRowIndex": last,
                           "startColumnIndex": c0, "endColumnIndex": c1}
    reqs = [{"repeatCell": {"range": grid(0, ncol), "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
                            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}}]
    for i, k in enumerate(kinds):
        if k["kind"] == "date":
            nf = {"type": "DATE", "pattern": k["pattern"]}
        elif k["kind"] == "number":
            nf = {"type": "NUMBER", "pattern": "0.##"}
        else:
            continue
        reqs.append({"repeatCell": {"range": grid(i, i + 1), "cell": {"userEnteredFormat": {"numberFormat": nf}},
                                    "fields": "userEnteredFormat.numberFormat"}})
    api.batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    return f"appended {updated} (replaced {len(clashes)}) — https://docs.google.com/spreadsheets/d/{sid}/edit#gid={gid}"
